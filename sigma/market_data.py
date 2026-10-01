"""sigma.market_data - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
from discord.ext import tasks
import os, json, re, hashlib, aiohttp, io, asyncio, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dt_time
import logging, logging.handlers, builtins, time as _time, traceback as _tb
import functools as _functools
from sigma.config import TRADFI_SYMBOLS
from sigma.http import http


# ================= Unified market data (Binance -> Bybit fallback) =================
# Bybit intervals differ from Binance: map them.
_BYBIT_IV = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "1d": "D", "1w": "W"}

async def _get_json(session, url, params=None, timeout=15):
    try:
        async with session.get(url, params=params, timeout=timeout) as r:
            if r.status != 200:
                return None
            return await r.json()
    except Exception:
        return None

async def md_klines(pair: str, interval: str, limit: int = 220):
    """Return list of [openTime, o, h, l, c, v, ...] Binance-style. Tries Binance then Bybit."""
    async with http() as s:
        data = await _get_json(s, "https://api.binance.com/api/v3/klines",
                               {"symbol": pair, "interval": interval, "limit": limit}, 20)
        if data and isinstance(data, list) and len(data) > 0:
            return data
        # Bybit fallback (spot). Bybit returns newest-first; reverse to oldest-first.
        biv = _BYBIT_IV.get(interval)
        if not biv:
            return None
        bd = await _get_json(s, "https://api.bybit.com/v5/market/kline",
                             {"category": "spot", "symbol": pair, "interval": biv, "limit": min(limit, 1000)}, 20)
        try:
            rows = bd["result"]["list"]
            if not rows:
                return None
            out = []
            for r in reversed(rows):
                # Bybit: [start, open, high, low, close, volume, turnover]
                out.append([int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), 0, 0, 0, 0, 0, 0])
            return out
        except Exception:
            return None

async def md_ticker24(pair: str):
    """Return dict with lastPrice, priceChangePercent, highPrice, lowPrice, quoteVolume. Binance then Bybit."""
    async with http() as s:
        d = await _get_json(s, "https://api.binance.com/api/v3/ticker/24hr", {"symbol": pair}, 15)
        if d and "lastPrice" in d:
            return {
                "lastPrice": float(d["lastPrice"]), "priceChangePercent": float(d["priceChangePercent"]),
                "highPrice": float(d["highPrice"]), "lowPrice": float(d["lowPrice"]),
                "quoteVolume": float(d["quoteVolume"]), "source": "Binance",
            }
        bd = await _get_json(s, "https://api.bybit.com/v5/market/tickers", {"category": "spot", "symbol": pair}, 15)
        try:
            t = bd["result"]["list"][0]
            last = float(t["lastPrice"])
            return {
                "lastPrice": last, "priceChangePercent": float(t["price24hPcnt"]) * 100,
                "highPrice": float(t["highPrice24h"]), "lowPrice": float(t["lowPrice24h"]),
                "quoteVolume": float(t.get("turnover24h", 0)), "source": "Bybit",
            }
        except Exception:
            return None

async def md_price(pair: str):
    """Return float last price. Binance then Bybit."""
    async with http() as s:
        d = await _get_json(s, "https://api.binance.com/api/v3/ticker/price", {"symbol": pair}, 15)
        if d and "price" in d:
            return float(d["price"])
        bd = await _get_json(s, "https://api.bybit.com/v5/market/tickers", {"category": "spot", "symbol": pair}, 15)
        try:
            return float(bd["result"]["list"][0]["lastPrice"])
        except Exception:
            return None

async def md_tradfi(name: str):
    """Fetch a traditional-market quote from Yahoo Finance. name is a friendly key (SPX, GOLD, DXY...)."""
    ysym = TRADFI_SYMBOLS.get(name.upper())
    if not ysym:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as s:
        d = await _get_json(s, url, {"interval": "1d", "range": "5d"}, 15)
    try:
        meta = d["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        chg = (price - prev) / prev * 100 if prev else 0.0
        return {"name": name.upper(), "ysym": ysym, "price": price, "chg": chg}
    except Exception:
        return None

async def md_coinbase_price(base: str):
    """Coinbase spot price for BASE-USD. Returns float or None."""
    url = f"https://api.exchange.coinbase.com/products/{base}-USD/ticker"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as s:
        d = await _get_json(s, url, None, 15)
    try:
        return float(d["price"])
    except Exception:
        return None

def is_tradfi(sym: str) -> bool:
    return sym.upper().replace("USDT", "") in TRADFI_SYMBOLS or sym.upper() in TRADFI_SYMBOLS

async def md_funding(pair: str):
    """Return dict rate(%), mark, nextFundingTime(s). Binance perp then Bybit linear."""
    async with http() as s:
        d = await _get_json(s, "https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": pair}, 15)
        if d and "lastFundingRate" in d:
            return {"rate": float(d["lastFundingRate"]) * 100, "mark": float(d["markPrice"]),
                    "next": int(d["nextFundingTime"]) // 1000, "source": "Binance"}
        bd = await _get_json(s, "https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": pair}, 15)
        try:
            t = bd["result"]["list"][0]
            return {"rate": float(t["fundingRate"]) * 100, "mark": float(t["markPrice"]),
                    "next": int(t["nextFundingTime"]) // 1000, "source": "Bybit"}
        except Exception:
            return None

async def md_oi(pair: str):
    """Return dict oi(coins), source. Binance perp then Bybit linear."""
    async with http() as s:
        d = await _get_json(s, "https://fapi.binance.com/fapi/v1/openInterest", {"symbol": pair}, 15)
        hist = await _get_json(s, "https://fapi.binance.com/futures/data/openInterestHist",
                               {"symbol": pair, "period": "1h", "limit": 25}, 15)
        if d and "openInterest" in d:
            oi_then = None
            if isinstance(hist, list) and len(hist) >= 24:
                try:
                    oi_then = float(hist[0]["sumOpenInterest"])
                except Exception:
                    oi_then = None
            return {"oi": float(d["openInterest"]), "oi_then": oi_then, "source": "Binance"}
        bd = await _get_json(s, "https://api.bybit.com/v5/market/open-interest",
                             {"category": "linear", "symbol": pair, "intervalTime": "1h", "limit": 25}, 15)
        try:
            rows = bd["result"]["list"]
            oi_now = float(rows[0]["openInterest"])
            oi_then = float(rows[-1]["openInterest"]) if len(rows) >= 24 else None
            return {"oi": oi_now, "oi_then": oi_then, "source": "Bybit"}
        except Exception:
            return None

async def fetch_agg_rows(base: str):
    """(exchange, oi_usd, funding_pct) rows across Binance/Bybit/OKX - shared by /agg and the euphoria guard."""
    pair = f"{base}USDT"
    rows = []
    async with http() as s:
        try:
            oi_d = await _get_json(s, "https://fapi.binance.com/fapi/v1/openInterest", {"symbol": pair}, 15)
            px_d = await _get_json(s, "https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": pair}, 15)
            if oi_d and px_d:
                mark = float(px_d["markPrice"])
                rows.append(("Binance", float(oi_d["openInterest"]) * mark, float(px_d["lastFundingRate"]) * 100))
        except Exception:
            pass
        try:
            bb = await _get_json(s, "https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": pair}, 15)
            t = bb["result"]["list"][0]
            rows.append(("Bybit", float(t["openInterestValue"]), float(t["fundingRate"]) * 100))
        except Exception:
            pass
        try:
            inst = f"{base}-USDT-SWAP"
            oi_o = await _get_json(s, "https://www.okx.com/api/v5/public/open-interest", {"instId": inst}, 15)
            fr_o = await _get_json(s, "https://www.okx.com/api/v5/public/funding-rate", {"instId": inst}, 15)
            oi_usd = float(oi_o["data"][0]["oiUsd"]) if oi_o and oi_o.get("data") else None
            fr = float(fr_o["data"][0]["fundingRate"]) * 100 if fr_o and fr_o.get("data") else None
            if oi_usd is not None and fr is not None:
                rows.append(("OKX", oi_usd, fr))
        except Exception:
            pass
    return rows

"""sigma.liquidations - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.logging_setup import print
from sigma.config import GREEN, LIQ_BIG_USD, LIQ_BYBIT_SYMBOLS, LIQ_CHANNEL_ID, LIQ_MAX_PER_MIN, LIQ_MIN_USD, RED
from sigma.core import bot
from sigma.ops import HEARTBEAT
from sigma.http import http
from sigma.market_data import _get_json
from sigma.calculations import fnum


_liq_rate = {"minute": None, "count": 0}

async def post_liquidation(exchange: str, base: str, liq_type: str, notional: float, price: float):
    """Shared liquidation poster with soft throttling."""
    if notional < LIQ_MIN_USD:
        return
    ch = bot.get_channel(LIQ_CHANNEL_ID)
    if ch is None:
        return
    now = datetime.now(timezone.utc)
    minute_key = now.strftime("%Y%m%d%H%M")
    if _liq_rate["minute"] != minute_key:
        _liq_rate["minute"] = minute_key
        _liq_rate["count"] = 0
    if _liq_rate["count"] >= LIQ_MAX_PER_MIN and notional < LIQ_BIG_USD:
        return  # busy minute - let only the big ones through
    _liq_rate["count"] += 1
    if notional >= 10_000_000:
        size_emoji = "\U0001F4A5\U0001F4A5"
    elif notional >= 5_000_000:
        size_emoji = "\U0001F4A5"
    elif notional >= 1_000_000:
        size_emoji = "\U0001F525"
    else:
        size_emoji = "\U0001F534" if liq_type == "Long" else "\U0001F7E2"
    amt = f"${notional / 1e6:.2f}M" if notional >= 1_000_000 else f"${notional / 1e3:.0f}K"
    embed = discord.Embed(
        color=RED if liq_type == "Long" else GREEN,
        timestamp=now,
        description=f"{size_emoji} **{base}** {liq_type} liquidated - **{amt}** @ ${fnum(price)}",
    )
    embed.set_footer(text=f"Liquidations - {exchange}")
    try:
        await ch.send(embed=embed)
    except Exception as e:
        print(f"[liq] post error: {e}")

async def liq_binance_loop():
    await bot.wait_until_ready()
    if not LIQ_CHANNEL_ID:
        return
    backoff = 5
    url = "wss://fstream.binance.com/ws/!forceOrder@arr"
    while not bot.is_closed():
        try:
            async with http() as session:
                async with session.ws_connect(url, heartbeat=30, timeout=30) as ws:
                    print("[liq] Binance stream connected", flush=True)
                    backoff = 5
                    async for msg in ws:
                        HEARTBEAT["ws_binance"] = _time.time()
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        try:
                            o = json.loads(msg.data).get("o", {})
                            sym = o.get("s", "")
                            side = o.get("S", "")
                            qty = float(o.get("q", 0))
                            price = float(o.get("ap") or o.get("p") or 0)
                        except Exception:
                            continue
                        if not sym or price <= 0:
                            continue
                        base = sym[:-4] if sym.endswith("USDT") else sym
                        liq_type = "Long" if side == "SELL" else "Short"
                        await post_liquidation("Binance", base, liq_type, qty * price, price)
        except Exception as e:
            print(f"[liq] Binance ws error: {e}", flush=True)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)

async def liq_bybit_loop():
    await bot.wait_until_ready()
    if not LIQ_CHANNEL_ID:
        return
    backoff = 5
    url = "wss://stream.bybit.com/v5/public/linear"
    while not bot.is_closed():
        try:
            async with http() as session:
                async with session.ws_connect(url, heartbeat=20, timeout=30) as ws:
                    await ws.send_json({"op": "subscribe",
                                        "args": [f"allLiquidation.{s}" for s in LIQ_BYBIT_SYMBOLS]})
                    print("[liq] Bybit stream connected", flush=True)
                    backoff = 5
                    async for msg in ws:
                        HEARTBEAT["ws_bybit"] = _time.time()
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        try:
                            payload = json.loads(msg.data)
                        except Exception:
                            continue
                        if not str(payload.get("topic", "")).startswith("allLiquidation"):
                            continue
                        for d in payload.get("data", []) or []:
                            try:
                                sym = d.get("s", "")
                                side = d.get("S", "")   # side of the liquidated order
                                qty = float(d.get("v", 0))
                                price = float(d.get("p", 0))
                            except Exception:
                                continue
                            if not sym or price <= 0:
                                continue
                            base = sym[:-4] if sym.endswith("USDT") else sym
                            # Bybit reports the side of the closing order: Sell = long liquidated
                            liq_type = "Long" if side.lower() == "sell" else "Short"
                            await post_liquidation("Bybit", base, liq_type, qty * price, price)
        except Exception as e:
            print(f"[liq] Bybit ws error: {e}", flush=True)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)

_okx_ctval = {}

async def _load_okx_ctval():
    """OKX reports liquidation size in contracts - we need each instrument's contract value."""
    try:
        async with http() as s:
            d = await _get_json(s, "https://www.okx.com/api/v5/public/instruments",
                                {"instType": "SWAP"}, 20)
        for inst in (d or {}).get("data", []) or []:
            try:
                _okx_ctval[inst["instId"]] = float(inst["ctVal"])
            except Exception:
                continue
        print(f"[liq] OKX contract values loaded: {len(_okx_ctval)}", flush=True)
    except Exception as e:
        print(f"[liq] OKX ctVal load failed: {e}", flush=True)

async def liq_okx_loop():
    await bot.wait_until_ready()
    if not LIQ_CHANNEL_ID:
        return
    await _load_okx_ctval()
    backoff = 5
    url = "wss://ws.okx.com:8443/ws/v5/public"
    while not bot.is_closed():
        try:
            async with http() as session:
                async with session.ws_connect(url, heartbeat=25, timeout=30) as ws:
                    await ws.send_json({"op": "subscribe",
                                        "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]})
                    print("[liq] OKX stream connected", flush=True)
                    backoff = 5
                    async for msg in ws:
                        HEARTBEAT["ws_okx"] = _time.time()
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        if msg.data == "pong":
                            continue
                        try:
                            payload = json.loads(msg.data)
                        except Exception:
                            continue
                        if payload.get("arg", {}).get("channel") != "liquidation-orders":
                            continue
                        for item in payload.get("data", []) or []:
                            inst = item.get("instId", "")
                            if not inst.endswith("-USDT-SWAP"):
                                continue
                            ctval = _okx_ctval.get(inst)
                            if not ctval:
                                continue
                            base = inst.split("-")[0]
                            for det in item.get("details", []) or []:
                                try:
                                    price = float(det.get("bkPx", 0))
                                    sz = float(det.get("sz", 0))
                                except Exception:
                                    continue
                                if price <= 0 or sz <= 0:
                                    continue
                                # side = side of the liquidation order: sell = long liquidated
                                liq_type = "Long" if str(det.get("side", "")).lower() == "sell" else "Short"
                                await post_liquidation("OKX", base, liq_type, sz * ctval * price, price)
        except Exception as e:
            print(f"[liq] OKX ws error: {e}", flush=True)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)

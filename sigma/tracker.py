"""sigma.tracker - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import BLUE, GREEN, GREY, RED
from sigma.core import bot
from sigma.ops import HEARTBEAT, ops_alert
from sigma.http import http
from sigma.storage import load_trades, save_trades
from sigma.calculations import any_entry_filled, entry_num, first_num, fnum, sl_num
from sigma.errors import UserError
from sigma.services import trades as _svc
from sigma.cards import post_update_feed, refresh_and_edit
from sigma.boards import refresh_board


PRICE_WATCH_ENABLED = True

PRICE_WATCH_SEC = 60

AUTO_CLOSE_ON_HARD_SL = True    # plain numeric SL: auto-close on touch. Soft SL ("4h close below X"): notify only.

_pw_unsupported: set = set()

_pw_fail: dict = {}            # symbol -> consecutive transient failures

_pw_lock = asyncio.Lock()

STATE_LOCK = asyncio.Lock()    # serialises read-modify-write on trades between the tracker and commands

def _with_state_lock(fn):
    """Run a mutating slash command under STATE_LOCK so it can't race the price tracker."""
    @_functools.wraps(fn)
    async def _w(*a, **kw):
        async with STATE_LOCK:
            return await fn(*a, **kw)
    return _w

def _pw_symbol(pair: str):
    s = (pair or "").upper().strip()
    if ":" in s:                       # BINANCE:BTCUSDT.P -> BTCUSDT.P
        s = s.split(":", 1)[1]
    s = s.replace("/", "").replace("-", "").replace(" ", "")
    for suf in (".P", "PERP", ".PS"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    if s.endswith("USD") and not s.endswith(("USDT", "USDC")):
        s = s + "T"                    # BTCUSD -> BTCUSDT
    if not s.endswith(("USDT", "USDC")):
        s = s + "USDT"
    return s

async def _pw_klines(symbol: str, since_ms: int):
    """1m Binance USDT-perp klines since since_ms. Returns [(open_ms, high, low, close)] or None."""
    url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}"
           f"&interval=1m&startTime={since_ms}&limit=1000")
    try:
        async with http() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status in (400, 404):
                    return "BAD_SYMBOL"          # permanent: not listed on Binance futures
                if r.status != 200:
                    return None                  # transient: 429 / 5xx
                rows = await r.json()
        return [(int(k[0]), float(k[2]), float(k[3]), float(k[4])) for k in rows]
    except Exception:
        return None                              # transient: network

async def _pw_announce(t, title, color, line):
    try:
        await post_update_feed(t, title, color, line)
    except Exception as ex:
        print(f"[watch] announce error: {ex}", flush=True)

async def _pw_process_trade(tid: str, t: dict, candles):
    """Walk candles chronologically; mutate t; return list of (title,color,line) events."""
    events = []
    is_long = t.get("direction") == "LONG"
    e1 = first_num(t.get("entry"))
    e2 = first_num(t.get("entry2")) if t.get("entry2") else None
    slv = sl_num(t)
    soft_sl = bool(t.get("sl_condition"))

    def crossed_entry(px, lo, hi):
        return (lo <= px) if is_long else (hi >= px)

    def crossed_tp(px, lo, hi):
        return (hi >= px) if is_long else (lo <= px)

    def crossed_sl(px, lo, hi):
        return (lo <= px) if is_long else (hi >= px)

    for open_ms, hi, lo, close in candles:
        if t.get("closed"):
            break
        # ── ambiguity gate: if this ONE candle touches a hard stop AND (a pending TP or an unfilled
        # entry), OHLC cannot prove the intra-candle order. Never guess a P&L - pause and ask. ──
        stop_now = (entry_num(t) if t.get("be") else slv)
        if stop_now and not soft_sl and crossed_sl(stop_now, lo, hi):
            tp_touched = any_entry_filled(t) and any(
                first_num(t.get(k)) is not None and not t.get(f"{k}_hit") and crossed_tp(first_num(t.get(k)), lo, hi)
                for k in ("tp1", "tp2", "tp3", "tp4"))
            entry_touched = (not any_entry_filled(t)) and t.get("entry_type") != "MARKET" and (
                (e1 and crossed_entry(e1, lo, hi)) or (e2 and crossed_entry(e2, lo, hi)))
            if tp_touched or entry_touched:
                t["watch_disabled"] = True
                t["watch_ambiguous_ms"] = open_ms
                what = "a take-profit" if tp_touched else "the entry"
                events.append(("Ambiguous candle - tracking paused", GREY,
                               f"One 1-minute candle touched both {what} and the stop ({fnum(stop_now)}). "
                               f"The order inside the candle can't be proven from OHLC, so nothing was recorded. "
                               f"Check your exchange fills and resolve with /setup update, then /setup track On if you want the tracker back."))
                break
        # entry fills (limit only - market fills at post)
        if e1 and not t.get("entry1_filled") and t.get("entry_type") != "MARKET" and crossed_entry(e1, lo, hi):
            t["entry1_filled"] = True
            events.append(("Entry 1 filled" if e2 else "Entry filled", BLUE,
                           f"Filled @ {fnum(e1)} - auto-tracked"))
        if e2 and not t.get("entry2_filled") and crossed_entry(e2, lo, hi):
            t["entry2_filled"] = True
            t["entry1_filled"] = True
            events.append(("DCA entry filled - full position live", BLUE,
                           f"Filled @ {fnum(e2)} - auto-tracked"))
        # TP hits, in order, only once in a position
        if any_entry_filled(t):
            for idx, key in enumerate(("tp1", "tp2", "tp3", "tp4")):
                tp_px = first_num(t.get(key))
                if not tp_px or t.get(f"{key}_hit"):
                    continue
                if crossed_tp(tp_px, lo, hi):
                    try:
                        out = _svc.apply_event("fut", t, key.upper())      # planned % from the card, same path as /setup update
                        events.append((out.title + " - auto-tracked", GREEN, out.line or out.desc))
                    except UserError:
                        # no planned % (or nothing left to close): tick the level, let the analyst record size
                        t[f"{key}_hit"] = True
                        for prev in ("tp1", "tp2", "tp3", "tp4")[:idx]:
                            t[f"{prev}_hit"] = True
                        events.append((f"{key.upper()} tagged @ {fnum(tp_px)}", GREEN,
                                       "Auto-tracked - no split % on the card, record size with /setup update"))
        # SL - once the analyst moved the stop to entry, ENTRY is the stop
        stop_lvl = (entry_num(t) if t.get("be") else slv)
        if stop_lvl and not t.get("closed") and any_entry_filled(t) and crossed_sl(stop_lvl, lo, hi):
            if soft_sl:
                warned = t.get("watch_sl_warned_ms") or 0
                if open_ms - warned > 4 * 3600 * 1000:
                    t["watch_sl_warned_ms"] = open_ms
                    events.append(("Price at soft invalidation", GREY,
                                   f"Traded through {fnum(stop_lvl)} ({t.get('sl_condition')}) - your call, confirm with /setup update if it closes there."))
            elif AUTO_CLOSE_ON_HARD_SL:
                try:
                    out = _svc.apply_event("fut", t, "SL", note="Auto-tracked stop")   # same close + grading as /setup update
                except UserError as ex:
                    t["watch_disabled"] = True
                    events.append(("Tracker could not close this setup", GREY, f"{ex.message} Resolve with /setup update."))
                    break
                where = "Stop at entry" if t.get("be") else "Stop"
                events.append((out.title, out.color, f"{where} hit @ {fnum(stop_lvl)} - auto-tracked"))
                break
    return events

@tasks.loop(seconds=PRICE_WATCH_SEC)
async def price_watch_loop():
    try:
        await _price_watch_tick()
    except Exception as ex:
        import traceback
        print(f"[watch] tick error: {ex}", flush=True)
        traceback.print_exc()

@price_watch_loop.before_loop
async def _before_price_watch():
    await bot.wait_until_ready()

async def _price_watch_tick():
    if not PRICE_WATCH_ENABLED or _pw_lock.locked():
        return
    async with _pw_lock, STATE_LOCK:
        HEARTBEAT["price_watch"] = _time.time()
        data = load_trades()
        open_trades = {tid: t for tid, t in data.items() if not t.get("closed")}
        if not open_trades:
            return
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        by_sym = {}
        for tid, t in open_trades.items():
            sym = _pw_symbol(t.get("pair"))
            if sym in _pw_unsupported:
                continue
            by_sym.setdefault(sym, []).append((tid, t))
        changed = False
        for sym, items in by_sym.items():
            since = min(int(t.get("watch_ms") or (now_ms - 120_000)) for _, t in items)
            candles = await _pw_klines(sym, since)
            if candles == "BAD_SYMBOL":
                _pw_unsupported.add(sym)
                print(f"[watch] {sym} not on Binance futures - manual tracking only", flush=True)
                continue
            if candles is None:
                n = _pw_fail[sym] = _pw_fail.get(sym, 0) + 1
                if n == 5:
                    await ops_alert(f"price feed for {sym} failing ({n} ticks in a row) - auto-tracking paused, will keep retrying", key=f"feed:{sym}")
                continue
            _pw_fail.pop(sym, None)
            for tid, t in items:
                if t.get("watch_disabled"):
                    continue
                # feed identity gate: Binance ka price is trade ke entry ke scale par hona chahiye
                if not t.get("watch_verified"):
                    ref = entry_num(t) or first_num(t.get("entry"))
                    last_close = candles[-1][3] if candles else None
                    if ref and last_close and not (0.5 <= last_close / ref <= 2.0):
                        t["watch_disabled"] = True
                        data[tid] = t
                        save_trades(data)
                        print(f"[watch] FEED MISMATCH {t.get('pair')} - binance {last_close} vs entry {ref}; auto-tracking OFF", flush=True)
                        try:
                            await post_update_feed(t, "Auto-tracking disabled", GREY,
                                f"Price feed for **{t.get('pair','?').upper()}** on Binance doesn't match this setup "
                                f"(feed {last_close:g} vs entry {ref:g}) - likely a different market with the same ticker. "
                                f"This setup is now **manual tracking only**.")
                        except Exception:
                            pass
                        continue
                    t["watch_verified"] = True
                    data[tid] = t
                t_since = int(t.get("watch_ms") or (now_ms - 120_000))
                mine = [c for c in candles if c[0] >= t_since]
                events = await _pw_process_trade(tid, t, mine)
                t["watch_ms"] = now_ms
                if events:
                    changed = True
                    data[tid] = t
                    save_trades(data)
                    try:
                        await refresh_and_edit(t)
                    except Exception as ex:
                        print(f"[watch] edit error {tid}: {ex}", flush=True)
                    for title, color, line in events:
                        await _pw_announce(t, title, color, line)
                    await asyncio.sleep(0.6)
        if changed:
            save_trades(data)
            await refresh_board()
        else:
            save_trades(data)   # persist watch_ms cursors

"""sigma.ops - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.logging_setup import log
from sigma.config import OPS_CHANNEL_ID
from sigma.core import bot


_ops_recent: dict = {}

_ERR_LOG: list = []           # (ts, where, msg) - last 200 for /health

BOOT_TS = _time.time()

HEARTBEAT: dict = {}          # loop name -> last successful tick ts

def _note_error(where: str, err):
    _ERR_LOG.append((_time.time(), where, str(err)[:300]))
    del _ERR_LOG[:-200]
    log.error(f"[{where}] {err}\n{_tb.format_exc()}")

async def ops_alert(text: str, key: str = None, cooldown: int = 600):
    """Post to the ops channel (deduped per key for `cooldown` seconds). Never raises."""
    key = key or text[:80]
    now = _time.time()
    if now - _ops_recent.get(key, 0) < cooldown:
        return
    _ops_recent[key] = now
    log.warning(f"[ops] {text}")
    try:
        ch = bot.get_channel(OPS_CHANNEL_ID) if OPS_CHANNEL_ID else None
        if ch:
            await ch.send(f"\u26a0 **ops** \u00b7 {text[:1800]}")
    except Exception:
        pass

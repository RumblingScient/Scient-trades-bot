"""sigma.http - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.core import bot


# ─── ONE managed HTTP session for every provider call ───────────────────────
# Created lazily on first use, reused everywhere (connection pool + DNS cache),
# closed cleanly when the bot shuts down. Call sites keep their own per-request timeouts.
_HTTP: "aiohttp.ClientSession | None" = None

HTTP_STATS = {"requests": 0, "created_at": None}

def _new_http_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=25, connect=8, sock_read=20),
        connector=aiohttp.TCPConnector(limit=48, limit_per_host=12, ttl_dns_cache=300, enable_cleanup_closed=True),
        headers={"User-Agent": "SigmaBot/1.0 (+discord; sigma trading)"},
        trust_env=True,
    )

class _SharedHTTP:
    """`async with http() as s:` - hands out the shared session and never closes it."""
    async def __aenter__(self):
        global _HTTP
        if _HTTP is None or _HTTP.closed:
            _HTTP = _new_http_session()
            HTTP_STATS["created_at"] = _time.time()
        HTTP_STATS["requests"] += 1
        return _HTTP

    async def __aexit__(self, *exc):
        return False

def http() -> _SharedHTTP:
    return _SharedHTTP()

_orig_bot_close = bot.close

async def _bot_close_with_http():
    try:
        if _HTTP is not None and not _HTTP.closed:
            await _HTTP.close()
            log.info("[http] shared session closed")
    except Exception as e:
        log.warning(f"[http] close error: {e}")
    await _orig_bot_close()

bot.close = _bot_close_with_http

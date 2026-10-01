"""sigma.logging_setup - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent   # repo root: .env, logs/, *.json live here



# ─── OPS: structured logging (journalctl + rotating file) ───────────────────
_LOG_DIR = _ROOT / "logs"

_LOG_DIR.mkdir(exist_ok=True)

log = logging.getLogger("sigma")

log.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

_fh = logging.handlers.RotatingFileHandler(_LOG_DIR / "sigma_bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")

_fh.setFormatter(_fmt)

_sh = logging.StreamHandler(); _sh.setFormatter(_fmt)

if not log.handlers:   # one file + one stream handler, however many times this module is imported
    log.addHandler(_fh)
    log.addHandler(_sh)

def print(*args, **kwargs):  # every existing print() lands in the log file too
    log.info(" ".join(str(a) for a in args))

builtins.print = print

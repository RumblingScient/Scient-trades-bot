"""sigma.storage - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.logging_setup import log
from sigma.config import BOARD_FILE, JOURNAL_FILE, RESULTS_FILE, SPOT_BOARD_FILE, SPOT_FILE, XSEEN_FILE
from sigma.ops import _note_error


_CORRUPT: set = set()   # paths that failed to parse - saves to them are refused until fixed

def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            try:
                data = json.loads(bak.read_text())
                log.error(f"[data] {path.name} is corrupt ({e}) - served from {bak.name}")
                _CORRUPT.add(str(path))
                return data
            except Exception:
                pass
        _CORRUPT.add(str(path))
        _note_error("data-load", f"{path.name} unreadable and no valid .bak: {e}")
        return {}

def _save(path: Path, data: dict):
    if str(path) in _CORRUPT:
        _note_error("data-save", f"REFUSED write to {path.name}: file was corrupt at load")
        raise RuntimeError(f"{path.name} is marked corrupt - restore from backup, then restart")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    if path.exists():
        try:
            os.replace(path, path.with_suffix(path.suffix + ".bak"))   # last-good copy, always
        except Exception:
            pass
    os.replace(tmp, path)  # atomic on POSIX - never leaves a half-written file

def load_trades() -> dict: return _load(JOURNAL_FILE)

def save_trades(d: dict): _save(JOURNAL_FILE, d)

def load_board() -> dict: return _load(BOARD_FILE)

def save_board(d: dict): _save(BOARD_FILE, d)

def load_spot() -> dict: return _load(SPOT_FILE)

def save_spot(d: dict): _save(SPOT_FILE, d)

def load_spot_board() -> dict: return _load(SPOT_BOARD_FILE)

def save_spot_board(d: dict): _save(SPOT_BOARD_FILE, d)

def load_xseen() -> dict: return _load(XSEEN_FILE)

def save_xseen(d: dict): _save(XSEEN_FILE, d)

DIGEST_FILE = _ROOT.joinpath("news_digest.json")

def load_digest() -> dict: return _load(DIGEST_FILE)

def save_digest(d: dict): _save(DIGEST_FILE, d)

SUBS_FILE = _ROOT.joinpath("subscriptions.json")
PAYMENTS_FILE = _ROOT.joinpath("payments.json")
PLANS_FILE = _ROOT.joinpath("plans.json")
def load_plans() -> dict: return _load(PLANS_FILE)
def save_plans(d: dict): _save(PLANS_FILE, d)
def load_payments() -> dict: return _load(PAYMENTS_FILE)
def save_payments(d: dict): _save(PAYMENTS_FILE, d)

def load_subs() -> dict: return _load(SUBS_FILE)

def save_subs(d: dict): _save(SUBS_FILE, d)

ALERTS_FILE = _ROOT.joinpath("alerts.json")

def load_alerts() -> dict: return _load(ALERTS_FILE)

def save_alerts(d: dict): _save(ALERTS_FILE, d)

WATCHLIST_FILE = _ROOT.joinpath("watchlists.json")

def load_watchlists() -> dict: return _load(WATCHLIST_FILE)

def save_watchlists(d: dict): _save(WATCHLIST_FILE, d)

SOURCES_FILE = _ROOT.joinpath("tg_sources.json")

def load_sources() -> dict: return _load(SOURCES_FILE)

def save_sources(d: dict): _save(SOURCES_FILE, d)

_results_lock = asyncio.Lock()

def load_results() -> dict: return _load(RESULTS_FILE)

def save_results(d: dict): _save(RESULTS_FILE, d)

"""sigma.jobs - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.logging_setup import print
from sigma.config import BACKUP_DIR, IST, LIQ_CHANNEL_ID, NAVY
from sigma.core import bot
from sigma.ops import HEARTBEAT, _note_error, ops_alert
from sigma.market_data import fetch_agg_rows


FUNDING_GUARD_CHANNEL_ID = LIQ_CHANNEL_ID  # euphoria warnings post here (change to a dedicated channel any time)

# ---------------- FUNDING EUPHORIA GUARD ----------------
FUNDING_HOT = 0.06      # %/8h OI-weighted, crossing above fires the warning

FUNDING_COLD = -0.02    # crossing below fires the shorts-crowded note

FUNDING_RESET = 0.03    # back inside +/- this = re-arm

FUNDING_COOLDOWN_H = 12

_funding_guard_state = {}   # base -> {"last_fire": ts, "armed": True}

@tasks.loop(minutes=15)
async def funding_guard_loop():
    try:
        await _funding_guard_tick()
        HEARTBEAT["funding"] = _time.time()
    except Exception as e:
        print(f"[fguard] tick error: {e}", flush=True)

async def _funding_guard_tick():
    if not FUNDING_GUARD_CHANNEL_ID:
        return
    ch = bot.get_channel(FUNDING_GUARD_CHANNEL_ID)
    if ch is None:
        return
    now = datetime.now(timezone.utc).timestamp()
    for base in ("BTC", "ETH"):
        rows = await fetch_agg_rows(base)
        if not rows:
            continue
        total_oi = sum(r[1] for r in rows)
        if total_oi <= 0:
            continue
        wf = sum(r[2] * r[1] for r in rows) / total_oi
        st = _funding_guard_state.setdefault(base, {"last_fire": 0, "armed": True})
        if abs(wf) < FUNDING_RESET:
            st["armed"] = True
            continue
        if not st["armed"] or now - st["last_fire"] < FUNDING_COOLDOWN_H * 3600:
            continue
        if wf >= FUNDING_HOT:
            title = f"\u26A0\uFE0F Funding Euphoria - {base}"
            body = (f"OI-weighted funding **{wf:+.3f}%/8h** across {len(rows)} venues.\n"
                    f"**What this means:** longs paying heavily to stay in - leverage crowded one side.\n"
                    f"**What usually follows:** violent wicks into that leverage.\n"
                    f"**What it isn't:** a short signal - it's a *tighten stops, don't add leverage here* signal.")
        elif wf <= FUNDING_COLD:
            title = f"\u2696\uFE0F Shorts Crowded - {base}"
            body = (f"OI-weighted funding **{wf:+.3f}%/8h** - shorts paying across venues.\n"
                    f"Crowded shorts are squeeze fuel above. Careful chasing weakness here.")
        else:
            continue
        embed = discord.Embed(title=title, description=body + f"\n\n*Resets when funding normalizes inside \u00B1{FUNDING_RESET:g}%.*",
                              color=NAVY, timestamp=datetime.now(timezone.utc))
        embed.set_footer(text="Sigma Trading - automated leverage guard")
        try:
            await ch.send(embed=embed)
            st["last_fire"] = now
            st["armed"] = False
            print(f"[fguard] fired {base} at {wf:+.3f}%", flush=True)
        except Exception as e:
            print(f"[fguard] send error: {e}", flush=True)

@funding_guard_loop.before_loop
async def _before_funding_guard():
    await bot.wait_until_ready()

def last_backup_age():
    """Seconds since the newest backup folder was written, or None when there is none (health embed)."""
    try:
        folders = [d for d in BACKUP_DIR.iterdir() if d.is_dir()]
        if not folders:
            return None
        return max(0.0, _time.time() - max(d.stat().st_mtime for d in folders))
    except Exception:
        return None

def _backup_once() -> int:
    """Copy every root *.json into BACKUP_DIR/<yyyymmdd>, keep the newest 7 days. Returns files copied."""
    import shutil
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ddir = BACKUP_DIR / datetime.now(IST).strftime("%Y%m%d")
    ddir.mkdir(exist_ok=True)
    n = 0
    for f in _ROOT.glob("*.json"):
        shutil.copy2(f, ddir / f.name)
        n += 1
    os.utime(ddir)                     # folder mtime = time of the last successful copy, which is what last_backup_age reads
    for old in sorted(d for d in BACKUP_DIR.iterdir() if d.is_dir())[:-7]:
        shutil.rmtree(old, ignore_errors=True)
    print(f"[backup] {n} files -> {ddir}", flush=True)
    return n

@tasks.loop(time=dt_time(hour=21, minute=30, tzinfo=timezone.utc))  # 3:00 AM IST
async def backup_loop():
    try:
        n = await asyncio.to_thread(_backup_once)
    except Exception as e:
        _note_error("backup", e)
        await ops_alert(f"nightly backup FAILED: `{type(e).__name__}: {str(e)[:160]}` (dir {BACKUP_DIR})", key="backup")
        return
    if n == 0:
        await ops_alert(f"nightly backup copied 0 files - nothing matched *.json under {_ROOT}", key="backup")
        return
    HEARTBEAT["backup"] = _time.time()

@backup_loop.before_loop
async def before_backup():
    await bot.wait_until_ready()

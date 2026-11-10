"""sigma.health - loop self-heal, watchdog, startup validation and the /admin health embed."""
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
from sigma.config import (GUILD_ID, JOURNAL_FILE, LIQ_CHANNEL_ID, MOD_LOG_CHANNEL_ID, NAVY, OPEN_BOARD_CHANNEL_ID, OPS_CHANNEL_ID,
                          PAYMENT_CHANNEL_ID, RECAP_CHANNEL_ID, RESULTS_CHANNEL_ID, RESULTS_FILE, SOL_WALLET, SPOT_FILE, SUB_ROLE_ID,
                          TG_NEWS_CHANNELS, TRADES_CHANNEL_ID, TWITTERAPIS_KEY, X_AUTO_ENABLED, X_FEED_CHANNEL_ID)
from sigma.core import bot
from sigma.ops import BOOT_TS, HEARTBEAT, _ERR_LOG, _note_error, ops_alert
from sigma.http import HTTP_STATS
from sigma.storage import _CORRUPT, _load
from sigma.members import subs_check_loop
from sigma.tracker import _pw_fail, _pw_unsupported, price_watch_loop
from sigma.telegram import tg_brief_loop, tg_digest_loop, tg_move_loop, tg_sources_loop
from sigma.alerts import alert_check_loop
from sigma.xfeed import x_poll_loop
from sigma.jobs import backup_loop, funding_guard_loop, last_backup_age
from sigma.results import results_watch_loop
from sigma.recaps import sigma_monthly_loop, sigma_recap_loop
from sigma.payments import payment_watch_loop


# filled in by sigma.app.on_ready; the health embed reads it so "did boot finish?" is a yes/no, not a log hunt
BOOT = {"done": False, "sync": "-", "loops": "-", "failed": [], "watchdog_last": None}

# loops whose body stamps HEARTBEAT on a successful tick; the watchdog checks staleness only for these
_STAMPING = frozenset({"price_watch", "results", "payments", "x_poll", "alerts", "backup", "funding",
                       "tg_move", "tg_brief", "tg_sources", "tg_digest", "monthly", "weekly"})


async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    """Every slash-command failure: user gets a real message, ops gets a ping, log gets the trace."""
    root = getattr(error, "original", error)
    cmd = interaction.command.qualified_name if interaction.command else "?"
    _note_error(f"cmd:/{cmd}", root)
    await ops_alert(f"/{cmd} failed for {interaction.user} - `{type(root).__name__}: {str(root)[:160]}`", key=f"cmd:{cmd}:{type(root).__name__}")
    msg = "Something broke on that command - it's logged and the admin has been pinged. Nothing was changed."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

@bot.event
async def on_error(event, *args, **kwargs):
    tail = _tb.format_exc().strip().splitlines()
    _note_error(f"event:{event}", tail[-1] if tail else "unknown")
    await ops_alert(f"event handler `{event}` raised - see logs", key=f"event:{event}")

# ---------------- self-heal ----------------
_HEAL: dict = {}   # loop name -> {"n": crashes in the current streak, "last": ts of the last crash}

def _heal_delay(name: str, now: float = None) -> int:
    """Restart delay for the next crash of `name`: 30s doubling to 30 min. An hour without a crash resets the streak."""
    now = now or _time.time()
    st = _HEAL.setdefault(name, {"n": 0, "last": 0.0})
    if now - st["last"] > 3600:
        st["n"] = 0
    delay = min(30 * (2 ** st["n"]), 1800)
    st["n"] += 1; st["last"] = now
    return delay

def _self_heal(loop_obj, name: str):
    """discord.py stops a tasks.loop on an unhandled exception. This restarts it after a backoff delay."""
    async def _on_err(*args):
        exc = args[-1]
        delay = _heal_delay(name)
        _note_error(f"loop:{name}", exc)
        await ops_alert(f"loop `{name}` crashed: `{type(exc).__name__}: {str(exc)[:160]}` - restarting in {delay}s", key=f"loop:{name}")
        await asyncio.sleep(delay)
        if loop_obj.time:
            # a scheduled (time=) loop that crashed in its minute would otherwise wait a day for the run it just lost
            try:
                await loop_obj.coro()
            except Exception as e2:
                _note_error(f"loop-rerun:{name}", e2)
        try:
            loop_obj.restart()
        except Exception as e2:
            _note_error(f"loop-restart:{name}", e2)
    loop_obj.error(_on_err)
    return loop_obj

# ---------------- startup validation ----------------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)          # ValueError on any character outside the alphabet
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body

def _validate_startup():
    """Config problems as human lines. Never raises - a broken check must not take boot down with it."""
    problems = []
    try:
        for name, cid in (("TRADES_CHANNEL_ID", TRADES_CHANNEL_ID), ("RESULTS_CHANNEL_ID", RESULTS_CHANNEL_ID),
                          ("OPEN_BOARD_CHANNEL_ID", OPEN_BOARD_CHANNEL_ID), ("X_FEED_CHANNEL_ID", X_FEED_CHANNEL_ID),
                          ("LIQ_CHANNEL_ID", LIQ_CHANNEL_ID), ("RECAP_CHANNEL_ID", RECAP_CHANNEL_ID),
                          ("PAYMENT_CHANNEL_ID", PAYMENT_CHANNEL_ID), ("MOD_LOG_CHANNEL_ID", MOD_LOG_CHANNEL_ID),
                          ("OPS_CHANNEL_ID", OPS_CHANNEL_ID)):
            if cid and bot.get_channel(cid) is None:
                problems.append(f"{name}={cid} not visible to the bot")
        if not (OPS_CHANNEL_ID or MOD_LOG_CHANNEL_ID):
            problems.append("neither SIGMA_OPS_CHANNEL_ID nor SIGMA_MODLOG_CHANNEL_ID is set - alerts fall back to DMing the owner")
        guild = bot.get_guild(GUILD_ID)
        if guild is None:
            problems.append(f"guild {GUILD_ID} not visible to the bot")
        elif SUB_ROLE_ID:
            role = guild.get_role(SUB_ROLE_ID)
            me = guild.me
            if role is None:
                problems.append(f"SUB_ROLE_ID={SUB_ROLE_ID} does not exist in the guild")
            elif me is not None:
                if not me.guild_permissions.manage_roles:
                    problems.append("bot lacks Manage Roles - subscriptions cannot grant/revoke the Pro role")
                if me.top_role <= role:
                    problems.append(f"bot's top role is not above the Pro role ({role.name}) - role grants will be refused")
        if not SOL_WALLET:
            problems.append("SIGMA_SOL_WALLET is not set - payments are off")
        else:
            try:
                raw = _b58decode(SOL_WALLET)
                if len(raw) != 32:
                    problems.append(f"SIGMA_SOL_WALLET decodes to {len(raw)} bytes, not 32 - not a Solana address")
            except ValueError:
                problems.append("SIGMA_SOL_WALLET is not valid base58 - check for a paste error")
        if _CORRUPT:
            problems.append("corrupt data files: " + ", ".join(Path(p).name for p in _CORRUPT))
    except Exception as ex:
        _note_error("validate-startup", ex)
        problems.append(f"startup validation itself failed: {type(ex).__name__}: {str(ex)[:120]}")
    return problems

# ---------------- loop registry + watchdog ----------------
_ALL_LOOPS = None

def _loops():
    """(name, loop, enabled). `enabled` mirrors the conditions under which the loop is meant to run,
    so a loop that is off by config shows as 'off' instead of as dead."""
    global _ALL_LOOPS
    if _ALL_LOOPS is None:
        _ALL_LOOPS = (("price_watch", price_watch_loop, True), ("results", results_watch_loop, bool(RESULTS_CHANNEL_ID)),
                      ("subs", subs_check_loop, True), ("monthly", sigma_monthly_loop, True), ("weekly", sigma_recap_loop, True),
                      ("backup", backup_loop, True), ("x_poll", x_poll_loop, bool(X_AUTO_ENABLED and TWITTERAPIS_KEY)),
                      ("alerts", alert_check_loop, True), ("tg_move", tg_move_loop, False), ("tg_brief", tg_brief_loop, False),
                      ("tg_sources", tg_sources_loop, bool(TG_NEWS_CHANNELS)), ("tg_digest", tg_digest_loop, False),
                      ("funding", funding_guard_loop, True), ("payments", payment_watch_loop, bool(SOL_WALLET)),
                      ("watchdog", watchdog_loop, True))
    return _ALL_LOOPS

def _interval(lp) -> float:
    """Seconds between ticks; a time= loop runs once a day."""
    if lp.time:
        return 86400.0
    return float(lp.seconds or 0) + float(lp.minutes or 0) * 60 + float(lp.hours or 0) * 3600

@tasks.loop(minutes=5)
async def watchdog_loop():
    now = _time.time()
    for nm, lp, enabled in _loops():
        if not enabled or lp is watchdog_loop:
            continue
        if not lp.is_running():
            await ops_alert(f"watchdog: loop `{nm}` is not running - starting it again", key=f"watchdog:{nm}", cooldown=3600)
            try:
                lp.start()
            except Exception as ex:
                _note_error(f"watchdog:{nm}", ex)
            continue
        if nm not in _STAMPING:
            continue
        iv = _interval(lp)
        hb = HEARTBEAT.get(nm) or BOOT_TS      # never ticked since boot counts from boot
        if now - hb > 3 * iv:
            await ops_alert(f"watchdog: loop `{nm}` has not completed a tick for {int((now - hb) // 60)} min "
                            f"(expected every {int(iv // 60)} min)", key=f"watchdog:{nm}", cooldown=3600)
    BOOT["watchdog_last"] = now

@watchdog_loop.before_loop
async def _before_watchdog():
    await bot.wait_until_ready()

def _age_txt(ts) -> str:
    if not ts:
        return "never"
    s = int(_time.time() - ts)
    return f"{s // 86400}d {(s % 86400) // 3600}h ago" if s >= 86400 else f"{s // 3600}h {(s % 3600) // 60}m ago" if s >= 3600 else f"{s // 60}m ago" if s >= 60 else f"{s}s ago"

# (moved to /admin - registered in sigma.commands_admin)
async def health_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    now = _time.time()
    up = int(now - BOOT_TS)
    e = discord.Embed(title="SigmaBot health", color=NAVY)
    e.add_field(name="Uptime", value=f"{up // 86400}d {(up % 86400) // 3600}h {(up % 3600) // 60}m", inline=True)
    e.add_field(name="Latency", value=f"{bot.latency * 1000:.0f} ms", inline=True)
    boot_txt = (f"{'yes' if BOOT['done'] else 'NO'} · sync={BOOT['sync']} · loops {BOOT['loops']}"
                + (f"\nfailed steps: {', '.join(BOOT['failed'])}" if BOOT["failed"] else ""))
    e.add_field(name="Boot completed", value=boot_txt, inline=False)
    rows = []
    for nm, lp, enabled in _loops():
        hb = HEARTBEAT.get(nm)
        age = f"{int(now - hb)}s ago" if hb else "-"
        mark = "off" if not enabled else ("✅" if lp.is_running() else "❌")
        rows.append(f"{mark} {nm} · {age}")
    e.add_field(name="Loops", value="\n".join(rows), inline=False)
    files = []
    for nm, pth in (("trades", JOURNAL_FILE), ("spot", SPOT_FILE), ("results", RESULTS_FILE)):
        try:
            d = _load(pth)
            files.append(f"{nm}: {len(d)} records · {pth.stat().st_size // 1024} KB" + ("  ⚠ CORRUPT" if str(pth) in _CORRUPT else ""))
        except Exception as ex:
            files.append(f"{nm}: {ex}")
    bk = last_backup_age()
    files.append(f"newest backup: {_age_txt(now - bk) if bk is not None else 'none found'}")
    files.append(f"watchdog last run: {_age_txt(BOOT['watchdog_last'])}")
    e.add_field(name="Data", value="\n".join(files), inline=False)
    e.add_field(name="Feed", value=(f"unsupported: {', '.join(sorted(_pw_unsupported)) or 'none'}\n"
                                    f"failing: {', '.join(f'{k}({v})' for k, v in _pw_fail.items()) or 'none'}"), inline=False)
    ws_rows = []
    for nm in ("ws_binance", "ws_bybit", "ws_okx"):
        hb = HEARTBEAT.get(nm)
        age = int(now - hb) if hb else None
        ws_rows.append(f"{'✅' if age is not None and age < 300 else '❌'} {nm[3:]} · " + (f"last msg {age}s ago" if age is not None else "no message yet"))
    e.add_field(name="Liquidation feeds", value="\n".join(ws_rows), inline=False)
    e.add_field(name="HTTP", value=(f"shared session · {HTTP_STATS['requests']} uses"
                                    + (f" · up {int(now - HTTP_STATS['created_at'])}s" if HTTP_STATS['created_at'] else " · not opened yet")), inline=False)
    recent = [x for x in _ERR_LOG if now - x[0] < 86400]
    e.add_field(name=f"Errors (24h): {len(recent)}",
                value=("\n".join(f"`{w}` {m[:80]}" for _, w, m in recent[-6:]) if recent else "none"), inline=False)
    probs = _validate_startup()
    e.add_field(name="Config", value=("\n".join(probs)[:1024] if probs else "ok"), inline=False)
    e.set_footer(text=f"ops channel: {'set' if OPS_CHANNEL_ID else 'NOT SET (SIGMA_OPS_CHANNEL_ID)'} · log: logs/sigma_bot.log")
    await interaction.response.send_message(embed=e, ephemeral=True)

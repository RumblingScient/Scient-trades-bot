"""sigma.health - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import JOURNAL_FILE, NAVY, OPEN_BOARD_CHANNEL_ID, OPS_CHANNEL_ID, RESULTS_CHANNEL_ID, RESULTS_FILE, SPOT_FILE, TRADES_CHANNEL_ID
from sigma.core import bot
from sigma.ops import BOOT_TS, HEARTBEAT, _ERR_LOG, _note_error, ops_alert
from sigma.http import HTTP_STATS
from sigma.storage import _CORRUPT, _load
from sigma.members import subs_check_loop
from sigma.tracker import _pw_fail, _pw_unsupported, price_watch_loop
from sigma.telegram import tg_brief_loop, tg_digest_loop, tg_move_loop, tg_sources_loop
from sigma.alerts import alert_check_loop
from sigma.xfeed import x_poll_loop
from sigma.jobs import backup_loop, funding_guard_loop
from sigma.results import results_watch_loop
from sigma.recaps import sigma_monthly_loop, sigma_recap_loop
from sigma.payments import payment_watch_loop


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

def _self_heal(loop_obj, name: str, delay: int = 30):
    """discord.py stops a tasks.loop on an unhandled exception. This restarts it after `delay`s."""
    async def _on_err(*args):
        exc = args[-1]
        _note_error(f"loop:{name}", exc)
        await ops_alert(f"loop `{name}` crashed: `{type(exc).__name__}: {str(exc)[:160]}` - restarting in {delay}s", key=f"loop:{name}")
        await asyncio.sleep(delay)
        try:
            loop_obj.restart()
        except Exception as e2:
            _note_error(f"loop-restart:{name}", e2)
    loop_obj.error(_on_err)
    return loop_obj

def _validate_startup():
    problems = []
    for name, cid in (("TRADES_CHANNEL_ID", TRADES_CHANNEL_ID), ("RESULTS_CHANNEL_ID", RESULTS_CHANNEL_ID),
                      ("OPEN_BOARD_CHANNEL_ID", OPEN_BOARD_CHANNEL_ID)):
        if cid and bot.get_channel(cid) is None:
            problems.append(f"{name}={cid} not visible to the bot")
    if _CORRUPT:
        problems.append("corrupt data files: " + ", ".join(Path(p).name for p in _CORRUPT))
    return problems

_ALL_LOOPS = None

def _loops():
    global _ALL_LOOPS
    if _ALL_LOOPS is None:
        _ALL_LOOPS = (("price_watch", price_watch_loop), ("results", results_watch_loop), ("subs", subs_check_loop),
                      ("monthly", sigma_monthly_loop), ("weekly", sigma_recap_loop), ("backup", backup_loop),
                      ("x_poll", x_poll_loop), ("alerts", alert_check_loop), ("tg_move", tg_move_loop),
                      ("tg_brief", tg_brief_loop), ("tg_sources", tg_sources_loop), ("tg_digest", tg_digest_loop),
                      ("funding", funding_guard_loop), ("payments", payment_watch_loop))
    return _ALL_LOOPS

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
    rows = []
    for nm, lp in _loops():
        hb = HEARTBEAT.get(nm)
        age = f"{int(now - hb)}s ago" if hb else "-"
        rows.append(f"{'\u2705' if lp.is_running() else '\u274c'} {nm} \u00b7 {age}")
    e.add_field(name="Loops", value="\n".join(rows), inline=False)
    files = []
    for nm, pth in (("trades", JOURNAL_FILE), ("spot", SPOT_FILE), ("results", RESULTS_FILE)):
        try:
            d = _load(pth)
            files.append(f"{nm}: {len(d)} records \u00b7 {pth.stat().st_size // 1024} KB" + ("  \u26a0 CORRUPT" if str(pth) in _CORRUPT else ""))
        except Exception as ex:
            files.append(f"{nm}: {ex}")
    e.add_field(name="Data", value="\n".join(files), inline=False)
    e.add_field(name="Feed", value=(f"unsupported: {', '.join(sorted(_pw_unsupported)) or 'none'}\n"
                                    f"failing: {', '.join(f'{k}({v})' for k, v in _pw_fail.items()) or 'none'}"), inline=False)
    ws_rows = []
    for nm in ("ws_binance", "ws_bybit", "ws_okx"):
        hb = HEARTBEAT.get(nm)
        age = int(now - hb) if hb else None
        ws_rows.append(f"{'\u2705' if age is not None and age < 300 else '\u274c'} {nm[3:]} \u00b7 " + (f"last msg {age}s ago" if age is not None else "no message yet"))
    e.add_field(name="Liquidation feeds", value="\n".join(ws_rows), inline=False)
    e.add_field(name="HTTP", value=(f"shared session \u00b7 {HTTP_STATS['requests']} uses"
                                    + (f" \u00b7 up {int(now - HTTP_STATS['created_at'])}s" if HTTP_STATS['created_at'] else " \u00b7 not opened yet")), inline=False)
    recent = [x for x in _ERR_LOG if now - x[0] < 86400]
    e.add_field(name=f"Errors (24h): {len(recent)}",
                value=("\n".join(f"`{w}` {m[:80]}" for _, w, m in recent[-6:]) if recent else "none"), inline=False)
    probs = _validate_startup()
    e.add_field(name="Config", value=("\n".join(probs) if probs else "ok"), inline=False)
    e.set_footer(text=f"ops channel: {'set' if OPS_CHANNEL_ID else 'NOT SET (SIGMA_OPS_CHANNEL_ID)'} \u00b7 log: logs/sigma_bot.log")
    await interaction.response.send_message(embed=e, ephemeral=True)

"""sigma.app - boot sequence. Loops start first and every other step is isolated, so one failure cannot kill the rest."""
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
from sigma.logging_setup import log, print
from sigma.config import GUILD_ID, LIQ_CHANNEL_ID, NEWS_CHANNEL_ID, NEWS_ENABLED, OPS_CHANNEL_ID
from sigma.core import bot
from sigma.ops import _note_error, ops_alert
from sigma.storage import _CORRUPT
from sigma.members import FollowPanel
from sigma.boards import refresh_board, refresh_spot_board
from sigma.news import news_ws_loop
from sigma.liquidations import liq_binance_loop, liq_bybit_loop, liq_okx_loop
from sigma import health
from sigma.health import _loops, _self_heal, _validate_startup, on_app_command_error
from sigma.payments import PaymentPanel, QuoteView, TokenPick
from sigma.services import plans as _plansvc
from sigma.storage import load_payments
from sigma.recaps import monthly_catchup


_TASKS: set = set()   # strong refs to fire-and-forget tasks - asyncio only keeps weak ones and may drop them mid-flight


def _keep(task):
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _start_loops():
    """Start every enabled loop, each in its own try/except. Returns (running, enabled)."""
    running = enabled = 0
    for nm, lp, on in _loops():
        if not on:
            continue
        enabled += 1
        try:
            if not getattr(lp, "_sigma_healed", False):
                _self_heal(lp, nm); lp._sigma_healed = True
            if not lp.is_running():
                lp.start()
            running += 1
        except Exception as ex:
            _note_error(f"boot:loop:{nm}", ex)
    return running, enabled


async def _step(name: str, fn, failed: list):
    """One boot step; a failure is logged and remembered, never propagated."""
    try:
        r = fn()
        if asyncio.iscoroutine(r):
            await r
        return True
    except Exception as ex:
        _note_error(f"boot:{name}", ex)
        failed.append(f"{name} ({type(ex).__name__}: {str(ex)[:80]})")
        return False


def _register_views():
    bot.add_view(FollowPanel())
    bot.add_view(PaymentPanel())
    for _pk in _plansvc.all():
        bot.add_view(TokenPick(_pk))
    for _sid, _s in (load_payments().get("sessions") or {}).items():
        if not _s.get("paid"):
            bot.add_view(QuoteView(_sid))


def _start_card_views():
    from sigma.actions import ensure_card_views
    _keep(asyncio.create_task(ensure_card_views()))      # open cards get the Manage button once


def _start_feeds():
    if NEWS_ENABLED and NEWS_CHANNEL_ID and not getattr(bot, "_news_task", None):
        bot._news_task = _keep(asyncio.create_task(news_ws_loop()))   # sigma.news reads bot._news_task for its status line
    if LIQ_CHANNEL_ID and not getattr(bot, "_liq_task", None):
        bot._liq_task = _keep(asyncio.create_task(liq_binance_loop()))
        bot._liq_task_bybit = _keep(asyncio.create_task(liq_bybit_loop()))
        bot._liq_task_okx = _keep(asyncio.create_task(liq_okx_loop()))


async def _validate():
    probs = _validate_startup()
    if probs:
        await ops_alert("startup problems: " + "; ".join(probs), key="startup")


@bot.event
async def on_ready():
    # on_ready fires again on every reconnect: loops and feeds are idempotent, sync/card-views run once per process
    bot.tree.on_error = on_app_command_error
    running, enabled = _start_loops()
    failed = []
    sync_state = "done earlier"
    if not getattr(bot, "_synced", False):
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        try:
            await bot.tree.sync(guild=guild)
            bot._synced = True
            sync_state = "ok"
        except Exception as ex:
            sync_state = "FAILED"
            log.error(f"[boot] COMMAND SYNC FAILED - Discord is serving the previous command list: {ex}")
            await ops_alert(f"command sync failed on boot - members see the OLD commands until this is fixed: {str(ex)[:300]}", key="sync")
    await _step("validate", _validate, failed)
    await _step("views", _register_views, failed)
    if not getattr(bot, "_card_views_started", False):
        if await _step("card_views", _start_card_views, failed):
            bot._card_views_started = True
    await _step("refresh_board", refresh_board, failed)
    await _step("refresh_spot_board", refresh_spot_board, failed)
    await _step("feeds", _start_feeds, failed)
    await _step("monthly_catchup", monthly_catchup, failed)
    health.BOOT.update({"done": True, "sync": sync_state, "loops": f"{running}/{enabled}", "failed": failed})
    log.info(f"[boot] ready - sync={sync_state}, loops={running}/{enabled}, failed={failed or 'none'}, "
             f"ops_channel={'set' if OPS_CHANNEL_ID else 'unset'}, corrupt={sorted(_CORRUPT) or 'none'}")
    if failed:
        await ops_alert("boot steps failed (bot is up, the rest of boot continued): " + "; ".join(failed), key="boot:failed", cooldown=0)
    await ops_alert(f"booted: sync={sync_state}, loops running={running}/{enabled}" + (f", failed steps={len(failed)}" if failed else ""),
                    key="boot", cooldown=0)
    print(f"Logged in as {bot.user} - commands {'synced' if sync_state == 'ok' else sync_state}.")

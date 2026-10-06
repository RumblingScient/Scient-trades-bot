"""sigma.app - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import GUILD_ID, LIQ_CHANNEL_ID, NEWS_CHANNEL_ID, NEWS_ENABLED, OPS_CHANNEL_ID, TELEGRAM_BOT_TOKEN, TG_ENABLED, TG_NEWS_CHANNELS, X_AUTO_ENABLED
from sigma.core import bot
from sigma.ops import ops_alert
from sigma.storage import _CORRUPT
from sigma.members import FollowPanel, subs_check_loop
from sigma.boards import refresh_board, refresh_spot_board
from sigma.news import news_ws_loop
from sigma.telegram import tg_brief_loop, tg_move_loop, tg_sources_loop
from sigma.alerts import alert_check_loop
from sigma.liquidations import liq_binance_loop, liq_bybit_loop, liq_okx_loop
from sigma.xfeed import x_poll_loop
from sigma.jobs import backup_loop, funding_guard_loop
from sigma.health import _loops, _self_heal, _validate_startup, on_app_command_error
from sigma.payments import PaymentPanel, QuoteView, TokenPick, payment_watch_loop
from sigma.services import plans as _plansvc
from sigma.storage import load_payments
from sigma.config import SOL_WALLET


@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    bot.tree.on_error = on_app_command_error
    await bot.tree.sync(guild=guild)
    for nm, lp in _loops():
        if not getattr(lp, "_sigma_healed", False):
            _self_heal(lp, nm); lp._sigma_healed = True
    probs = _validate_startup()
    if probs:
        await ops_alert("startup problems: " + "; ".join(probs), key="startup")
    log.info(f"[boot] ready - ops_channel={'set' if OPS_CHANNEL_ID else 'unset'}, corrupt={sorted(_CORRUPT) or 'none'}")
    bot.add_view(FollowPanel())
    bot.add_view(PaymentPanel())
    for _pk in _plansvc.all():
        bot.add_view(TokenPick(_pk))
    for _sid, _s in (load_payments().get("sessions") or {}).items():
        if not _s.get("paid"):
            bot.add_view(QuoteView(_sid))
    if SOL_WALLET and not payment_watch_loop.is_running():
        payment_watch_loop.start()
    await refresh_board()
    await refresh_spot_board()
    if X_AUTO_ENABLED and not x_poll_loop.is_running():
        x_poll_loop.start()
    if NEWS_ENABLED and NEWS_CHANNEL_ID and not getattr(bot, "_news_task", None):
        bot._news_task = asyncio.create_task(news_ws_loop())
    if LIQ_CHANNEL_ID and not getattr(bot, "_liq_task", None):
        bot._liq_task = asyncio.create_task(liq_binance_loop())
        bot._liq_task_bybit = asyncio.create_task(liq_bybit_loop())
        bot._liq_task_okx = asyncio.create_task(liq_okx_loop())
    if TG_ENABLED and TELEGRAM_BOT_TOKEN and not tg_brief_loop.is_running():
        pass  # tg_brief_loop disabled - manual via /tg_send
    if TG_ENABLED and TELEGRAM_BOT_TOKEN and not tg_move_loop.is_running():
        pass  # tg_move_loop disabled - manual via /tg_send
    if not alert_check_loop.is_running():
        alert_check_loop.start()
    if not backup_loop.is_running():
        backup_loop.start()
    if not subs_check_loop.is_running():
        subs_check_loop.start()
    if not funding_guard_loop.is_running():
        funding_guard_loop.start()
    # tg_digest_loop disabled - TG updates are manual now via /tg_send
    if TG_NEWS_CHANNELS and not tg_sources_loop.is_running():
        tg_sources_loop.start()
    print(f"Logged in as {bot.user} - commands synced.")

"""sigma.boards - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import ANALYSTS, NAVY, OPEN_BOARD_CHANNEL_ID
from sigma.core import bot
from sigma.storage import load_board, load_spot, load_spot_board, load_trades, save_board, save_spot_board
from sigma.calculations import spot_status_line, tf
from sigma.cards import emo, entry_display, jump_url, short_status


def _fit_lines(lines, limit=1024):
    """Keep whole lines under Discord's 1024-char field cap; note how many were cut."""
    out, used = [], 0
    for i, ln in enumerate(lines):
        if used + len(ln) + 1 > limit - 24:
            out.append(f"*+{len(lines) - i} more*")
            break
        out.append(ln); used += len(ln) + 1
    return "\n".join(out) if out else "\u200b"

def build_combined_board_embed() -> discord.Embed:
    """One pinned card: FUTURES section on top, SPOT section below, per-analyst rows in each."""
    fut = [t for t in load_trades().values() if not t.get("closed")]
    spo = [p for p in load_spot().values() if not p.get("closed")]
    embed = discord.Embed(title="Open Positions - Live Board", color=NAVY,
                          timestamp=datetime.now(timezone.utc))
    embed.description = f"**{len(fut)} futures \u00b7 {len(spo)} spot** open right now"
    order = list(ANALYSTS.keys())

    def _grouped(items):
        items = sorted(items, key=lambda t: (order.index(t.get("analyst_key", "")) if t.get("analyst_key", "") in order
                                             else len(order), t.get("created_at", "")))
        groups = {}
        for t in items:
            groups.setdefault(t.get("analyst_key", "other"), []).append(t)
        keys = [k for k in order if k in groups] + [k for k in groups if k not in order]
        return [(groups[k][0].get("analyst_name", k.capitalize()), groups[k]) for k in keys]

    # ── FUTURES ──
    embed.add_field(name="\u2500\u2500\u2500  FUTURES  \u2500\u2500\u2500", value="\u200b", inline=False)
    if not fut:
        embed.add_field(name="\u200b", value="*No open futures setups.*", inline=False)
    for name, trades in _grouped(fut):
        lines = []
        for t in trades:
            d = emo("longR", "\u25b2") + " L" if t["direction"] == "LONG" else emo("shortR", "\u25bc") + " S"
            e = entry_display(t, marks=False)
            lines.append(f"{d} **{t['pair'].upper()}**" + (f" - {tf(t)}" if tf(t) else "")
                         + f" - entry `{e}` - {short_status(t)} - [view]({jump_url(t)})")
        embed.add_field(name=f"{name} ({len(trades)})", value=_fit_lines(lines), inline=False)

    # ── SPOT ──
    embed.add_field(name="\u2500\u2500\u2500  SPOT  \u2500\u2500\u2500", value="\u200b", inline=False)
    if not spo:
        embed.add_field(name="\u200b", value="*No active spot plays.*", inline=False)
    for name, plays in _grouped(spo):
        lines = []
        for p in plays:
            avg = f" - avg `{p['avg_entry']}`" if p.get("avg_entry") else ""
            lines.append(f"\U0001FA99 **{p['pair'].upper()}** - zone `{p['dca_zone']}`{avg}"
                         f" - {spot_status_line(p)} - [view]({jump_url(p)})")
        embed.add_field(name=f"{name} ({len(plays)})", value=_fit_lines(lines), inline=False)

    embed.set_footer(text=f"Sigma Trading - {len(fut)} futures / {len(spo)} spot - auto-updates")
    # Discord rejects embeds over 6000 chars - trim the longest fields until it fits
    while len(embed) > 5900 and any(len(f.value) > 300 for f in embed.fields):
        idx = max(range(len(embed.fields)), key=lambda i: len(embed.fields[i].value))
        f = embed.fields[idx]
        keep = f.value.splitlines()[:-2]
        embed.set_field_at(idx, name=f.name, value=_fit_lines(keep, limit=max(300, len(f.value) - 200)), inline=False)
    return embed

async def refresh_board():
    if not OPEN_BOARD_CHANNEL_ID:
        return
    ch = bot.get_channel(OPEN_BOARD_CHANNEL_ID)
    if ch is None:
        return
    embed = build_combined_board_embed()
    # one-time cleanup: the old standalone spot board message is retired
    sb = load_spot_board()
    if sb.get("message_id"):
        try:
            old_sb = await ch.fetch_message(sb["message_id"])
            await old_sb.delete()
        except Exception:
            pass
        save_spot_board({})
    board = load_board()
    msg_id = board.get("message_id")
    if msg_id:
        try:
            msg = await ch.fetch_message(msg_id)
            await msg.edit(embed=embed)
            return
        except discord.NotFound:
            pass
        except discord.HTTPException:
            pass
    msg = await ch.send(embed=embed)
    try:
        await msg.pin()
    except discord.HTTPException:
        pass
    save_board({"message_id": msg.id, "channel_id": ch.id})

async def refresh_spot_board():
    # spot rows live inside the combined board now
    await refresh_board()

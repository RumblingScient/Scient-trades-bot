"""sigma.sizer - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.logging_setup import print
from sigma.config import NAVY
from sigma.core import bot
from sigma.storage import load_trades
from sigma.calculations import entry_num, first_num


def _sizer_compute(entry: float, stop: float, risk_usd: float, pair: str, direction: str):
    dist = abs(entry - stop)
    if dist <= 0:
        return None
    coins = risk_usd / dist
    notional = coins * entry
    move_pct = dist / entry * 100
    warn = None
    if direction == "LONG" and stop > entry:
        warn = "Your stop is ABOVE entry for a long - double-check the numbers."
    if direction == "SHORT" and stop < entry:
        warn = "Your stop is BELOW entry for a short - double-check the numbers."
    e = discord.Embed(title=f"Position size - {pair}", color=NAVY)
    e.description = (
        f"**Size:** `{coins:.6g}` {pair.split('/')[0].upper()}  (\u2248 `${notional:,.2f}` notional)\n"
        f"**If the stop hits:** you lose `${risk_usd:,.2f}` - by design.\n"
        f"Stop distance: `{move_pct:.2f}%` from entry."
    )
    if warn:
        e.add_field(name="\u26a0 Check", value=warn, inline=False)
    e.set_footer(text="Leverage doesn't change the risk - only the margin you post. Educational tool - sizing is yours.")
    return e

class _SizerUSDModal(discord.ui.Modal, title="Position size - fixed $ risk"):
    def __init__(self, entry_default: str, stop_default: str, pair: str, direction: str):
        super().__init__(timeout=300)
        self._pair, self._dir = pair, direction
        self.i_entry = discord.ui.TextInput(label="Entry", default=entry_default, max_length=20)
        self.i_stop = discord.ui.TextInput(label="Invalidation / stop", default=stop_default, max_length=20)
        self.i_risk = discord.ui.TextInput(label="Max loss in $ (what you can lose)", placeholder="e.g. 40", max_length=12)
        for it in (self.i_entry, self.i_stop, self.i_risk):
            self.add_item(it)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            e = float(str(self.i_entry.value).replace(",", ""))
            s = float(str(self.i_stop.value).replace(",", ""))
            r = float(str(self.i_risk.value).replace(",", "").replace("$", ""))
            assert r > 0
        except Exception:
            await interaction.response.send_message("Numbers only - entry, stop and a $ amount.", ephemeral=True)
            return
        emb = _sizer_compute(e, s, r, self._pair, self._dir)
        if emb is None:
            await interaction.response.send_message("Entry and stop can't be the same price.", ephemeral=True)
            return
        await interaction.response.send_message(embed=emb, ephemeral=True)

class _SizerPctModal(discord.ui.Modal, title="Position size - % of capital"):
    def __init__(self, entry_default: str, stop_default: str, pair: str, direction: str):
        super().__init__(timeout=300)
        self._pair, self._dir = pair, direction
        self.i_entry = discord.ui.TextInput(label="Entry", default=entry_default, max_length=20)
        self.i_stop = discord.ui.TextInput(label="Invalidation / stop", default=stop_default, max_length=20)
        self.i_cap = discord.ui.TextInput(label="Capital ($)", placeholder="e.g. 2000", max_length=14)
        self.i_pct = discord.ui.TextInput(label="Risk % per trade", placeholder="e.g. 1", max_length=6)
        for it in (self.i_entry, self.i_stop, self.i_cap, self.i_pct):
            self.add_item(it)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            e = float(str(self.i_entry.value).replace(",", ""))
            s = float(str(self.i_stop.value).replace(",", ""))
            cap = float(str(self.i_cap.value).replace(",", "").replace("$", ""))
            pct = float(str(self.i_pct.value).replace("%", ""))
            assert cap > 0 and 0 < pct <= 100
        except Exception:
            await interaction.response.send_message("Numbers only - and risk % between 0 and 100.", ephemeral=True)
            return
        risk_usd = cap * pct / 100
        emb = _sizer_compute(e, s, risk_usd, self._pair, self._dir)
        if emb is None:
            await interaction.response.send_message("Entry and stop can't be the same price.", ephemeral=True)
            return
        emb.description = f"Risking **{pct:g}%** of `${cap:,.0f}` = `${risk_usd:,.2f}`\n\n" + emb.description
        await interaction.response.send_message(embed=emb, ephemeral=True)

class TradeSizerView(discord.ui.View):
    def __init__(self, tid):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Position size ($ risk)", style=discord.ButtonStyle.secondary,
                                        custom_id=f"tsz:usd:{tid}"))
        self.add_item(discord.ui.Button(label="Position size (% risk)", style=discord.ButtonStyle.secondary,
                                        custom_id=f"tsz:pct:{tid}"))

@bot.listen("on_interaction")
async def _sizer_router(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    cid = (interaction.data or {}).get("custom_id", "")
    if not cid.startswith("tsz:"):
        return
    try:
        _, kind, tid = cid.split(":", 2)
        t = load_trades().get(tid)
        if t is None:
            await interaction.response.send_message("This setup isn't in the log anymore.", ephemeral=True)
            return
        e = entry_num(t)
        s = first_num(t.get("sl"))
        e_def = f"{e:g}" if e else ""
        s_def = f"{s:g}" if s else ""
        pair = (t.get("pair") or "?").upper()
        direction = t.get("direction") or "LONG"
        modal = (_SizerUSDModal if kind == "usd" else _SizerPctModal)(e_def, s_def, pair, direction)
        await interaction.response.send_modal(modal)
    except Exception as ex:
        print(f"[sizer] error: {ex}", flush=True)
        try:
            await interaction.response.send_message("Something broke - try again.", ephemeral=True)
        except Exception:
            pass

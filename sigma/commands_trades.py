"""sigma.commands_trades - member journal commands (/open, /recent, /stats, /spot_stats) + the board rebuild.
Trade lifecycle commands live in sigma.commands_setup (/setup ...) and their old names in sigma.aliases."""
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
from sigma.config import ANALYST_ROLE_NAME, BLUE, DGREY, EDIT_WINDOW_MIN, FRAMEWORKS, GOLD, GREEN, GREY, NAVY, OPEN_BOARD_CHANNEL_ID, PING_ROLE_ID, RED, RESULTS_CHANNEL_ID, SPOT_CHANNEL_ID, SPOT_STATUSES, TRADES_CHANNEL_ID
from sigma.core import bot
from sigma.storage import load_results, load_spot, load_trades, save_board, save_results, save_spot, save_trades
from sigma.calculations import _in_window, _is_tp_fill, _stats_window, _sync_tp_flags, entry_num, fills_pct, finalize_close, first_num, fnum, parse_num, parse_sl, parse_spot_split, sl_num, spot_num, spot_ref_entry, spot_result_r, spot_signed_r, spot_weighted_entry, spot_weighted_exit, tf
from sigma.members import analyst_color_hex, is_analyst, resolve_analyst
from sigma.cards import _ac_label, _ac_sortkey, build_embed, build_spot_embed, emo, jump_url, post_update_feed, refresh_and_edit, thread_note, within_edit_window
from sigma.sizer import TradeSizerView
from sigma.boards import _fit_lines, build_combined_board_embed, refresh_board, refresh_spot_board
from sigma.tracker import _with_state_lock
from sigma.results import refresh_results_summary


@bot.tree.command(name="open", description="Every live setup and spot play right now")
async def open_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(embed=build_combined_board_embed(), ephemeral=True)

@bot.tree.command(name="recent", description="Latest closed setups and spot plays with results")
@app_commands.describe(analyst="Only this analyst - blank = everyone")
async def recent(interaction: discord.Interaction, analyst: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    data = load_trades()
    closed = [t for t in data.values() if t.get("closed")]
    if analyst:
        closed = [t for t in closed if t.get("analyst_id") == analyst.id]
    closed.sort(key=lambda t: t.get("closed_at", ""), reverse=True)
    closed = closed[:7]
    sdata = load_spot()
    sclosed = [p for p in sdata.values() if p.get("closed")]
    if analyst:
        sclosed = [p for p in sclosed if p.get("analyst_id") == analyst.id]
    sclosed.sort(key=lambda p: p.get("closed_at", ""), reverse=True)
    sclosed = sclosed[:5]
    title = "Recent Results"
    if analyst:
        title += f" - {analyst.display_name}"
    embed = discord.Embed(title=title, color=NAVY, timestamp=datetime.now(timezone.utc))
    if closed:
        lines = []
        for t in closed:
            d = emo("longR", "\u25b2") + " L" if t["direction"] == "LONG" else emo("shortR", "\u25bc") + " S"
            res = t.get("result", "?")
            r = t.get("result_r")
            rtxt = f" ({r:+g}R)" if isinstance(r, (int, float)) else ""
            emoji = {"WIN": "\u2705", "LOSS": "\u274C", "BE": "\u2796", "INVALID": "\U0001F6AB"}.get(res, "")
            lines.append(f"{emoji} {d} **{t['pair'].upper()}**" + (f" - {tf(t)}" if tf(t) else "") + f" - {res}{rtxt} - {t.get('analyst_name', '')} - [view]({jump_url(t)})")
        embed.add_field(name="Futures", value=_fit_lines(lines), inline=False)
    if sclosed:
        lines = []
        for p in sclosed:
            res = p.get("result", "?")
            pct = f" ({p['result_pct']})" if p.get("result_pct") else ""
            emoji = {"WIN": "\u2705", "LOSS": "\u274C", "BE": "\u2796", "INVALID": "\U0001F6AB"}.get(res, "")
            lines.append(f"{emoji} \U0001FA99 **{p['pair'].upper()}** - {res}{pct} - {p.get('analyst_name', '')} - [view]({jump_url(p)})")
        embed.add_field(name="Spot", value=_fit_lines(lines), inline=False)
    if not closed and not sclosed:
        embed.description = "*No closed trades yet.*"
    embed.set_footer(text="Sigma Trading - Journal")
    await interaction.followup.send(embed=embed, ephemeral=True)

# (moved to /admin board - registered in sigma.commands_admin)
async def board_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not OPEN_BOARD_CHANNEL_ID:
        await interaction.followup.send("Set OPEN_BOARD_CHANNEL_ID first.", ephemeral=True)
        return
    save_board({})
    await refresh_board()
    await interaction.followup.send("Board rebuilt.", ephemeral=True)

def build_trades_csv(trades: list, analyst_name: str) -> io.BytesIO:
    import csv as _csv
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["date", "analyst", "pair", "direction", "timeframe", "entry_type",
                "entry", "entry2", "sl", "risk_pct", "tp1", "tp2", "tp3", "tp4",
                "fills", "avg_exit", "result", "result_r", "status", "frameworks", "edited"])
    for t in trades:
        fills = "; ".join(f"{f.get('label','')}@{f.get('price','')}x{f.get('pct','')}%" for f in t.get("fills", []) or [])
        status = "CLOSED" if t.get("closed") else ("BE-set" if t.get("be") else "OPEN")
        created = t.get("created_at", "") or ""
        w.writerow([
            created[:10], analyst_name, t.get("pair", ""), t.get("direction", ""),
            t.get("timeframe", ""), t.get("entry_type", ""),
            t.get("entry", ""), t.get("entry2", ""), t.get("sl", ""), t.get("risk", ""),
            t.get("tp1", ""), t.get("tp2", ""), t.get("tp3", ""), t.get("tp4", ""),
            fills, t.get("avg_exit", ""), t.get("result", ""), t.get("result_r", ""),
            status, " + ".join(t.get("frameworks", []) or []), "yes" if t.get("edited") else "",
        ])
    out = io.BytesIO(buf.getvalue().encode("utf-8-sig"))  # BOM so Excel opens it cleanly
    out.seek(0)
    return out

class StatsCSVView(View):
    def __init__(self, trades: list, analyst_name: str):
        super().__init__(timeout=600)
        self.trades = trades
        self.analyst_name = analyst_name
        btn = Button(label="\U0001F4E5 Download CSV", style=discord.ButtonStyle.secondary)
        btn.callback = self.send_csv
        self.add_item(btn)

    async def send_csv(self, interaction: discord.Interaction):
        f = discord.File(build_trades_csv(self.trades, self.analyst_name),
                         filename=f"{self.analyst_name}_journal.csv")
        await interaction.response.send_message(file=f, ephemeral=True)

_PERIOD_CHOICES = [
    app_commands.Choice(name="Last month (complete)", value=-1),
    app_commands.Choice(name="This month (so far)", value=-2),
    app_commands.Choice(name="Last 7 days", value=7),
    app_commands.Choice(name="Last 30 days", value=30),
    app_commands.Choice(name="Last 90 days", value=90),
    app_commands.Choice(name="All time", value=0),
]

@bot.tree.command(name="stats", description="Analyst scorecard - futures R, spot R, total. Default: last complete month")
@app_commands.describe(analyst="Which analyst - blank = you", period="Time window - blank = last complete month")
@app_commands.choices(period=_PERIOD_CHOICES)
async def stats(interaction: discord.Interaction, analyst: discord.Member = None,
                period: app_commands.Choice[int] = None):
    await interaction.response.defer(ephemeral=True)
    target = analyst or interaction.user
    w_start, w_end, plabel = _stats_window(period.value if period else -1)
    data = load_trades()
    mine = [t for t in data.values() if t.get("analyst_id") == target.id]
    total = len(mine)
    closed = [t for t in mine if t.get("closed") and _in_window(t, w_start, w_end)]
    wins = [t for t in closed if t["result"] == "WIN"]
    losses = [t for t in closed if t["result"] == "LOSS"]
    be = [t for t in closed if t["result"] == "BE"]
    invalid = [t for t in closed if t["result"] == "INVALID"]
    decided = len(wins) + len(losses)
    wr = (len(wins) / decided * 100) if decided else 0
    tp1_rate = (sum(1 for t in closed if t.get("tp1_hit")) / len(closed) * 100) if closed else 0
    rs = [t["result_r"] for t in closed if isinstance(t.get("result_r"), (int, float))]
    total_r = sum(rs) if rs else None
    avg_r = (sum(rs) / len(rs)) if rs else None
    best = max(rs) if rs else None
    worst = min(rs) if rs else None
    try:
        ecolor = discord.Color.from_str(analyst_color_hex(target))
    except Exception:
        ecolor = NAVY
    # spot, same window
    s_all = [p for p in load_spot().values() if p.get("analyst_id") == target.id]
    s_closed = [p for p in s_all if p.get("closed") and _in_window(p, w_start, w_end)]
    s_rs = [r for r in (spot_result_r(p) for p in s_closed) if r is not None]
    s_w = sum(1 for p in s_closed if p.get("result") == "WIN")
    s_l = sum(1 for p in s_closed if p.get("result") == "LOSS")
    s_wr = (s_w / (s_w + s_l) * 100) if (s_w + s_l) else 0
    fut_r = total_r if total_r is not None else 0.0
    spot_r = sum(s_rs) if s_rs else 0.0
    graded = [(t.get("result_r"), f"{t.get('pair','?').upper()} fut") for t in closed
              if isinstance(t.get("result_r"), (int, float))]
    graded += [(spot_result_r(p), f"{p.get('pair','?').upper()} spot") for p in s_closed
               if spot_result_r(p) is not None]
    embed = discord.Embed(title=f"Scorecard - {target.display_name} \u00b7 {plabel}", color=ecolor)
    embed.add_field(name="Futures R", value=(f"**{fut_r:+.2f}R**" if rs else "-"), inline=True)
    embed.add_field(name="Spot R", value=(f"**{spot_r:+.2f}R**" if s_rs else "-"), inline=True)
    embed.add_field(name="Total R", value=(f"**{fut_r + spot_r:+.2f}R**" if (rs or s_rs) else "-"), inline=True)
    embed.add_field(name="Futures", value=(f"{len(closed)} closed \u00b7 {len(wins)}W/{len(losses)}L \u00b7 {wr:.0f}%" if closed else "no closes"), inline=True)
    embed.add_field(name="Spot", value=(f"{len(s_closed)} closed \u00b7 {s_w}W/{s_l}L \u00b7 {s_wr:.0f}%" if s_closed else "no closes"), inline=True)
    embed.add_field(name="Open now", value=f"{sum(1 for t in mine if not t.get('closed'))} futures \u00b7 {sum(1 for p in s_all if not p.get('closed'))} spot", inline=True)
    tail = []
    if graded:
        b = max(graded, key=lambda x: x[0]); w_ = min(graded, key=lambda x: x[0])
        tail.append(f"**Best** {b[0]:+.2f}R ({b[1]})  \u00b7  **Worst** {w_[0]:+.2f}R ({w_[1]})")
    tail.append(f"**BE / Invalidated** {len(be)} / {len(invalid)}")
    embed.add_field(name="\u200b", value="  \u00b7  ".join(tail), inline=False)
    embed.set_footer(text="Sigma Trading - Journal \u00b7 R only counts graded closes")
    await interaction.followup.send(embed=embed, view=StatsCSVView(mine, target.display_name), ephemeral=True)

@bot.tree.command(name="spot_stats", description="Spot-only scorecard with each play's result %. Default: last complete month")
@app_commands.describe(analyst="Which analyst - blank = you", period="Time window - blank = last complete month")
@app_commands.choices(period=_PERIOD_CHOICES)
async def spot_stats(interaction: discord.Interaction, analyst: discord.Member = None,
                     period: app_commands.Choice[int] = None):
    await interaction.response.defer(ephemeral=True)
    target = analyst or interaction.user
    w_start, w_end, plabel = _stats_window(period.value if period else -1)
    data = load_spot()
    mine = [p for p in data.values() if p.get("analyst_id") == target.id]
    total = len(mine)
    closed = [p for p in mine if p.get("closed") and _in_window(p, w_start, w_end)]
    wins = [p for p in closed if p["result"] == "WIN"]
    losses = [p for p in closed if p["result"] == "LOSS"]
    be = [p for p in closed if p["result"] == "BE"]
    invalid = [p for p in closed if p["result"] == "INVALID"]
    decided = len(wins) + len(losses)
    wr = (len(wins) / decided * 100) if decided else 0
    try:
        ecolor = discord.Color.from_str(analyst_color_hex(target))
    except Exception:
        ecolor = NAVY
    embed = discord.Embed(title=f"Spot Scorecard - {target.display_name} \u00b7 {plabel}", color=ecolor)
    embed.add_field(name="Total plays", value=str(total), inline=True)
    embed.add_field(name="Closed", value=str(len(closed)), inline=True)
    embed.add_field(name="Active", value=str(total - len(closed)), inline=True)
    embed.add_field(name="Win rate", value=(f"{wr:.0f}% ({len(wins)}W/{len(losses)}L)" if decided else "-"), inline=True)
    embed.add_field(name="BE / Invalid", value=f"{len(be)} / {len(invalid)}", inline=True)
    rs = [r for r in (spot_result_r(p) for p in closed) if r is not None]
    embed.add_field(name="Total R", value=(f"{sum(rs):+.2f}R" if rs else "-"), inline=True)
    embed.add_field(name="Avg R", value=(f"{sum(rs)/len(rs):+.2f}R" if rs else "-"), inline=True)
    embed.add_field(name="Graded on", value=(f"{len(rs)} / {len(closed)} closed" if closed else "-"), inline=True)
    results = [p.get("result_pct") for p in closed if p.get("result_pct")]
    embed.add_field(name="Results (%)", value=(", ".join(results[:10]) if results else "-"), inline=False)
    embed.set_footer(text="Sigma Trading - Spot Journal \u00b7 R = (exit - entry) / (entry - invalidation) \u00b7 plays without a numeric invalidation aren't graded")
    await interaction.followup.send(embed=embed, ephemeral=True)

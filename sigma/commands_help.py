"""sigma.commands_help - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import NAVY
from sigma.core import bot


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="\u03a3 Sigma Terminal - Command Guide",
        description="Everything the bot can do, grouped by what you need. All replies to market commands are public; anything marked *(private)* is visible only to you.",
        color=NAVY,
    )
    embed.add_field(
        name="\U0001F4CA Market Data",
        value=(
            "`/price` - live price, 24h range (crypto + SPX/GOLD/DXY)\n"
            "`/snapshot` - full market check in one command\n"
            "`/chart` - candlestick chart with EMAs\n"
            "`/heatmap` - whole market at a glance\n"
            "`/gainers` `/losers` - top movers (24h)\n"
            "`/dominance` - BTC dominance\n"
            "`/fear` - Fear & Greed index\n"
            "`/calendar` - CPI / FOMC dates\n"
            "`/stables` - stablecoin supply, liquidity in or out"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F525 Bull-market scanners",
        value=(
            "`/rs` - relative strength vs BTC, where rotation is\n"
            "`/breakouts` - coins at 30d highs on expanding volume\n"
            "`/crowded` - funding extremes, crowded longs and shorts\n"
            "`/exitwatch` - heat gauge: structure + leverage + sentiment, one score\n"
            "`/brief` - today's market read in one message"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F52C Derivatives & Flow",
        value=(
            "`/funding` - funding rate, who's paying\n"
            "`/oi` - open interest + 24h change\n"
            "`/agg` - OI + funding across Binance/Bybit/OKX\n"
            "`/cvd` - spot vs perp CVD, who's driving the move\n"
            "`/lsr` - long/short ratio of top traders\n"
            "`/whale` - large trades in the last hour\n"
            "`/liqzones` - estimated liquidation heatmap (magnet zones)\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F4C9 Volatility & Levels",
        value=(
            "`/levels` - auto support/resistance with strength\n"
            "`/vol` - current volatility snapshot (for SL sizing)\n"
            "`/rvol` - volatility regime, compressed or wild\n"
            "`/cycle` - BTC cycle heat: Pi Cycle, Mayer, 200W MA\n"
            "`/altseason` - % of top 50 beating BTC, rotation index\n"
            "`/ratio` - coin vs BTC: real strength or USD beta?\n"
            "`/unlocks` - token unlock calendar, next 14 days\n"
            "`/fees` - BTC + ETH fees, the retail thermometer\n"
            "`/rainbow` - the Bitcoin Rainbow, which band are we in\n"
            "`/monthly` - BTC monthly returns heatmap\n"
            "`/bmsb` - Bull Market Support Band, structure check\n"
            "`/roi` - this cycle vs 2020, multiple since halving\n"
            "`/etf` - Bitcoin ETF daily flows, the institutional bid\n"
            "`/options` - DVOL, put/call, max pain (Deribit)\n"
            "`/hash` - Hash Ribbons, miner capitulation signal\n"
            "`/premium` - Coinbase + Kimchi premium, US vs Korean bid\n"
            "`/basis` - quarterly futures basis, the carry regime\n"
            "`/sessions` - Asia vs EU vs US, who's buying (30d)\n"
            "`/corr` - correlation matrix vs SPX, DXY, GOLD"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F9EE Calculators *(private)*",
        value=(
            "`/pnl` - position size from account, risk %, entry, SL\n"
            "`/liq` - liquidation price for any entry and leverage"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F440 Tracking & Alerts *(private)*",
        value=(
            "`/watch` - your watchlist, crypto + stocks/gold, live prices\n"
            "`/alert` - price alert, DM when it triggers\n"
            "`/alerts` - view and manage your alerts"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F4C8 Trade Journal",
        value=(
            "`/open` - every live position right now\n"
            "`/recent` - latest closed trades with results\n"
            "`/stats` - analyst scorecard + CSV download *(private)*\n"
            "`/spot_stats` - spot journal scorecard *(private)*"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F514 Pings & Learning",
        value=(
            "`/follow` `/unfollow` - analyst trade pings\n"
            "Or use the buttons in #select-analyst-alerts"
        ),
        inline=False,
    )
    embed.set_footer(text="Sigma Trading - Sigma Terminal")
    return embed

# (moved to /admin - registered in sigma.commands_admin)
async def setup_help_panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    msg = await interaction.channel.send(embed=build_help_embed())
    try:
        await msg.pin()
        note = "posted and pinned"
    except discord.HTTPException:
        note = "posted (couldn't pin - check my Manage Messages permission)"
    await interaction.followup.send(f"Command guide {note}.", ephemeral=True)

@bot.tree.command(name="help", description="See all commands you can use")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(embed=build_help_embed(), ephemeral=True)

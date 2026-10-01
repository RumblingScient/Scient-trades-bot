"""sigma.alerts - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import ALERT_CHECK_MIN, FREE_ALERT_LIMIT, GREEN, NAVY, RED
from sigma.core import bot
from sigma.storage import load_alerts, load_watchlists, save_alerts, save_watchlists
from sigma.market_data import is_tradfi, md_price, md_ticker24, md_tradfi
from sigma.calculations import fnum, parse_num
from sigma.members import member_is_pro


@bot.tree.command(name="alert", description="Set a price alert - DM when a coin crosses your target")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL", price="Target price to alert at")
async def alert_cmd(interaction: discord.Interaction, coin: str, price: str):
    await interaction.response.defer(ephemeral=True)
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    target = parse_num(price)
    if not target or target <= 0:
        await interaction.followup.send("Target price must be a positive number.", ephemeral=True)
        return
    cur = await md_price(pair)
    if cur is None:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit - check the symbol.", ephemeral=True)
        return
    alerts = load_alerts()
    uid = str(interaction.user.id)
    user_alerts = alerts.get(uid, [])
    is_pro = member_is_pro(interaction.user)
    if not is_pro and len(user_alerts) >= FREE_ALERT_LIMIT:
        await interaction.followup.send(
            f"You've hit the free limit of **{FREE_ALERT_LIMIT} active alerts**. "
            f"Delete one with `/alerts` or upgrade for unlimited alerts.",
            ephemeral=True,
        )
        return
    direction = "above" if target > cur else "below"
    user_alerts.append({
        "pair": pair, "symbol": symbol, "target": target,
        "direction": direction, "set_price": cur,
        "created": datetime.now(timezone.utc).isoformat(),
    })
    alerts[uid] = user_alerts
    save_alerts(alerts)
    left = "unlimited" if is_pro else f"{FREE_ALERT_LIMIT - len(user_alerts)} left"
    await interaction.followup.send(
        f"\u2705 Alert set: **{symbol}** {direction} **{fnum(target)}** (now {fnum(cur)}).\n"
        f"I'll DM you when it triggers. Active alerts: {len(user_alerts)} ({left}).",
        ephemeral=True,
    )

@bot.tree.command(name="alerts", description="View and manage your active price alerts")
async def alerts_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    alerts = load_alerts()
    uid = str(interaction.user.id)
    user_alerts = alerts.get(uid, [])
    if not user_alerts:
        await interaction.followup.send("You have no active alerts. Set one with `/alert BTC 70000`.", ephemeral=True)
        return
    embed = discord.Embed(title="Your Price Alerts", color=NAVY)
    lines = []
    for idx, a in enumerate(user_alerts, 1):
        lines.append(f"**{idx}.** {a['symbol']} {a['direction']} {fnum(a['target'])}")
    is_pro = member_is_pro(interaction.user)
    cap = "unlimited" if is_pro else f"{len(user_alerts)}/{FREE_ALERT_LIMIT}"
    embed.description = "\n".join(lines) + f"\n\n*Active: {cap}. Use the buttons below to remove.*"
    view = AlertManageView(uid, user_alerts)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

class AlertManageView(View):
    def __init__(self, uid: str, user_alerts: list):
        super().__init__(timeout=300)
        for idx, a in enumerate(user_alerts):
            if idx >= 20:
                break
            self.add_item(AlertDeleteButton(uid, idx, a))

class AlertDeleteButton(Button):
    def __init__(self, uid: str, idx: int, a: dict):
        super().__init__(label=f"\u2716 {a['symbol']} {fnum(a['target'])}"[:80], style=discord.ButtonStyle.secondary)
        self.uid = uid
        self.target = a["target"]
        self.pair = a["pair"]

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message("Not your alert.", ephemeral=True)
            return
        alerts = load_alerts()
        arr = alerts.get(self.uid, [])
        arr = [x for x in arr if not (x["pair"] == self.pair and x["target"] == self.target)]
        alerts[self.uid] = arr
        save_alerts(alerts)
        self.disabled = True
        await interaction.response.edit_message(content="Alert removed.", view=None)

@bot.tree.command(name="watch", description="Your personal coin watchlist with live prices")
@app_commands.describe(action="add / remove / clear (leave empty to view)", coins="Coin symbols, space or comma separated, e.g. BTC ETH SOL")
@app_commands.choices(action=[
    app_commands.Choice(name="view", value="view"),
    app_commands.Choice(name="add", value="add"),
    app_commands.Choice(name="remove", value="remove"),
    app_commands.Choice(name="clear", value="clear"),
])
async def watch_cmd(interaction: discord.Interaction, action: app_commands.Choice[str] = None, coins: str = None):
    await interaction.response.defer(ephemeral=True)
    act = action.value if action else "view"
    wl = load_watchlists()
    uid = str(interaction.user.id)
    current = wl.get(uid, [])

    if act == "clear":
        wl.pop(uid, None)
        save_watchlists(wl)
        await interaction.followup.send("Watchlist cleared.", ephemeral=True)
        return

    if act in ("add", "remove"):
        if not coins:
            await interaction.followup.send(f"Give me coins to {act}, e.g. `/watch {act} coins:BTC ETH SOL`", ephemeral=True)
            return
        syms = [re.sub(r"[^A-Za-z0-9]", "", c).upper() for c in re.split(r"[ ,]+", coins) if c.strip()][:10]
        if act == "add":
            added = []
            for s in syms:
                if s and s not in current:
                    if is_tradfi(s):
                        if await md_tradfi(s) is not None:
                            current.append(s)
                            added.append(s)
                        continue
                    pair = s if s.endswith("USDT") else f"{s}USDT"
                    if await md_price(pair) is not None:
                        current.append(s)
                        added.append(s)
            current = current[:25]
            wl[uid] = current
            save_watchlists(wl)
            msg = f"Added: {', '.join(added)}" if added else "Nothing added (already listed or not found)."
            await interaction.followup.send(msg, ephemeral=True)
        else:
            current = [s for s in current if s not in syms]
            wl[uid] = current
            save_watchlists(wl)
            await interaction.followup.send(f"Removed: {', '.join(syms)}", ephemeral=True)
        return

    # view
    if not current:
        await interaction.followup.send("Your watchlist is empty. Add coins with `/watch add coins:BTC ETH SOL`.", ephemeral=True)
        return
    async def _row(s):
        if is_tradfi(s):
            tf = await md_tradfi(s)
            if not tf:
                return None
            arrow = "\U0001F7E2" if tf["chg"] >= 0 else "\U0001F534"
            return f"{arrow} **{s}** {tf['price']:,.2f} ({tf['chg']:+.2f}%)"
        pair = s if s.endswith("USDT") else f"{s}USDT"
        t = await md_ticker24(pair)
        if not t:
            return None
        arrow = "\U0001F7E2" if t["priceChangePercent"] >= 0 else "\U0001F534"
        return f"{arrow} **{s}** ${fnum(t['lastPrice'])} ({t['priceChangePercent']:+.2f}%)"
    results = await asyncio.gather(*[_row(s) for s in current], return_exceptions=True)
    lines = [r for r in results if isinstance(r, str)]
    embed = discord.Embed(title="Your Watchlist", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines) if lines else "*Couldn't fetch prices right now.*"
    embed.set_footer(text="Sigma Trading - 24h change")
    await interaction.followup.send(embed=embed, ephemeral=True)

ECON_EVENTS = [
    # CPI: BLS official release schedule (8:30 AM ET) - bls.gov/schedule/news_release/cpi.htm
    ("2026-08-12", "US CPI (July) - 8:30 AM ET"),
    ("2026-09-11", "US CPI (August) - 8:30 AM ET"),
    ("2026-10-14", "US CPI (September) - 8:30 AM ET"),
    ("2026-11-10", "US CPI (October) - 8:30 AM ET"),
    ("2026-12-10", "US CPI (November) - 8:30 AM ET"),
    # FOMC: decision day = second day of meeting, 2:00 PM ET
    # federalreserve.gov/monetarypolicy/fomccalendars.htm
    ("2026-09-16", "FOMC Rate Decision + dot plot - 2:00 PM ET"),
    ("2026-10-28", "FOMC Rate Decision - 2:00 PM ET"),
    ("2026-12-09", "FOMC Rate Decision + dot plot - 2:00 PM ET"),
]

@bot.tree.command(name="calendar", description="Upcoming market-moving economic events")
async def calendar_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    today = datetime.now(timezone.utc).date()
    upcoming = []
    for ds, name in ECON_EVENTS:
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except Exception:
            continue
        if d >= today:
            days_away = (d - today).days
            when = "today" if days_away == 0 else "tomorrow" if days_away == 1 else f"in {days_away} days"
            upcoming.append(f"**{d.strftime('%d %b')}** ({when}) - {name}")
    embed = discord.Embed(title="\U0001F4C5 Economic Calendar", color=NAVY, timestamp=datetime.now(timezone.utc))
    if upcoming:
        embed.description = "\n".join(upcoming[:10]) + "\n\n*CPI and FOMC dates move crypto. Manage risk around them.*"
    else:
        embed.description = "No upcoming events on file. Ping an admin to refresh the calendar."
    embed.set_footer(text="Sigma Trading - official BLS + Federal Reserve schedules")
    await interaction.followup.send(embed=embed)

@tasks.loop(minutes=ALERT_CHECK_MIN)
async def alert_check_loop():
    alerts = load_alerts()
    if not alerts:
        return
    # gather unique pairs to price once
    pairs = list({a["pair"] for arr in alerts.values() for a in arr})
    fetched = await asyncio.gather(*[md_price(p) for p in pairs], return_exceptions=True)
    prices = {p: px for p, px in zip(pairs, fetched) if isinstance(px, (int, float)) and px is not None}
    changed = False
    for uid, arr in list(alerts.items()):
        remaining = []
        for a in arr:
            cur = prices.get(a["pair"])
            if cur is None:
                remaining.append(a)
                continue
            hit = (a["direction"] == "above" and cur >= a["target"]) or (a["direction"] == "below" and cur <= a["target"])
            if not hit:
                remaining.append(a)
                continue
            changed = True
            try:
                user = bot.get_user(int(uid)) or await bot.fetch_user(int(uid))
                arrow = "\U0001F7E2" if a["direction"] == "above" else "\U0001F534"
                embed = discord.Embed(
                    title=f"{arrow} Price Alert: {a['symbol']}",
                    description=f"**{a['symbol']}** has crossed **{a['direction']} {fnum(a['target'])}**\nCurrent price: **{fnum(cur)}**",
                    color=GREEN if a["direction"] == "above" else RED,
                    timestamp=datetime.now(timezone.utc),
                )
                embed.set_footer(text="Sigma Trading - Quant alerts")
                await user.send(embed=embed)
            except discord.Forbidden:
                print(f"[alert] DM blocked for {uid}")
            except Exception as e:
                print(f"[alert] DM error {uid}: {e}")
        if remaining:
            alerts[uid] = remaining
        else:
            alerts.pop(uid, None)
    if changed:
        save_alerts(alerts)

@alert_check_loop.before_loop
async def before_alert_check():
    await bot.wait_until_ready()

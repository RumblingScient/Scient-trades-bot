"""sigma.xfeed - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import ANALYST_ROLE_NAME, TWITTERAPIS_KEY, X_AUTO_ENABLED, X_AUTO_USERNAME, X_FEED_CHANNEL_ID, X_PING_ROLE_ID, X_POLL_MINUTES
from sigma.core import bot
from sigma.http import http
from sigma.storage import load_xseen, save_xseen
from sigma.calculations import fix_x_link
from sigma.members import is_analyst


@tasks.loop(minutes=X_POLL_MINUTES)
async def x_poll_loop():
    if not (X_AUTO_ENABLED and TWITTERAPIS_KEY and X_FEED_CHANNEL_ID and X_AUTO_USERNAME):
        return
    channel = bot.get_channel(X_FEED_CHANNEL_ID)
    if channel is None:
        return
    query = f"from:{X_AUTO_USERNAME} -filter:replies -filter:retweets"
    url = "https://api.twitterapis.com/twitter/tweet/advanced_search"
    params = {"query": query, "product": "Latest"}
    headers = {"Authorization": f"Bearer {TWITTERAPIS_KEY}"}
    try:
        async with http() as session:
            async with session.get(url, params=params, headers=headers, timeout=30) as resp:
                if resp.status != 200:
                    print(f"[x_poll] non-200: {resp.status}")
                    return
                data = await resp.json()
    except Exception as e:
        print(f"[x_poll] error: {e}")
        return
    tweets = data.get("tweets", []) or []
    if not tweets:
        return
    seen = load_xseen()
    seen_ids = set(seen.get("ids", []))
    first_run = not seen.get("initialized", False)
    new_tweets = [tw for tw in tweets if str(tw.get("id")) not in seen_ids]
    new_tweets.reverse()
    if first_run:
        for tw in tweets:
            seen_ids.add(str(tw.get("id")))
        seen["ids"] = list(seen_ids)[-500:]
        seen["initialized"] = True
        save_xseen(seen)
        print(f"[x_poll] first run - seeded {len(tweets)} tweets, none posted")
        return
    for tw in new_tweets:
        tid = str(tw.get("id"))
        username = (tw.get("author") or {}).get("username", X_AUTO_USERNAME)
        link = f"https://x.com/{username}/status/{tid}"
        fixed = fix_x_link(link)
        parts = []
        allowed = discord.AllowedMentions.none()
        if X_PING_ROLE_ID:
            parts.append(f"<@&{X_PING_ROLE_ID}>")
            allowed = discord.AllowedMentions(roles=True)
        parts.append(fixed)
        try:
            await channel.send(content="\n".join(parts), allowed_mentions=allowed)
            seen_ids.add(tid)
        except Exception as e:
            print(f"[x_poll] post error: {e}")
    seen["ids"] = list(seen_ids)[-500:]
    seen["initialized"] = True
    save_xseen(seen)
    if new_tweets:
        print(f"[x_poll] posted {len(new_tweets)} new tweet(s)")

@x_poll_loop.before_loop
async def before_x_poll():
    await bot.wait_until_ready()

# (moved to /admin - registered in sigma.commands_admin)
@app_commands.describe(link="X/Twitter post URL", comment="Optional intro text")
async def xpost(interaction: discord.Interaction, link: str, comment: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message(f"Only members with the **{ANALYST_ROLE_NAME}** role can share X posts.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not X_FEED_CHANNEL_ID:
        await interaction.followup.send("X feed channel not set.", ephemeral=True)
        return
    channel = bot.get_channel(X_FEED_CHANNEL_ID)
    if channel is None:
        await interaction.followup.send("X feed channel not found.", ephemeral=True)
        return
    if not re.match(r"https?://(www\.|mobile\.|m\.)?(twitter|x|fxtwitter|vxtwitter|nitter)\.com/", link.strip(), flags=re.I):
        await interaction.followup.send("That doesn't look like an X/Twitter post link.", ephemeral=True)
        return
    fixed = fix_x_link(link)
    parts = []
    allowed = discord.AllowedMentions.none()
    if X_PING_ROLE_ID:
        parts.append(f"<@&{X_PING_ROLE_ID}>")
        allowed = discord.AllowedMentions(roles=True)
    if comment:
        parts.append(comment)
    parts.append(fixed)
    msg = await channel.send(content="\n".join(parts), allowed_mentions=allowed)
    await interaction.followup.send(f"Posted to {channel.mention} ({msg.jump_url})", ephemeral=True)

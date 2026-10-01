"""sigma.news - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import NAVY, NEWS_CHANNEL_ID, NEWS_COINS, NEWS_ENABLED, NEWS_KEYWORDS, NEWS_PING_ROLE_ID, NEWS_SOURCE_BLACKLIST, NEWS_SOURCE_WHITELIST, NEWS_WS_URL, RED
from sigma.core import bot
from sigma.http import http
from sigma.storage import load_digest, save_digest


_news_seen = []

_news_last_msg = {"time": None, "posted": 0}

def _news_relevant(text: str, coins: list) -> bool:
    low = " " + text.lower()
    # majors must be mentioned in the TEXT itself - coin TAGS are not trusted
    # (TreeNews over-tags majors on ecosystem news)
    if re.search(r"\b(btc|bitcoin|eth|ethereum|sol|solana)\b", low):
        return True
    if not any(k in low for k in NEWS_KEYWORDS):
        return False
    # keyword matched: reject if it's clearly about a random small project
    up = {c.upper() for c in coins if c}
    if up and not (up & NEWS_COINS):
        heavy = ("sec ", "fed ", "fomc", "etf", "cpi", "rate cut", "rate hike",
                 "hack", "exploit", "bankrupt", "binance", "coinbase", "blackrock", "tether")
        if not any(k in low for k in heavy):
            return False
    return True

def _news_urgent(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in ("hack", "exploit", "breach", "stolen", "bankrupt", "halt", "delist"))

async def _post_news(item: dict):
    ch = bot.get_channel(NEWS_CHANNEL_ID)
    if ch is None:
        return
    title = str(item.get("title") or "")
    body = str(item.get("body") or item.get("en") or "")
    link = item.get("url") or item.get("link") or ""
    source = str(item.get("source") or "").strip()
    coins = []
    for s in item.get("symbols", []) or []:
        coins.append(str(s).replace("USDT", "").replace("_", ""))
    for sug in item.get("suggestions", []) or []:
        if isinstance(sug, dict) and sug.get("coin"):
            coins.append(str(sug["coin"]))
    text = f"{title} {body}".strip()
    if not text:
        return
    src_sig = f"{title} {source} {link}".lower()
    if any(b in src_sig for b in NEWS_SOURCE_BLACKLIST):
        return
    trusted = any(w in src_sig for w in NEWS_SOURCE_WHITELIST)
    # Twitter items are whitelist-only: project promo tweets never pass,
    # regardless of what coins they mention
    is_twitter = ("twitter.com" in src_sig or "x.com/" in src_sig or re.search(r"\(@\w+\)", title or ""))
    if is_twitter and not trusted:
        return
    if not trusted and not _news_relevant(text, coins):
        return
    key = hashlib.md5(text[:200].encode()).hexdigest()
    if key in _news_seen:
        return
    _news_seen.append(key)
    del _news_seen[:-300]
    urgent = _news_urgent(text)
    color = RED if urgent else NAVY
    embed = discord.Embed(color=color, timestamp=datetime.now(timezone.utc))
    headline = title if title else body[:250]
    embed.title = ("\U0001F6A8 " if urgent else "\U0001F4F0 ") + headline[:250]
    desc = ""
    if body and body != headline:
        desc = body[:400]
    if coins:
        tags = " ".join(f"`{c.upper()}`" for c in dict.fromkeys(coins[:6]))
        desc = (desc + "\n\n" if desc else "") + tags
    if desc:
        embed.description = desc
    if link:
        embed.url = link
    embed.set_footer(text=f"News Wire - {source or 'TreeNews'}")
    content = None
    allowed = discord.AllowedMentions.none()
    if urgent and NEWS_PING_ROLE_ID:
        content = f"<@&{NEWS_PING_ROLE_ID}>"
        allowed = discord.AllowedMentions(roles=True)
    try:
        await ch.send(content=content, embed=embed, allowed_mentions=allowed)
        _news_last_msg["posted"] += 1
    except Exception as e:
        print(f"[news] post error: {e}")
        return
    # buffer for the daily TG digest (no real-time mirror - one post per day)
    try:
        dg = load_digest()
        items = dg.get("items", [])
        items.append({
            "headline": headline[:250],
            "urgent": bool(urgent),
            "coins": [c.upper() for c in coins][:6],
            "link": link or "",
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        dg["items"] = items[-100:]
        save_digest(dg)
    except Exception as e:
        print(f"[tg] digest buffer error: {e}")

async def news_ws_loop():
    await bot.wait_until_ready()
    backoff = 5
    while not bot.is_closed():
        if not (NEWS_ENABLED and NEWS_CHANNEL_ID):
            await asyncio.sleep(60)
            continue
        try:
            async with http() as session:
                async with session.ws_connect(NEWS_WS_URL, heartbeat=30, timeout=30) as ws:
                    print("[news] connected to TreeNews")
                    backoff = 5
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            _news_last_msg["time"] = datetime.now(timezone.utc)
                            try:
                                item = json.loads(msg.data)
                            except Exception:
                                continue
                            if isinstance(item, dict):
                                await _post_news(item)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
        except Exception as e:
            print(f"[news] ws error: {e}")
        print(f"[news] disconnected - retrying in {backoff}s")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)

# (moved to /admin - registered in sigma.commands_admin)
async def news_status(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    if not NEWS_CHANNEL_ID:
        await interaction.response.send_message("News wire disabled - set NEWS_CHANNEL_ID in the code.", ephemeral=True)
        return
    running = bool(getattr(bot, "_news_task", None)) and not bot._news_task.done()
    last = _news_last_msg["time"]
    last_txt = f"<t:{int(last.timestamp())}:R>" if last else "never (no messages yet this session)"
    await interaction.response.send_message(
        f"**News wire:** {'\U0001F7E2 running' if running else '\U0001F534 not running'}\n"
        f"**Last message received:** {last_txt}\n"
        f"**Posted this session:** {_news_last_msg['posted']}",
        ephemeral=True,
    )

"""sigma.telegram - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import DIGEST_SPONSOR_WORDS, DISCORD_INVITE, IST, NAVY, NEWS_CHANNEL_ID, TELEGRAM_BOT_TOKEN, TG_BRIEF_UTC_HOUR, TG_BRIEF_UTC_MIN, TG_CHANNEL, TG_DIGEST_MAX, TG_DIGEST_UTC_HOUR, TG_DIGEST_UTC_MIN, TG_ENABLED, TG_MACRO_CORE, TG_MOVE_COOLDOWN_MIN, TG_MOVE_SYMBOLS, TG_MOVE_THRESHOLD, TG_NEWS_CHANNELS, TG_NEWS_POLL_MIN
from sigma.core import bot
from sigma.http import http
from sigma.storage import load_digest, load_sources, save_digest, save_sources
from sigma.market_data import md_klines
from sigma.charts_theme import make_chart_image
from sigma.news import _news_urgent


async def tg_send(text: str, disable_preview: bool = True) -> bool:
    if not (TG_ENABLED and TELEGRAM_BOT_TOKEN and TG_CHANNEL):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
        "reply_markup": json.dumps({"inline_keyboard": [[{"text": "Join Sigma Trading \u2192", "url": DISCORD_INVITE}]]}),
    }
    try:
        async with http() as session:
            async with session.post(url, data=payload, timeout=20) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[tg] send failed {resp.status}: {body[:200]}")
                    return False
                return True
    except Exception as e:
        print(f"[tg] send error: {e}")
        return False

async def tg_send_photo(photo: io.BytesIO, caption: str) -> bool:
    if not (TG_ENABLED and TELEGRAM_BOT_TOKEN and TG_CHANNEL):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    form = aiohttp.FormData()
    form.add_field("chat_id", TG_CHANNEL)
    form.add_field("caption", caption)
    form.add_field("parse_mode", "HTML")
    form.add_field("reply_markup", json.dumps({"inline_keyboard": [[{"text": "Join Sigma Trading \u2192", "url": DISCORD_INVITE}]]}))
    form.add_field("photo", photo, filename="chart.png", content_type="image/png")
    try:
        async with http() as session:
            async with session.post(url, data=form, timeout=30) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[tg] photo failed {resp.status}: {body[:200]}")
                    return False
                return True
    except Exception as e:
        print(f"[tg] photo error: {e}")
        return False

_tg_move_state = {}

@tasks.loop(minutes=10)
async def tg_move_loop():
    if not (TG_ENABLED and TELEGRAM_BOT_TOKEN):
        return
    now = datetime.now(timezone.utc)
    for sym in TG_MOVE_SYMBOLS:
        pair = f"{sym}USDT"
        klines = await md_klines(pair, "1h", 2)
        if not klines:
            continue
        if len(klines) < 2:
            continue
        # rolling 1h: current price vs price ~60 min ago (open of previous 1h candle's close side)
        prev_close = float(klines[-2][4])
        c = float(klines[-1][4])
        if prev_close <= 0:
            continue
        chg = (c - prev_close) / prev_close * 100
        if abs(chg) < TG_MOVE_THRESHOLD:
            continue
        state = _tg_move_state.get(sym, {})
        last_alert = state.get("last_alert")
        if last_alert and (now - last_alert).total_seconds() < TG_MOVE_COOLDOWN_MIN * 60:
            continue
        chart_klines = await md_klines(pair, "1h", 220)
        if not chart_klines:
            continue
        try:
            buf = await asyncio.to_thread(make_chart_image, f"{sym}/USDT", "1H", chart_klines)
        except Exception as e:
            print(f"[tg] move chart error: {e}")
            continue
        up = chg > 0
        emoji = "\U0001F680" if up else "\U0001F4C9"
        word = "up" if up else "down"
        ptxt = f"{c:,.0f}" if c >= 1000 else f"{c:,.2f}"
        caption = (
            f"{emoji} <b>{sym} {word} {chg:+.2f}% in the last hour</b>\n"
            f"Now trading at ${ptxt}"
        )
        ok = await tg_send_photo(buf, caption)
        if ok:
            _tg_move_state[sym] = {"last_alert": now}
            print(f"[tg] move alert sent: {sym} {chg:+.2f}%")

@tg_move_loop.before_loop
async def before_tg_move():
    await bot.wait_until_ready()

def _tg_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def build_daily_brief() -> str:
    prices = {}
    fear_txt = dom_txt = fund_txt = movers_txt = ""
    try:
        async with http() as session:
            for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
                try:
                    async with session.get("https://api.binance.com/api/v3/ticker/24hr", params={"symbol": sym}, timeout=15) as r:
                        d = await r.json()
                        prices[sym[:-4]] = (float(d["lastPrice"]), float(d["priceChangePercent"]))
                except Exception:
                    pass
            try:
                async with session.get("https://api.alternative.me/fng/?limit=1", timeout=15) as r:
                    d = await r.json()
                    fg = d["data"][0]
                    fear_txt = f'{fg["value"]}/100 ({fg["value_classification"]})'
            except Exception:
                pass
            try:
                async with session.get("https://api.coingecko.com/api/v3/global", timeout=15) as r:
                    d = (await r.json())["data"]
                    dom_txt = f'{d["market_cap_percentage"]["btc"]:.1f}%'
            except Exception:
                pass
            try:
                async with session.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": "BTCUSDT"}, timeout=15) as r:
                    d = await r.json()
                    rate = float(d["lastFundingRate"]) * 100
                    lean = "longs paying (crowded long)" if rate > 0 else "shorts paying (crowded short)" if rate < 0 else "neutral"
                    fund_txt = f"{rate:+.4f}% - {lean}"
            except Exception:
                pass
            try:
                async with session.get("https://api.binance.com/api/v3/ticker/24hr", timeout=20) as r:
                    data = await r.json()
                rows = []
                for d in data:
                    s = d.get("symbol", "")
                    if not s.endswith("USDT") or any(x in s for x in ("UP", "DOWN", "BULL", "BEAR", "USDC", "FDUSD", "TUSD", "DAI", "EUR")):
                        continue
                    try:
                        if float(d["quoteVolume"]) < 10_000_000:
                            continue
                        rows.append((s[:-4], float(d["priceChangePercent"])))
                    except Exception:
                        continue
                if rows:
                    top = max(rows, key=lambda r: r[1])
                    bot_ = min(rows, key=lambda r: r[1])
                    movers_txt = f"{top[0]} {top[1]:+.1f}% / {bot_[0]} {bot_[1]:+.1f}%"
            except Exception:
                pass
    except Exception as e:
        print(f"[tg] brief data error: {e}")
    def pline(sym, emoji):
        if sym not in prices:
            return None
        p, c = prices[sym]
        arrow = "\U0001F7E2" if c >= 0 else "\U0001F534"
        ptxt = f"{p:,.0f}" if p >= 1000 else f"{p:,.2f}"
        return f"{emoji} <b>{sym}</b> ${ptxt} {arrow} {c:+.2f}%"
    today = datetime.now(IST).strftime("%-d %b %Y")
    lines = [f"\U0001F4CA <b>Daily Market Brief</b> \u2014 {today}", ""]
    for sym, emoji in (("BTC", "\u20BF"), ("ETH", "\u27E0"), ("SOL", "\u25CE")):
        pl = pline(sym, emoji)
        if pl:
            lines.append(pl)
    lines.append("")
    if fear_txt:
        lines.append(f"\U0001F628 <b>Fear &amp; Greed:</b> {fear_txt}")
    if dom_txt:
        lines.append(f"\U0001F451 <b>BTC Dominance:</b> {dom_txt}")
    if fund_txt:
        lines.append(f"\U0001F4B8 <b>BTC Funding:</b> {fund_txt}")
    if movers_txt:
        lines.append(f"\U0001F3C6 <b>Top mover / loser:</b> {movers_txt}")
    return "\n".join(lines)

@tasks.loop(time=dt_time(hour=TG_BRIEF_UTC_HOUR, minute=TG_BRIEF_UTC_MIN, tzinfo=timezone.utc))
async def tg_brief_loop():
    if not (TG_ENABLED and TELEGRAM_BOT_TOKEN):
        return
    text = await build_daily_brief()
    ok = await tg_send(text)
    print(f"[tg] daily brief {'sent' if ok else 'FAILED'}")

@tg_brief_loop.before_loop
async def before_tg_brief():
    await bot.wait_until_ready()

_TAG_RE = re.compile(r"<[^>]+>")

def _parse_tg_preview(html_text: str):
    import html as _html
    out = []
    for m in re.finditer(r'data-post="([^"]+)"', html_text):
        post_id = m.group(1)
        seg = html_text[m.end():m.end() + 20000]
        tm = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', seg, re.S)
        if not tm:
            continue
        raw = tm.group(1)
        raw = re.sub(r"<br\s*/?>", "\n", raw)
        txt = _html.unescape(_TAG_RE.sub("", raw)).strip()
        if txt:
            out.append((post_id, txt))
    return out

@tasks.loop(minutes=TG_NEWS_POLL_MIN)
async def tg_sources_loop():
    if not TG_NEWS_CHANNELS:
        return
    state = load_sources()
    dg = load_digest()
    items = dg.get("items", [])
    added = 0
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    for ch in TG_NEWS_CHANNELS:
        try:
            async with http() as session:
                async with session.get(f"https://t.me/s/{ch}", headers=headers, timeout=20) as resp:
                    if resp.status != 200:
                        print(f"[digest] {ch}: HTTP {resp.status}")
                        continue
                    html_text = await resp.text()
        except Exception as e:
            print(f"[digest] {ch}: fetch error {e}")
            continue
        posts = _parse_tg_preview(html_text)
        if not posts:
            print(f"[digest] {ch}: no posts parsed")
            continue
        seen = set(state.get(ch, []))
        first_run = ch not in state
        new_ids = []
        for post_id, txt in posts:
            new_ids.append(post_id)
            if first_run or post_id in seen:
                continue
            low = txt.lower()
            if len(txt) < 25:
                continue
            if any(w in low for w in DIGEST_SPONSOR_WORDS):
                continue
            headline = txt.split("\n")[0][:250]
            if len(headline) < 20 and len(txt) > len(headline):
                headline = txt.replace("\n", " ")[:250]
            items.append({
                "headline": headline,
                "urgent": _news_urgent(txt),
                "coins": [],
                "link": f"https://t.me/{post_id}",
                "source": ch,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            added += 1
        state[ch] = list(dict.fromkeys(list(seen) + new_ids))[-200:]
    dg["items"] = items[-100:]
    save_digest(dg)
    save_sources(state)
    if added:
        print(f"[digest] buffered {added} new item(s)")

@tg_sources_loop.before_loop
async def before_tg_sources():
    await bot.wait_until_ready()

def build_digest_text() -> str:
    dg = load_digest()
    items = dg.get("items", [])
    if not items:
        return ""
    majors = {"BTC", "ETH", "SOL", "BITCOIN", "ETHEREUM", "SOLANA"}
    def rank(it):
        return (0 if it.get("urgent") else 1, 0 if set(it.get("coins", [])) & majors else 1)
    items = sorted(items, key=rank)[:TG_DIGEST_MAX]
    today = datetime.now(IST).strftime("%-d %b %Y")
    lines = [f"\U0001F4F0 <b>Daily News Digest</b> \u2014 {today}", ""]
    for i, it in enumerate(items, 1):
        h = _tg_escape(it["headline"])
        prefix = "\U0001F6A8 " if it.get("urgent") else ""
        src = it.get("source")
        link = it.get("link")
        # headline stays plain; link is embedded on the source name (or the word "link")
        if link and src:
            tail = f' <a href="{link}"><i>{_tg_escape(src)}</i></a>'
        elif link:
            tail = f' <a href="{link}">link</a>'
        elif src:
            tail = f' <i>- {_tg_escape(src)}</i>'
        else:
            tail = ""
        lines.append(f"{i}. {prefix}<b>{h}</b>{tail}")
    return "\n".join(lines)

async def post_digest_discord():
    ch = bot.get_channel(NEWS_CHANNEL_ID)
    if ch is None:
        return False
    dg = load_digest()
    items = dg.get("items", [])
    if not items:
        return False
    majors_rank = lambda it: (0 if it.get("urgent") else 1,)
    items = sorted(items, key=majors_rank)[:TG_DIGEST_MAX]
    today = datetime.now(IST).strftime("%d %b %Y")
    lines = []
    for i, it in enumerate(items, 1):
        prefix = "\U0001F6A8 " if it.get("urgent") else ""
        h = it["headline"][:200]
        src = it.get("source")
        link = it.get("link")
        # headline plain (bold); link embedded on source name or the word "link"
        if link and src:
            tail = f" [*{src}*]({link})"
        elif link:
            tail = f" [link]({link})"
        elif src:
            tail = f" - *{src}*"
        else:
            tail = ""
        lines.append(f"{i}. {prefix}**{h}**{tail}")
    embed = discord.Embed(title=f"\U0001F4F0 Daily News Digest - {today}", description="\n".join(lines)[:3900], color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text="News Wire - curated daily digest")
    try:
        await ch.send(embed=embed)
        return True
    except Exception as e:
        print(f"[digest] discord post error: {e}")
        return False

@tasks.loop(time=dt_time(hour=TG_DIGEST_UTC_HOUR, minute=TG_DIGEST_UTC_MIN, tzinfo=timezone.utc))
async def tg_digest_loop():
    text = build_digest_text()
    if not text:
        print("[tg] digest skipped - no items today")
        return
    d_ok = await post_digest_discord()
    t_ok = False
    if TG_ENABLED and TELEGRAM_BOT_TOKEN:
        t_ok = await tg_send(text)
    if d_ok or t_ok:
        save_digest({"items": []})
    print(f"[tg] digest discord={'ok' if d_ok else 'fail'} tg={'ok' if t_ok else 'fail'}")

@tg_digest_loop.before_loop
async def before_tg_digest():
    await bot.wait_until_ready()

def _tg_news_worthy(text: str, coins: list, urgent: bool) -> bool:
    if urgent:
        return True
    up = {c.upper() for c in coins if c}
    if up & {"BTC", "ETH", "SOL", "BITCOIN", "ETHEREUM", "SOLANA"}:
        return True
    low = text.lower()
    return any(k in low for k in TG_MACRO_CORE)

# (moved to /admin - registered in sigma.commands_admin)
async def tg_digest_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    text = build_digest_text()
    if not text:
        await interaction.followup.send("No news buffered yet today.", ephemeral=True)
        return
    d_ok = await post_digest_discord()
    t_ok = await tg_send(text) if TELEGRAM_BOT_TOKEN else False
    if d_ok or t_ok:
        save_digest({"items": []})
    await interaction.followup.send(f"Digest: Discord {'\u2705' if d_ok else '\u274C'} | Telegram {'\u2705' if t_ok else '\u274C'}", ephemeral=True)

# (moved to /admin - registered in sigma.commands_admin)
async def tg_brief_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not TELEGRAM_BOT_TOKEN:
        await interaction.followup.send("TELEGRAM_BOT_TOKEN not set in .env on the VPS.", ephemeral=True)
        return
    text = await build_daily_brief()
    ok = await tg_send(text)
    await interaction.followup.send("Brief sent to @scientclub." if ok else "Send failed - check [tg] errors in journalctl.", ephemeral=True)

# (moved to /admin - registered in sigma.commands_admin)
@app_commands.rename(what="action")
@app_commands.describe(what="Which update to send now", text="Custom message (only for Custom)")
@app_commands.choices(what=[
    app_commands.Choice(name="Daily Market Brief", value="brief"),
    app_commands.Choice(name="News Digest", value="digest"),
    app_commands.Choice(name="Custom message", value="custom"),
])
async def tg_send_cmd(interaction: discord.Interaction, what: app_commands.Choice[str], text: str = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if what.value == "brief":
            await tg_brief_loop.coro()
            await interaction.followup.send("\u2705 Daily brief sent to Telegram.", ephemeral=True)
        elif what.value == "digest":
            await tg_digest_loop.coro()
            await interaction.followup.send("\u2705 News digest sent to Telegram.", ephemeral=True)
        else:
            if not text:
                await interaction.followup.send("Custom needs `text`.", ephemeral=True)
                return
            ok = await tg_send(text)
            await interaction.followup.send("\u2705 Sent." if ok else "\u274C Telegram send failed - check logs.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274C Failed: {e}", ephemeral=True)

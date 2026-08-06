# scient_trades_bot.py
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
from discord.ext import tasks
import os, json, re, hashlib, aiohttp, io, asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dt_time

_env_file = Path(__file__).with_name(".env")
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

BOT_TOKEN = os.getenv("SCIENT_BOT_TOKEN", "PASTE_TOKEN_HERE")
TWITTERAPIS_KEY = os.getenv("TWITTERAPIS_KEY", "")
GUILD_ID = 1213101801675554846
TRADES_CHANNEL_ID = 1525147189360332840
TRADE_UPDATES_CHANNEL_ID = 1525863205174378617
OPEN_BOARD_CHANNEL_ID = 1525863082256109690
X_FEED_CHANNEL_ID = 1525862152076923020
SPOT_CHANNEL_ID = 1533876035462889482
QUANT_CHANNEL_ID = 0  # paste #quant-terminal channel ID here (0 = commands work everywhere)

# ---- News wire config (TreeNews) ----
NEWS_CHANNEL_ID = 1535048677406539797
NEWS_PING_ROLE_ID = 1535053641378037760  # pinged on URGENT news only
NEWS_ENABLED = True
NEWS_WS_URL = "wss://news.treeofalpha.com/ws"
NEWS_COINS = {"BTC", "ETH", "SOL", "BITCOIN", "ETHEREUM", "SOLANA"}
NEWS_KEYWORDS = {
    "sec", "etf", "fed", "fomc", "rate cut", "rate hike", "cpi", "inflation",
    "hack", "hacked", "exploit", "exploited", "breach", "stolen",
    "listing", "lists", "delist", "delisting", "bankrupt", "bankruptcy",
    "liquidat", "halt", "halted", "approval", "approved", "lawsuit", "sues", "settle",
    "binance", "coinbase", "tether", "usdt", "blackrock", "grayscale", "microstrategy", "strategy",
}

# ---- Telegram (Scient Club) config ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHANNEL = "@scientclub"
TG_ENABLED = True
DISCORD_INVITE = "https://discord.gg/scientlounge"
TG_BRIEF_UTC_HOUR = 6   # 12:00 PM IST = 06:30 UTC
TG_BRIEF_UTC_MIN = 30
TG_MACRO_CORE = ("sec", "etf", "fed", "fomc", "cpi", "rate cut", "rate hike")
EMA_PERIODS = [20, 50, 100, 200]  # change to match Scient 4EMA periods
EMA_COLORS = ["#E8590C", "#FAC775", "#378ADD", "#1C4E80"]
ANALYST_ROLE_NAME = "Analyst"
PING_ROLE_ID = 1525861312729452704
X_PING_ROLE_ID = 1525861448088031462

EDIT_WINDOW_MIN = 60  # minutes after posting during which /edit is allowed

# ---- X auto-feed config ----
X_AUTO_USERNAME = "Crypto_Scient"
X_POLL_MINUTES = 30
X_AUTO_ENABLED = True

ANALYSTS = {
    "scient":  {"color": "#1C4E80", "ping_role_id": 1481692018211291186, "user_ids": [249880856993202187]},
    "owais":   {"color": "#7C3AED", "ping_role_id": 1498738610118066286, "user_ids": [1120017600026513468]},
    "94":      {"color": "#2E7D32", "ping_role_id": 1493498310558748742, "user_ids": [1268246432197120090]},
}

JOURNAL_FILE = Path(__file__).with_name("trades.json")
BOARD_FILE = Path(__file__).with_name("board.json")
SPOT_FILE = Path(__file__).with_name("spot_plays.json")
SPOT_BOARD_FILE = Path(__file__).with_name("spot_board.json")
XSEEN_FILE = Path(__file__).with_name("x_posted.json")
IST = timezone(timedelta(hours=5, minutes=30))
NAVY = discord.Color.from_str("#1C4E80")
GREEN = discord.Color.from_str("#2E7D32")
RED = discord.Color.from_str("#C62828")
BLUE = discord.Color.from_str("#378ADD")
GOLD = discord.Color.from_str("#C9A227")
GREY = discord.Color.light_grey()
DGREY = discord.Color.dark_grey()
FRAMEWORKS = [
    "FRVP / POC", "AMD", "Wyckoff Accumulation", "RSI Divergence",
    "BOS / MSS", "Fib Pocket (0.75/0.786)", "Range (sweep-reclaim)",
    "Three Drives", "Deviation Reclaim", "EMA Cross", "Other",
]
SPOT_STATUSES = ["ACCUMULATING", "HOLDING", "DISTRIBUTING"]
ANALYST_CHOICES = [app_commands.Choice(name=k.capitalize(), value=k) for k in ANALYSTS.keys()]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _save(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_trades() -> dict: return _load(JOURNAL_FILE)
def save_trades(d: dict): _save(JOURNAL_FILE, d)
def load_board() -> dict: return _load(BOARD_FILE)
def save_board(d: dict): _save(BOARD_FILE, d)
def load_spot() -> dict: return _load(SPOT_FILE)
def save_spot(d: dict): _save(SPOT_FILE, d)
def load_spot_board() -> dict: return _load(SPOT_BOARD_FILE)
def save_spot_board(d: dict): _save(SPOT_BOARD_FILE, d)
def load_xseen() -> dict: return _load(XSEEN_FILE)
def save_xseen(d: dict): _save(XSEEN_FILE, d)


def is_analyst(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    return any(r.name == ANALYST_ROLE_NAME for r in interaction.user.roles)


def parse_num(val):
    if val is None:
        return None
    cleaned = "".join(c for c in str(val).replace(",", "") if c in "0123456789.")
    try:
        return float(cleaned)
    except ValueError:
        return None


def first_num(s):
    if s is None:
        return None
    s = re.sub(r"\([^)]*\)", "", str(s))
    nums = re.findall(r"\d+(?:\.\d+)?", s.replace(",", ""))
    return float(nums[0]) if nums else None


def entry_num(t):
    e1 = first_num(t.get("entry"))
    e2 = first_num(t.get("entry2")) if t.get("entry2") else None
    if e2 is None:
        s = re.sub(r"\([^)]*\)", "", str(t.get("entry", "")))
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s.replace(",", ""))]
        if len(nums) >= 2:
            return (nums[0] + nums[1]) / 2
        return e1
    f1, f2 = t.get("entry1_filled"), t.get("entry2_filled")
    if f1 and f2:
        return (e1 + e2) / 2
    if f1:
        return e1
    if f2:
        return e2
    return (e1 + e2) / 2


def sl_num(t):
    return first_num(t.get("sl"))


def risk_per_unit(t):
    e, s = entry_num(t), sl_num(t)
    if e is None or s is None or e == s:
        return None
    return abs(e - s)


def signed_r(t, price):
    e = entry_num(t)
    rpu = risk_per_unit(t)
    if e is None or rpu is None or price is None:
        return None
    diff = (price - e) if t.get("direction") == "LONG" else (e - price)
    return diff / rpu


def fills_pct(t) -> float:
    return sum(f.get("pct", 0) for f in t.get("fills", []))


def finalize_close(t, final_price=None):
    fills = list(t.get("fills", []))
    rem = 100 - sum(f.get("pct", 0) for f in fills)
    if final_price is not None and rem > 0.01:
        fills.append({"price": final_price, "pct": rem, "label": "close"})
    total = sum(f.get("pct", 0) for f in fills)
    if total <= 0:
        return None, None
    avg_exit = sum(f["price"] * f["pct"] for f in fills) / total
    return avg_exit, signed_r(t, avg_exit)


def fnum(x):
    if x is None:
        return "-"
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    return f"{x:.8f}".rstrip("0")


def tf(t: dict) -> str:
    v = t.get("timeframe")
    return v.upper() if v else ""


def fmt_risk(val):
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if "%" in s:
        return s
    try:
        float(s)
        return f"{s}%"
    except ValueError:
        return s


def fmt_frameworks(t: dict) -> str:
    fws = t.get("frameworks")
    if fws:
        return " + ".join(fws)
    return t.get("framework") or "-"


def fix_x_link(url: str) -> str:
    url = url.strip()
    return re.sub(r"https?://(www\.)?(twitter|x)\.com", "https://fxtwitter.com", url, flags=re.I)


def any_entry_filled(t: dict) -> bool:
    return bool(t.get("entry1_filled") or t.get("entry2_filled"))


def entry_display(t: dict, marks: bool = True) -> str:
    e1 = str(t.get("entry"))
    e2 = t.get("entry2")
    closed = t.get("closed")
    if not e2:
        s = e1
        if marks and not closed:
            s += " (filled)" if t.get("entry1_filled") else " (pending)"
        return s
    if marks and not closed:
        m1 = " \u2713" if t.get("entry1_filled") else ""
        m2 = " \u2713" if t.get("entry2_filled") else ""
        return f"{e1}{m1} / {e2}{m2} (DCA)"
    return f"{e1} / {e2} (DCA)"


def display_rr(t: dict):
    for key in ("tp3", "tp2", "tp1"):
        if t.get(key):
            r = signed_r(t, first_num(t[key]))
            if r is not None:
                return f"{r:.1f}"
    return t.get("rr")


def full_status(t: dict) -> str:
    if t.get("closed"):
        r = t.get("result_r")
        rtxt = f" ({r:+.2f}R)" if isinstance(r, (int, float)) else ""
        return {
            "WIN": f"CLOSED - WIN{rtxt}",
            "LOSS": f"CLOSED - LOSS{rtxt}",
            "BE": f"CLOSED - BREAKEVEN{rtxt}",
            "INVALID": "INVALIDATED",
        }.get(t.get("result"), "CLOSED")
    closed_pct = fills_pct(t)
    pct_txt = f" - {closed_pct:g}% closed" if closed_pct > 0 else ""
    if t.get("tp3_hit"):
        return f"TP3 HIT{pct_txt}"
    if t.get("tp2_hit"):
        return f"TP2 HIT{pct_txt}"
    if t.get("tp1_hit"):
        return f"TP1 HIT{pct_txt}"
    if t.get("be"):
        return "SL AT ENTRY - RISK-FREE" + pct_txt
    if t.get("entry2") and t.get("entry1_filled") and not t.get("entry2_filled"):
        return "ACTIVE - Entry 1 filled, DCA pending"
    if any_entry_filled(t):
        return "ACTIVE" + pct_txt
    return "PENDING - waiting for fill"


def short_status(t: dict) -> str:
    if t.get("tp3_hit"):
        return "TP3"
    if t.get("tp2_hit"):
        return "TP2"
    if t.get("tp1_hit"):
        return "TP1"
    if t.get("be"):
        return "BE"
    if any_entry_filled(t):
        return "Active"
    return "Pending"


def spot_status_line(p: dict) -> str:
    if p.get("closed"):
        res = p.get("result_pct")
        rtxt = f" ({res})" if res else ""
        return {"WIN": f"CLOSED - WIN{rtxt}", "LOSS": f"CLOSED - LOSS{rtxt}", "BE": "CLOSED - BREAKEVEN", "INVALID": "INVALIDATED"}.get(p.get("result"), "CLOSED")
    s = p.get("status", "ACCUMULATING")
    hits = sum(1 for k in ("t1_hit", "t2_hit", "t3_hit") if p.get(k))
    if hits:
        return f"{s} - Target {hits} hit"
    return s


def resolve_analyst(user):
    uid = user.id
    dname = (getattr(user, "display_name", "") or "").lower()
    for key, cfg in ANALYSTS.items():
        if uid in cfg.get("user_ids", []):
            return key, cfg
    for key, cfg in ANALYSTS.items():
        if key in dname:
            return key, cfg
    return (dname or str(uid)), None


def analyst_color_hex(user) -> str:
    key, cfg = resolve_analyst(user)
    if cfg and cfg.get("color"):
        return cfg["color"]
    h = int(hashlib.md5(str(user.id).encode()).hexdigest()[:6], 16)
    return f"#{h:06X}"


def jump_url(t: dict) -> str:
    return f"https://discord.com/channels/{GUILD_ID}/{t['channel_id']}/{t['message_id']}"


def within_edit_window(t: dict) -> bool:
    try:
        created = datetime.fromisoformat(t["created_at"])
    except Exception:
        return False
    return (datetime.now(timezone.utc) - created).total_seconds() <= EDIT_WINDOW_MIN * 60


def footer_with_edit(t: dict, base: str) -> str:
    if t.get("edited_at"):
        try:
            _ed = datetime.fromisoformat(t["edited_at"]).astimezone(IST)
            return base + f" \u00b7 edited {_ed.strftime('%d/%m %I:%M %p')}"
        except Exception:
            return base + " \u00b7 edited"
    if t.get("edited"):
        return base + " \u00b7 edited"
    return base


def build_embed(t: dict, image_url: str = None) -> discord.Embed:
    is_long = t["direction"] == "LONG"
    closed = t.get("closed")
    result = t.get("result")
    try:
        color = discord.Color.from_str(t.get("analyst_color") or "#1C4E80")
    except Exception:
        color = NAVY
    arrow = "\U0001F7E2 LONG" if is_long else "\U0001F534 SHORT"
    prefix = ""
    if closed:
        prefix = {"WIN": "[WIN] ", "LOSS": "[LOSS] ", "BE": "[BE] ", "INVALID": "[INV] "}.get(result, "")
    elif not any_entry_filled(t):
        prefix = "[PENDING] "
    tftxt = tf(t)
    title = f"{prefix}{arrow} | {t['pair'].upper()}"
    if tftxt:
        title += f" | {tftxt}"
    embed = discord.Embed(title=title, color=color)

    sl_mark = " (hit)" if t.get("sl_hit") else ""
    type_label = "Market" if t.get("entry_type") == "MARKET" else "Limit"
    line1 = (
        f"**Entry ({type_label}):** {entry_display(t)}"
        f" | **SL:** {t['sl']}{sl_mark}"
        f" | **Risk:** {fmt_risk(t.get('risk')) or '-'}"
        f" | **R:R:** {display_rr(t) or '-'}"
    )

    tps = []
    for key, hit in (("tp1", "tp1_hit"), ("tp2", "tp2_hit"), ("tp3", "tp3_hit")):
        if t.get(key):
            r = signed_r(t, first_num(t[key]))
            rtxt = f" ({r:.1f}R)" if r is not None else ""
            tps.append(f"{t[key]}{rtxt}" + (" \u2705" if t.get(hit) else ""))
    line2 = f"**TP:** {' / '.join(tps)}" if tps else None

    fw = fmt_frameworks(t)
    if t.get("setup_detail"):
        fw = f"{fw} - {t['setup_detail']}"
    line3 = f"**Setup:** {fw}" if fw != "-" else None

    line4 = f"**Status:** {full_status(t)}"

    lines = [line1]
    if line2:
        lines.append(line2)
    if line3:
        lines.append(line3)
    lines.append(line4)
    if closed and t.get("avg_exit") is not None:
        lines.append(f"**Avg Exit:** {fnum(t['avg_exit'])}")
    if closed and t.get("close_note"):
        lines.append(f"**Note:** {t['close_note'][:300]}")
    embed.description = "\n".join(lines)

    embed.set_author(name=t["analyst_name"], icon_url=t.get("analyst_avatar") or None)
    embed.set_footer(text=footer_with_edit(t, "Scient Lounge - Trade Setups"))
    if image_url:
        embed.set_image(url=image_url)
    return embed


def build_spot_embed(p: dict, image_url: str = None) -> discord.Embed:
    closed = p.get("closed")
    result = p.get("result")
    try:
        color = discord.Color.from_str(p.get("analyst_color") or "#1C4E80")
    except Exception:
        color = NAVY
    prefix = ""
    if closed:
        prefix = {"WIN": "[WIN] ", "LOSS": "[LOSS] ", "BE": "[BE] ", "INVALID": "[INV] "}.get(result, "")
    title = f"{prefix}\U0001F7E2 SPOT | {p['pair'].upper()}"
    embed = discord.Embed(title=title, color=color)

    lines = []
    if closed:
        l1 = f"**Avg Entry:** {p.get('avg_entry') or '-'}"
        if p.get("avg_exit"):
            l1 += f" | **Avg Exit:** {p['avg_exit']}"
        lines.append(l1)
        if p.get("result_pct"):
            lines.append(f"**Result:** {p['result_pct']}")
    else:
        zone_mark = " (filled)" if p.get("zone_filled") else ""
        l1 = f"**DCA Zone:** {p['dca_zone']}{zone_mark}"
        if p.get("allocation"):
            l1 += f" | **Allocation:** {fmt_risk(p['allocation'])}"
        lines.append(l1)
        if p.get("avg_entry"):
            lines.append(f"**Avg Entry:** {p['avg_entry']}")
        tgs = []
        for key, hit in (("t1", "t1_hit"), ("t2", "t2_hit"), ("t3", "t3_hit")):
            if p.get(key):
                tgs.append(f"{p[key]}" + (" \u2705" if p.get(hit) else ""))
        if tgs:
            lines.append(f"**Targets:** {' / '.join(tgs)}")
        if p.get("invalidation"):
            lines.append(f"**Invalidation:** {p['invalidation']}")
    lines.append(f"**Status:** {spot_status_line(p)}")
    if closed and p.get("close_note"):
        lines.append(f"**Note:** {p['close_note'][:300]}")
    embed.description = "\n".join(lines)

    embed.set_author(name=p["analyst_name"], icon_url=p.get("analyst_avatar") or None)
    embed.set_footer(text=footer_with_edit(p, "Scient Lounge - Spot Plays"))
    if image_url:
        embed.set_image(url=image_url)
    return embed


def build_board_embed() -> discord.Embed:
    data = load_trades()
    open_trades = [t for t in data.values() if not t.get("closed")]
    embed = discord.Embed(title="Open Positions - Live Board", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=f"Scient Lounge - {len(open_trades)} open - auto-updates")
    if not open_trades:
        embed.description = "*No open positions right now.*"
        return embed
    order = list(ANALYSTS.keys())
    def sort_key(t):
        k = t.get("analyst_key", "")
        return (order.index(k) if k in order else len(order), t.get("created_at", ""))
    open_trades.sort(key=sort_key)
    groups = {}
    for t in open_trades:
        groups.setdefault(t.get("analyst_key", "other"), []).append(t)
    ordered_keys = [k for k in order if k in groups] + [k for k in groups if k not in order]
    for k in ordered_keys:
        trades = groups[k]
        name = trades[0].get("analyst_name", k.capitalize())
        lines = []
        for t in trades:
            d = "\U0001F7E2 L" if t["direction"] == "LONG" else "\U0001F534 S"
            e = entry_display(t, marks=False)
            lines.append(f"{d} **{t['pair'].upper()}**" + (f" - {tf(t)}" if tf(t) else "") + f" - entry `{e}` - {short_status(t)} - [view]({jump_url(t)})")
        embed.add_field(name=f"{name} ({len(trades)})", value="\n".join(lines)[:1024], inline=False)
    return embed


def build_spot_board_embed() -> discord.Embed:
    data = load_spot()
    open_plays = [p for p in data.values() if not p.get("closed")]
    embed = discord.Embed(title="Spot Portfolio - Live Board", color=GOLD, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=f"Scient Lounge - {len(open_plays)} active plays - auto-updates")
    if not open_plays:
        embed.description = "*No active spot plays right now.*"
        return embed
    order = list(ANALYSTS.keys())
    def sort_key(p):
        k = p.get("analyst_key", "")
        return (order.index(k) if k in order else len(order), p.get("created_at", ""))
    open_plays.sort(key=sort_key)
    groups = {}
    for p in open_plays:
        groups.setdefault(p.get("analyst_key", "other"), []).append(p)
    ordered_keys = [k for k in order if k in groups] + [k for k in groups if k not in order]
    for k in ordered_keys:
        plays = groups[k]
        name = plays[0].get("analyst_name", k.capitalize())
        lines = []
        for p in plays:
            avg = f" - avg `{p['avg_entry']}`" if p.get("avg_entry") else ""
            lines.append(f"\U0001FA99 **{p['pair'].upper()}** - zone `{p['dca_zone']}`{avg} - {spot_status_line(p)} - [view]({jump_url(p)})")
        embed.add_field(name=f"{name} ({len(plays)})", value="\n".join(lines)[:1024], inline=False)
    return embed


async def refresh_board():
    if not OPEN_BOARD_CHANNEL_ID:
        return
    ch = bot.get_channel(OPEN_BOARD_CHANNEL_ID)
    if ch is None:
        return
    embed = build_board_embed()
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
    if not OPEN_BOARD_CHANNEL_ID:
        return
    ch = bot.get_channel(OPEN_BOARD_CHANNEL_ID)
    if ch is None:
        return
    embed = build_spot_board_embed()
    board = load_spot_board()
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
    save_spot_board({"message_id": msg.id, "channel_id": ch.id})


async def tg_send(text: str, disable_preview: bool = True) -> bool:
    if not (TG_ENABLED and TELEGRAM_BOT_TOKEN and TG_CHANNEL):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
        "reply_markup": json.dumps({"inline_keyboard": [[{"text": "Join Scient Lounge \u2192", "url": DISCORD_INVITE}]]}),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=20) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[tg] send failed {resp.status}: {body[:200]}")
                    return False
                return True
    except Exception as e:
        print(f"[tg] send error: {e}")
        return False


def _tg_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def build_daily_brief() -> str:
    prices = {}
    fear_txt = dom_txt = fund_txt = movers_txt = ""
    try:
        async with aiohttp.ClientSession() as session:
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
    lines = ["\U0001F4CA <b>Daily Market Brief</b>", ""]
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
    lines.append("")
    lines.append("<i>Setups, not signals - full analysis inside Scient Lounge</i>")
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


def _tg_news_worthy(text: str, coins: list, urgent: bool) -> bool:
    if urgent:
        return True
    up = {c.upper() for c in coins if c}
    if up & {"BTC", "ETH", "SOL", "BITCOIN", "ETHEREUM", "SOLANA"}:
        return True
    low = text.lower()
    return any(k in low for k in TG_MACRO_CORE)


_news_seen = []
_news_last_msg = {"time": None, "posted": 0}


def _news_relevant(text: str, coins: list) -> bool:
    up = {c.upper() for c in coins if c}
    if up & NEWS_COINS:
        return True
    low = text.lower()
    return any(k in low for k in NEWS_KEYWORDS)


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
    if not text or not _news_relevant(text, coins):
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
    if TG_ENABLED and TELEGRAM_BOT_TOKEN and _tg_news_worthy(text, coins, urgent):
        prefix = "\U0001F6A8" if urgent else "\U0001F4F0"
        tg_text = f"{prefix} <b>{_tg_escape(headline[:250])}</b>"
        if body and body != headline:
            tg_text += f"\n\n{_tg_escape(body[:350])}"
        if link:
            tg_text += f"\n\n<a href=\"{link}\">Source</a>"
        await tg_send(tg_text)


async def news_ws_loop():
    await bot.wait_until_ready()
    backoff = 5
    while not bot.is_closed():
        if not (NEWS_ENABLED and NEWS_CHANNEL_ID):
            await asyncio.sleep(60)
            continue
        try:
            async with aiohttp.ClientSession() as session:
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
        async with aiohttp.ClientSession() as session:
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


async def toggle_role(interaction: discord.Interaction, role_id, label: str):
    if not role_id:
        await interaction.response.send_message(f"Pings for **{label}** aren't set up yet.", ephemeral=True)
        return
    role = interaction.guild.get_role(role_id)
    if role is None:
        await interaction.response.send_message("That role no longer exists - tell an admin.", ephemeral=True)
        return
    try:
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="Follow panel toggle off")
            await interaction.response.send_message(f"Unfollowed **{label}** - you won't be pinged.", ephemeral=True)
        else:
            await interaction.user.add_roles(role, reason="Follow panel toggle on")
            await interaction.response.send_message(f"Following **{label}** - you'll be pinged on new posts.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have permission to manage that role (need Manage Roles, and my role must be above it).", ephemeral=True)


class FollowPanel(View):
    def __init__(self):
        super().__init__(timeout=None)
        for key, cfg in ANALYSTS.items():
            self.add_item(FollowButton(key.capitalize(), cfg.get("ping_role_id"), f"follow_{key}"))
        self.add_item(FollowAllButton())
        self.add_item(FollowXButton())
        self.add_item(FollowNewsButton())


class FollowButton(Button):
    def __init__(self, label, role_id, custom_id):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self.role_id = role_id
        self.label_name = label

    async def callback(self, interaction: discord.Interaction):
        await toggle_role(interaction, self.role_id, self.label_name)


class FollowAllButton(Button):
    def __init__(self):
        super().__init__(label="Follow All", style=discord.ButtonStyle.primary, custom_id="follow_all")

    async def callback(self, interaction: discord.Interaction):
        await toggle_role(interaction, PING_ROLE_ID, "All Trades")


class FollowXButton(Button):
    def __init__(self):
        super().__init__(label="X Updates", style=discord.ButtonStyle.success, custom_id="follow_x")

    async def callback(self, interaction: discord.Interaction):
        await toggle_role(interaction, X_PING_ROLE_ID, "X Updates")


class FollowNewsButton(Button):
    def __init__(self):
        super().__init__(label="\U0001F6A8 Breaking News", style=discord.ButtonStyle.danger, custom_id="follow_news")

    async def callback(self, interaction: discord.Interaction):
        await toggle_role(interaction, NEWS_PING_ROLE_ID, "Breaking News")


@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    bot.add_view(FollowPanel())
    await refresh_board()
    await refresh_spot_board()
    if X_AUTO_ENABLED and not x_poll_loop.is_running():
        x_poll_loop.start()
    if NEWS_ENABLED and NEWS_CHANNEL_ID and not getattr(bot, "_news_task", None):
        bot._news_task = asyncio.create_task(news_ws_loop())
    if TG_ENABLED and TELEGRAM_BOT_TOKEN and not tg_brief_loop.is_running():
        tg_brief_loop.start()
    print(f"Logged in as {bot.user} - commands synced.")


@bot.tree.command(name="tg_test", description="(Admin) Send a test message to the Scient Club Telegram")
async def tg_test(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not TELEGRAM_BOT_TOKEN:
        await interaction.followup.send("TELEGRAM_BOT_TOKEN not set in .env on the VPS.", ephemeral=True)
        return
    ok = await tg_send("\u2705 <b>Quant connected.</b> Scient Club wire is live.")
    await interaction.followup.send("Test sent to Telegram - check @scientclub." if ok else "Send failed - check bot is admin of the channel and token is correct (see journalctl for [tg] errors).", ephemeral=True)


@bot.tree.command(name="tg_brief", description="(Admin) Send the daily market brief to Telegram now")
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


@bot.tree.command(name="news_status", description="(Admin) Check the news wire connection")
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


@bot.tree.command(name="setup_follow_panel", description="(Admin) Post the analyst follow panel in this channel")
async def setup_follow_panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    embed = discord.Embed(
        title="Follow Your Analysts",
        description=(
            "Tap a button to get pinged when that analyst posts a trade. "
            "Tap again to stop.\n\n"
            "**Analysts:** Scient, Owais, 94, Michael, Delta, Gaijin\n"
            "**Follow All** - get pinged on every new trade\n"
            "**X Updates** - get pinged when new X posts are shared\n"
            "**\U0001F6A8 Breaking News** - get pinged on urgent market news only (hacks, halts, delistings)"
        ),
        color=NAVY,
    )
    embed.set_footer(text="Scient Lounge - Alerts")
    await interaction.channel.send(embed=embed, view=FollowPanel())
    await interaction.response.send_message("Follow panel posted.", ephemeral=True)


@bot.tree.command(name="trade", description="Post a trade setup")
@app_commands.describe(
    pair="e.g. BTC/USDT",
    direction="Long or Short",
    entry_type="Market (filled now), Limit single, or Limit DCA (two entries)",
    entry="Entry price (Entry 1 if DCA)",
    stop_loss="SL price",
    risk="Account risk (just a number = %, e.g. 1 shows as 1%)",
    entry2="Second DCA entry price (only for Limit DCA)",
    framework="Setup framework (optional)",
    framework2="Second framework (optional)",
    chart="Chart image (optional)",
    tp1="Take profit 1 (R auto-calculated)",
    timeframe="e.g. 4H (optional)",
    setup_detail="Extra specifics (optional)",
    tp2="TP2 (optional)",
    tp3="TP3 (optional)",
    notes="Reasoning (optional, posted in the trade thread)",
)
@app_commands.choices(
    direction=[app_commands.Choice(name="Long", value="LONG"), app_commands.Choice(name="Short", value="SHORT")],
    framework=[app_commands.Choice(name=f, value=f) for f in FRAMEWORKS],
    framework2=[app_commands.Choice(name=f, value=f) for f in FRAMEWORKS],
    entry_type=[
        app_commands.Choice(name="Market - filled now", value="MARKET"),
        app_commands.Choice(name="Limit - single entry", value="LIMIT"),
        app_commands.Choice(name="Limit - Range/DCA (two entries)", value="DCA"),
    ],
)
async def trade(interaction: discord.Interaction, pair: str, direction: app_commands.Choice[str], entry_type: app_commands.Choice[str], entry: str, stop_loss: str, risk: str, entry2: str = None, framework: app_commands.Choice[str] = None, framework2: app_commands.Choice[str] = None, chart: discord.Attachment = None, tp1: str = None, timeframe: str = None, setup_detail: str = None, tp2: str = None, tp3: str = None, notes: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message(f"Only members with the **{ANALYST_ROLE_NAME}** role can post setups.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    channel = bot.get_channel(TRADES_CHANNEL_ID)
    if channel is None:
        await interaction.followup.send("Trades channel not found - check TRADES_CHANNEL_ID.", ephemeral=True)
        return
    etype = entry_type.value
    if etype == "DCA" and not entry2:
        await interaction.followup.send("Limit - Range/DCA needs **entry2** (the second entry price).", ephemeral=True)
        return
    if etype != "DCA":
        entry2 = None
    is_market = etype == "MARKET"
    akey, acfg = resolve_analyst(interaction.user)
    frameworks = [f.value for f in (framework, framework2) if f]
    t = {
        "analyst_id": interaction.user.id, "analyst_name": interaction.user.display_name,
        "analyst_avatar": interaction.user.display_avatar.url, "analyst_key": akey,
        "analyst_color": analyst_color_hex(interaction.user),
        "pair": pair, "direction": direction.value, "timeframe": timeframe,
        "framework": frameworks[0] if frameworks else None, "frameworks": frameworks, "setup_detail": setup_detail,
        "entry": entry, "entry2": entry2, "sl": stop_loss, "entry_type": "MARKET" if is_market else "LIMIT",
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "risk": risk, "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entry1_filled": bool(is_market), "entry2_filled": False,
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False, "sl_hit": False, "be": False,
        "fills": [], "avg_exit": None,
        "closed": False, "result": None, "result_r": None, "close_note": None,
        "edited": False, "edited_at": None,
    }
    files = []
    embed = build_embed(t)
    if chart:
        f = await chart.to_file()
        files.append(f)
        embed.set_image(url=f"attachment://{chart.filename}")
    mention_ids = []
    if PING_ROLE_ID:
        mention_ids.append(PING_ROLE_ID)
    if acfg and acfg.get("ping_role_id"):
        mention_ids.append(acfg["ping_role_id"])
    content = None
    allowed = discord.AllowedMentions.none()
    if mention_ids:
        content = " ".join(f"<@&{r}>" for r in mention_ids) + " New setup"
        allowed = discord.AllowedMentions(roles=True)
    msg = await channel.send(content=content, embed=embed, files=files, allowed_mentions=allowed)
    t["message_id"] = msg.id
    t["channel_id"] = channel.id
    try:
        thread = await msg.create_thread(name=f"{pair.upper()} {direction.value} - {interaction.user.display_name}")
        t["thread_id"] = thread.id
        if notes:
            try:
                await thread.send(f"**Reasoning:** {notes[:1900]}")
            except Exception:
                pass
    except discord.HTTPException:
        t["thread_id"] = None
    data = load_trades()
    data[str(msg.id)] = t
    save_trades(data)
    await refresh_board()
    await interaction.followup.send(f"Setup posted in {channel.mention} ({msg.jump_url})", ephemeral=True)


@bot.tree.command(name="spot", description="Post a long-term spot DCA play")
@app_commands.describe(
    pair="e.g. SOL, BTC",
    dca_zone="Accumulation zone, e.g. 65 - 52",
    target1="First target",
    allocation="Portfolio allocation (just a number = %)",
    avg_entry="Current average entry (optional, can update later)",
    target2="Second target (optional)",
    target3="Third target (optional)",
    invalidation="Thesis invalidation, e.g. Weekly close below 48 (optional)",
    chart="Chart image (optional)",
    thesis="Long-term thesis (optional, posted in the play thread)",
)
async def spot(interaction: discord.Interaction, pair: str, dca_zone: str, target1: str, allocation: str = None, avg_entry: str = None, target2: str = None, target3: str = None, invalidation: str = None, chart: discord.Attachment = None, thesis: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message(f"Only members with the **{ANALYST_ROLE_NAME}** role can post plays.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    channel = bot.get_channel(SPOT_CHANNEL_ID)
    if channel is None:
        await interaction.followup.send("Spot channel not found - check SPOT_CHANNEL_ID.", ephemeral=True)
        return
    akey, acfg = resolve_analyst(interaction.user)
    p = {
        "kind": "spot",
        "analyst_id": interaction.user.id, "analyst_name": interaction.user.display_name,
        "analyst_avatar": interaction.user.display_avatar.url, "analyst_key": akey,
        "analyst_color": analyst_color_hex(interaction.user),
        "pair": pair, "dca_zone": dca_zone, "allocation": allocation,
        "avg_entry": avg_entry, "avg_exit": None,
        "t1": target1, "t2": target2, "t3": target3,
        "t1_hit": False, "t2_hit": False, "t3_hit": False,
        "invalidation": invalidation, "thesis": thesis,
        "status": "ACCUMULATING", "zone_filled": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "closed": False, "result": None, "result_pct": None, "close_note": None,
        "edited": False, "edited_at": None,
    }
    files = []
    embed = build_spot_embed(p)
    if chart:
        f = await chart.to_file()
        files.append(f)
        embed.set_image(url=f"attachment://{chart.filename}")
    mention_ids = []
    if PING_ROLE_ID:
        mention_ids.append(PING_ROLE_ID)
    if acfg and acfg.get("ping_role_id"):
        mention_ids.append(acfg["ping_role_id"])
    content = None
    allowed = discord.AllowedMentions.none()
    if mention_ids:
        content = " ".join(f"<@&{r}>" for r in mention_ids) + " New spot play"
        allowed = discord.AllowedMentions(roles=True)
    msg = await channel.send(content=content, embed=embed, files=files, allowed_mentions=allowed)
    p["message_id"] = msg.id
    p["channel_id"] = channel.id
    try:
        thread = await msg.create_thread(name=f"{pair.upper()} SPOT - {interaction.user.display_name}")
        p["thread_id"] = thread.id
        if thesis:
            try:
                await thread.send(f"**Thesis:** {thesis[:1900]}")
            except Exception:
                pass
    except discord.HTTPException:
        p["thread_id"] = None
    data = load_spot()
    data[str(msg.id)] = p
    save_spot(data)
    await refresh_spot_board()
    await interaction.followup.send(f"Spot play posted in {channel.mention} ({msg.jump_url})", ephemeral=True)


async def open_trades_ac(interaction: discord.Interaction, current: str):
    data = load_trades()
    is_admin = interaction.user.guild_permissions.administrator
    out = []
    for mid, t in data.items():
        if t.get("closed"):
            continue
        if not is_admin and t.get("analyst_id") != interaction.user.id:
            continue
        label = f"{t['pair'].upper()} {t['direction']} {tf(t)} - {short_status(t)}"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=mid))
    return out[:25]


async def open_spot_ac(interaction: discord.Interaction, current: str):
    data = load_spot()
    is_admin = interaction.user.guild_permissions.administrator
    out = []
    for mid, p in data.items():
        if p.get("closed"):
            continue
        if not is_admin and p.get("analyst_id") != interaction.user.id:
            continue
        label = f"{p['pair'].upper()} SPOT - {spot_status_line(p)}"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=mid))
    return out[:25]


async def editable_any_ac(interaction: discord.Interaction, current: str):
    is_admin = interaction.user.guild_permissions.administrator
    out = []
    for mid, t in load_trades().items():
        if t.get("closed"):
            continue
        if not is_admin and t.get("analyst_id") != interaction.user.id:
            continue
        if not is_admin and not within_edit_window(t):
            continue
        label = f"{t['pair'].upper()} {t['direction']} {tf(t)} - {short_status(t)}"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=f"f:{mid}"))
    for mid, p in load_spot().items():
        if p.get("closed"):
            continue
        if not is_admin and p.get("analyst_id") != interaction.user.id:
            continue
        if not is_admin and not within_edit_window(p):
            continue
        label = f"{p['pair'].upper()} SPOT - {spot_status_line(p)}"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=f"s:{mid}"))
    return out[:25]


async def refresh_and_edit(t: dict, spot_mode: bool = False):
    channel = bot.get_channel(t["channel_id"])
    msg = await channel.fetch_message(t["message_id"])
    image_url = msg.attachments[0].url if msg.attachments else None
    builder = build_spot_embed if spot_mode else build_embed
    await msg.edit(embed=builder(t, image_url=image_url))
    return msg


async def thread_note(t: dict, text: str):
    if t.get("thread_id"):
        try:
            th = bot.get_channel(t["thread_id"]) or await bot.fetch_channel(t["thread_id"])
            await th.send(text)
        except Exception:
            pass


async def post_update_feed(t: dict, title: str, color: discord.Color, line: str, footer: str = "Scient Lounge - Trade Updates"):
    if not TRADE_UPDATES_CHANNEL_ID:
        return
    ch = bot.get_channel(TRADE_UPDATES_CHANNEL_ID)
    if ch is None:
        return
    e = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    pair_line = f"**{t['pair'].upper()}"
    if t.get("kind") == "spot":
        pair_line += " SPOT"
    else:
        pair_line += f" {t['direction']}" + (f" - {tf(t)}" if tf(t) else "")
    e.description = f"{pair_line}**\n{line}\n[View original call]({jump_url(t)})"
    e.set_author(name=t["analyst_name"], icon_url=t.get("analyst_avatar") or None)
    e.set_footer(text=footer)
    await ch.send(embed=e)


@bot.tree.command(name="spot_update", description="Update a spot play (avg, status, target hit, note)")
@app_commands.describe(
    play="Pick an active spot play",
    avg_entry="New average entry after DCA (optional)",
    status="New phase (optional)",
    target_hit="Mark a target as reached (optional)",
    zone_filled="Mark DCA zone fully filled (optional)",
    note="Update note (optional)",
)
@app_commands.choices(
    status=[app_commands.Choice(name=s.capitalize(), value=s) for s in SPOT_STATUSES],
    target_hit=[
        app_commands.Choice(name="Target 1", value="t1"),
        app_commands.Choice(name="Target 2", value="t2"),
        app_commands.Choice(name="Target 3", value="t3"),
    ],
    zone_filled=[app_commands.Choice(name="Yes", value="yes")],
)
@app_commands.autocomplete(play=open_spot_ac)
async def spot_update(interaction: discord.Interaction, play: str, avg_entry: str = None, status: app_commands.Choice[str] = None, target_hit: app_commands.Choice[str] = None, zone_filled: app_commands.Choice[str] = None, note: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message("Analysts only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    data = load_spot()
    p = data.get(play)
    if not p:
        await interaction.followup.send("Play not found.", ephemeral=True)
        return
    changes = []
    if avg_entry is not None:
        p["avg_entry"] = avg_entry; changes.append(f"avg entry -> {avg_entry}")
    if status is not None:
        p["status"] = status.value; changes.append(f"status -> {status.value}")
    if target_hit is not None:
        p[f"{target_hit.value}_hit"] = True; changes.append(f"{target_hit.name} hit")
    if zone_filled is not None:
        p["zone_filled"] = True; changes.append("zone filled")
    if not changes and not note:
        await interaction.followup.send("Nothing to update - fill at least one field.", ephemeral=True)
        return
    data[play] = p
    save_spot(data)
    await refresh_and_edit(p, spot_mode=True)
    await refresh_spot_board()
    line = ", ".join(changes) if changes else "Update"
    if note:
        line += f"\n> {note}"
    await post_update_feed(p, "Spot Play Update", GOLD, line, footer="Scient Lounge - Spot Plays")
    await thread_note(p, f"**Update** - {', '.join(changes) if changes else ''}" + (f" - {note}" if note else ""))
    await interaction.followup.send(f"Play updated: {', '.join(changes) if changes else 'note added'}", ephemeral=True)


@bot.tree.command(name="spot_close", description="Close a spot play")
@app_commands.describe(play="Pick an active spot play", result="Outcome", result_pct="Result in % e.g. +190%", avg_exit="Average exit price (optional)", note="Closing note (optional)")
@app_commands.choices(result=[
    app_commands.Choice(name="Win", value="WIN"),
    app_commands.Choice(name="Loss", value="LOSS"),
    app_commands.Choice(name="Breakeven", value="BE"),
    app_commands.Choice(name="Invalidated", value="INVALID"),
])
@app_commands.autocomplete(play=open_spot_ac)
async def spot_close(interaction: discord.Interaction, play: str, result: app_commands.Choice[str], result_pct: str = None, avg_exit: str = None, note: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message("Analysts only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    data = load_spot()
    p = data.get(play)
    if not p:
        await interaction.followup.send("Play not found.", ephemeral=True)
        return
    p["closed"] = True
    p["result"] = result.value
    p["result_pct"] = result_pct
    p["avg_exit"] = avg_exit
    p["closed_at"] = datetime.now(timezone.utc).isoformat()
    if note:
        p["close_note"] = note
    data[play] = p
    save_spot(data)
    await refresh_and_edit(p, spot_mode=True)
    await refresh_spot_board()
    ptxt = f" ({result_pct})" if result_pct else ""
    feed = {
        "WIN": (f"Spot Closed - Win{ptxt}", GREEN, "Play closed in profit."),
        "LOSS": (f"Spot Closed - Loss{ptxt}", RED, "Play closed at a loss."),
        "BE": ("Spot Closed - Breakeven", GREY, "Play closed flat."),
        "INVALID": ("Spot Invalidated", DGREY, "Thesis invalidated."),
    }
    title, color, line = feed[result.value]
    if note:
        line += f"\n> {note}"
    await post_update_feed(p, title, color, line, footer="Scient Lounge - Spot Plays")
    await thread_note(p, f"**Closed - {result.value}{ptxt}**" + (f" - {note}" if note else ""))
    await interaction.followup.send(f"Play closed: {result.value}{ptxt}", ephemeral=True)


@bot.tree.command(name="edit", description="Fix a mistake in a recently posted trade or spot play (within the edit window)")
@app_commands.describe(
    trade="Pick your recent trade or spot play",
    pair="Corrected pair (optional)",
    direction="Corrected direction - futures only (optional)",
    entry="Corrected entry / DCA zone (optional)",
    entry2="Corrected second DCA entry - futures only (optional)",
    stop_loss="Corrected SL / invalidation (optional)",
    risk="Corrected risk / allocation (optional)",
    entry_type="Corrected entry type - futures only (optional)",
    framework="Corrected framework - futures only (optional)",
    framework2="Corrected second framework - futures only (optional)",
    chart="Replacement chart image (optional)",
    tp1="Corrected TP1 / Target 1 (optional)",
    tp2="Corrected TP2 / Target 2 (optional)",
    tp3="Corrected TP3 / Target 3 (optional)",
    timeframe="Corrected timeframe - futures only (optional)",
    setup_detail="Corrected setup detail - futures only (optional)",
    notes="Corrected reasoning / thesis (optional, posted in the thread)",
)
@app_commands.choices(
    direction=[app_commands.Choice(name="Long", value="LONG"), app_commands.Choice(name="Short", value="SHORT")],
    framework=[app_commands.Choice(name=f, value=f) for f in FRAMEWORKS],
    framework2=[app_commands.Choice(name=f, value=f) for f in FRAMEWORKS],
    entry_type=[
        app_commands.Choice(name="Market - filled now", value="MARKET"),
        app_commands.Choice(name="Limit", value="LIMIT"),
    ],
)
@app_commands.autocomplete(trade=editable_any_ac)
async def edit(interaction: discord.Interaction, trade: str, pair: str = None, direction: app_commands.Choice[str] = None, entry: str = None, entry2: str = None, stop_loss: str = None, risk: str = None, entry_type: app_commands.Choice[str] = None, framework: app_commands.Choice[str] = None, framework2: app_commands.Choice[str] = None, chart: discord.Attachment = None, tp1: str = None, tp2: str = None, tp3: str = None, timeframe: str = None, setup_detail: str = None, notes: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message("Analysts only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    spot_mode = trade.startswith("s:")
    key = trade[2:] if (trade.startswith("f:") or trade.startswith("s:")) else trade
    data = load_spot() if spot_mode else load_trades()
    t = data.get(key)
    if not t:
        await interaction.followup.send("Trade not found.", ephemeral=True)
        return
    is_admin = interaction.user.guild_permissions.administrator
    if not is_admin and t.get("analyst_id") != interaction.user.id:
        await interaction.followup.send("You can only edit your own trades.", ephemeral=True)
        return
    if not is_admin and not within_edit_window(t):
        await interaction.followup.send(
            f"Edit window ({EDIT_WINDOW_MIN} min) has expired. Use `/update` or `/spot_update` for status changes, or ask an admin.",
            ephemeral=True,
        )
        return
    changes = []
    if pair is not None:
        t["pair"] = pair; changes.append("pair")
    if spot_mode:
        if entry is not None:
            t["dca_zone"] = entry; changes.append("DCA zone")
        if stop_loss is not None:
            t["invalidation"] = stop_loss; changes.append("invalidation")
        if risk is not None:
            t["allocation"] = risk; changes.append("allocation")
        if tp1 is not None:
            t["t1"] = tp1; changes.append("Target 1")
        if tp2 is not None:
            t["t2"] = tp2; changes.append("Target 2")
        if tp3 is not None:
            t["t3"] = tp3; changes.append("Target 3")
        if notes is not None:
            t["thesis"] = notes; changes.append("thesis")
    else:
        if direction is not None:
            t["direction"] = direction.value; changes.append("direction")
        if entry is not None:
            t["entry"] = entry; changes.append("entry")
        if entry2 is not None:
            t["entry2"] = entry2; changes.append("entry 2")
        if stop_loss is not None:
            t["sl"] = stop_loss; changes.append("SL")
        if risk is not None:
            t["risk"] = risk; changes.append("risk")
        if entry_type is not None:
            t["entry_type"] = entry_type.value
            t["entry1_filled"] = entry_type.value == "MARKET"
            changes.append("entry type")
        if framework is not None or framework2 is not None:
            fws = [f.value for f in (framework, framework2) if f]
            t["frameworks"] = fws
            t["framework"] = fws[0] if fws else None
            changes.append("framework")
        if tp1 is not None:
            t["tp1"] = tp1; changes.append("TP1")
        if tp2 is not None:
            t["tp2"] = tp2; changes.append("TP2")
        if tp3 is not None:
            t["tp3"] = tp3; changes.append("TP3")
        if timeframe is not None:
            t["timeframe"] = timeframe; changes.append("timeframe")
        if setup_detail is not None:
            t["setup_detail"] = setup_detail; changes.append("setup detail")
        if notes is not None:
            t["notes"] = notes; changes.append("notes")
    if chart is not None:
        changes.append("chart")
    if not changes:
        await interaction.followup.send("Nothing to change - fill at least one field.", ephemeral=True)
        return
    t["edited"] = True
    t["edited_at"] = datetime.now(timezone.utc).isoformat()
    data[key] = t
    (save_spot if spot_mode else save_trades)(data)
    channel = bot.get_channel(t["channel_id"])
    try:
        msg = await channel.fetch_message(t["message_id"])
    except Exception:
        await interaction.followup.send("Original message not found - it may have been deleted.", ephemeral=True)
        return
    builder = build_spot_embed if spot_mode else build_embed
    if chart is not None:
        f = await chart.to_file()
        embed = builder(t)
        embed.set_image(url=f"attachment://{chart.filename}")
        await msg.edit(embed=embed, attachments=[f])
    else:
        image_url = msg.attachments[0].url if msg.attachments else None
        await msg.edit(embed=builder(t, image_url=image_url))
    if spot_mode:
        await refresh_spot_board()
    else:
        await refresh_board()
    changed_txt = ", ".join(changes)
    await thread_note(t, f"**Edited** - corrected: {changed_txt}")
    if notes is not None:
        label = "Thesis (updated)" if spot_mode else "Reasoning (updated)"
        await thread_note(t, f"**{label}:** {notes[:1900]}")
    await interaction.followup.send(f"Updated ({changed_txt}). {jump_url(t)}", ephemeral=True)


@bot.tree.command(name="update", description="Update or close a running trade")
@app_commands.describe(
    trade="Pick an open trade",
    event="What happened",
    size_pct="TP/Partial TP only - % of position closed (e.g. 25). Skip for other events",
    price="Partial TP and Closed only - fill/exit price. Skip for fills and BE",
    note="Optional note",
)
@app_commands.choices(event=[
    app_commands.Choice(name="Entry 1 Filled", value="EF1"),
    app_commands.Choice(name="DCA Entry Filled (Entry 2)", value="EF2"),
    app_commands.Choice(name="TP1 Hit", value="TP1"),
    app_commands.Choice(name="TP2 Hit", value="TP2"),
    app_commands.Choice(name="TP3 Hit", value="TP3"),
    app_commands.Choice(name="Partial TP (custom price)", value="PTP"),
    app_commands.Choice(name="SL Moved to Entry (Risk-Free)", value="BE"),
    app_commands.Choice(name="SL Hit (closes trade)", value="SL"),
    app_commands.Choice(name="Closed (bot calculates result)", value="CLOSE"),
    app_commands.Choice(name="Invalidated (never triggered)", value="CI"),
])
@app_commands.autocomplete(trade=open_trades_ac)
async def update(interaction: discord.Interaction, trade: str, event: app_commands.Choice[str], size_pct: str = None, price: str = None, note: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message("Analysts only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    data = load_trades()
    t = data.get(trade)
    if not t:
        await interaction.followup.send("Trade not found.", ephemeral=True)
        return
    t.setdefault("fills", [])
    ev = event.value
    pct = parse_num(size_pct)
    px = parse_num(price)

    if ev in ("TP1", "TP2", "TP3", "PTP"):
        if pct is None:
            await interaction.followup.send("**size_pct is required** - how much of the position was closed at this level? (e.g. 25)", ephemeral=True)
            return
        if pct <= 0 or pct > 100:
            await interaction.followup.send("size_pct must be between 0 and 100.", ephemeral=True)
            return
        if fills_pct(t) + pct > 100.01:
            await interaction.followup.send(f"That would close {fills_pct(t) + pct:g}% total - only {100 - fills_pct(t):g}% of the position is left.", ephemeral=True)
            return
    if ev == "PTP" and px is None:
        await interaction.followup.send("**price is required** for Partial TP - where did you take profits?", ephemeral=True)
        return

    desc = ""
    if ev == "EF1":
        t["entry1_filled"] = True
        desc = "Entry 1 filled" if t.get("entry2") else "Entry filled"
    elif ev == "EF2":
        if not t.get("entry2"):
            await interaction.followup.send("This trade has no DCA entry (entry2) - use Entry 1 Filled.", ephemeral=True)
            return
        t["entry2_filled"] = True
        t["entry1_filled"] = True
        desc = "DCA entry filled - full position live"
    elif ev in ("TP1", "TP2", "TP3"):
        keymap = {"TP1": "tp1", "TP2": "tp2", "TP3": "tp3"}
        k = keymap[ev]
        t[f"{k}_hit"] = True
        t["entry1_filled"] = True
        if ev == "TP2":
            t["tp1_hit"] = True
        if ev == "TP3":
            t["tp1_hit"] = True; t["tp2_hit"] = True
        fill_price = px if px is not None else first_num(t.get(k))
        if fill_price is None:
            await interaction.followup.send(f"{ev} has no price set on the trade - pass `price` with this update.", ephemeral=True)
            return
        t["fills"].append({"price": fill_price, "pct": pct, "label": ev})
        desc = f"{ev} hit @ {fnum(fill_price)} ({pct:g}%)"
    elif ev == "PTP":
        slot = next((k for k in ("tp1", "tp2", "tp3") if t.get(k) and not t.get(f"{k}_hit")), None)
        if slot:
            t[f"{slot}_hit"] = True
            label = slot.upper()
        else:
            label = "Partial TP"
        t["entry1_filled"] = True
        t["fills"].append({"price": px, "pct": pct, "label": label})
        desc = f"{label} hit @ {fnum(px)} ({pct:g}%)"
    elif ev == "BE":
        t["be"] = True
        desc = "SL moved to entry - trade is risk-free"
    elif ev == "SL":
        exit_px = px if px is not None else (entry_num(t) if t.get("be") else sl_num(t))
        if exit_px is None:
            await interaction.followup.send("Couldn't read the SL price - pass `price` with this update.", ephemeral=True)
            return
        t["sl_hit"] = not t.get("be")
        avg_exit, r = finalize_close(t, exit_px)
        t["closed"] = True
        t["avg_exit"] = avg_exit
        t["result_r"] = round(r, 2) if r is not None else None
        t["result"] = ("WIN" if r > 0.05 else "LOSS" if r < -0.05 else "BE") if r is not None else "LOSS"
        t["closed_at"] = datetime.now(timezone.utc).isoformat()
        if note:
            t["close_note"] = note
        desc = "Stopped out - closed"
    elif ev == "CLOSE":
        rem = 100 - fills_pct(t)
        if rem > 0.01 and px is None:
            await interaction.followup.send(f"**price is required** - {rem:g}% of the position is still open, at what price was it closed?", ephemeral=True)
            return
        avg_exit, r = finalize_close(t, px)
        if r is None:
            await interaction.followup.send("Couldn't calculate the result - check that entry and SL are numeric on this trade.", ephemeral=True)
            return
        t["closed"] = True
        t["avg_exit"] = avg_exit
        t["result_r"] = round(r, 2)
        t["result"] = "WIN" if r > 0.05 else "LOSS" if r < -0.05 else "BE"
        t["closed_at"] = datetime.now(timezone.utc).isoformat()
        if note:
            t["close_note"] = note
        desc = "Closed"
    elif ev == "CI":
        t["closed"] = True
        t["result"] = "INVALID"
        t["closed_at"] = datetime.now(timezone.utc).isoformat()
        if note:
            t["close_note"] = note
        desc = "Invalidated"

    data[trade] = t
    save_trades(data)
    await refresh_and_edit(t)
    await refresh_board()

    rtxt = f" ({t['result_r']:+.2f}R)" if t.get("closed") and isinstance(t.get("result_r"), (int, float)) else ""
    aetxt = f" | Avg exit: {fnum(t['avg_exit'])}" if t.get("closed") and t.get("avg_exit") is not None else ""
    if t.get("closed") and t.get("result") != "INVALID":
        res = t["result"]
        title = f"Closed - {'Win' if res == 'WIN' else 'Loss' if res == 'LOSS' else 'Breakeven'}{rtxt}"
        color = GREEN if res == "WIN" else RED if res == "LOSS" else GREY
        line = f"{desc}{rtxt}{aetxt}"
    elif ev == "CI":
        title, color, line = "Invalidated", DGREY, "Setup invalidated before trigger."
    else:
        title = desc
        color = GREEN if ev in ("PTP", "TP1", "TP2", "TP3") else BLUE if ev in ("EF1", "EF2") else GREY
        closed_pct = fills_pct(t)
        line = desc + (f"\n{closed_pct:g}% of position closed, {100 - closed_pct:g}% running" if closed_pct > 0 and not t.get("closed") else "")
    if note:
        line += f"\n> {note}"
    await post_update_feed(t, title, color, line)
    await thread_note(t, f"**{desc}{rtxt}**" + (f" - {note}" if note else ""))
    await interaction.followup.send(f"Updated: {desc}{rtxt}{aetxt}", ephemeral=True)


@bot.tree.command(name="open", description="See all live positions (futures + spot)")
async def open_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embeds = [build_board_embed(), build_spot_board_embed()]
    await interaction.followup.send(embeds=embeds, ephemeral=True)


@bot.tree.command(name="recent", description="Latest closed trades with results")
@app_commands.describe(analyst="Filter by analyst (optional)")
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
            d = "\U0001F7E2 L" if t["direction"] == "LONG" else "\U0001F534 S"
            res = t.get("result", "?")
            r = t.get("result_r")
            rtxt = f" ({r:+g}R)" if isinstance(r, (int, float)) else ""
            emoji = {"WIN": "\u2705", "LOSS": "\u274C", "BE": "\u2796", "INVALID": "\U0001F6AB"}.get(res, "")
            lines.append(f"{emoji} {d} **{t['pair'].upper()}**" + (f" - {tf(t)}" if tf(t) else "") + f" - {res}{rtxt} - {t.get('analyst_name', '')} - [view]({jump_url(t)})")
        embed.add_field(name="Futures", value="\n".join(lines)[:1024], inline=False)
    if sclosed:
        lines = []
        for p in sclosed:
            res = p.get("result", "?")
            pct = f" ({p['result_pct']})" if p.get("result_pct") else ""
            emoji = {"WIN": "\u2705", "LOSS": "\u274C", "BE": "\u2796", "INVALID": "\U0001F6AB"}.get(res, "")
            lines.append(f"{emoji} \U0001FA99 **{p['pair'].upper()}** - {res}{pct} - {p.get('analyst_name', '')} - [view]({jump_url(p)})")
        embed.add_field(name="Spot", value="\n".join(lines)[:1024], inline=False)
    if not closed and not sclosed:
        embed.description = "*No closed trades yet.*"
    embed.set_footer(text="Scient Lounge - Journal")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="price", description="Live price for any coin")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL, ETH")
async def price(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    if symbol.endswith("USDT"):
        pair = symbol
    else:
        pair = f"{symbol}USDT"
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"symbol": pair}, timeout=15) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"Couldn't find **{symbol}** - check the symbol (e.g. BTC, SOL, ETH).")
                    return
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Price feed unavailable right now - try again in a minute.")
        return
    try:
        last = float(data["lastPrice"])
        chg = float(data["priceChangePercent"])
        high = float(data["highPrice"])
        low = float(data["lowPrice"])
    except Exception:
        await interaction.followup.send(f"Couldn't parse price data for **{symbol}**.")
        return
    arrow = "\U0001F7E2" if chg >= 0 else "\U0001F534"
    color = GREEN if chg >= 0 else RED
    embed = discord.Embed(title=f"{arrow} {symbol}/USDT", color=color, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**Price:** ${fnum(last)}\n"
        f"**24h:** {chg:+.2f}%\n"
        f"**24h High:** ${fnum(high)} | **24h Low:** ${fnum(low)}"
    )
    embed.set_footer(text="Scient Lounge - Binance spot")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="pnl", description="Position size calculator - know your size before you enter")
@app_commands.describe(
    account="Account size in $ (e.g. 5000)",
    risk="Risk per trade in % (e.g. 1)",
    entry="Entry price",
    stop_loss="Stop loss price",
    leverage="Leverage (optional, shows margin needed)",
)
async def pnl(interaction: discord.Interaction, account: str, risk: str, entry: str, stop_loss: str, leverage: str = None):
    await interaction.response.defer(ephemeral=True)
    acc = parse_num(account)
    rk = parse_num(risk)
    en = parse_num(entry)
    sl = parse_num(stop_loss)
    lev = parse_num(leverage) if leverage else None
    if not all([acc, rk, en, sl]) or acc <= 0 or rk <= 0 or en <= 0 or sl <= 0:
        await interaction.followup.send("Check your inputs - account, risk, entry, and SL must all be positive numbers.", ephemeral=True)
        return
    if en == sl:
        await interaction.followup.send("Entry and SL can't be the same price.", ephemeral=True)
        return
    if lev is not None and lev <= 0:
        await interaction.followup.send("Leverage must be a positive number.", ephemeral=True)
        return
    risk_amount = acc * rk / 100
    sl_dist_pct = abs(en - sl) / en * 100
    position_value = risk_amount / (sl_dist_pct / 100)
    units = position_value / en
    direction = "LONG" if sl < en else "SHORT"
    lines = [
        f"**Direction:** {direction} (based on SL vs entry)",
        f"**Risk:** ${fnum(risk_amount)} ({rk:g}% of ${fnum(acc)})",
        f"**SL distance:** {sl_dist_pct:.2f}%",
        f"**Position size:** {fnum(units)} units (${fnum(position_value)} notional)",
    ]
    if lev:
        margin = position_value / lev
        if margin > acc:
            lines.append(f"**Margin @ {lev:g}x:** ${fnum(margin)} \u26A0\uFE0F exceeds your account size")
        else:
            lines.append(f"**Margin @ {lev:g}x:** ${fnum(margin)} ({margin / acc * 100:.1f}% of account)")
    embed = discord.Embed(title="Position Size Calculator", color=NAVY)
    embed.description = "\n".join(lines)
    embed.set_footer(text="Scient Lounge - risk first, always")
    await interaction.followup.send(embed=embed, ephemeral=True)


CHART_INTERVALS = {"15m": "15m", "1H": "1h", "4H": "4h", "1D": "1d", "1W": "1w"}


def make_chart_image(symbol: str, interval: str, klines: list) -> io.BytesIO:
    import pandas as pd
    import mplfinance as mpf
    df = pd.DataFrame(klines, columns=["t", "o", "h", "l", "c", "v", "ct", "qv", "n", "tb", "tq", "ig"])
    df["Date"] = pd.to_datetime(df["t"], unit="ms")
    df = df.set_index("Date")
    for col, name in (("o", "Open"), ("h", "High"), ("l", "Low"), ("c", "Close"), ("v", "Volume")):
        df[name] = df[col].astype(float)
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    addplots = []
    for period, color in zip(EMA_PERIODS, EMA_COLORS):
        if len(df) >= period:
            ema = df["Close"].ewm(span=period, adjust=False).mean()
            addplots.append(mpf.make_addplot(ema, color=color, width=1.3))
    mc = mpf.make_marketcolors(up="#0ECB81", down="#F6465D", edge="inherit", wick="inherit", volume={"up": "#0ECB8155", "down": "#F6465D55"})
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc, facecolor="#131722", edgecolor="#2A2E39", figcolor="#131722", gridcolor="#1E222D", gridstyle="-", rc={"axes.labelcolor": "#B2B5BE", "xtick.color": "#B2B5BE", "ytick.color": "#B2B5BE", "font.size": 9})
    last = df["Close"].iloc[-1]
    price_txt = f"{last:,.2f}" if last >= 1000 else f"{last:,.4f}".rstrip("0").rstrip(".")
    fig, axes = mpf.plot(
        df, type="candle", style=style, volume=True,
        addplot=addplots if addplots else None,
        panel_ratios=(5, 1), figsize=(13, 7.5),
        scale_width_adjustment=dict(candle=1.5, volume=0.9),
        tight_layout=True, returnfig=True,
        hlines=dict(hlines=[float(last)], colors=["#B2B5BE"], linestyle="--", linewidths=0.8, alpha=0.6),
        ylabel="", ylabel_lower="",
    )
    for ax in axes:
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
    x0, x1 = axes[0].get_xlim()
    axes[0].set_xlim(x0, x1 + (x1 - x0) * 0.06)
    axes[0].set_title(f"{symbol}  {interval}  |  {price_txt}", color="#EAECEF", fontsize=13, loc="left", pad=12)
    up = df["Close"].iloc[-1] >= df["Open"].iloc[-1]
    tag_color = "#0ECB81" if up else "#F6465D"
    axes[0].annotate(
        price_txt, xy=(1.0, float(last)), xycoords=("axes fraction", "data"),
        xytext=(4, 0), textcoords="offset points", ha="left", va="center",
        color="#131722", fontsize=9, fontweight="bold", clip_on=False,
        annotation_clip=False, zorder=10,
        bbox=dict(boxstyle="round,pad=0.25", facecolor=tag_color, edgecolor="none"),
    )
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor="#131722", bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    buf.seek(0)
    return buf


@bot.tree.command(name="chart", description="Quick price chart with EMAs (Binance data)")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL", timeframe="Chart timeframe")
@app_commands.choices(timeframe=[app_commands.Choice(name=k, value=k) for k in CHART_INTERVALS])
async def chart_cmd(interaction: discord.Interaction, coin: str, timeframe: app_commands.Choice[str]):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    interval = CHART_INTERVALS[timeframe.value]
    url = "https://api.binance.com/api/v3/klines"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"symbol": pair, "interval": interval, "limit": 220}, timeout=20) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"Couldn't find **{symbol}** - check the symbol.")
                    return
                klines = await resp.json()
    except Exception:
        await interaction.followup.send("Chart data unavailable right now - try again in a minute.")
        return
    if not klines or len(klines) < 20:
        await interaction.followup.send(f"Not enough data for **{symbol}** on {timeframe.value}.")
        return
    try:
        buf = await asyncio.to_thread(make_chart_image, f"{symbol}/USDT", timeframe.value, klines)
    except Exception as e:
        await interaction.followup.send(f"Chart rendering failed: {e}")
        return
    f = discord.File(buf, filename=f"{symbol}_{timeframe.value}.png")
    ema_txt = " / ".join(str(p) for p in EMA_PERIODS)
    await interaction.followup.send(content=f"**{symbol}/USDT - {timeframe.value}** | EMAs: {ema_txt}", file=f)


@bot.tree.command(name="liq", description="Liquidation price calculator")
@app_commands.describe(entry="Entry price", leverage="Leverage, e.g. 10", direction="Long or Short")
@app_commands.choices(direction=[app_commands.Choice(name="Long", value="LONG"), app_commands.Choice(name="Short", value="SHORT")])
async def liq(interaction: discord.Interaction, entry: str, leverage: str, direction: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    en = parse_num(entry)
    lev = parse_num(leverage)
    if not en or not lev or en <= 0 or lev <= 1:
        await interaction.followup.send("Check inputs - entry must be positive and leverage above 1.", ephemeral=True)
        return
    mmr = 0.005
    if direction.value == "LONG":
        liq_price = en * (1 - 1 / lev + mmr)
        dist = (en - liq_price) / en * 100
    else:
        liq_price = en * (1 + 1 / lev - mmr)
        dist = (liq_price - en) / en * 100
    embed = discord.Embed(title="Liquidation Calculator", color=NAVY)
    embed.description = (
        f"**Direction:** {direction.value} @ {fnum(en)} | **Leverage:** {lev:g}x\n"
        f"**Est. liquidation:** {fnum(liq_price)}\n"
        f"**Distance:** {dist:.2f}% against you\n\n"
        f"*Estimate with 0.5% maintenance margin - exact level varies by exchange, position size, and margin mode. Always check your exchange.*"
    )
    embed.set_footer(text="Scient Lounge - risk first, always")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="funding", description="Current funding rate (Binance perps)")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL")
async def funding(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"symbol": pair}, timeout=15) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"No perp market found for **{symbol}**.")
                    return
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Funding data unavailable right now.")
        return
    try:
        rate = float(data["lastFundingRate"]) * 100
        mark = float(data["markPrice"])
        nxt = int(data["nextFundingTime"]) // 1000
    except Exception:
        await interaction.followup.send(f"Couldn't parse funding data for **{symbol}**.")
        return
    lean = "Longs paying shorts (crowded long)" if rate > 0 else "Shorts paying longs (crowded short)" if rate < 0 else "Neutral"
    color = RED if abs(rate) > 0.05 else GREEN if abs(rate) < 0.01 else GOLD
    embed = discord.Embed(title=f"Funding - {symbol} Perp", color=color, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**Rate:** {rate:+.4f}% per 8h ({rate * 3 * 365:+.1f}% annualized)\n"
        f"**Lean:** {lean}\n"
        f"**Mark:** ${fnum(mark)} | **Next funding:** <t:{nxt}:R>"
    )
    embed.set_footer(text="Scient Lounge - Binance perps")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="fear", description="Crypto Fear & Greed index")
async def fear(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.alternative.me/fng/?limit=2", timeout=15) as resp:
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Fear & Greed data unavailable right now.")
        return
    try:
        today = data["data"][0]
        val = int(today["value"])
        label = today["value_classification"]
        prev = int(data["data"][1]["value"]) if len(data["data"]) > 1 else None
    except Exception:
        await interaction.followup.send("Couldn't parse Fear & Greed data.")
        return
    if val <= 25:
        color, emoji = RED, "\U0001F628"
    elif val <= 45:
        color, emoji = GOLD, "\U0001F61F"
    elif val <= 55:
        color, emoji = GREY, "\U0001F610"
    elif val <= 75:
        color, emoji = GREEN, "\U0001F642"
    else:
        color, emoji = GREEN, "\U0001F911"
    bar_filled = round(val / 10)
    bar = "\u2588" * bar_filled + "\u2591" * (10 - bar_filled)
    chg = f" ({val - prev:+d} vs yesterday)" if prev is not None else ""
    embed = discord.Embed(title=f"{emoji} Fear & Greed: {val} - {label}", color=color, timestamp=datetime.now(timezone.utc))
    embed.description = f"`{bar}` {val}/100{chg}\n\n*Extreme fear = others panicking. Extreme greed = time to be careful.*"
    embed.set_footer(text="Scient Lounge - alternative.me")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="oi", description="Open interest for a perp market")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL")
async def oi(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": pair}, timeout=15) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"No perp market found for **{symbol}**.")
                    return
                now_data = await resp.json()
            async with session.get("https://fapi.binance.com/futures/data/openInterestHist", params={"symbol": pair, "period": "1h", "limit": 25}, timeout=15) as resp2:
                hist = await resp2.json() if resp2.status == 200 else []
            async with session.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": pair}, timeout=15) as resp3:
                px = await resp3.json() if resp3.status == 200 else {}
    except Exception:
        await interaction.followup.send("OI data unavailable right now.")
        return
    try:
        oi_now = float(now_data["openInterest"])
        mark = float(px.get("markPrice", 0))
        oi_usd = oi_now * mark if mark else None
    except Exception:
        await interaction.followup.send(f"Couldn't parse OI data for **{symbol}**.")
        return
    chg_txt = ""
    color = NAVY
    if isinstance(hist, list) and len(hist) >= 24:
        try:
            oi_then = float(hist[0]["sumOpenInterest"])
            chg = (oi_now - oi_then) / oi_then * 100
            arrow = "\U0001F4C8" if chg >= 0 else "\U0001F4C9"
            chg_txt = f"\n**24h change:** {arrow} {chg:+.2f}%"
            color = GREEN if chg > 2 else RED if chg < -2 else NAVY
        except Exception:
            pass
    usd_txt = f" (${oi_usd / 1e9:.2f}B)" if oi_usd and oi_usd >= 1e9 else (f" (${oi_usd / 1e6:.1f}M)" if oi_usd else "")
    embed = discord.Embed(title=f"Open Interest - {symbol} Perp", color=color, timestamp=datetime.now(timezone.utc))
    embed.description = f"**OI:** {fnum(oi_now)} {symbol}{usd_txt}{chg_txt}"
    embed.set_footer(text="Scient Lounge - Binance perps")
    await interaction.followup.send(embed=embed)


async def _movers(interaction: discord.Interaction, top: bool):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/ticker/24hr", timeout=20) as resp:
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Market data unavailable right now.")
        return
    rows = []
    for d in data:
        s = d.get("symbol", "")
        if not s.endswith("USDT") or any(x in s for x in ("UP", "DOWN", "BULL", "BEAR")):
            continue
        try:
            qv = float(d["quoteVolume"])
            chg = float(d["priceChangePercent"])
            last = float(d["lastPrice"])
        except Exception:
            continue
        if qv < 10_000_000:
            continue
        rows.append((s[:-4], chg, last, qv))
    rows.sort(key=lambda r: r[1], reverse=top)
    rows = rows[:5]
    if not rows:
        await interaction.followup.send("No data right now.")
        return
    title = "\U0001F4C8 Top Gainers (24h)" if top else "\U0001F4C9 Top Losers (24h)"
    color = GREEN if top else RED
    lines = []
    for i, (sym, chg, last, qv) in enumerate(rows, 1):
        vol = f"${qv / 1e9:.1f}B" if qv >= 1e9 else f"${qv / 1e6:.0f}M"
        lines.append(f"**{i}. {sym}** {chg:+.2f}% - ${fnum(last)} - vol {vol}")
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines) + "\n\n*USDT pairs, min $10M volume*"
    embed.set_footer(text="Scient Lounge - Binance spot")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="gainers", description="Top 5 gainers of the day")
async def gainers(interaction: discord.Interaction):
    await _movers(interaction, top=True)


@bot.tree.command(name="losers", description="Top 5 losers of the day")
async def losers(interaction: discord.Interaction):
    await _movers(interaction, top=False)


@bot.tree.command(name="convert", description="Convert coin amount to USD (or USD to coin)")
@app_commands.describe(amount="Amount, e.g. 0.5 or 1500", coin="Coin symbol, e.g. BTC", to_coin="Convert USD amount INTO this coin instead")
@app_commands.choices(to_coin=[app_commands.Choice(name="Yes - amount is USD, show me coin quantity", value="yes")])
async def convert(interaction: discord.Interaction, amount: str, coin: str, to_coin: app_commands.Choice[str] = None):
    await interaction.response.defer()
    amt = parse_num(amount)
    if not amt or amt <= 0:
        await interaction.followup.send("Amount must be a positive number.")
        return
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": pair}, timeout=15) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"Couldn't find **{symbol}**.")
                    return
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Price feed unavailable right now.")
        return
    price = float(data["price"])
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    if to_coin:
        qty = amt / price
        line = f"**${amt:,.2f}** = **{fnum(qty)} {base}** @ ${fnum(price)}"
    else:
        usd = amt * price
        line = f"**{fnum(amt)} {base}** = **${usd:,.2f}** @ ${fnum(price)}"
    embed = discord.Embed(title="Converter", color=NAVY, description=line)
    embed.set_footer(text="Scient Lounge - Binance spot")
    await interaction.followup.send(embed=embed)


QUIZ_BANK = [
    {"q": "In the AMD cycle, what phase comes right after Accumulation?", "opts": ["Distribution", "Manipulation", "Markup", "Re-accumulation"], "a": 1},
    {"q": "On a Fixed Range Volume Profile, the POC is...", "opts": ["The highest price in the range", "The price with the most traded volume", "The midpoint of the range", "The lowest volume node"], "a": 1},
    {"q": "A sweep of the range low followed by a fast reclaim usually sets up...", "opts": ["A short", "A long", "Nothing - it's random", "A breakout short"], "a": 1},
    {"q": "BOS (Break of Structure) in an uptrend signals...", "opts": ["Trend reversal", "Trend continuation", "Ranging market", "Low liquidity"], "a": 1},
    {"q": "Bearish RSI divergence means...", "opts": ["Price makes higher high, RSI makes lower high", "Price and RSI both make higher highs", "Price makes lower low, RSI makes higher low", "RSI crosses above 70"], "a": 0},
    {"q": "A Wyckoff spring typically appears in which phase?", "opts": ["Distribution", "Markup", "Accumulation", "Markdown"], "a": 2},
    {"q": "Your position size should be calculated from...", "opts": ["Gut feeling and leverage", "Account risk % and SL distance", "How confident you are", "The max your margin allows"], "a": 1},
    {"q": "A three drives pattern into a level with RSI divergence usually signals...", "opts": ["Continuation", "Exhaustion / potential reversal", "Increased volatility only", "Nothing tradeable"], "a": 1},
    {"q": "Positive funding rate means...", "opts": ["Shorts pay longs", "Longs pay shorts", "Exchange pays traders", "Volume is increasing"], "a": 1},
    {"q": "Moving your SL to entry makes the trade...", "opts": ["More profitable", "Risk-free", "Higher R:R", "Invalid"], "a": 1},
    {"q": "VAH and VAL are the boundaries of...", "opts": ["The full range", "The value area (~70% of volume)", "The weekly candle", "The liquidation zone"], "a": 1},
    {"q": "Equal highs (EQH) on a chart usually sit right below...", "opts": ["Support", "Resting liquidity", "The POC", "A fair value gap"], "a": 1},
    {"q": "MSS (Market Structure Shift) differs from BOS because it signals...", "opts": ["Continuation", "A potential reversal", "Higher volume", "A new range"], "a": 1},
    {"q": "A Fair Value Gap (FVG) is...", "opts": ["A gap between exchanges", "An imbalance where price moved too fast to fill orders", "The spread between bid and ask", "A weekend CME gap only"], "a": 1},
    {"q": "In Wyckoff, a UTAD (Upthrust After Distribution) is...", "opts": ["A bullish breakout", "A failed push above the range before markdown", "An accumulation event", "A volume spike at support"], "a": 1},
    {"q": "If your risk is 1% and your SL is 4% away, your position size is...", "opts": ["4x your account", "25% of your account", "1% of your account", "You can't calculate it"], "a": 1},
    {"q": "A trade with 40% win rate and 3R average winner is...", "opts": ["A losing system", "Breakeven at best", "A profitable system", "Impossible to judge"], "a": 2},
    {"q": "A deviation below the range low that closes back inside is often...", "opts": ["Confirmation of breakdown", "A long trigger (sweep + reclaim)", "A short trigger", "Meaningless noise"], "a": 1},
    {"q": "The 'M' in AMD stands for...", "opts": ["Markup", "Momentum", "Manipulation", "Margin"], "a": 2},
    {"q": "High Volume Nodes (HVN) on a volume profile act as...", "opts": ["Magnets that repel price", "Areas price moves through quickly", "Areas of acceptance where price tends to slow down", "Guaranteed reversal zones"], "a": 2},
    {"q": "Low Volume Nodes (LVN) tend to...", "opts": ["Hold price for weeks", "Let price move through quickly", "Mark the POC", "Only appear on weekly charts"], "a": 1},
    {"q": "A CHoCH (Change of Character) is...", "opts": ["The first sign structure may be reversing", "A confirmed trend continuation", "A type of candlestick", "An exchange listing event"], "a": 0},
    {"q": "An order block in ICT terms is...", "opts": ["A large limit order on the book", "The last opposing candle before a strong impulsive move", "A blocked exchange account", "The daily open"], "a": 1},
    {"q": "Liquidity in trading usually rests...", "opts": ["At round numbers only", "Above equal highs and below equal lows", "At the POC", "In low volume nodes"], "a": 1},
    {"q": "Revenge trading after a loss usually leads to...", "opts": ["Faster recovery", "Bigger losses from emotional decisions", "Better focus", "Higher win rate"], "a": 1},
    {"q": "If price breaks the range high and immediately reverses back inside, it's called...", "opts": ["A clean breakout", "A deviation / fakeout", "A BOS", "An FVG"], "a": 1},
    {"q": "The safest general place for a stop loss is...", "opts": ["A fixed 2% from entry always", "Beyond the level that invalidates your idea", "At the round number", "As tight as possible"], "a": 1},
    {"q": "OI rising while price rises usually means...", "opts": ["Shorts covering", "New money entering the trend", "The trend is ending", "Low liquidity"], "a": 1},
    {"q": "OI falling while price rises usually means...", "opts": ["New longs opening", "Short covering driving the move", "Distribution", "Nothing"], "a": 1},
    {"q": "Wyckoff's 'Sign of Strength' (SOS) appears...", "opts": ["During markdown", "After the spring, breaking out of accumulation", "At the UTAD", "Only on weekly charts"], "a": 1},
    {"q": "Trading a smaller size after a losing streak is an example of...", "opts": ["Fear-based trading", "Sound risk management", "Revenge trading", "Overtrading"], "a": 1},
    {"q": "A 'premium' zone in ICT terms is...", "opts": ["Below the 50% of the range - discount", "Above the 50% of the range - where shorts are favored", "The POC", "The weekly open"], "a": 1},
    {"q": "Buying in the 'discount' zone of a range means buying...", "opts": ["Above the midpoint", "Below the midpoint (equilibrium)", "At resistance", "At the high"], "a": 1},
    {"q": "The main purpose of a trading journal is...", "opts": ["Bragging rights", "Finding and fixing patterns in your own behavior", "Tax reporting", "Copying other traders"], "a": 1},
    {"q": "If BTC dominance is rising during a market dip, alts usually...", "opts": ["Outperform BTC", "Bleed harder than BTC", "Stay flat", "Pump"], "a": 1},
    {"q": "A doji candle at a key level after a long trend suggests...", "opts": ["Strong continuation", "Indecision - possible exhaustion", "Guaranteed reversal", "Low volume only"], "a": 1},
    {"q": "Risk:Reward of 1:3 means...", "opts": ["You risk 3 to make 1", "You risk 1 to make 3", "You need 3 wins to break even", "3% account risk"], "a": 1},
    {"q": "The 'spring' in Wyckoff accumulation is designed to...", "opts": ["Confirm the breakdown", "Grab liquidity below support before markup", "Mark the top", "Fill the FVG"], "a": 1},
    {"q": "Averaging into a loser WITHOUT a plan is called...", "opts": ["DCA strategy", "Martingale / hope trading", "Scaling in", "Hedging"], "a": 1},
    {"q": "The best time to size up your risk per trade is...", "opts": ["During a losing streak to recover", "After proving consistency over many trades", "When you feel confident", "When leverage is cheap"], "a": 1},
    {"q": "A green (bullish) candle means...", "opts": ["Price closed below its open", "Price closed above its open", "Volume was high", "Buyers are guaranteed to win next candle"], "a": 1},
    {"q": "The wick (shadow) of a candle shows...", "opts": ["The open and close", "The highest and lowest prices reached", "The volume traded", "The funding rate"], "a": 1},
    {"q": "Support is a zone where...", "opts": ["Price tends to find sellers", "Price tends to find buyers", "Volume is always low", "The trend must reverse"], "a": 1},
    {"q": "Resistance is a zone where...", "opts": ["Price tends to find buyers", "Price tends to find sellers", "Liquidations happen", "The chart ends"], "a": 1},
    {"q": "When strong support finally breaks, it often...", "opts": ["Becomes resistance", "Disappears forever", "Becomes the new POC", "Doubles in strength"], "a": 0},
    {"q": "An uptrend is defined by...", "opts": ["Higher highs and higher lows", "Lower highs and lower lows", "Equal highs", "High volume only"], "a": 0},
    {"q": "A market order...", "opts": ["Waits at your chosen price", "Executes immediately at the best available price", "Never pays fees", "Can't be filled in a fast market"], "a": 1},
    {"q": "A limit order...", "opts": ["Executes immediately", "Rests at your chosen price until filled", "Guarantees a fill", "Only works on spot"], "a": 1},
    {"q": "10x leverage on a position means...", "opts": ["Profits only are multiplied by 10", "Both profits AND losses move 10x faster", "Your risk stays the same", "You can't get liquidated"], "a": 1},
    {"q": "Liquidation happens when...", "opts": ["You close a trade in profit", "Your margin can no longer cover the position's loss", "The exchange goes offline", "Funding turns negative"], "a": 1},
    {"q": "The main difference between spot and futures is...", "opts": ["Spot has more leverage", "In spot you own the asset; futures are contracts", "Futures can't be shorted", "Spot has funding fees"], "a": 1},
    {"q": "'Shorting' means...", "opts": ["Buying and holding", "Profiting when price goes down", "Trading small size", "Selling only at a loss"], "a": 1},
    {"q": "Market cap of a coin is...", "opts": ["Its price", "Price multiplied by circulating supply", "Total volume traded", "The exchange's valuation"], "a": 1},
    {"q": "A coin at $0.10 is cheaper than a coin at $100...", "opts": ["Always true", "Not necessarily - market cap matters, not unit price", "True if volume is high", "True only for memecoins"], "a": 1},
    {"q": "High trading volume on a breakout usually means...", "opts": ["The breakout is more likely to be real", "The breakout will fail", "Nothing", "Fees will be higher"], "a": 0},
    {"q": "A higher timeframe (like 1D or 1W) generally gives...", "opts": ["More noise", "More reliable levels than lower timeframes", "Faster signals", "Worse data"], "a": 1},
    {"q": "FOMO (fear of missing out) usually makes traders...", "opts": ["Enter early with a plan", "Buy late into extended moves without a plan", "Reduce their size", "Wait for confirmation"], "a": 1},
    {"q": "A stablecoin like USDT is designed to...", "opts": ["Grow 10% a year", "Stay pegged to $1", "Track Bitcoin's price", "Pay staking rewards always"], "a": 1},
    {"q": "If you risk $100 to potentially make $300, your R:R is...", "opts": ["1:3", "3:1", "1:1", "0.3"], "a": 0},
    {"q": "'DYOR' stands for...", "opts": ["Daily yield on returns", "Do your own research", "Don't yield on resistance", "Dollar yearly output rate"], "a": 1},
    {"q": "A moving average smooths out...", "opts": ["Volume", "Price data over a set number of candles", "Funding rates", "Order book depth"], "a": 1},
    {"q": "Price trading above a rising 200 EMA generally suggests...", "opts": ["A downtrend", "A long-term uptrend context", "A range", "Nothing at all"], "a": 1},
    {"q": "'Buy the rumor, sell the news' refers to...", "opts": ["Buying after news drops", "Price often running up before an event and dumping on it", "Only trading news coins", "Avoiding all news"], "a": 1},
    {"q": "Slippage is...", "opts": ["A type of chart pattern", "The difference between expected and actual fill price", "An exchange fee", "A stop loss error"], "a": 1},
    {"q": "The order book shows...", "opts": ["Past trades only", "Resting buy and sell limit orders", "Liquidation levels", "Whale wallets"], "a": 1},
    {"q": "Dollar-cost averaging (DCA) means...", "opts": ["Going all-in at one price", "Buying in planned portions over time or a price zone", "Doubling down on losers randomly", "Only buying dips"], "a": 1},
    {"q": "Keeping most long-term holdings in self-custody protects you from...", "opts": ["Price drops", "Exchange failures and hacks", "Taxes", "Funding fees"], "a": 1},
    {"q": "Overtrading usually results in...", "opts": ["More profit from more chances", "Fees and emotional mistakes eating your edge", "Better discipline", "Faster learning only"], "a": 1},
    {"q": "A trading plan should be written...", "opts": ["After the trade closes", "Before entering the trade", "Only for big positions", "Never - stay flexible"], "a": 1},
    {"q": "If a trade setup invalidates before entry, the correct move is...", "opts": ["Enter anyway at a worse price", "Skip it - no setup, no trade", "Double the size", "Flip direction randomly"], "a": 1},
    {"q": "Paper trading is...", "opts": ["Trading with fake money to practice", "Trading paper industry stocks", "A scam", "Only for beginners with no value"], "a": 0},
    {"q": "Portfolio diversification helps because...", "opts": ["Every coin pumps together", "It reduces the damage any single asset can do", "It guarantees profit", "Exchanges require it"], "a": 1},
    {"q": "ATH stands for...", "opts": ["Average trading hours", "All-time high", "Automated trade handler", "Above the high"], "a": 1},
    {"q": "A 50% loss on your account requires what gain to recover?", "opts": ["50%", "75%", "100%", "25%"], "a": 2},
]


def _quiz_pick(used: set = None):
    import random as _r
    if used is None:
        used = set()
    pool = [i for i in range(len(QUIZ_BANK)) if i not in used]
    if not pool:
        used.clear()
        pool = list(range(len(QUIZ_BANK)))
    idx = _r.choice(pool)
    used.add(idx)
    return QUIZ_BANK[idx], used


def _quiz_embed(q, score=None, answered=None):
    desc = f"**{q['q']}**"
    if score is not None:
        desc += f"\n\n*Streak score: {score}/{answered} this session*"
    embed = discord.Embed(title="\U0001F9E0 TA Quiz", description=desc, color=NAVY)
    embed.set_footer(text="Scient Lounge - setups, not signals")
    return embed


def _result_embed(correct: bool, right_letter: str, score: int, answered: int):
    if correct:
        title = "\u2705 Correct! Nice read."
        color = GREEN
    else:
        title = f"\u274C Not quite - the answer was {right_letter}."
        color = RED
    embed = discord.Embed(title=title, description=f"*Score: {score}/{answered} this session*", color=color)
    embed.set_footer(text="Scient Lounge - keep going")
    return embed


class QuizNextView(View):
    def __init__(self, score: int, answered: int, used: set = None):
        super().__init__(timeout=3600)
        self.score = score
        self.answered = answered
        self.used = used if used is not None else set()
        btn = Button(label="Next question \u25B6", style=discord.ButtonStyle.primary)
        btn.callback = self.next_q
        self.add_item(btn)

    async def next_q(self, interaction: discord.Interaction):
        q, used = _quiz_pick(self.used)
        await interaction.response.edit_message(embed=_quiz_embed(q, self.score, self.answered), view=QuizSessionView(q, self.score, self.answered, used))


class QuizSessionView(View):
    def __init__(self, q: dict, score: int, answered: int, used: set = None):
        super().__init__(timeout=3600)
        self.q = q
        self.score = score
        self.answered = answered
        self.used = used if used is not None else set()
        for i, opt in enumerate(q["opts"]):
            self.add_item(QuizSessionButton(i, opt, self))


class QuizSessionButton(Button):
    def __init__(self, idx: int, label: str, sview: "QuizSessionView"):
        super().__init__(label=f"{chr(65 + idx)}. {label}"[:80], style=discord.ButtonStyle.secondary)
        self.idx = idx
        self.sview = sview

    async def callback(self, interaction: discord.Interaction):
        correct = self.idx == self.sview.q["a"]
        score = self.sview.score + (1 if correct else 0)
        answered = self.sview.answered + 1
        right = chr(65 + self.sview.q["a"])
        await interaction.response.edit_message(embed=_result_embed(correct, right, score, answered), view=QuizNextView(score, answered, self.sview.used))


class QuizView(View):
    def __init__(self, q: dict, message=None):
        super().__init__(timeout=3600)
        self.q = q
        self.correct = q["a"]
        self.answered = set()
        self.message = message
        for i, opt in enumerate(q["opts"]):
            self.add_item(QuizButton(i, opt, self))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class QuizButton(Button):
    def __init__(self, idx: int, label: str, qview: "QuizView"):
        super().__init__(label=f"{chr(65 + idx)}. {label}"[:80], style=discord.ButtonStyle.secondary)
        self.idx = idx
        self.qview = qview

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id in self.qview.answered:
            await interaction.response.send_message("You already answered this one - hit Next question on your result to keep going.", ephemeral=True)
            return
        self.qview.answered.add(interaction.user.id)
        correct = self.idx == self.qview.correct
        score = 1 if correct else 0
        right = chr(65 + self.qview.correct)
        await interaction.response.send_message(embed=_result_embed(correct, right, score, 1), view=QuizNextView(score, 1, {QUIZ_BANK.index(self.qview.q)} if self.qview.q in QUIZ_BANK else set()), ephemeral=True)


@bot.tree.command(name="quiz", description="Random TA quiz question - test yourself")
async def quiz(interaction: discord.Interaction):
    q, _ = _quiz_pick()
    embed = _quiz_embed(q)
    embed.description += "\n\n*Answer is private - only you see your result. Keep the streak going with Next question.*"
    view = QuizView(q)
    await interaction.response.send_message(embed=embed, view=view)
    try:
        view.message = await interaction.original_response()
    except Exception:
        pass


@bot.tree.command(name="coinflip", description="Flip a coin (results may teach you about 50% win rates)")
async def coinflip(interaction: discord.Interaction):
    import random as _r
    result = _r.choice(["HEADS", "TAILS"])
    emoji = "\U0001FA99"
    lessons = [
        "50% win rate + 2R average winner = profitable system. It's never about one flip.",
        "Even a coin gets 5 heads in a row sometimes. Losing streaks don't mean your edge is gone.",
        "You can't predict one flip. You can manage what you risk on it.",
        "The flip is random. Your position size shouldn't be.",
    ]
    embed = discord.Embed(title=f"{emoji} {result}", description=_r.choice(lessons), color=GOLD if result == "HEADS" else NAVY)
    embed.set_footer(text="Scient Lounge - Quant")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dominance", description="BTC dominance + total market cap")
async def dominance(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coingecko.com/api/v3/global", timeout=15) as resp:
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Market data unavailable right now.")
        return
    try:
        d = data["data"]
        btc_d = d["market_cap_percentage"]["btc"]
        eth_d = d["market_cap_percentage"].get("eth", 0)
        mcap = d["total_market_cap"]["usd"]
        mcap_chg = d.get("market_cap_change_percentage_24h_usd", 0)
    except Exception:
        await interaction.followup.send("Couldn't parse dominance data.")
        return
    others = 100 - btc_d - eth_d
    arrow = "\U0001F4C8" if mcap_chg >= 0 else "\U0001F4C9"
    embed = discord.Embed(title="Market Dominance", color=GOLD, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**BTC:** {btc_d:.1f}% | **ETH:** {eth_d:.1f}% | **Others:** {others:.1f}%\n"
        f"**Total market cap:** ${mcap / 1e12:.2f}T {arrow} {mcap_chg:+.2f}% (24h)\n\n"
        f"*BTC dominance rising = money rotating to BTC. Falling = alts catching bids.*"
    )
    embed.set_footer(text="Scient Lounge - CoinGecko")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="vol", description="Volatility snapshot - how much does this coin move?")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL")
async def vol(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/klines", params={"symbol": pair, "interval": "4h", "limit": 60}, timeout=15) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"Couldn't find **{symbol}**.")
                    return
                klines = await resp.json()
    except Exception:
        await interaction.followup.send("Data unavailable right now.")
        return
    if len(klines) < 20:
        await interaction.followup.send(f"Not enough data for **{symbol}**.")
        return
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    last = closes[-1]
    trs = []
    for i in range(1, len(klines)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr14 = sum(trs[-14:]) / 14
    atr_pct = atr14 / last * 100
    day_ranges = []
    for i in range(0, len(klines) - 6, 6):
        chunk_h = max(highs[i:i + 6])
        chunk_l = min(lows[i:i + 6])
        day_ranges.append((chunk_h - chunk_l) / chunk_l * 100)
    avg_day = sum(day_ranges) / len(day_ranges) if day_ranges else 0
    rating = "\U0001F525 High" if atr_pct > 2.5 else "\U0001F321\uFE0F Moderate" if atr_pct > 1.2 else "\U0001F9CA Low"
    embed = discord.Embed(title=f"Volatility - {symbol}", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**ATR (14, 4H):** {fnum(atr14)} ({atr_pct:.2f}% of price)\n"
        f"**Avg daily range (10d):** {avg_day:.2f}%\n"
        f"**Volatility:** {rating}\n\n"
        f"*Rule of thumb: your SL should live outside the noise - tighter than ATR usually means getting wicked out.*"
    )
    embed.set_footer(text="Scient Lounge - Binance")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="levels", description="Auto-detected support & resistance levels")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL", timeframe="Timeframe for structure")
@app_commands.choices(timeframe=[app_commands.Choice(name=k, value=k) for k in ("1H", "4H", "1D")])
async def levels(interaction: discord.Interaction, coin: str, timeframe: app_commands.Choice[str] = None):
    await interaction.response.defer()
    tfv = timeframe.value if timeframe else "4H"
    interval = {"1H": "1h", "4H": "4h", "1D": "1d"}[tfv]
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/klines", params={"symbol": pair, "interval": interval, "limit": 300}, timeout=15) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"Couldn't find **{symbol}**.")
                    return
                klines = await resp.json()
    except Exception:
        await interaction.followup.send("Data unavailable right now.")
        return
    if len(klines) < 50:
        await interaction.followup.send(f"Not enough data for **{symbol}** on {tfv}.")
        return
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    last = float(klines[-1][4])
    piv = 5
    raw = []
    for i in range(piv, len(klines) - piv):
        if highs[i] == max(highs[i - piv:i + piv + 1]):
            raw.append(highs[i])
        if lows[i] == min(lows[i - piv:i + piv + 1]):
            raw.append(lows[i])
    raw.sort()
    clusters = []
    tol = last * 0.006
    for lv in raw:
        if clusters and lv - clusters[-1][-1] <= tol:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])
    scored = [(sum(c) / len(c), len(c)) for c in clusters]
    res = sorted([s for s in scored if s[0] > last], key=lambda x: x[0])[:4]
    sup = sorted([s for s in scored if s[0] <= last], key=lambda x: -x[0])[:4]
    def fmt_lv(s):
        price, touches = s
        strength = "\u2B50" * min(touches, 3)
        dist = abs(price - last) / last * 100
        return f"`{fnum(price)}` {strength} ({dist:.1f}% away)"
    lines = [f"**Current price:** {fnum(last)}\n"]
    if res:
        lines.append("**Resistance above:**")
        lines += [fmt_lv(s) for s in res]
    if sup:
        lines.append("\n**Support below:**")
        lines += [fmt_lv(s) for s in sup]
    lines.append("\n*\u2B50 = number of touches (max 3 shown). Auto-detected from swing pivots - always confirm with your own chart.*")
    embed = discord.Embed(title=f"Key Levels - {symbol} ({tfv})", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Scient Lounge - swing pivot clusters")
    await interaction.followup.send(embed=embed)


def make_heatmap_image(rows: list) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    cols = 5
    nrows = (len(rows) + cols - 1) // cols
    fig, ax = plt.subplots(figsize=(11, nrows * 1.5), facecolor="#131722")
    ax.set_facecolor("#131722")
    ax.set_xlim(0, cols)
    ax.set_ylim(0, nrows)
    ax.axis("off")
    for idx, (sym, chg, vol_usd) in enumerate(rows):
        r_i = nrows - 1 - idx // cols
        c_i = idx % cols
        mag = min(abs(chg) / 8, 1.0)
        if chg >= 0:
            color = (0.05, 0.35 + 0.35 * mag, 0.25 + 0.2 * mag)
        else:
            color = (0.45 + 0.35 * mag, 0.13, 0.2)
        rect = mpatches.FancyBboxPatch((c_i + 0.03, r_i + 0.04), 0.94, 0.92, boxstyle="round,pad=0.01,rounding_size=0.03", facecolor=color, edgecolor="#131722", linewidth=2)
        ax.add_patch(rect)
        ax.text(c_i + 0.5, r_i + 0.62, sym, ha="center", va="center", color="#EAECEF", fontsize=13, fontweight="bold")
        ax.text(c_i + 0.5, r_i + 0.33, f"{chg:+.2f}%", ha="center", va="center", color="#EAECEF", fontsize=11)
    fig.suptitle("24h Market Heatmap - top volume", color="#EAECEF", fontsize=13, y=0.995)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor="#131722", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


@bot.tree.command(name="heatmap", description="24h market heatmap - top 20 coins by volume")
async def heatmap(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.binance.com/api/v3/ticker/24hr", timeout=20) as resp:
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Market data unavailable right now.")
        return
    rows = []
    for d in data:
        s = d.get("symbol", "")
        if not s.endswith("USDT") or any(x in s for x in ("UP", "DOWN", "BULL", "BEAR", "USDC", "FDUSD", "TUSD", "DAI", "EUR")):
            continue
        try:
            rows.append((s[:-4], float(d["priceChangePercent"]), float(d["quoteVolume"])))
        except Exception:
            continue
    rows.sort(key=lambda r: r[2], reverse=True)
    rows = rows[:20]
    if not rows:
        await interaction.followup.send("No data right now.")
        return
    try:
        buf = await asyncio.to_thread(make_heatmap_image, rows)
    except Exception as e:
        await interaction.followup.send(f"Heatmap rendering failed: {e}")
        return
    f = discord.File(buf, filename="heatmap.png")
    green = sum(1 for r in rows if r[1] >= 0)
    await interaction.followup.send(content=f"**Market Heatmap** - {green}/20 green (24h)", file=f)


def make_compare_image(sym1: str, sym2: str, closes1: list, closes2: list, dates: list) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n1 = [c / closes1[0] * 100 - 100 for c in closes1]
    n2 = [c / closes2[0] * 100 - 100 for c in closes2]
    fig, ax = plt.subplots(figsize=(11, 5.5), facecolor="#131722")
    ax.set_facecolor("#131722")
    ax.plot(dates, n1, color="#E8590C", linewidth=2, label=sym1)
    ax.plot(dates, n2, color="#378ADD", linewidth=2, label=sym2)
    ax.axhline(0, color="#B2B5BE", linewidth=0.6, linestyle="--", alpha=0.5)
    ax.grid(color="#1E222D", linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color("#2A2E39")
    ax.tick_params(colors="#B2B5BE", labelsize=8)
    ax.yaxis.tick_right()
    leg = ax.legend(facecolor="#131722", edgecolor="#2A2E39", labelcolor="#EAECEF", fontsize=10)
    ax.set_title(f"{sym1} vs {sym2} - 30d performance (%)", color="#EAECEF", fontsize=12, loc="left", pad=10)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor="#131722", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


@bot.tree.command(name="compare", description="Compare 30-day performance of two coins")
@app_commands.describe(coin1="First coin, e.g. BTC", coin2="Second coin, e.g. ETH")
async def compare(interaction: discord.Interaction, coin1: str, coin2: str):
    await interaction.response.defer()
    syms = []
    for c in (coin1, coin2):
        s = re.sub(r"[^A-Za-z0-9]", "", c).upper()
        syms.append(s if s.endswith("USDT") else f"{s}USDT")
    results = []
    try:
        async with aiohttp.ClientSession() as session:
            for pair in syms:
                async with session.get("https://api.binance.com/api/v3/klines", params={"symbol": pair, "interval": "1d", "limit": 30}, timeout=15) as resp:
                    if resp.status != 200:
                        await interaction.followup.send(f"Couldn't find **{pair[:-4]}**.")
                        return
                    results.append(await resp.json())
    except Exception:
        await interaction.followup.send("Data unavailable right now.")
        return
    n = min(len(results[0]), len(results[1]))
    if n < 5:
        await interaction.followup.send("Not enough data to compare.")
        return
    k1, k2 = results[0][-n:], results[1][-n:]
    closes1 = [float(k[4]) for k in k1]
    closes2 = [float(k[4]) for k in k2]
    from datetime import datetime as _dt
    dates = [_dt.fromtimestamp(k[0] / 1000) for k in k1]
    s1, s2 = syms[0][:-4], syms[1][:-4]
    try:
        buf = await asyncio.to_thread(make_compare_image, s1, s2, closes1, closes2, dates)
    except Exception as e:
        await interaction.followup.send(f"Chart rendering failed: {e}")
        return
    p1 = (closes1[-1] / closes1[0] - 1) * 100
    p2 = (closes2[-1] / closes2[0] - 1) * 100
    winner = s1 if p1 > p2 else s2
    f = discord.File(buf, filename=f"{s1}_vs_{s2}.png")
    await interaction.followup.send(content=f"**{s1}** {p1:+.1f}% vs **{s2}** {p2:+.1f}% (30d) - **{winner}** leading", file=f)


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(title="Quant - Command Guide", color=NAVY)
    embed.description = "Everything you can do with the bot. All replies marked *private* are visible only to you."
    embed.add_field(
        name="\U0001F4CA Market Tools",
        value=(
            "`/price` - live price, 24h change, high/low\n"
            "`/chart` - candlestick chart with EMAs (15m to 1W)\n"
            "`/funding` - perp funding rate + market lean\n"
            "`/oi` - open interest + 24h change\n"
            "`/gainers` / `/losers` - top 5 movers of the day\n"
            "`/heatmap` - 24h market heatmap image\n"
            "`/levels` - auto support & resistance levels\n"
            "`/vol` - volatility snapshot (ATR, daily range)\n"
            "`/dominance` - BTC dominance + market cap\n"
            "`/compare` - 30d performance, coin vs coin\n"
            "`/convert` - coin to USD (or USD to coin)\n"
            "`/fear` - Fear & Greed index"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F9EE Calculators *(private)*",
        value=(
            "`/pnl` - position size from account, risk %, entry, SL\n"
            "`/liq` - estimated liquidation price for any leverage"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F4C8 Trades & Results *(private)*",
        value=(
            "`/open` - all live positions (futures + spot)\n"
            "`/recent` - latest closed trades with results\n"
            "`/stats` - any analyst's futures scorecard\n"
            "`/spot_stats` - any analyst's spot scorecard"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F3B2 Fun & Learning",
        value=(
            "`/quiz` - random TA question, test yourself\n"
            "`/coinflip` - flip a coin, learn about win rates"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F514 Alerts",
        value=(
            "`/follow` / `/unfollow` - get pinged when an analyst posts\n"
            "Or use the buttons in #follow-analysts-roles"
        ),
        inline=False,
    )
    embed.set_footer(text="Scient Lounge - Quant")
    return embed


@bot.tree.command(name="setup_help_panel", description="(Admin) Post the public command guide in this channel and pin it")
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


@bot.tree.command(name="xpost", description="Share an X post into the X feed channel")
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
    if not re.search(r"(twitter|x|fxtwitter|vxtwitter)\.com/", link, flags=re.I):
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


@bot.tree.command(name="follow", description="Get pinged when an analyst posts a trade")
@app_commands.describe(analyst="Which analyst to follow")
@app_commands.choices(analyst=ANALYST_CHOICES)
async def follow(interaction: discord.Interaction, analyst: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    cfg = ANALYSTS.get(analyst.value)
    rid = cfg and cfg.get("ping_role_id")
    if not rid:
        await interaction.followup.send(f"Pings aren't set up for **{analyst.name}** yet.", ephemeral=True)
        return
    role = interaction.guild.get_role(rid)
    if role is None:
        await interaction.followup.send("That ping role no longer exists.", ephemeral=True)
        return
    try:
        await interaction.user.add_roles(role, reason="Analyst follow opt-in")
    except discord.Forbidden:
        await interaction.followup.send("I don't have permission to assign that role.", ephemeral=True)
        return
    await interaction.followup.send(f"You'll now be pinged for **{analyst.name}**'s calls.", ephemeral=True)


@bot.tree.command(name="unfollow", description="Stop getting pinged for an analyst")
@app_commands.describe(analyst="Which analyst to unfollow")
@app_commands.choices(analyst=ANALYST_CHOICES)
async def unfollow(interaction: discord.Interaction, analyst: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    cfg = ANALYSTS.get(analyst.value)
    rid = cfg and cfg.get("ping_role_id")
    if not rid:
        await interaction.followup.send(f"No ping role set for **{analyst.name}**.", ephemeral=True)
        return
    role = interaction.guild.get_role(rid)
    if role and role in interaction.user.roles:
        try:
            await interaction.user.remove_roles(role, reason="Analyst unfollow")
        except discord.Forbidden:
            await interaction.followup.send("I can't remove that role.", ephemeral=True)
            return
    await interaction.followup.send(f"You'll no longer be pinged for **{analyst.name}**.", ephemeral=True)


@bot.tree.command(name="board", description="(Admin) Rebuild the open-positions board")
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


@bot.tree.command(name="spot_board", description="(Admin) Rebuild the spot plays board")
async def spot_board_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not OPEN_BOARD_CHANNEL_ID:
        await interaction.followup.send("Set OPEN_BOARD_CHANNEL_ID first.", ephemeral=True)
        return
    save_spot_board({})
    await refresh_spot_board()
    await interaction.followup.send("Spot board rebuilt.", ephemeral=True)


@bot.tree.command(name="stats", description="Trade journal scorecard (futures)")
@app_commands.describe(analyst="Whose stats? (blank = your own)")
async def stats(interaction: discord.Interaction, analyst: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    target = analyst or interaction.user
    data = load_trades()
    mine = [t for t in data.values() if t.get("analyst_id") == target.id]
    total = len(mine)
    closed = [t for t in mine if t.get("closed")]
    wins = [t for t in closed if t["result"] == "WIN"]
    losses = [t for t in closed if t["result"] == "LOSS"]
    be = [t for t in closed if t["result"] == "BE"]
    invalid = [t for t in closed if t["result"] == "INVALID"]
    decided = len(wins) + len(losses)
    wr = (len(wins) / decided * 100) if decided else 0
    tp1_rate = (sum(1 for t in mine if t.get("tp1_hit")) / total * 100) if total else 0
    rs = [t["result_r"] for t in closed if isinstance(t.get("result_r"), (int, float))]
    total_r = sum(rs) if rs else None
    avg_r = (sum(rs) / len(rs)) if rs else None
    best = max(rs) if rs else None
    worst = min(rs) if rs else None
    try:
        ecolor = discord.Color.from_str(analyst_color_hex(target))
    except Exception:
        ecolor = NAVY
    embed = discord.Embed(title=f"Scorecard - {target.display_name}", color=ecolor)
    embed.add_field(name="Total setups", value=str(total), inline=True)
    embed.add_field(name="Closed", value=str(len(closed)), inline=True)
    embed.add_field(name="Open", value=str(total - len(closed)), inline=True)
    embed.add_field(name="Win rate", value=(f"{wr:.0f}% ({len(wins)}W/{len(losses)}L)" if decided else "-"), inline=True)
    embed.add_field(name="TP1 hit rate", value=(f"{tp1_rate:.0f}%" if total else "-"), inline=True)
    embed.add_field(name="BE / Invalid", value=f"{len(be)} / {len(invalid)}", inline=True)
    embed.add_field(name="Total R", value=(f"{total_r:+.2f}R" if total_r is not None else "-"), inline=True)
    embed.add_field(name="Avg R", value=(f"{avg_r:+.2f}R" if avg_r is not None else "-"), inline=True)
    embed.add_field(name="Graded on", value=(f"{len(rs)} closed" if rs else "-"), inline=True)
    embed.add_field(name="Best", value=(f"{best:+g}R" if best is not None else "-"), inline=True)
    embed.add_field(name="Worst", value=(f"{worst:+g}R" if worst is not None else "-"), inline=True)
    embed.set_footer(text="Scient Lounge - Journal")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="spot_stats", description="Spot plays scorecard")
@app_commands.describe(analyst="Whose stats? (blank = your own)")
async def spot_stats(interaction: discord.Interaction, analyst: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    target = analyst or interaction.user
    data = load_spot()
    mine = [p for p in data.values() if p.get("analyst_id") == target.id]
    total = len(mine)
    closed = [p for p in mine if p.get("closed")]
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
    embed = discord.Embed(title=f"Spot Scorecard - {target.display_name}", color=ecolor)
    embed.add_field(name="Total plays", value=str(total), inline=True)
    embed.add_field(name="Closed", value=str(len(closed)), inline=True)
    embed.add_field(name="Active", value=str(total - len(closed)), inline=True)
    embed.add_field(name="Win rate", value=(f"{wr:.0f}% ({len(wins)}W/{len(losses)}L)" if decided else "-"), inline=True)
    embed.add_field(name="BE / Invalid", value=f"{len(be)} / {len(invalid)}", inline=True)
    results = [p.get("result_pct") for p in closed if p.get("result_pct")]
    embed.add_field(name="Results", value=(", ".join(results[:10]) if results else "-"), inline=False)
    embed.set_footer(text="Scient Lounge - Spot Journal")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="xtest", description="(Admin) Test the X auto-feed connection")
async def xtest(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not TWITTERAPIS_KEY:
        await interaction.followup.send("TWITTERAPIS_KEY not set in .env.", ephemeral=True)
        return
    query = f"from:{X_AUTO_USERNAME} -filter:replies -filter:retweets"
    url = "https://api.twitterapis.com/twitter/tweet/advanced_search"
    params = {"query": query, "product": "Latest"}
    headers = {"Authorization": f"Bearer {TWITTERAPIS_KEY}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=30) as resp:
                status = resp.status
                data = await resp.json()
    except Exception as e:
        await interaction.followup.send(f"Error: {e}", ephemeral=True)
        return
    if status != 200:
        await interaction.followup.send(f"API returned {status}: {str(data)[:400]}", ephemeral=True)
        return
    tweets = data.get("tweets", []) or []
    if not tweets:
        await interaction.followup.send("Connected OK, but no tweets returned for that query.", ephemeral=True)
        return
    latest = tweets[0]
    await interaction.followup.send(
        f"Connected. Latest tweet from @{X_AUTO_USERNAME}:\nID: {latest.get('id')}\nText: {str(latest.get('text'))[:200]}",
        ephemeral=True,
    )


bot.run(BOT_TOKEN)

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
# Pro roles that unlock full access (either one triggers the Pro welcome DM)
PRO_ROLE_IDS = {1500476858477576374, 1484576832362905630}  # Scient Pass (referral), Scient Pro (payment)
SUB_ROLE_ID = 1484576832362905630  # role granted/removed by the subscription system (Scient Pro)
SUB_PLANS = {
    "1month":  {"days": 30,  "price": 50,  "label": "Scient Pro - 1 Month ($50)"},
    "3months": {"days": 90,  "price": 140, "label": "Scient Pro - 3 Months ($140)"},
    "6months": {"days": 180, "price": 250, "label": "Scient Pro - 6 Months ($250)"},
}
SUB_REMINDER_DAYS = 3  # DM a renewal reminder this many days before expiry
FREE_ALERT_LIMIT = 5      # max active alerts for non-pro members
ALERT_CHECK_MIN = 3       # how often (minutes) to check alert prices
LIQ_CHANNEL_ID = 1535755722678341733  # #liquidations feed
LIQ_MIN_USD = 250_000     # Binance splits big liquidations into smaller orders, so $1M+ almost never fires
LIQ_BIG_USD = 1_000_000   # always posts, even when throttling
LIQ_MAX_PER_MIN = 8       # soft throttle: past this in a minute, only LIQ_BIG_USD+ gets through
LIQ_BYBIT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                     "BNBUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT")

# Traditional markets (Yahoo Finance) - map friendly names to Yahoo symbols
TRADFI_SYMBOLS = {
    "SPX": "^GSPC", "SPX500": "^GSPC", "SP500": "^GSPC", "ES": "^GSPC",
    "NASDAQ": "^IXIC", "NDX": "^IXIC", "NQ": "^IXIC",
    "DOW": "^DJI", "DJI": "^DJI",
    "DXY": "DX-Y.NYB", "DOLLAR": "DX-Y.NYB",
    "GOLD": "GC=F", "XAU": "GC=F", "GC": "GC=F",
    "SILVER": "SI=F", "XAG": "SI=F",
    "OIL": "CL=F", "USOIL": "CL=F", "WTI": "CL=F", "CL": "CL=F",
    "VIX": "^VIX",
}
QUANT_CHANNEL_ID = 0  # paste #quant-terminal channel ID here (0 = commands work everywhere)

# ---- News wire config (TreeNews) ----
NEWS_CHANNEL_ID = 1535048677406539797
NEWS_PING_ROLE_ID = 1535053641378037760  # pinged on URGENT news only
NEWS_ENABLED = False  # TreeNews realtime wire OFF - news now flows via the daily digest only
NEWS_WS_URL = "wss://news.treeofalpha.com/ws"
# Curated TG channels polled for the daily digest (web preview, no API needed)
TG_NEWS_CHANNELS = ("dbnewsdelayed", "ZoomerfiedNews", "unfolded")
TG_NEWS_POLL_MIN = 20
DIGEST_SPONSOR_WORDS = ("sponsor", "sponsored", "#ad", "promo code", "use code", "partnered with", "in partnership")
NEWS_COINS = {"BTC", "ETH", "SOL", "BITCOIN", "ETHEREUM", "SOLANA"}
NEWS_KEYWORDS = {
    "sec ", "etf", "fed ", "fomc", "rate cut", "rate hike", "cpi", "interest rate",
    "hack", "hacked", "exploit", "exploited", "breach", "stolen",
    "delist", "bankrupt", "bankruptcy", "halted",
    "binance", "coinbase", "tether", "blackrock", "microstrategy",
    "white house", "trump", "congress", "treasury",
}
# word-boundary sensitive terms are written with a trailing space above ("sec ", "fed ")
NEWS_MIN_COIN_ONLY = True  # if a headline only matched via coin tags, require a MAJOR coin (already enforced)
# Trusted sources: their headlines ALWAYS pass (bypass keyword filter)
NEWS_SOURCE_WHITELIST = ("tier10k", "news_of_alpha", "tree of alpha", "treeofalpha", "zoomerfied", "unfolded")
# Low-quality sources: their items are ALWAYS dropped
NEWS_SOURCE_BLACKLIST = ("cointelegraph", "wu blockchain", "wublockchain")

# ---- Telegram (Scient Club) config ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHANNEL = "@scientclub"
TG_ENABLED = True
DISCORD_INVITE = "https://discord.gg/scientlounge"
TG_BRIEF_UTC_HOUR = 6   # 12:00 PM IST = 06:30 UTC
TG_BRIEF_UTC_MIN = 30
TG_MACRO_CORE = ("sec", "etf", "fed", "fomc", "cpi", "rate cut", "rate hike")
TG_DIGEST_UTC_HOUR = 14   # 8:00 PM IST = 14:30 UTC
TG_DIGEST_UTC_MIN = 30
TG_DIGEST_MAX = 10
TG_MOVE_SYMBOLS = ("BTC", "ETH")
TG_MOVE_THRESHOLD = 1.5      # % rolling 1h move (live) that triggers a chart post
TG_MOVE_COOLDOWN_MIN = 90    # min minutes between move alerts per symbol
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
SPOT_STATUSES = ["WATCHING", "ACCUMULATING", "HOLDING", "TRIMMED", "DISTRIBUTING"]
ANALYST_CHOICES = [app_commands.Choice(name=k.capitalize(), value=k) for k in ANALYSTS.keys()]

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _save(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)  # atomic on POSIX - never leaves a half-written file


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
DIGEST_FILE = Path(__file__).with_name("news_digest.json")
def load_digest() -> dict: return _load(DIGEST_FILE)
def save_digest(d: dict): _save(DIGEST_FILE, d)
SUBS_FILE = Path(__file__).with_name("subscriptions.json")
def load_subs() -> dict: return _load(SUBS_FILE)
def save_subs(d: dict): _save(SUBS_FILE, d)
ALERTS_FILE = Path(__file__).with_name("alerts.json")
def load_alerts() -> dict: return _load(ALERTS_FILE)
def save_alerts(d: dict): _save(ALERTS_FILE, d)
WATCHLIST_FILE = Path(__file__).with_name("watchlists.json")
def load_watchlists() -> dict: return _load(WATCHLIST_FILE)
def save_watchlists(d: dict): _save(WATCHLIST_FILE, d)


def member_is_pro(member) -> bool:
    try:
        return bool({r.id for r in member.roles} & PRO_ROLE_IDS) or member.guild_permissions.administrator
    except Exception:
        return False


def parse_sl(text):
    """Smart SL parser. '63000' -> ('63000', None). '4h close below 63000' -> ('63000', '4h close below').
    Takes the LAST standalone number as the level; the rest becomes the condition."""
    if not text:
        return None, None
    s = str(text).strip()
    matches = list(re.finditer(r"(?<![\w.])(\d[\d,]*\.?\d*)(?![\w])", s))
    if not matches:
        return None, None
    last = matches[-1]
    num = last.group(1).replace(",", "")
    cond = (s[:last.start()] + s[last.end():]).strip(" -:@")
    cond = re.sub(r"\s+", " ", cond).strip()[:60]
    return num, (cond or None)


def is_analyst(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    return any(r.name == ANALYST_ROLE_NAME for r in interaction.user.roles)


# ================= Unified market data (Binance -> Bybit fallback) =================
# Bybit intervals differ from Binance: map them.
_BYBIT_IV = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "1d": "D", "1w": "W"}


async def _get_json(session, url, params=None, timeout=15):
    try:
        async with session.get(url, params=params, timeout=timeout) as r:
            if r.status != 200:
                return None
            return await r.json()
    except Exception:
        return None


async def md_klines(pair: str, interval: str, limit: int = 220):
    """Return list of [openTime, o, h, l, c, v, ...] Binance-style. Tries Binance then Bybit."""
    async with aiohttp.ClientSession() as s:
        data = await _get_json(s, "https://api.binance.com/api/v3/klines",
                               {"symbol": pair, "interval": interval, "limit": limit}, 20)
        if data and isinstance(data, list) and len(data) > 0:
            return data
        # Bybit fallback (spot). Bybit returns newest-first; reverse to oldest-first.
        biv = _BYBIT_IV.get(interval)
        if not biv:
            return None
        bd = await _get_json(s, "https://api.bybit.com/v5/market/kline",
                             {"category": "spot", "symbol": pair, "interval": biv, "limit": min(limit, 1000)}, 20)
        try:
            rows = bd["result"]["list"]
            if not rows:
                return None
            out = []
            for r in reversed(rows):
                # Bybit: [start, open, high, low, close, volume, turnover]
                out.append([int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), 0, 0, 0, 0, 0, 0])
            return out
        except Exception:
            return None


async def md_ticker24(pair: str):
    """Return dict with lastPrice, priceChangePercent, highPrice, lowPrice, quoteVolume. Binance then Bybit."""
    async with aiohttp.ClientSession() as s:
        d = await _get_json(s, "https://api.binance.com/api/v3/ticker/24hr", {"symbol": pair}, 15)
        if d and "lastPrice" in d:
            return {
                "lastPrice": float(d["lastPrice"]), "priceChangePercent": float(d["priceChangePercent"]),
                "highPrice": float(d["highPrice"]), "lowPrice": float(d["lowPrice"]),
                "quoteVolume": float(d["quoteVolume"]), "source": "Binance",
            }
        bd = await _get_json(s, "https://api.bybit.com/v5/market/tickers", {"category": "spot", "symbol": pair}, 15)
        try:
            t = bd["result"]["list"][0]
            last = float(t["lastPrice"])
            return {
                "lastPrice": last, "priceChangePercent": float(t["price24hPcnt"]) * 100,
                "highPrice": float(t["highPrice24h"]), "lowPrice": float(t["lowPrice24h"]),
                "quoteVolume": float(t.get("turnover24h", 0)), "source": "Bybit",
            }
        except Exception:
            return None


async def md_price(pair: str):
    """Return float last price. Binance then Bybit."""
    async with aiohttp.ClientSession() as s:
        d = await _get_json(s, "https://api.binance.com/api/v3/ticker/price", {"symbol": pair}, 15)
        if d and "price" in d:
            return float(d["price"])
        bd = await _get_json(s, "https://api.bybit.com/v5/market/tickers", {"category": "spot", "symbol": pair}, 15)
        try:
            return float(bd["result"]["list"][0]["lastPrice"])
        except Exception:
            return None


async def md_tradfi(name: str):
    """Fetch a traditional-market quote from Yahoo Finance. name is a friendly key (SPX, GOLD, DXY...)."""
    ysym = TRADFI_SYMBOLS.get(name.upper())
    if not ysym:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as s:
        d = await _get_json(s, url, {"interval": "1d", "range": "5d"}, 15)
    try:
        meta = d["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        chg = (price - prev) / prev * 100 if prev else 0.0
        return {"name": name.upper(), "ysym": ysym, "price": price, "chg": chg}
    except Exception:
        return None


async def md_coinbase_price(base: str):
    """Coinbase spot price for BASE-USD. Returns float or None."""
    url = f"https://api.exchange.coinbase.com/products/{base}-USD/ticker"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as s:
        d = await _get_json(s, url, None, 15)
    try:
        return float(d["price"])
    except Exception:
        return None


def is_tradfi(sym: str) -> bool:
    return sym.upper().replace("USDT", "") in TRADFI_SYMBOLS or sym.upper() in TRADFI_SYMBOLS


async def md_funding(pair: str):
    """Return dict rate(%), mark, nextFundingTime(s). Binance perp then Bybit linear."""
    async with aiohttp.ClientSession() as s:
        d = await _get_json(s, "https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": pair}, 15)
        if d and "lastFundingRate" in d:
            return {"rate": float(d["lastFundingRate"]) * 100, "mark": float(d["markPrice"]),
                    "next": int(d["nextFundingTime"]) // 1000, "source": "Binance"}
        bd = await _get_json(s, "https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": pair}, 15)
        try:
            t = bd["result"]["list"][0]
            return {"rate": float(t["fundingRate"]) * 100, "mark": float(t["markPrice"]),
                    "next": int(t["nextFundingTime"]) // 1000, "source": "Bybit"}
        except Exception:
            return None


async def md_oi(pair: str):
    """Return dict oi(coins), source. Binance perp then Bybit linear."""
    async with aiohttp.ClientSession() as s:
        d = await _get_json(s, "https://fapi.binance.com/fapi/v1/openInterest", {"symbol": pair}, 15)
        hist = await _get_json(s, "https://fapi.binance.com/futures/data/openInterestHist",
                               {"symbol": pair, "period": "1h", "limit": 25}, 15)
        if d and "openInterest" in d:
            oi_then = None
            if isinstance(hist, list) and len(hist) >= 24:
                try:
                    oi_then = float(hist[0]["sumOpenInterest"])
                except Exception:
                    oi_then = None
            return {"oi": float(d["openInterest"]), "oi_then": oi_then, "source": "Binance"}
        bd = await _get_json(s, "https://api.bybit.com/v5/market/open-interest",
                             {"category": "linear", "symbol": pair, "intervalTime": "1h", "limit": 25}, 15)
        try:
            rows = bd["result"]["list"]
            oi_now = float(rows[0]["openInterest"])
            oi_then = float(rows[-1]["openInterest"]) if len(rows) >= 24 else None
            return {"oi": oi_now, "oi_then": oi_then, "source": "Bybit"}
        except Exception:
            return None


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
    split = t.get("entry_split")
    split_txt = f" [{split}]" if split else ""
    if marks and not closed:
        m1 = " \u2713" if t.get("entry1_filled") else ""
        m2 = " \u2713" if t.get("entry2_filled") else ""
        return f"{e1}{m1} / {e2}{m2} (DCA{split_txt})"
    return f"{e1} / {e2} (DCA{split_txt})"


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


def spot_num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "").strip())
    except Exception:
        return None


def spot_ref_entry(p: dict):
    """Reference entry for % math: avg_entry if numeric, else DCA-zone midpoint."""
    v = spot_num(p.get("avg_entry"))
    if v:
        return v
    nums = [spot_num(x) for x in re.findall(r"\d[\d,]*\.?\d*", str(p.get("dca_zone", "")))]
    nums = [n for n in nums if n]
    if not nums:
        return None
    return sum(nums[:2]) / min(2, len(nums))


def spot_pct_text(p: dict, target) -> str:
    ref = spot_ref_entry(p)
    t = spot_num(target)
    if ref and t and ref > 0:
        return f" ({(t - ref) / ref * 100:+.0f}%)"
    return ""


def spot_sells_summary(p: dict) -> str:
    sells = p.get("sells") or []
    if not sells:
        return ""
    return " · ".join(f"{s['pct']:g}% @ {s['price']:g}" for s in sells)


def spot_weighted_exit(p: dict):
    sells = p.get("sells") or []
    tot = sum(s["pct"] for s in sells)
    if tot <= 0:
        return None
    return sum(s["pct"] * s["price"] for s in sells) / tot


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
    sl_val = f"{(t.get('sl_condition') + ' ') if t.get('sl_condition') else ''}{t['sl']}{sl_mark}"

    # Row 1: three inline columns - Entry | Stop Loss | Risk / R:R
    embed.add_field(name=f"Entry ({type_label})", value=entry_display(t) or "-", inline=True)
    embed.add_field(name="Stop Loss", value=sl_val, inline=True)
    rr = display_rr(t)
    risk_rr = fmt_risk(t.get("risk")) or "-"
    if rr:
        risk_rr += f"  ·  R:R {rr}"
    embed.add_field(name="Risk", value=risk_rr, inline=True)

    # Targets (full width)
    tps = []
    plan = t.get("tp_split") or []
    for idx, (key, hit) in enumerate((("tp1", "tp1_hit"), ("tp2", "tp2_hit"), ("tp3", "tp3_hit"))):
        if t.get(key):
            r = signed_r(t, first_num(t[key]))
            rtxt = f" ({r:.1f}R)" if r is not None else ""
            ptxt = f" [{plan[idx]:g}%]" if idx < len(plan) else ""
            tps.append(f"{t[key]}{rtxt}{ptxt}" + (" \u2705" if t.get(hit) else ""))
    if tps:
        embed.add_field(name="Targets", value=" · ".join(tps), inline=False)

    # Setup (full width)
    fw = fmt_frameworks(t)
    if t.get("setup_detail"):
        fw = f"{fw} - {t['setup_detail']}"
    if fw and fw != "-":
        embed.add_field(name="Setup", value=fw[:1020], inline=False)

    # Status (full width)
    embed.add_field(name="Status", value=full_status(t), inline=False)

    if closed and t.get("avg_exit") is not None:
        embed.add_field(name="Avg Exit", value=fnum(t["avg_exit"]), inline=True)
    if closed and t.get("close_note"):
        embed.add_field(name="Note", value=t["close_note"][:1020], inline=False)

    embed.set_author(name=t["analyst_name"], icon_url=t.get("analyst_avatar") or None)
    embed.set_footer(text=footer_with_edit(t, "Journal entry, not financial advice · Risk % = portfolio risked · Never risk more than you can afford to lose"))
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

    if closed:
        embed.add_field(name="Avg Entry", value=str(p.get("avg_entry") or "-"), inline=True)
        if p.get("avg_exit"):
            embed.add_field(name="Avg Exit", value=str(p["avg_exit"]), inline=True)
        if p.get("result_pct"):
            embed.add_field(name="Result", value=str(p["result_pct"]), inline=True)
    else:
        zone_mark = " (filled)" if p.get("zone_filled") else ""
        embed.add_field(name="DCA Zone", value=f"{p['dca_zone']}{zone_mark}", inline=True)
        if p.get("allocation"):
            embed.add_field(name="Allocation", value=fmt_risk(p["allocation"]) or "-", inline=True)
        if p.get("avg_entry"):
            embed.add_field(name="Avg Entry", value=str(p["avg_entry"]), inline=True)
        tgs = []
        for key, hit in (("t1", "t1_hit"), ("t2", "t2_hit"), ("t3", "t3_hit")):
            if p.get(key):
                tgs.append(f"{p[key]}{spot_pct_text(p, p[key])}" + (" \u2705" if p.get(hit) else ""))
        if tgs:
            embed.add_field(name="Targets", value=" · ".join(tgs), inline=False)
        if p.get("sells"):
            embed.add_field(name="Sold", value=spot_sells_summary(p), inline=False)
        if p.get("invalidation"):
            embed.add_field(name="Invalidation", value=str(p["invalidation"]), inline=False)
    embed.add_field(name="Status", value=spot_status_line(p), inline=False)
    if closed and p.get("close_note"):
        embed.add_field(name="Note", value=p["close_note"][:1020], inline=False)

    embed.set_author(name=p["analyst_name"], icon_url=p.get("analyst_avatar") or None)
    embed.set_footer(text=footer_with_edit(p, "Spot journal entry, not financial advice · Never risk more than you can afford to lose"))
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


async def tg_send_photo(photo: io.BytesIO, caption: str) -> bool:
    if not (TG_ENABLED and TELEGRAM_BOT_TOKEN and TG_CHANNEL):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    form = aiohttp.FormData()
    form.add_field("chat_id", TG_CHANNEL)
    form.add_field("caption", caption)
    form.add_field("parse_mode", "HTML")
    form.add_field("reply_markup", json.dumps({"inline_keyboard": [[{"text": "Join Scient Lounge \u2192", "url": DISCORD_INVITE}]]}))
    form.add_field("photo", photo, filename="chart.png", content_type="image/png")
    try:
        async with aiohttp.ClientSession() as session:
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
    embed.set_footer(text="Scient Lounge - 24h change")
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
    embed.set_footer(text="Scient Lounge - official BLS + Federal Reserve schedules")
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
                embed.set_footer(text="Scient Lounge - Quant alerts")
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


SOURCES_FILE = Path(__file__).with_name("tg_sources.json")
def load_sources() -> dict: return _load(SOURCES_FILE)
def save_sources(d: dict): _save(SOURCES_FILE, d)

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
            async with aiohttp.ClientSession() as session:
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


_liq_rate = {"minute": None, "count": 0}


async def post_liquidation(exchange: str, base: str, liq_type: str, notional: float, price: float):
    """Shared liquidation poster with soft throttling."""
    if notional < LIQ_MIN_USD:
        return
    ch = bot.get_channel(LIQ_CHANNEL_ID)
    if ch is None:
        return
    now = datetime.now(timezone.utc)
    minute_key = now.strftime("%Y%m%d%H%M")
    if _liq_rate["minute"] != minute_key:
        _liq_rate["minute"] = minute_key
        _liq_rate["count"] = 0
    if _liq_rate["count"] >= LIQ_MAX_PER_MIN and notional < LIQ_BIG_USD:
        return  # busy minute - let only the big ones through
    _liq_rate["count"] += 1
    if notional >= 10_000_000:
        size_emoji = "\U0001F4A5\U0001F4A5"
    elif notional >= 5_000_000:
        size_emoji = "\U0001F4A5"
    elif notional >= 1_000_000:
        size_emoji = "\U0001F525"
    else:
        size_emoji = "\U0001F534" if liq_type == "Long" else "\U0001F7E2"
    amt = f"${notional / 1e6:.2f}M" if notional >= 1_000_000 else f"${notional / 1e3:.0f}K"
    embed = discord.Embed(
        color=RED if liq_type == "Long" else GREEN,
        timestamp=now,
        description=f"{size_emoji} **{base}** {liq_type} liquidated - **{amt}** @ ${fnum(price)}",
    )
    embed.set_footer(text=f"Liquidations - {exchange}")
    try:
        await ch.send(embed=embed)
    except Exception as e:
        print(f"[liq] post error: {e}")


async def liq_binance_loop():
    await bot.wait_until_ready()
    if not LIQ_CHANNEL_ID:
        return
    backoff = 5
    url = "wss://fstream.binance.com/ws/!forceOrder@arr"
    while not bot.is_closed():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30, timeout=30) as ws:
                    print("[liq] Binance stream connected", flush=True)
                    backoff = 5
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        try:
                            o = json.loads(msg.data).get("o", {})
                            sym = o.get("s", "")
                            side = o.get("S", "")
                            qty = float(o.get("q", 0))
                            price = float(o.get("ap") or o.get("p") or 0)
                        except Exception:
                            continue
                        if not sym or price <= 0:
                            continue
                        base = sym[:-4] if sym.endswith("USDT") else sym
                        liq_type = "Long" if side == "SELL" else "Short"
                        await post_liquidation("Binance", base, liq_type, qty * price, price)
        except Exception as e:
            print(f"[liq] Binance ws error: {e}", flush=True)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)


async def liq_bybit_loop():
    await bot.wait_until_ready()
    if not LIQ_CHANNEL_ID:
        return
    backoff = 5
    url = "wss://stream.bybit.com/v5/public/linear"
    while not bot.is_closed():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=20, timeout=30) as ws:
                    await ws.send_json({"op": "subscribe",
                                        "args": [f"allLiquidation.{s}" for s in LIQ_BYBIT_SYMBOLS]})
                    print("[liq] Bybit stream connected", flush=True)
                    backoff = 5
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        try:
                            payload = json.loads(msg.data)
                        except Exception:
                            continue
                        if not str(payload.get("topic", "")).startswith("allLiquidation"):
                            continue
                        for d in payload.get("data", []) or []:
                            try:
                                sym = d.get("s", "")
                                side = d.get("S", "")   # side of the liquidated order
                                qty = float(d.get("v", 0))
                                price = float(d.get("p", 0))
                            except Exception:
                                continue
                            if not sym or price <= 0:
                                continue
                            base = sym[:-4] if sym.endswith("USDT") else sym
                            # Bybit reports the side of the closing order: Sell = long liquidated
                            liq_type = "Long" if side.lower() == "sell" else "Short"
                            await post_liquidation("Bybit", base, liq_type, qty * price, price)
        except Exception as e:
            print(f"[liq] Bybit ws error: {e}", flush=True)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)


_okx_ctval = {}


async def _load_okx_ctval():
    """OKX reports liquidation size in contracts - we need each instrument's contract value."""
    try:
        async with aiohttp.ClientSession() as s:
            d = await _get_json(s, "https://www.okx.com/api/v5/public/instruments",
                                {"instType": "SWAP"}, 20)
        for inst in (d or {}).get("data", []) or []:
            try:
                _okx_ctval[inst["instId"]] = float(inst["ctVal"])
            except Exception:
                continue
        print(f"[liq] OKX contract values loaded: {len(_okx_ctval)}", flush=True)
    except Exception as e:
        print(f"[liq] OKX ctVal load failed: {e}", flush=True)


async def liq_okx_loop():
    await bot.wait_until_ready()
    if not LIQ_CHANNEL_ID:
        return
    await _load_okx_ctval()
    backoff = 5
    url = "wss://ws.okx.com:8443/ws/v5/public"
    while not bot.is_closed():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=25, timeout=30) as ws:
                    await ws.send_json({"op": "subscribe",
                                        "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]})
                    print("[liq] OKX stream connected", flush=True)
                    backoff = 5
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        if msg.data == "pong":
                            continue
                        try:
                            payload = json.loads(msg.data)
                        except Exception:
                            continue
                        if payload.get("arg", {}).get("channel") != "liquidation-orders":
                            continue
                        for item in payload.get("data", []) or []:
                            inst = item.get("instId", "")
                            if not inst.endswith("-USDT-SWAP"):
                                continue
                            ctval = _okx_ctval.get(inst)
                            if not ctval:
                                continue
                            base = inst.split("-")[0]
                            for det in item.get("details", []) or []:
                                try:
                                    price = float(det.get("bkPx", 0))
                                    sz = float(det.get("sz", 0))
                                except Exception:
                                    continue
                                if price <= 0 or sz <= 0:
                                    continue
                                # side = side of the liquidation order: sell = long liquidated
                                liq_type = "Long" if str(det.get("side", "")).lower() == "sell" else "Short"
                                await post_liquidation("OKX", base, liq_type, sz * ctval * price, price)
        except Exception as e:
            print(f"[liq] OKX ws error: {e}", flush=True)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)


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


def build_join_dm() -> discord.Embed:
    embed = discord.Embed(
        title="Welcome to Scient Lounge \U0001F44B",
        color=NAVY,
        description=(
            "Glad to have you here. Scient Lounge is a trading community built around "
            "**process, not hype** - real setups, real tools, and a market terminal built into the server.\n\n"
            "Here's what you can start using right now, free:"
        ),
    )
    embed.add_field(
        name="\U0001F4CA The Quant Terminal",
        value=(
            "Head to **#quant-terminal** and try:\n"
            "`/price BTC` - live price\n"
            "`/chart BTC 4H` - instant candlestick chart with EMAs\n"
            "`/fear` - market Fear & Greed index\n"
            "`/heatmap` - the whole market at a glance\n"
            "Type `/help` to see everything."
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F9E0 Learn & Test Yourself",
        value="`/quiz` - trading quizzes from basics to advanced. Build a streak.",
        inline=False,
    )
    embed.add_field(
        name="\U0001F513 Want the full picture?",
        value=(
            "Members with **full access** get live analyst trade setups, entry/SL/targets, "
            "the full trade journal with verified results, priority tools, and the breaking-news wire. "
            "Check the upgrade options in the server whenever you're ready - no pressure."
        ),
        inline=False,
    )
    embed.set_footer(text="Scient Lounge - setups, not signals")
    return embed


def build_pro_dm() -> discord.Embed:
    embed = discord.Embed(
        title="You're in. Full access unlocked \U0001F680",
        color=GOLD,
        description=(
            "Welcome to the full Scient Lounge experience. Here's exactly what you now have access to - "
            "take two minutes to set yourself up so you don't miss anything."
        ),
    )
    embed.add_field(
        name="\U0001F4C8 Live Analyst Setups",
        value=(
            "Every trade our analysts take is posted in **#future-trades** and **#spot-trades** with "
            "entry, stop loss, targets, and the reasoning in a thread. Updates (TP hits, SL moves, closes) "
            "are tracked live on the card and in **#trade-updates**."
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F514 Never miss a setup",
        value=(
            "Go to **#select-analyst-alerts** and pick which analysts you want to be pinged for. "
            "You can also turn on the **Breaking News** ping for urgent market events."
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F4CB Check the track record",
        value=(
            "`/stats` - any analyst's full scorecard: win rate, total R, best/worst\n"
            "`/recent` - the latest closed trades with results\n"
            "`/open` - every live position right now"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F9EE Your risk tools (private)",
        value=(
            "`/pnl` - position size from your account, risk %, entry and SL. Use this before every trade.\n"
            "`/liq` - liquidation price for any entry and leverage"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F4CA Full Quant Terminal",
        value=(
            "**#quant-terminal**: `/chart` `/levels` `/funding` `/oi` `/vol` `/heatmap` `/dominance` "
            "`/compare` and more. Type `/help` for the full list."
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F4F0 News",
        value="The daily news digest and market brief keep you on top of what matters. Watch **#news-wire**.",
        inline=False,
    )
    embed.set_footer(text="Scient Lounge - welcome aboard")
    return embed


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    try:
        await member.send(embed=build_join_dm())
        print(f"[welcome] join DM sent to {member} ({member.id})")
    except discord.Forbidden:
        print(f"[welcome] join DM blocked (DMs closed) for {member} ({member.id})")
    except Exception as e:
        print(f"[welcome] join DM error for {member.id}: {e}")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.bot:
        return
    before_ids = {r.id for r in before.roles}
    after_ids = {r.id for r in after.roles}
    gained = after_ids - before_ids
    if gained & PRO_ROLE_IDS:
        # only fire once even if both pro roles are added together
        try:
            await after.send(embed=build_pro_dm())
            print(f"[welcome] PRO DM sent to {after} ({after.id})")
        except discord.Forbidden:
            print(f"[welcome] PRO DM blocked (DMs closed) for {after} ({after.id})")
        except Exception as e:
            print(f"[welcome] PRO DM error for {after.id}: {e}")


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
    if LIQ_CHANNEL_ID and not getattr(bot, "_liq_task", None):
        bot._liq_task = asyncio.create_task(liq_binance_loop())
        bot._liq_task_bybit = asyncio.create_task(liq_bybit_loop())
        bot._liq_task_okx = asyncio.create_task(liq_okx_loop())
    if TG_ENABLED and TELEGRAM_BOT_TOKEN and not tg_brief_loop.is_running():
        tg_brief_loop.start()
    if TG_ENABLED and TELEGRAM_BOT_TOKEN and not tg_move_loop.is_running():
        tg_move_loop.start()
    if not alert_check_loop.is_running():
        alert_check_loop.start()
    if not backup_loop.is_running():
        backup_loop.start()
    if not subs_check_loop.is_running():
        subs_check_loop.start()
    if not tg_digest_loop.is_running():
        tg_digest_loop.start()
    if TG_NEWS_CHANNELS and not tg_sources_loop.is_running():
        tg_sources_loop.start()
    print(f"Logged in as {bot.user} - commands synced.")


@bot.tree.command(name="tg_digest", description="(Admin) Send the daily news digest to Telegram now")
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
        title="\U0001F514 Choose Your Alerts",
        description=(
            "Pick what you want to be notified about. Tap a button to turn it on, "
            "tap it again to turn it off. You can change these any time."
        ),
        color=NAVY,
    )
    embed.add_field(
        name="Per-analyst alerts",
        value=(
            f"{' · '.join(f'**{k.capitalize()}**' for k in ANALYSTS)}\n"
            "Pinged only when that analyst posts a new setup - good if you follow one style closely."
        ),
        inline=False,
    )
    embed.add_field(
        name="Follow All",
        value="Pinged on every new setup from every analyst. The one to pick if you don't want to miss anything.",
        inline=False,
    )
    embed.add_field(
        name="X Updates",
        value="Pinged when a new post from our X account is shared in the server.",
        inline=False,
    )
    embed.add_field(
        name="\U0001F6A8 Breaking News",
        value="Urgent market events only - hacks, exchange halts, delistings. Rare by design, so it stays worth reading.",
        inline=False,
    )
    embed.set_footer(text="Scient Lounge - you're in control of your pings")
    await interaction.channel.send(embed=embed, view=FollowPanel())
    await interaction.response.send_message("Follow panel posted.", ephemeral=True)


@bot.tree.command(name="trade", description="Post a trade setup")
@app_commands.describe(
    pair="e.g. BTC/USDT",
    direction="Long or Short",
    entry_type="Market (filled now), Limit single, or Limit DCA (two entries)",
    entry="Entry price (Entry 1 if DCA)",
    stop_loss="SL price (the level)",
    sl_condition="Optional soft SL, e.g. 4H close below - shows as a condition, not a hard stop",
    risk="Account risk (just a number = %, e.g. 1 shows as 1%)",
    entry2="Second DCA entry price (only for Limit DCA)",
    entry_split="DCA size split, e.g. 20/80 (Entry 1 gets 20%, Entry 2 gets 80%). Optional",
    tp_split="Planned TP sizes, e.g. 25/50/25 (TP1/TP2/TP3). TP updates then default to these. Optional",
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
async def trade(interaction: discord.Interaction, pair: str, direction: app_commands.Choice[str], entry_type: app_commands.Choice[str], entry: str, stop_loss: str, risk: str, sl_condition: str = None, entry2: str = None, entry_split: str = None, tp_split: str = None, framework: app_commands.Choice[str] = None, framework2: app_commands.Choice[str] = None, chart: discord.Attachment = None, tp1: str = None, timeframe: str = None, setup_detail: str = None, tp2: str = None, tp3: str = None, notes: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message(f"Only members with the **{ANALYST_ROLE_NAME}** role can post setups.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    channel = bot.get_channel(TRADES_CHANNEL_ID)
    if channel is None:
        await interaction.followup.send("Trades channel not found - check TRADES_CHANNEL_ID.", ephemeral=True)
        return
    sl_num, sl_cond = parse_sl(stop_loss)
    if sl_num is None:
        await interaction.followup.send("Couldn't find a price in the SL - e.g. `63000` or `4h close below 63000`.", ephemeral=True)
        return
    stop_loss = sl_num
    etype = entry_type.value
    if etype == "DCA" and not entry2:
        await interaction.followup.send("Limit - Range/DCA needs **entry2** (the second entry price).", ephemeral=True)
        return
    if etype != "DCA":
        entry2 = None
        entry_split = None
    split_disp = None
    if etype == "DCA" and entry_split:
        nums = re.findall(r"\d+(?:\.\d+)?", entry_split)
        if len(nums) >= 2:
            a, b = float(nums[0]), float(nums[1])
            if a + b > 0:
                a_pct = round(a / (a + b) * 100)
                split_disp = f"{a_pct}% / {100 - a_pct}%"
        if split_disp is None:
            await interaction.followup.send("Couldn't read the split - use a format like `20/80`.", ephemeral=True)
            return
    tp_plan = None
    if tp_split:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", tp_split)]
        n_tps = sum(1 for x in (tp1, tp2, tp3) if x)
        if not nums or len(nums) != n_tps:
            await interaction.followup.send(
                f"tp_split has {len(nums)} number(s) but you set {n_tps} TP level(s) - they must match (e.g. `25/50/25` for 3 TPs).",
                ephemeral=True,
            )
            return
        total = sum(nums)
        if total <= 0 or total > 100.5:
            await interaction.followup.send("tp_split must add up to 100 or less (the rest rides).", ephemeral=True)
            return
        tp_plan = [round(x, 1) for x in nums]
    is_market = etype == "MARKET"
    akey, acfg = resolve_analyst(interaction.user)
    frameworks = [f.value for f in (framework, framework2) if f]
    t = {
        "sl_condition": sl_cond,
        "analyst_id": interaction.user.id, "analyst_name": interaction.user.display_name,
        "analyst_avatar": interaction.user.display_avatar.url, "analyst_key": akey,
        "analyst_color": analyst_color_hex(interaction.user),
        "pair": pair, "direction": direction.value, "timeframe": timeframe,
        "framework": frameworks[0] if frameworks else None, "frameworks": frameworks, "setup_detail": setup_detail,
        "entry": entry, "entry2": entry2, "entry_split": split_disp, "tp_split": tp_plan, "sl": stop_loss, "entry_type": "MARKET" if is_market else "LIMIT",
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


def _ac_label(t: dict, spot: bool = False) -> str:
    """Grouped autocomplete label: analyst · side · pair - status."""
    who = t.get("analyst_name", "?")
    if spot:
        return f"{who} · \U0001F48E SPOT · {t['pair'].upper()} - {spot_status_line(t)}"
    side = "\U0001F7E2 LONG" if str(t.get("direction", "")).upper() == "LONG" else "\U0001F534 SHORT"
    return f"{who} · {side} · {t['pair'].upper()} {tf(t)} - {short_status(t)}"


def _ac_sortkey(t: dict, spot: bool = False, uid: int = 0):
    # invoker's own trades first, then other analysts alphabetically;
    # within each analyst: longs, then shorts, spot always last
    own_rank = 0 if t.get("analyst_id") == uid else 1
    side_rank = 2 if spot else (0 if str(t.get("direction", "")).upper() == "LONG" else 1)
    return (own_rank, t.get("analyst_name", "z").lower(), side_rank, t.get("pair", ""))


async def open_trades_ac(interaction: discord.Interaction, current: str):
    data = load_trades()
    is_admin = interaction.user.guild_permissions.administrator
    rows = []
    for mid, t in data.items():
        if t.get("closed"):
            continue
        if not is_admin and t.get("analyst_id") != interaction.user.id:
            continue
        label = _ac_label(t)
        if current.lower() in label.lower():
            rows.append((_ac_sortkey(t, uid=interaction.user.id), label, mid))
    rows.sort(key=lambda r: r[0])
    return [app_commands.Choice(name=lbl[:100], value=mid) for _, lbl, mid in rows[:25]]


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
    rows = []
    for mid, t in load_trades().items():
        if t.get("closed"):
            continue
        if not is_admin and t.get("analyst_id") != interaction.user.id:
            continue
        if not is_admin and not within_edit_window(t):
            continue
        label = _ac_label(t)
        if current.lower() in label.lower():
            rows.append((_ac_sortkey(t, uid=interaction.user.id), label, f"f:{mid}"))
    for mid, p in load_spot().items():
        if p.get("closed"):
            continue
        if not is_admin and p.get("analyst_id") != interaction.user.id:
            continue
        if not is_admin and not within_edit_window(p):
            continue
        label = _ac_label(p, spot=True)
        if current.lower() in label.lower():
            rows.append((_ac_sortkey(p, spot=True, uid=interaction.user.id), label, f"s:{mid}"))
    rows.sort(key=lambda r: r[0])
    return [app_commands.Choice(name=lbl[:100], value=val) for _, lbl, val in rows[:25]]


async def refresh_and_edit(t: dict, spot_mode: bool = False):
    channel = bot.get_channel(t["channel_id"])
    msg = await channel.fetch_message(t["message_id"])
    builder = build_spot_embed if spot_mode else build_embed
    embed = builder(t)
    if msg.attachments:
        # reference the existing attachment (attachment://) instead of its CDN url -
        # the CDN url makes Discord render the chart twice (attachment + embed image)
        embed.set_image(url=f"attachment://{msg.attachments[0].filename}")
        await msg.edit(embed=embed, attachments=list(msg.attachments))
    else:
        await msg.edit(embed=embed)
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
    sold_pct="Partial sell - % of the bag sold (e.g. 30). Pair with sell_price",
    sell_price="Partial sell - price sold at (required with sold_pct)",
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
async def spot_update(interaction: discord.Interaction, play: str, avg_entry: str = None, status: app_commands.Choice[str] = None, target_hit: app_commands.Choice[str] = None, sold_pct: str = None, sell_price: str = None, zone_filled: app_commands.Choice[str] = None, note: str = None):
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
    if sold_pct is not None or sell_price is not None:
        sp = spot_num(sold_pct)
        px = spot_num(sell_price)
        if sp is None or px is None:
            await interaction.followup.send("Partial sell needs **both** `sold_pct` and `sell_price`.", ephemeral=True)
            return
        if sp <= 0 or sp > 100:
            await interaction.followup.send("sold_pct must be between 0 and 100.", ephemeral=True)
            return
        already = sum(s["pct"] for s in (p.get("sells") or []))
        if already + sp > 100.01:
            await interaction.followup.send(f"That totals {already + sp:g}% sold - only {100 - already:g}% of the bag is left.", ephemeral=True)
            return
        p.setdefault("sells", []).append({"pct": round(sp, 1), "price": px})
        changes.append(f"sold {sp:g}% @ {px:g}")
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
@app_commands.describe(play="Pick an active spot play", result="Outcome", result_pct="Manual override - auto-calculated from entry/exit if blank", avg_exit="Average exit price (auto from partial sells if blank)", note="Closing note (optional)")
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
    # auto avg_exit from recorded partial sells if not given
    if avg_exit is None and spot_weighted_exit(p):
        avg_exit = f"{spot_weighted_exit(p):g}"
    had_position = bool(spot_num(p.get("avg_entry")) or p.get("zone_filled") or (p.get("sells") or []))
    if result.value == "INVALID" and had_position and spot_num(avg_exit) is None:
        await interaction.followup.send(
            "This play had fills - **avg_exit is required** on invalidation so the journal records where you cut.",
            ephemeral=True,
        )
        return
    # auto result % from entry vs exit when not manually given
    if result_pct is None:
        ref = spot_ref_entry(p)
        ex = spot_num(avg_exit)
        if ref and ex and ref > 0:
            result_pct = f"{(ex - ref) / ref * 100:+.1f}%"
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
    tp_split="Planned TP sizes e.g. 25/50/25 (space to clear)", entry_split="DCA size split e.g. 20/80 (space to clear)", entry2="Corrected second DCA entry - futures only (optional)",
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
async def edit(interaction: discord.Interaction, trade: str, pair: str = None, direction: app_commands.Choice[str] = None, entry: str = None, entry2: str = None, entry_split: str = None, tp_split: str = None, stop_loss: str = None, risk: str = None, entry_type: app_commands.Choice[str] = None, framework: app_commands.Choice[str] = None, framework2: app_commands.Choice[str] = None, chart: discord.Attachment = None, tp1: str = None, tp2: str = None, tp3: str = None, timeframe: str = None, setup_detail: str = None, notes: str = None):
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
        if entry_split is not None:
            raw = entry_split.strip()
            if not raw:
                t["entry_split"] = None; changes.append("split removed")
            else:
                nums = re.findall(r"\d+(?:\.\d+)?", raw)
                if len(nums) >= 2 and (float(nums[0]) + float(nums[1])) > 0:
                    a, b = float(nums[0]), float(nums[1])
                    a_pct = round(a / (a + b) * 100)
                    t["entry_split"] = f"{a_pct}% / {100 - a_pct}%"; changes.append("entry split")
        if tp_split is not None:
            raw = tp_split.strip()
            if not raw:
                t["tp_split"] = None; changes.append("TP split removed")
            else:
                nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw)]
                n_tps = sum(1 for k2 in ("tp1", "tp2", "tp3") if t.get(k2))
                if nums and len(nums) == n_tps and 0 < sum(nums) <= 100.5:
                    t["tp_split"] = [round(x, 1) for x in nums]; changes.append("TP split")
        if stop_loss is not None:
            e_num, e_cond = parse_sl(stop_loss)
            if e_num:
                t["sl"] = e_num
                t["sl_condition"] = e_cond
                changes.append("SL")
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
        # keep the existing attachment and reference it the same way the original post does
        # (using the CDN url here makes Discord render the image twice - once as the
        #  attachment, once inside the embed)
        if msg.attachments:
            att = msg.attachments[0]
            embed = builder(t)
            embed.set_image(url=f"attachment://{att.filename}")
            await msg.edit(embed=embed, attachments=list(msg.attachments))
        else:
            await msg.edit(embed=builder(t))
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
    price="Partial TP, Closed - exit price. Required on SL Hit if the trade has a soft SL",
    new_sl="SL Updated only - number for hard (64000) or full condition (4h close below 64000)",
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
    app_commands.Choice(name="SL Updated (new level/condition)", value="SLU"),
    app_commands.Choice(name="SL Hit (closes trade)", value="SL"),
    app_commands.Choice(name="Closed (bot calculates result)", value="CLOSE"),
    app_commands.Choice(name="Invalidated (never triggered)", value="CI"),
])
@app_commands.autocomplete(trade=open_trades_ac)
async def update(interaction: discord.Interaction, trade: str, event: app_commands.Choice[str], size_pct: str = None, price: str = None, new_sl: str = None, note: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message("Analysts only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    data = load_trades()
    t = data.get(trade)
    if t and not interaction.user.guild_permissions.administrator and t.get("analyst_id") != interaction.user.id:
        await interaction.followup.send("You can only update your own trades.", ephemeral=True)
        return
    if not t:
        await interaction.followup.send("Trade not found.", ephemeral=True)
        return
    t.setdefault("fills", [])
    ev = event.value
    pct = parse_num(size_pct)
    px = parse_num(price)

    if ev in ("TP1", "TP2", "TP3", "PTP"):
        if pct is None and ev != "PTP":
            plan = t.get("tp_split") or []
            idx = {"TP1": 0, "TP2": 1, "TP3": 2}[ev]
            if idx < len(plan):
                pct = float(plan[idx])  # planned size from the trade card
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
    elif ev == "SLU":
        raw = new_sl if new_sl else (str(px) if px is not None else None)
        s_num, s_cond = parse_sl(raw) if raw else (None, None)
        if s_num is None:
            await interaction.followup.send("**new_sl is required** - e.g. `64000` or `4h close below 64000`.", ephemeral=True)
            return
        t["sl"] = s_num
        t["sl_condition"] = s_cond
        t["be"] = False
        shown = (s_cond + " " if s_cond else "") + s_num
        desc = "SL updated -> " + shown
    elif ev == "SL":
        if t.get("sl_condition") and px is None and not t.get("be"):
            await interaction.followup.send(
                f"This trade has a **soft SL** ({t['sl_condition']} {t['sl']}). "
                f"**price is required** - at what exact price did it close?",
                ephemeral=True,
            )
            return
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
    if is_tradfi(symbol):
        tf = await md_tradfi(symbol)
        if not tf:
            await interaction.followup.send(f"Couldn't fetch **{symbol}** right now.")
            return
        arrow = "\U0001F7E2" if tf["chg"] >= 0 else "\U0001F534"
        color = GREEN if tf["chg"] >= 0 else RED
        embed = discord.Embed(title=f"{arrow} {symbol}", color=color, timestamp=datetime.now(timezone.utc))
        embed.description = f"**Price:** {tf['price']:,.2f}\n**24h:** {tf['chg']:+.2f}%"
        embed.set_footer(text="Scient Lounge - traditional markets")
        await interaction.followup.send(embed=embed)
        return
    data = await md_ticker24(pair)
    if not data:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit - check the symbol (e.g. BTC, SOL, ETH).")
        return
    last = data["lastPrice"]
    chg = data["priceChangePercent"]
    high = data["highPrice"]
    low = data["lowPrice"]
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
    klines = await md_klines(pair, interval, 220)
    if not klines:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit - check the symbol.")
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
    fd = await md_funding(pair)
    if not fd:
        await interaction.followup.send(f"No perp market found for **{symbol}** on Binance or Bybit.")
        return
    rate = fd["rate"]
    mark = fd["mark"]
    nxt = fd["next"]
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
    od = await md_oi(pair)
    fd = await md_funding(pair)
    if not od:
        await interaction.followup.send(f"No perp market found for **{symbol}** on Binance or Bybit.")
        return
    oi_now = od["oi"]
    mark = fd["mark"] if fd else 0
    oi_usd = oi_now * mark if mark else None
    chg_txt = ""
    color = NAVY
    if od.get("oi_then"):
        try:
            oi_then = od["oi_then"]
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
    klines = await md_klines(pair, "4h", 60)
    if not klines:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit.")
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


def make_cvd_image(symbol: str, tf_label: str, dates: list, closes: list, cvd_spot: list, cvd_perp: list) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1.4], "hspace": 0.06},
                                   facecolor="#131722")
    for ax in (ax1, ax2):
        ax.set_facecolor("#131722")
        ax.grid(color="#1E222D", linewidth=0.5)
        for sp in ax.spines.values():
            sp.set_color("#2A2E39")
        ax.tick_params(colors="#B2B5BE", labelsize=8)
        ax.yaxis.tick_right()
    ax1.plot(dates, closes, color="#EAECEF", linewidth=1.4)
    ax1.set_title(f"{symbol}  {tf_label}  |  Price vs CVD", color="#EAECEF", fontsize=12, loc="left", pad=10)
    ax2.plot(dates, cvd_spot, color="#E8590C", linewidth=1.6, label="Spot CVD")
    ax2.plot(dates, cvd_perp, color="#378ADD", linewidth=1.6, label="Perp CVD")
    ax2.axhline(0, color="#B2B5BE", linewidth=0.6, linestyle="--", alpha=0.5)
    leg = ax2.legend(facecolor="#131722", edgecolor="#2A2E39", labelcolor="#EAECEF", fontsize=9, loc="upper left")
    def _fmt_usd(x, _):
        ax_abs = abs(x)
        if ax_abs >= 1e9: return f"{x/1e9:.1f}B"
        if ax_abs >= 1e6: return f"{x/1e6:.0f}M"
        if ax_abs >= 1e3: return f"{x/1e3:.0f}K"
        return f"{x:.0f}"
    import matplotlib.ticker as mticker
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_usd))
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor="#131722", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


async def _fetch_cvd_klines(url: str, pair: str, interval: str, limit: int):
    async with aiohttp.ClientSession() as s:
        return await _get_json(s, url, {"symbol": pair, "interval": interval, "limit": limit}, 20)


def _cvd_series(klines: list):
    """Cumulative volume delta in USD from taker-buy quote volume (idx 10) vs total quote volume (idx 7)."""
    out = []
    run = 0.0
    for k in klines:
        try:
            qv = float(k[7])
            tq = float(k[10])
        except Exception:
            out.append(run)
            continue
        run += (2 * tq - qv)  # taker buys minus taker sells, in quote (USD) terms
        out.append(run)
    return out


CVD_INTERVALS = {"15m": "15m", "1H": "1h", "4H": "4h"}


@bot.tree.command(name="cvd", description="Spot vs Perp CVD with OI and funding - who is driving the move?")
@app_commands.describe(coin="Coin symbol, e.g. BTC", timeframe="Candle timeframe")
@app_commands.choices(timeframe=[app_commands.Choice(name=k, value=k) for k in CVD_INTERVALS])
async def cvd_cmd(interaction: discord.Interaction, coin: str, timeframe: app_commands.Choice[str] = None):
    await interaction.response.defer()
    tfv = timeframe.value if timeframe else "1H"
    interval = CVD_INTERVALS[tfv]
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    spot = await _fetch_cvd_klines("https://api.binance.com/api/v3/klines", pair, interval, 200)
    perp = await _fetch_cvd_klines("https://fapi.binance.com/fapi/v1/klines", pair, interval, 200)
    if not spot or not isinstance(spot, list) or len(spot) < 30:
        await interaction.followup.send(f"CVD needs Binance taker data and **{symbol}** isn't on Binance spot - try a major pair.")
        return
    if not perp or not isinstance(perp, list):
        perp = []
    n = min(len(spot), len(perp)) if perp else len(spot)
    spot = spot[-n:]
    perp = perp[-n:] if perp else []
    cvd_spot = _cvd_series(spot)
    cvd_perp = _cvd_series(perp) if perp else [0.0] * n
    from datetime import datetime as _dt
    dates = [_dt.fromtimestamp(int(k[0]) / 1000) for k in spot]
    closes = [float(k[4]) for k in spot]
    try:
        buf = await asyncio.to_thread(make_cvd_image, f"{symbol}/USDT", tfv, dates, closes, cvd_spot, cvd_perp)
    except Exception as e:
        await interaction.followup.send(f"CVD rendering failed: {e}")
        return
    # context numbers
    fd = await md_funding(pair)
    od = await md_oi(pair)
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    cb_px = await md_coinbase_price(base)
    premium_pct = None
    if cb_px and closes[-1] > 0:
        premium_pct = (cb_px - closes[-1]) / closes[-1] * 100
    def _musd(x):
        return f"{'+' if x >= 0 else '-'}${abs(x)/1e6:.1f}M" if abs(x) < 1e9 else f"{'+' if x >= 0 else '-'}${abs(x)/1e9:.2f}B"
    d_spot = cvd_spot[-1] - cvd_spot[0]
    d_perp = (cvd_perp[-1] - cvd_perp[0]) if perp else 0.0
    px_chg = (closes[-1] - closes[0]) / closes[0] * 100
    # simple auto-read
    if px_chg > 0.5 and d_spot > 0 and d_spot >= d_perp:
        read = "Spot-led move - real buyers, healthier structure."
    elif px_chg > 0.5 and d_perp > 0 and d_perp > d_spot:
        read = "Perp-led move - leverage driving, watch funding for crowding."
    elif px_chg > 0.5 and d_spot < 0 and d_perp < 0:
        read = "Price up while CVD bleeds - short covering or thin absorption. Fragile."
    elif px_chg < -0.5 and (d_spot > 0 or d_perp > 0):
        read = "Price down into positive delta - buyers absorbing, possible accumulation."
    else:
        read = "No strong divergence between price and flows right now."
    lines = [f"**{symbol} ({n} x {tfv}):** price {px_chg:+.2f}% | Spot CVD {_musd(d_spot)} | Perp CVD {_musd(d_perp)}"]
    ctx = []
    if od and od.get("oi_then"):
        oi_chg = (od["oi"] - od["oi_then"]) / od["oi_then"] * 100
        ctx.append(f"OI 24h {oi_chg:+.2f}%")
    if fd:
        ctx.append(f"funding {fd['rate']:+.4f}%")
    if premium_pct is not None:
        ctx.append(f"Coinbase premium {premium_pct:+.3f}%")
    if ctx:
        lines.append("**Context:** " + " | ".join(ctx))
    if premium_pct is not None:
        if premium_pct >= 0.05:
            read += " Coinbase premium positive - US bid active."
        elif premium_pct <= -0.05:
            read += " Coinbase premium negative - US money absent or selling."
    lines.append(f"**Read:** {read}")
    f = discord.File(buf, filename=f"{symbol}_cvd_{tfv}.png")
    await interaction.followup.send(content="\n".join(lines), file=f)


def make_rvol_image(symbol: str, months: list, rvs: list, current_rv: float) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5.5), facecolor="#131722")
    ax.set_facecolor("#131722")
    ax.grid(color="#1E222D", linewidth=0.5, axis="y")
    for sp in ax.spines.values():
        sp.set_color("#2A2E39")
    ax.tick_params(colors="#B2B5BE", labelsize=8)
    colors = ["#E8590C" if i == len(rvs) - 1 else "#1C4E80" for i in range(len(rvs))]
    ax.bar(range(len(rvs)), rvs, color=colors, width=0.8)
    step = max(1, len(months) // 12)
    ax.set_xticks(range(0, len(months), step))
    ax.set_xticklabels([months[i] for i in range(0, len(months), step)], rotation=45, ha="right")
    ax.set_title(f"{symbol}  |  30d Realized Volatility by Month (annualized)",
                 color="#EAECEF", fontsize=12, loc="left", pad=10)
    ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor="#131722", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


@bot.tree.command(name="rvol", description="Realized volatility regime - is the market compressed or wild?")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def rvol_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    import math
    klines = None
    try:
        async with aiohttp.ClientSession() as s:
            klines = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                     {"symbol": pair, "interval": "1d", "limit": 1000}, 20)
    except Exception:
        pass
    if not klines or not isinstance(klines, list) or len(klines) < 60:
        await interaction.followup.send(f"Not enough history for **{symbol}** (needs Binance daily data).")
        return
    closes = [float(k[4]) for k in klines]
    stamps = [int(k[0]) // 1000 for k in klines]
    rets = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            rets.append(math.log(closes[i] / closes[i-1]))
        else:
            rets.append(0.0)
    def _std(xs):
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    # rolling 30d RV annualized, then bucket by month (avg)
    monthly = {}
    for i in range(30, len(rets) + 1):
        rv = _std(rets[i-30:i]) * math.sqrt(365) * 100
        mkey = datetime.fromtimestamp(stamps[i], tz=timezone.utc).strftime("%Y-%m")
        monthly.setdefault(mkey, []).append(rv)
    months = sorted(monthly.keys())
    rvs = [sum(monthly[m]) / len(monthly[m]) for m in months]
    current_rv = _std(rets[-30:]) * math.sqrt(365) * 100
    # 1y percentile of current vs daily rolling values
    recent_series = []
    for i in range(max(30, len(rets) - 365), len(rets) + 1):
        recent_series.append(_std(rets[i-30:i]) * math.sqrt(365) * 100)
    below = sum(1 for x in recent_series if x <= current_rv)
    pctile = below / len(recent_series) * 100 if recent_series else 50
    if pctile <= 20:
        read = "Volatility compressed to the low end of its range - expansion usually follows. Watch for the break."
    elif pctile >= 80:
        read = "Elevated volatility - wide stops or smaller size. Mean reversion likely eventually."
    else:
        read = "Mid-range volatility - no regime extreme."
    labels = [m[2:] for m in months]  # YY-MM
    try:
        buf = await asyncio.to_thread(make_rvol_image, f"{symbol}/USDT", labels, rvs, current_rv)
    except Exception as e:
        await interaction.followup.send(f"Chart render failed: {e}")
        return
    content = (f"**{symbol} 30d Realized Vol:** {current_rv:.1f}% - **{pctile:.0f}th percentile** (1yr)\n"
               f"**Read:** {read}")
    f = discord.File(buf, filename=f"{symbol}_rvol.png")
    await interaction.followup.send(content=content, file=f)


@bot.tree.command(name="snapshot", description="Full market check for a coin in one command")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def snapshot_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    t24, fd, od = await asyncio.gather(md_ticker24(pair), md_funding(pair), md_oi(pair))
    if not t24:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit.")
        return
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    cb = await md_coinbase_price(base)
    # quick 24h CVD from 1h klines (Binance only)
    cvd_line = None
    try:
        async with aiohttp.ClientSession() as s:
            kl_s = await _get_json(s, "https://api.binance.com/api/v3/klines", {"symbol": pair, "interval": "1h", "limit": 24}, 15)
            kl_p = await _get_json(s, "https://fapi.binance.com/fapi/v1/klines", {"symbol": pair, "interval": "1h", "limit": 24}, 15)
        def _delta(kl):
            tot = 0.0
            for k in kl:
                tot += 2 * float(k[10]) - float(k[7])
            return tot
        if kl_s and isinstance(kl_s, list):
            ds = _delta(kl_s)
            dp = _delta(kl_p) if kl_p and isinstance(kl_p, list) else None
            def _m(x):
                return f"{'+' if x >= 0 else '-'}${abs(x)/1e6:.0f}M"
            cvd_line = f"**CVD (24h):** Spot {_m(ds)}" + (f" | Perp {_m(dp)}" if dp is not None else "")
    except Exception:
        pass
    # fear & greed
    fg_line = None
    try:
        async with aiohttp.ClientSession() as s:
            fg = await _get_json(s, "https://api.alternative.me/fng/", None, 10)
        v = fg["data"][0]
        fg_line = f"**Fear & Greed:** {v['value']} ({v['value_classification']})"
    except Exception:
        pass
    arrow = "\U0001F7E2" if t24["priceChangePercent"] >= 0 else "\U0001F534"
    lines = [f"**Price:** ${fnum(t24['lastPrice'])} {arrow} {t24['priceChangePercent']:+.2f}% (24h)"]
    ctx = []
    if fd:
        lean = "longs paying" if fd["rate"] > 0 else "shorts paying"
        ctx.append(f"**Funding:** {fd['rate']:+.4f}% - {lean}")
    if od and od.get("oi_then"):
        ctx.append(f"**OI 24h:** {(od['oi'] - od['oi_then']) / od['oi_then'] * 100:+.2f}%")
    if ctx:
        lines.append(" | ".join(ctx))
    if cvd_line:
        lines.append(cvd_line)
    if cb and t24["lastPrice"] > 0:
        prem = (cb - t24["lastPrice"]) / t24["lastPrice"] * 100
        lines.append(f"**Coinbase premium:** {prem:+.3f}%")
    if fg_line:
        lines.append(fg_line)
    embed = discord.Embed(title=f"\U0001F4F8 {symbol} Snapshot", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Scient Lounge - morning check, one command")
    await interaction.followup.send(embed=embed)


@tasks.loop(time=dt_time(hour=21, minute=30, tzinfo=timezone.utc))  # 3:00 AM IST
async def backup_loop():
    try:
        import shutil
        bdir = Path.home() / "bot_backups"
        bdir.mkdir(exist_ok=True)
        stamp = datetime.now(IST).strftime("%Y%m%d")
        ddir = bdir / stamp
        ddir.mkdir(exist_ok=True)
        n = 0
        for f in Path(__file__).parent.glob("*.json"):
            shutil.copy2(f, ddir / f.name)
            n += 1
        # rotate: keep newest 7 daily folders
        folders = sorted([d for d in bdir.iterdir() if d.is_dir()])
        for old in folders[:-7]:
            shutil.rmtree(old, ignore_errors=True)
        print(f"[backup] {n} files -> {ddir}", flush=True)
    except Exception as e:
        print(f"[backup] error: {e}", flush=True)


@backup_loop.before_loop
async def before_backup():
    await bot.wait_until_ready()


@bot.tree.command(name="stables", description="Stablecoin supply - is dry powder flowing in or out?")
async def stables_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as s:
            d = await _get_json(s, "https://stablecoins.llama.fi/stablecoincharts/all", None, 20)
    except Exception:
        d = None
    if not d or not isinstance(d, list) or len(d) < 31:
        await interaction.followup.send("Stablecoin data unavailable right now.")
        return
    def _tot(row):
        try:
            return float(row["totalCirculating"]["peggedUSD"])
        except Exception:
            return None
    now = _tot(d[-1])
    d7 = _tot(d[-8])
    d30 = _tot(d[-31])
    if not now:
        await interaction.followup.send("Couldn't parse stablecoin data.")
        return
    chg7 = (now - d7) / d7 * 100 if d7 else 0
    chg30 = (now - d30) / d30 * 100 if d30 else 0
    if chg7 >= 0.75:
        read = "Supply expanding - fresh dry powder entering. Liquidity tailwind."
    elif chg7 <= -0.75:
        read = "Supply contracting - capital leaving the system. Liquidity headwind."
    else:
        read = "Supply flat - no strong liquidity signal either way."
    arrow7 = "\U0001F7E2" if chg7 >= 0 else "\U0001F534"
    arrow30 = "\U0001F7E2" if chg30 >= 0 else "\U0001F534"
    embed = discord.Embed(title="\U0001F4B5 Stablecoin Supply", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**Total supply:** ${now/1e9:.1f}B\n"
        f"**7d:** {arrow7} {chg7:+.2f}% | **30d:** {arrow30} {chg30:+.2f}%\n"
        f"**Read:** {read}"
    )
    embed.set_footer(text="Scient Lounge - DefiLlama data · stablecoin supply leads price")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="agg", description="Aggregated OI + funding across Binance, Bybit and OKX")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def agg_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    pair = f"{base}USDT"
    rows = []
    async with aiohttp.ClientSession() as s:
        # Binance
        try:
            oi_d = await _get_json(s, "https://fapi.binance.com/fapi/v1/openInterest", {"symbol": pair}, 15)
            px_d = await _get_json(s, "https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": pair}, 15)
            if oi_d and px_d:
                mark = float(px_d["markPrice"])
                rows.append(("Binance", float(oi_d["openInterest"]) * mark, float(px_d["lastFundingRate"]) * 100))
        except Exception:
            pass
        # Bybit
        try:
            bb = await _get_json(s, "https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": pair}, 15)
            t = bb["result"]["list"][0]
            rows.append(("Bybit", float(t["openInterestValue"]), float(t["fundingRate"]) * 100))
        except Exception:
            pass
        # OKX
        try:
            inst = f"{base}-USDT-SWAP"
            oi_o = await _get_json(s, "https://www.okx.com/api/v5/public/open-interest", {"instId": inst}, 15)
            fr_o = await _get_json(s, "https://www.okx.com/api/v5/public/funding-rate", {"instId": inst}, 15)
            oi_usd = float(oi_o["data"][0]["oiUsd"]) if oi_o and oi_o.get("data") else None
            fr = float(fr_o["data"][0]["fundingRate"]) * 100 if fr_o and fr_o.get("data") else None
            if oi_usd is not None and fr is not None:
                rows.append(("OKX", oi_usd, fr))
        except Exception:
            pass
    if not rows:
        await interaction.followup.send(f"No perp data found for **{base}** on any tracked exchange.")
        return
    total_oi = sum(r[1] for r in rows)
    lines = []
    for name, oi_usd, fr in rows:
        share = oi_usd / total_oi * 100 if total_oi else 0
        lines.append(f"**{name}:** ${oi_usd/1e9:.2f}B OI ({share:.0f}%) | funding {fr:+.4f}%")
    avg_fr = sum(r[2] * r[1] for r in rows) / total_oi if total_oi else 0
    lines.append("")
    lines.append(f"**Total OI:** ${total_oi/1e9:.2f}B | **OI-weighted funding:** {avg_fr:+.4f}%")
    if avg_fr >= 0.03:
        lines.append("**Read:** Longs paying heavily across venues - crowded.")
    elif avg_fr <= -0.01:
        lines.append("**Read:** Shorts paying across venues - squeeze fuel.")
    else:
        lines.append("**Read:** Funding balanced across venues.")
    embed = discord.Embed(title=f"\U0001F310 Aggregated Derivatives - {base}", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Scient Lounge - Binance + Bybit + OKX")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="whale", description="Large individual trades in the last hour - what are whales doing?")
@app_commands.describe(coin="Coin symbol, e.g. BTC", min_usd="Minimum trade size in USD (default 500000)")
async def whale_cmd(interaction: discord.Interaction, coin: str, min_usd: int = 500000):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    min_usd = max(50_000, min(min_usd, 10_000_000))
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = end - 3600_000
    trades = []
    try:
        async with aiohttp.ClientSession() as s:
            cur = start
            for _ in range(6):  # aggTrades pages, max ~6k trades scanned
                d = await _get_json(s, "https://api.binance.com/api/v3/aggTrades",
                                    {"symbol": pair, "startTime": cur, "endTime": end, "limit": 1000}, 15)
                if not d or not isinstance(d, list):
                    break
                trades += d
                if len(d) < 1000:
                    break
                cur = int(d[-1]["T"]) + 1
    except Exception:
        pass
    if not trades:
        await interaction.followup.send(f"No trade data for **{symbol}** on Binance (whale scan is Binance-only).")
        return
    buys = []
    sells = []
    for t in trades:
        try:
            usd = float(t["p"]) * float(t["q"])
        except Exception:
            continue
        if usd < min_usd:
            continue
        # m = True means buyer is maker -> aggressive SELL
        (sells if t.get("m") else buys).append((usd, float(t["p"])))
    buy_usd = sum(u for u, _ in buys)
    sell_usd = sum(u for u, _ in sells)
    top = sorted(buys + [(-u, p) for u, p in sells], key=lambda x: -abs(x[0]))[:8]
    lines = [f"**Last 1h, trades >= ${min_usd/1e3:.0f}K:** {len(buys)} buys (${buy_usd/1e6:.1f}M) vs {len(sells)} sells (${sell_usd/1e6:.1f}M)"]
    if buy_usd + sell_usd > 0:
        lean = buy_usd / (buy_usd + sell_usd) * 100
        lines.append(f"**Whale lean:** {lean:.0f}% buy-side")
    for u, p in top:
        side = "\U0001F7E2 BUY " if u > 0 else "\U0001F534 SELL"
        lines.append(f"{side} ${abs(u)/1e6:.2f}M @ ${fnum(p)}")
    if len(buys) + len(sells) == 0:
        lines.append(f"*No single trades above ${min_usd/1e3:.0f}K this hour - quiet whales.*")
    embed = discord.Embed(title=f"\U0001F40B Whale Watch - {symbol}", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Scient Lounge - Binance spot aggTrades")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="lsr", description="Long/Short ratio of top traders (Binance futures)")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def lsr_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    try:
        async with aiohttp.ClientSession() as s:
            acct = await _get_json(s, "https://fapi.binance.com/futures/data/topLongShortAccountRatio",
                                   {"symbol": pair, "period": "1h", "limit": 25}, 15)
            pos = await _get_json(s, "https://fapi.binance.com/futures/data/topLongShortPositionRatio",
                                  {"symbol": pair, "period": "1h", "limit": 25}, 15)
            glob = await _get_json(s, "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                                   {"symbol": pair, "period": "1h", "limit": 2}, 15)
    except Exception:
        acct = pos = glob = None
    if not acct or not isinstance(acct, list):
        await interaction.followup.send(f"No LSR data for **{symbol}** (Binance futures only).")
        return
    def _ratio(d):
        try:
            return float(d[-1]["longShortRatio"])
        except Exception:
            return None
    r_acct = _ratio(acct)
    r_pos = _ratio(pos) if pos and isinstance(pos, list) else None
    r_glob = _ratio(glob) if glob and isinstance(glob, list) else None
    chg = ""
    try:
        prev = float(acct[0]["longShortRatio"])
        if prev:
            chg = f" ({(r_acct - prev) / prev * 100:+.1f}% vs 24h ago)"
    except Exception:
        pass
    lines = []
    if r_acct is not None:
        pct_long = r_acct / (1 + r_acct) * 100
        lines.append(f"**Top traders (accounts):** {r_acct:.2f} - {pct_long:.0f}% long{chg}")
    if r_pos is not None:
        lines.append(f"**Top traders (positions):** {r_pos:.2f}")
    if r_glob is not None:
        lines.append(f"**All accounts:** {r_glob:.2f}")
    read = ""
    if r_acct is not None:
        if r_acct >= 2.5:
            read = "Heavily long-crowded - fuel for downside wicks."
        elif r_acct <= 0.7:
            read = "Short-crowded - squeeze fuel above."
        else:
            read = "Positioning balanced."
    if read:
        lines.append(f"**Read:** {read}")
    embed = discord.Embed(title=f"\u2696\uFE0F Long/Short Ratio - {symbol}", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Scient Lounge - Binance futures, 1h data")
    await interaction.followup.send(embed=embed)


def make_liqzones_image(symbol: str, price: float, zones: list) -> io.BytesIO:
    """Clean two-sided liquidation chart: long-liq clusters below price (red),
    short-liq clusters above price (green). Bar length = estimated intensity."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    longs = sorted([z for z in zones if z["side"] == "long"], key=lambda z: z["price"])
    shorts = sorted([z for z in zones if z["side"] == "short"], key=lambda z: z["price"])

    fig, ax = plt.subplots(figsize=(11, 7.5), facecolor="#131722")
    ax.set_facecolor("#131722")

    bar_h = price * 0.006
    for z in longs:
        ax.barh(z["price"], -z["intensity"], height=bar_h, color="#E5484D", alpha=0.92,
                edgecolor="#ff6b6f", linewidth=0.5)
        dist = (z["price"] - price) / price * 100
        ax.text(-z["intensity"] - 0.03, z["price"], f"{z['lev']}x  {dist:+.1f}%",
                color="#ff8f92", fontsize=8.5, va="center", ha="right")
    for z in shorts:
        ax.barh(z["price"], z["intensity"], height=bar_h, color="#30A46C", alpha=0.92,
                edgecolor="#4fd18b", linewidth=0.5)
        dist = (z["price"] - price) / price * 100
        ax.text(z["intensity"] + 0.03, z["price"], f"{z['lev']}x  {dist:+.1f}%",
                color="#5fe0a0", fontsize=8.5, va="center", ha="left")

    ax.axhline(price, color="#EAECEF", linewidth=1.4)
    ax.text(0, price, f"  ${fnum(price)}  ", color="#131722", fontsize=9.5, fontweight="bold",
            va="center", ha="center", bbox=dict(boxstyle="round,pad=0.3", fc="#EAECEF", ec="none"))

    ax.set_xlim(-1.35, 1.35)
    ax.axvline(0, color="#2A2E39", linewidth=0.8)
    ax.text(-0.7, ax.get_ylim()[1], "LONG liquidations \u25BC", color="#E5484D",
            fontsize=10, ha="center", va="bottom", fontweight="bold")
    ax.text(0.7, ax.get_ylim()[1], "\u25B2 SHORT liquidations", color="#30A46C",
            fontsize=10, ha="center", va="bottom", fontweight="bold")

    ax.set_title(f"{symbol}   Estimated Liquidation Zones", color="#EAECEF", fontsize=13, loc="left", pad=24)
    ax.set_xticks([])
    ax.tick_params(colors="#B2B5BE", labelsize=8)
    ax.yaxis.set_major_formatter(lambda x, _: f"${fnum(x)}")
    ax.grid(color="#1E222D", linewidth=0.5, axis="y")
    for sp in ax.spines.values():
        sp.set_color("#20242E")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, dpi=130, facecolor="#131722", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


@bot.tree.command(name="liqzones", description="Estimated liquidation heatmap - where leverage gets flushed")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def liqzones_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    pair = f"{base}USDT"
    price = await md_price(pair)
    od = await md_oi(pair)
    if price is None or price <= 0:
        await interaction.followup.send(f"Couldn't price **{base}** - liqzones needs a Binance/Bybit perp.")
        return
    # long/short skew from LSR (fallback 1.0 balanced)
    ls_ratio = 1.0
    try:
        async with aiohttp.ClientSession() as s:
            acct = await _get_json(s, "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                                   {"symbol": pair, "period": "1h", "limit": 1}, 15)
        if acct and isinstance(acct, list):
            ls_ratio = float(acct[0]["longShortRatio"])
    except Exception:
        pass
    long_share = ls_ratio / (1 + ls_ratio)
    short_share = 1 - long_share
    oi_usd = (od["oi"] * price) if od else None
    # maintenance margin approx per tier (Binance-like): higher lev -> tighter
    LEV_TIERS = [(5, 0.004), (10, 0.005), (20, 0.008), (25, 0.01), (40, 0.015),
                 (50, 0.02), (75, 0.025), (100, 0.005), (125, 0.004)]
    # assumed position-count weighting: mid leverage most populated, extremes lighter
    TIER_WEIGHT = {5: 0.10, 10: 0.16, 20: 0.15, 25: 0.15, 40: 0.10,
                   50: 0.13, 75: 0.08, 100: 0.09, 125: 0.04}
    zones = []
    for lev, mmr in LEV_TIERS:
        long_liq = price * (1 - 1/lev + mmr)
        short_liq = price * (1 + 1/lev - mmr)
        w = TIER_WEIGHT[lev]
        zones.append({"price": long_liq, "side": "long", "lev": lev, "raw": w * long_share})
        zones.append({"price": short_liq, "side": "short", "lev": lev, "raw": w * short_share})
    # drop zones hugging the price (within 1.5%) - they clutter the center and aren't actionable
    zones = [z for z in zones if abs(z["price"] - price) / price >= 0.015]
    if not zones:
        await interaction.followup.send("Not enough separation to map liquidation zones right now.")
        return
    max_raw = max(z["raw"] for z in zones)
    for z in zones:
        z["intensity"] = max(0.15, z["raw"] / max_raw)
    try:
        buf = await asyncio.to_thread(make_liqzones_image, f"{base}/USDT", price, zones)
    except Exception as e:
        await interaction.followup.send(f"Heatmap render failed: {e}")
        return
    top_long = sorted([z for z in zones if z["side"] == "long"], key=lambda z: -z["intensity"])[:2]
    top_short = sorted([z for z in zones if z["side"] == "short"], key=lambda z: -z["intensity"])[:2]
    lines = [f"**Price:** ${fnum(price)}" + (f" | **OI:** ${oi_usd/1e9:.2f}B" if oi_usd else "")]
    lines.append(f"**Positioning:** {long_share*100:.0f}% long / {short_share*100:.0f}% short")
    lines.append("**\U0001F53B Long liquidations below** (downside magnets):")
    for z in top_long:
        dist = (z["price"] - price) / price * 100
        lines.append(f"   ${fnum(z['price'])} ({dist:+.1f}%) - {z['lev']}x")
    lines.append("**\U0001F53A Short liquidations above** (upside magnets):")
    for z in top_short:
        dist = (z["price"] - price) / price * 100
        lines.append(f"   ${fnum(z['price'])} ({dist:+.1f}%) - {z['lev']}x")
    lines.append("*Estimated from OI + positioning, not exchange-confirmed. Best on BTC/ETH.*")
    f = discord.File(buf, filename=f"{base}_liqzones.png")
    await interaction.followup.send(content="\n".join(lines), file=f)


@bot.tree.command(name="levels", description="Auto-detected support & resistance levels")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL", timeframe="Timeframe for structure")
@app_commands.choices(timeframe=[app_commands.Choice(name=k, value=k) for k in ("1H", "4H", "1D")])
async def levels(interaction: discord.Interaction, coin: str, timeframe: app_commands.Choice[str] = None):
    await interaction.response.defer()
    tfv = timeframe.value if timeframe else "4H"
    interval = {"1H": "1h", "4H": "4h", "1D": "1d"}[tfv]
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    klines = await md_klines(pair, interval, 300)
    if not klines:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit.")
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


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="\U0001F916 Quant Terminal - Command Guide",
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
            "`/rvol` - volatility regime, compressed or wild"
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
            "Or use the buttons in #select-analyst-alerts\n"
            "`/quiz` - trading quizzes, basics to advanced"
        ),
        inline=False,
    )
    embed.set_footer(text="Scient Lounge - Quant Terminal")
    return embed


PLAN_CHOICES = [app_commands.Choice(name=v["label"], value=k) for k, v in SUB_PLANS.items()]


async def _sub_grant_role(guild: discord.Guild, uid: int) -> bool:
    try:
        member = guild.get_member(uid) or await guild.fetch_member(uid)
        role = guild.get_role(SUB_ROLE_ID)
        if member and role and role not in member.roles:
            await member.add_roles(role, reason="Subscription granted")
        return True
    except Exception as e:
        print(f"[subs] grant role error {uid}: {e}", flush=True)
        return False


async def _sub_remove_role(guild: discord.Guild, uid: int) -> bool:
    try:
        member = guild.get_member(uid) or await guild.fetch_member(uid)
        role = guild.get_role(SUB_ROLE_ID)
        if member and role and role in member.roles:
            await member.remove_roles(role, reason="Subscription expired/revoked")
        return True
    except Exception as e:
        print(f"[subs] remove role error {uid}: {e}", flush=True)
        return False


@bot.tree.command(name="grant", description="(Admin) Grant or extend a Pro subscription")
@app_commands.describe(member="Who gets Pro", plan="Which plan", note="Optional note, e.g. tx hash or payment ref")
@app_commands.choices(plan=PLAN_CHOICES)
async def grant_cmd(interaction: discord.Interaction, member: discord.Member, plan: app_commands.Choice[str], note: str = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    p = SUB_PLANS[plan.value]
    subs = load_subs()
    uid = str(member.id)
    now = datetime.now(timezone.utc)
    existing = subs.get(uid)
    if existing and existing.get("expires"):
        try:
            cur_exp = datetime.fromisoformat(existing["expires"])
            base = max(cur_exp, now)  # extend from current expiry if still active
        except Exception:
            base = now
    else:
        base = now
    expires = base + timedelta(days=p["days"])
    subs[uid] = {
        "name": member.display_name,
        "plan": plan.value,
        "price": p["price"],
        "started": existing.get("started") if existing else now.isoformat(),
        "expires": expires.isoformat(),
        "reminded": False,
        "note": (note or "")[:120],
        "history": (existing.get("history", []) if existing else []) + [
            {"plan": plan.value, "price": p["price"], "at": now.isoformat(), "by": interaction.user.display_name}
        ],
    }
    save_subs(subs)
    ok = await _sub_grant_role(interaction.guild, member.id)
    try:
        dm = discord.Embed(
            title="Scient Pro activated \U0001F389",
            description=(
                f"Your **{p['label']}** is live.\n"
                f"**Access until:** {expires.strftime('%d %b %Y')}\n\n"
                f"You'll get a renewal reminder {SUB_REMINDER_DAYS} days before it ends."
            ),
            color=GOLD,
        )
        await member.send(embed=dm)
    except Exception:
        pass
    await interaction.followup.send(
        f"\u2705 **{member.display_name}** -> {p['label']}\n"
        f"Expires: **{expires.strftime('%d %b %Y')}** | role {'granted' if ok else 'FAILED - check manually'}",
        ephemeral=True,
    )


@bot.tree.command(name="revoke", description="(Admin) Revoke a Pro subscription")
@app_commands.describe(member="Whose subscription to revoke")
async def revoke_cmd(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    subs = load_subs()
    if str(member.id) not in subs:
        await interaction.followup.send("No subscription on record for that member.", ephemeral=True)
        return
    subs.pop(str(member.id), None)
    save_subs(subs)
    await _sub_remove_role(interaction.guild, member.id)
    await interaction.followup.send(f"Subscription revoked for **{member.display_name}** - role removed.", ephemeral=True)


@bot.tree.command(name="subs", description="(Admin) Active subscriptions overview")
async def subs_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    subs = load_subs()
    if not subs:
        await interaction.followup.send("No active subscriptions on record.", ephemeral=True)
        return
    now = datetime.now(timezone.utc)
    rows = []
    total_rev = 0
    for uid, s in subs.items():
        try:
            exp = datetime.fromisoformat(s["expires"])
            days_left = (exp - now).days
        except Exception:
            days_left = -1
        total_rev += sum(h.get("price", 0) for h in s.get("history", []))
        flag = "\U0001F7E2" if days_left > SUB_REMINDER_DAYS else ("\U0001F7E1" if days_left >= 0 else "\U0001F534")
        rows.append((days_left, f"{flag} **{s.get('name','?')}** - {SUB_PLANS.get(s.get('plan'), {}).get('label', s.get('plan'))} - {days_left}d left"))
    rows.sort(key=lambda r: r[0])
    embed = discord.Embed(title="\U0001F4B3 Subscriptions", color=NAVY, timestamp=now)
    embed.description = "\n".join(r[1] for r in rows[:30])
    embed.add_field(name="Total", value=f"{len(subs)} active | ${total_rev} lifetime recorded", inline=False)
    embed.set_footer(text="Scient Lounge - subscription system")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="mysub", description="Check your Pro subscription status")
async def mysub_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    subs = load_subs()
    s = subs.get(str(interaction.user.id))
    if not s:
        await interaction.followup.send("No subscription on record. If you have Pro via referral (Scient Pass), that's managed separately.", ephemeral=True)
        return
    try:
        exp = datetime.fromisoformat(s["expires"])
        days_left = (exp - datetime.now(timezone.utc)).days
        await interaction.followup.send(
            f"**{SUB_PLANS.get(s.get('plan'), {}).get('label', 'Pro')}**\n"
            f"Expires: **{exp.strftime('%d %b %Y')}** ({days_left} days left)",
            ephemeral=True,
        )
    except Exception:
        await interaction.followup.send("Couldn't read your subscription record - ping an admin.", ephemeral=True)


@tasks.loop(time=dt_time(hour=4, minute=30, tzinfo=timezone.utc))  # 10:00 AM IST daily
async def subs_check_loop():
    subs = load_subs()
    if not subs:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    now = datetime.now(timezone.utc)
    changed = False
    for uid, s in list(subs.items()):
        try:
            exp = datetime.fromisoformat(s["expires"])
        except Exception:
            continue
        days_left = (exp - now).days
        if exp <= now:
            # expired: remove role, DM, drop record
            await _sub_remove_role(guild, int(uid))
            try:
                user = bot.get_user(int(uid)) or await bot.fetch_user(int(uid))
                await user.send(
                    "Your **Scient Pro** access has ended. It's been great having you in the full lounge - "
                    "renew any time via the payment options in the server to jump back in. \U0001F91D"
                )
            except Exception:
                pass
            subs.pop(uid, None)
            changed = True
            print(f"[subs] expired + removed: {s.get('name')} ({uid})", flush=True)
        elif days_left <= SUB_REMINDER_DAYS and not s.get("reminded"):
            try:
                user = bot.get_user(int(uid)) or await bot.fetch_user(int(uid))
                await user.send(
                    f"Heads up - your **Scient Pro** expires in **{max(days_left,0)} day(s)** "
                    f"({exp.strftime('%d %b %Y')}). Renew via the payment options in the server to keep uninterrupted access. \U0001F514"
                )
            except Exception:
                pass
            s["reminded"] = True
            changed = True
            print(f"[subs] reminder sent: {s.get('name')} ({uid})", flush=True)
    if changed:
        save_subs(subs)


@subs_check_loop.before_loop
async def before_subs_check():
    await bot.wait_until_ready()


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


def build_trades_csv(trades: list, analyst_name: str) -> io.BytesIO:
    import csv as _csv
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["date", "analyst", "pair", "direction", "timeframe", "entry_type",
                "entry", "entry2", "sl", "risk_pct", "tp1", "tp2", "tp3",
                "fills", "avg_exit", "result", "result_r", "status", "frameworks", "edited"])
    for t in trades:
        fills = "; ".join(f"{f.get('label','')}@{f.get('price','')}x{f.get('pct','')}%" for f in t.get("fills", []) or [])
        status = "CLOSED" if t.get("closed") else ("BE-set" if t.get("be") else "OPEN")
        created = t.get("created_at", "") or ""
        w.writerow([
            created[:10], analyst_name, t.get("pair", ""), t.get("direction", ""),
            t.get("timeframe", ""), t.get("entry_type", ""),
            t.get("entry", ""), t.get("entry2", ""), t.get("sl", ""), t.get("risk", ""),
            t.get("tp1", ""), t.get("tp2", ""), t.get("tp3", ""),
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
    await interaction.followup.send(embed=embed, view=StatsCSVView(mine, target.display_name), ephemeral=True)


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




# ═══════════════════════════════════════════════════════════════════════════
#  SIGMA RESULTS BOARD
#  - watches trades.json / spot plays for newly closed entries, posts each to
#    #results-board as a permanent card with original Posted/Closed timestamps
#  - maintains a pinned summary embed (combined + per-analyst)
#  - weekly recap image every Monday 10:00 IST -> #monthly-recap
#  - first run backfills the entire DB chronologically (rate-limit safe)
# ═══════════════════════════════════════════════════════════════════════════

RESULTS_CHANNEL_ID = 1540681895812005928       # results-board
INVALIDATIONS_CHANNEL_ID = 0                   # off (feature cut)
RECAP_CHANNEL_ID = 1500920688515616922         # monthly-recap
RESULTS_POLL_MIN = 2
RECAP_DAY = 0                                  # Monday
RECAP_UTC = dt_time(hour=4, minute=30, tzinfo=timezone.utc)  # 10:00 IST

SIGMA_BG = "#0A0C10"; SIGMA_CARD = "#141A22"; SIGMA_SLATE = "#2A3644"
SIGMA_CYAN = "#22D3C5"; SIGMA_AMBER = "#E8590C"; SIGMA_PAPER = "#EEF3F8"
SIGMA_ASH = "#8593A6"; SIGMA_GREEN = "#16C784"; SIGMA_RED = "#EA3943"
SIGMA_EMBED_CYAN = discord.Color.from_str(SIGMA_CYAN)

RESULTS_FILE = Path(__file__).with_name("results_board.json")
def load_results() -> dict: return _load(RESULTS_FILE)
def save_results(d: dict): _save(RESULTS_FILE, d)


def _res_ts(iso) -> int:
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except Exception:
        return int(datetime.now(timezone.utc).timestamp())


def _res_all_closed():
    out = []
    for mid, t in load_trades().items():
        if t.get("closed"):
            out.append(("fut", mid, t))
    for mid, p in load_spot().items():
        if p.get("closed"):
            out.append(("spot", mid, p))
    out.sort(key=lambda x: x[2].get("closed_at") or x[2].get("created_at") or "")
    return out


def sigma_tracking_since():
    dates = [t.get("created_at") for t in load_trades().values() if t.get("created_at")]
    dates += [p.get("created_at") for p in load_spot().values() if p.get("created_at")]
    return min(dates) if dates else None


def _res_totals(entries):
    rs = [t.get("result_r") for k, _, t in entries
          if k == "fut" and isinstance(t.get("result_r"), (int, float))]
    wins = sum(1 for _, _, t in entries if t.get("result") == "WIN")
    losses = sum(1 for _, _, t in entries if t.get("result") == "LOSS")
    be = sum(1 for _, _, t in entries if t.get("result") == "BE")
    inv = sum(1 for _, _, t in entries if t.get("result") == "INVALID")
    decided = wins + losses
    return {"n": len(entries), "wins": wins, "losses": losses, "be": be, "inv": inv,
            "wr": (wins / decided * 100) if decided else 0,
            "total_r": sum(rs) if rs else 0.0, "graded": len(rs),
            "best": max(rs) if rs else None, "worst": min(rs) if rs else None}


def build_results_summary_embed() -> discord.Embed:
    entries = _res_all_closed()
    tot = _res_totals(entries)
    since = sigma_tracking_since()
    embed = discord.Embed(title="Results Board - Full Log", color=SIGMA_EMBED_CYAN,
                          timestamp=datetime.now(timezone.utc))
    head = []
    if since:
        head.append(f"**Tracking since:** <t:{_res_ts(since)}:D>")
    head.append("Every entry below was logged by the bot **at the moment the call was "
                "posted** - the `Posted` timestamp on each card is the original, not added later.")
    embed.description = "\n".join(head)
    embed.add_field(name="Calls closed", value=str(tot["n"]), inline=True)
    embed.add_field(name="Win rate", value=f"{tot['wr']:.0f}% ({tot['wins']}W / {tot['losses']}L)", inline=True)
    embed.add_field(name="Net result", value=f"{tot['total_r']:+.2f}R ({tot['graded']} graded)", inline=True)
    embed.add_field(name="Breakeven / Invalidated", value=f"{tot['be']} / {tot['inv']}", inline=True)
    if tot["best"] is not None:
        embed.add_field(name="Best / Worst", value=f"{tot['best']:+g}R / {tot['worst']:+g}R", inline=True)
    per = {}
    for k, mid, t in entries:
        per.setdefault(t.get("analyst_name", "?"), []).append((k, mid, t))
    lines = []
    for name, ent in sorted(per.items()):
        s = _res_totals(ent)
        lines.append(f"**{name}** - {s['n']} closed - {s['wr']:.0f}% WR - {s['total_r']:+.2f}R")
    if lines:
        embed.add_field(name="By analyst", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="Sigma Trading - setups, not signals - wins and losses both logged - not financial advice")
    return embed


def build_result_entry_embed(kind: str, t: dict) -> discord.Embed:
    res = t.get("result", "?")
    color = {"WIN": GREEN, "LOSS": RED, "BE": GREY, "INVALID": DGREY}.get(res, GREY)
    if kind == "spot":
        rtxt = f" {t['result_pct']}" if t.get("result_pct") else ""
        title = f"[{res}]{rtxt} - SPOT {t.get('pair', '?').upper()}"
    else:
        r = t.get("result_r")
        rtxt = f" {r:+.2f}R" if isinstance(r, (int, float)) else ""
        d = "LONG" if t.get("direction") == "LONG" else "SHORT"
        tfs = tf(t)
        title = f"[{res}]{rtxt} - {d} {t.get('pair', '?').upper()}" + (f" {tfs}" if tfs else "")
    embed = discord.Embed(title=title, color=color)
    if kind == "fut":
        embed.add_field(name="Entry", value=entry_display(t, marks=False) or "-", inline=True)
        embed.add_field(name="Invalidation", value=str(t.get("sl") or "-"), inline=True)
        if t.get("avg_exit") is not None:
            embed.add_field(name="Avg exit", value=fnum(t["avg_exit"]), inline=True)
    else:
        embed.add_field(name="DCA zone", value=str(t.get("dca_zone") or "-"), inline=True)
        if t.get("avg_entry"):
            embed.add_field(name="Avg entry", value=str(t["avg_entry"]), inline=True)
        if t.get("avg_exit"):
            embed.add_field(name="Avg exit", value=str(t["avg_exit"]), inline=True)
    posted = t.get("created_at")
    closed = t.get("closed_at") or posted
    embed.add_field(name="Timeline",
                    value=f"Posted <t:{_res_ts(posted)}:f>\nClosed <t:{_res_ts(closed)}:f>",
                    inline=False)
    embed.set_author(name=t.get("analyst_name", "?"), icon_url=t.get("analyst_avatar") or None)
    embed.set_footer(text="Sigma Trading - logged at post time - not financial advice")
    return embed


async def refresh_results_summary():
    if not RESULTS_CHANNEL_ID:
        return
    ch = bot.get_channel(RESULTS_CHANNEL_ID)
    if ch is None:
        return
    state = load_results()
    embed = build_results_summary_embed()
    msg_id = state.get("summary_message_id")
    if msg_id:
        try:
            msg = await ch.fetch_message(msg_id)
            await msg.edit(embed=embed)
            return
        except (discord.NotFound, discord.HTTPException):
            pass
    try:
        msg = await ch.send(embed=embed)
    except Exception as e:
        print(f"[results] summary send error: {e}", flush=True)
        return
    try:
        await msg.pin()
    except discord.HTTPException:
        pass
    state["summary_message_id"] = msg.id
    save_results(state)


@tasks.loop(minutes=RESULTS_POLL_MIN)
async def results_watch_loop():
    if not RESULTS_CHANNEL_ID:
        return
    ch = bot.get_channel(RESULTS_CHANNEL_ID)
    if ch is None:
        return
    state = load_results()
    posted = set(state.get("posted", []))
    new = [(k, mid, t) for k, mid, t in _res_all_closed() if f"{k}:{mid}" not in posted]
    if not new:
        return
    inv_ch = bot.get_channel(INVALIDATIONS_CHANNEL_ID) if INVALIDATIONS_CHANNEL_ID else None
    for k, mid, t in new:
        try:
            await ch.send(embed=build_result_entry_embed(k, t))
        except Exception as e:
            print(f"[results] post error {mid}: {e}", flush=True)
            continue
        if inv_ch and t.get("result") in ("LOSS", "INVALID"):
            try:
                await inv_ch.send(embed=build_result_entry_embed(k, t))
            except Exception as e:
                print(f"[results] mirror error: {e}", flush=True)
        posted.add(f"{k}:{mid}")
        state["posted"] = list(posted)
        save_results(state)
        await asyncio.sleep(1.5)
    await refresh_results_summary()
    print(f"[results] posted {len(new)} closed trade(s)", flush=True)


@results_watch_loop.before_loop
async def _before_results_watch():
    await bot.wait_until_ready()


# ---------------- weekly recap image ----------------

def _sigma_fonts():
    try:
        from matplotlib import font_manager
        fdir = Path(__file__).with_name("fonts")
        fams = {"disp": "DejaVu Sans", "mono": "DejaVu Sans Mono"}
        if fdir.exists():
            found = set()
            for f in fdir.glob("*.ttf"):
                try:
                    font_manager.fontManager.addfont(str(f))
                    for fe in font_manager.fontManager.ttflist:
                        if str(f) == fe.fname:
                            found.add(fe.name)
                except Exception:
                    continue
            for name in found:
                low = name.lower()
                if "grotesk" in low or "sigmadisplay" in low:
                    fams["disp"] = name
                if "jetbrains" in low or "sigmamono" in low:
                    fams["mono"] = name
        return fams
    except Exception:
        return {"disp": "DejaVu Sans", "mono": "DejaVu Sans Mono"}


def make_recap_image(stats: dict) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    fams = _sigma_fonts()
    fig = plt.figure(figsize=(10.8, 13.5), facecolor=SIGMA_BG)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 108); ax.set_ylim(0, 135)
    ax.axis("off"); ax.set_facecolor(SIGMA_BG)

    def box(x, y, w, h, fc=SIGMA_CARD, ec=SIGMA_SLATE):
        ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h,
                     boxstyle="round,pad=0,rounding_size=1.4",
                     facecolor=fc, edgecolor=ec, linewidth=1.2))

    box(7, 122, 9, 9, fc=SIGMA_CYAN, ec=SIGMA_CYAN)
    ax.plot([14.2, 9.0, 11.8, 9.0, 14.2], [129.2, 129.2, 126.5, 123.8, 123.8],
            color=SIGMA_BG, linewidth=3.4, solid_capstyle="butt")
    ax.text(19, 126.8, "SIGMA TRADING", color=SIGMA_PAPER, fontsize=21,
            fontweight="bold", family=fams["disp"], va="center")
    ax.text(19, 123.4, stats["range_txt"], color=SIGMA_ASH, fontsize=11,
            family=fams["mono"], va="center")
    ax.plot([7, 101], [119.5, 119.5], color=SIGMA_SLATE, linewidth=1.2)

    ax.text(7, 109, "WEEKLY", color=SIGMA_PAPER, fontsize=34, fontweight="bold", family=fams["disp"])
    ax.text(7, 101, "RECAP", color=SIGMA_CYAN, fontsize=34, fontweight="bold", family=fams["disp"])

    cells = [("CALLS CLOSED", str(stats["n"]), SIGMA_PAPER),
             ("CLOSED GREEN", str(stats["wins"]), SIGMA_GREEN),
             ("CLOSED RED", str(stats["losses"]), SIGMA_RED),
             ("NET", f"{stats['total_r']:+.1f}R", SIGMA_CYAN)]
    for i, (label, val, col) in enumerate(cells):
        x = 7 + (i % 2) * 48.5; y = 78 - (i // 2) * 19
        box(x, y, 45.5, 16)
        ax.text(x + 3, y + 11.5, label, color=SIGMA_ASH, fontsize=10, family=fams["mono"])
        ax.text(x + 3, y + 3.5, val, color=col, fontsize=25, fontweight="bold", family=fams["mono"])

    y = 52
    if stats["best_lines"]:
        ax.text(7, y, "BEST", color=SIGMA_CYAN, fontsize=10.5, family=fams["mono"]); y -= 4.5
        for line, r in stats["best_lines"]:
            ax.text(7, y, line, color=SIGMA_PAPER, fontsize=12.5, family=fams["disp"])
            ax.text(101, y, r, color=SIGMA_GREEN, fontsize=12.5, family=fams["mono"], ha="right")
            y -= 4.6
        y -= 2.5
    if stats["worst_lines"]:
        ax.text(7, y, "WORST", color=SIGMA_AMBER, fontsize=10.5, family=fams["mono"]); y -= 4.5
        for line, r in stats["worst_lines"]:
            ax.text(7, y, line, color=SIGMA_PAPER, fontsize=12.5, family=fams["disp"])
            ax.text(101, y, r, color=SIGMA_RED, fontsize=12.5, family=fams["mono"], ha="right")
            y -= 4.6
    ax.plot([7, 101], [max(y, 15.5), max(y, 15.5)], color=SIGMA_SLATE, linewidth=1.2)
    ax.text(7, 11.5, "Every call was posted before it played out.", color=SIGMA_ASH,
            fontsize=12, family=fams["disp"])
    ax.text(7, 7.8, "Full log open in #results-board.", color=SIGMA_ASH, fontsize=12, family=fams["disp"])
    ax.text(7, 3.4, "SETUPS, NOT SIGNALS", color=SIGMA_CYAN, fontsize=10.5, family=fams["mono"])

    buf = io.BytesIO()
    fig.savefig(buf, dpi=100, facecolor=SIGMA_BG)
    plt.close(fig)
    buf.seek(0)
    return buf


def _sigma_week_stats(days: int = 7) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries = []
    for k, mid, t in _res_all_closed():
        try:
            if datetime.fromisoformat(t["closed_at"]) >= cutoff:
                entries.append((k, mid, t))
        except Exception:
            continue
    tot = _res_totals(entries)
    futs = [(k, m, t) for k, m, t in entries
            if k == "fut" and isinstance(t.get("result_r"), (int, float))]
    futs.sort(key=lambda x: x[2]["result_r"], reverse=True)

    def _line(t):
        d = "long" if t.get("direction") == "LONG" else "short"
        nm = t.get("analyst_name", "")
        return f"{t.get('pair', '?').upper()} {d}" + (f", {nm}" if nm else "")

    tot["best_lines"] = [(_line(t), f"{t['result_r']:+.1f}R") for _, _, t in futs[:2]
                         if t["result_r"] > 0]
    tot["worst_lines"] = [(_line(t), f"{t['result_r']:+.1f}R") for _, _, t in futs[-2:]
                          if t["result_r"] < 0]
    end = datetime.now(IST); start = end - timedelta(days=days)
    tot["range_txt"] = f"week of {start.strftime('%d')}-{end.strftime('%d %b %Y')}"
    return tot


async def post_weekly_recap() -> bool:
    stats = _sigma_week_stats(7)
    if stats["n"] == 0:
        print("[recap] skipped - no closed trades this week", flush=True)
        return False
    ch = bot.get_channel(RECAP_CHANNEL_ID or RESULTS_CHANNEL_ID)
    if ch is None:
        return False
    try:
        buf = await asyncio.to_thread(make_recap_image, stats)
    except Exception as e:
        print(f"[recap] render error: {e}", flush=True)
        return False
    content = f"**Weekly Recap** - {stats['n']} calls, {stats['total_r']:+.2f}R net."
    if RESULTS_CHANNEL_ID:
        content += f" Full log in <#{RESULTS_CHANNEL_ID}>."
    try:
        await ch.send(content=content, file=discord.File(buf, filename="sigma_weekly_recap.png"))
        return True
    except Exception as e:
        print(f"[recap] post error: {e}", flush=True)
        return False


@tasks.loop(time=RECAP_UTC)
async def sigma_recap_loop():
    if datetime.now(timezone.utc).weekday() != RECAP_DAY:
        return
    await post_weekly_recap()


@sigma_recap_loop.before_loop
async def _before_sigma_recap():
    await bot.wait_until_ready()


@bot.tree.command(name="results", description="Public results scorecard - every call logged, wins and losses")
async def results_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_results_summary_embed())


@bot.tree.command(name="recap_now", description="(Admin) Post the weekly recap image right now")
async def recap_now_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ok = await post_weekly_recap()
    await interaction.followup.send("Recap posted." if ok else
                                    "Recap failed or no closed trades this week - check logs.",
                                    ephemeral=True)


@bot.listen("on_ready")
async def _sigma_results_on_ready():
    if RESULTS_CHANNEL_ID and not results_watch_loop.is_running():
        results_watch_loop.start()
    if not sigma_recap_loop.is_running():
        sigma_recap_loop.start()
    print("[results] board watcher armed", flush=True)

# ═════════════════════════════ END SIGMA RESULTS BOARD ═════════════════════════════


bot.run(BOT_TOKEN)

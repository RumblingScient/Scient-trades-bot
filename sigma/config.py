"""sigma.config - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent   # repo root: .env, logs/, *.json live here



OPS_CHANNEL_ID = int(os.getenv("SIGMA_OPS_CHANNEL_ID", "0") or 0)   # BACKSTAGE ops channel for alerts

_env_file = _ROOT.joinpath(".env")

if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

BOT_TOKEN = os.getenv("SCIENT_BOT_TOKEN", "PASTE_TOKEN_HERE")

OPS_CHANNEL_ID = int(os.getenv("SIGMA_OPS_CHANNEL_ID", "0") or 0)   # re-read now that .env is loaded

TWITTERAPIS_KEY = os.getenv("TWITTERAPIS_KEY", "")

GUILD_ID = 1213101801675554846

TRADES_CHANNEL_ID = 1525147189360332840

TRADE_UPDATES_CHANNEL_ID = 1525863205174378617

OPEN_BOARD_CHANNEL_ID = 1525863082256109690

X_FEED_CHANNEL_ID = 1525862152076923020

SPOT_CHANNEL_ID = TRADES_CHANNEL_ID  # spot plays post in the same channel as futures setups

# Pro roles that unlock full access (either one triggers the Pro welcome DM)
PRO_ROLE_IDS = {1500476858477576374, 1484576832362905630}  # Scient Pass (referral), Scient Pro (payment)

SUB_ROLE_ID = 1484576832362905630  # role granted/removed by the subscription system (Scient Pro)

SUB_PLANS = {
    "1month":   {"days": 30,    "price": 100,  "label": "Sigma Pro - Monthly ($100)",         "short": "Monthly"},
    "3months":  {"days": 90,    "price": 270,  "label": "Sigma Pro - 3 Months ($270)",        "short": "3 months"},
    "6months":  {"days": 180,   "price": 500,  "label": "Sigma Pro - 6 Months ($500)",        "short": "6 months"},
    "1year":    {"days": 365,   "price": 1000, "label": "Sigma Pro - Yearly ($1,000)",        "short": "Yearly"},
    "lifetime": {"days": 36500, "price": 1999, "label": "Founding Lifetime ($1,999)",         "short": "Lifetime", "seats": 25},
}

SUB_REMINDER_DAYS = (7, 3, 1)   # DM a renewal reminder at each of these days-before-expiry

ALUMNI_ROLE_ID = int(os.getenv("SIGMA_ALUMNI_ROLE_ID", "0") or 0)        # lapsed members, read-only (0 = off)
MOD_LOG_CHANNEL_ID = int(os.getenv("SIGMA_MODLOG_CHANNEL_ID", "0") or 0)  # every grant/revoke/expiry lands here (0 = ops channel)

# ─── Payments: SOL to a fixed wallet, unique amount per session, chain-verified ───
PAYMENT_CHANNEL_ID = int(os.getenv("SIGMA_PAYMENT_CHANNEL_ID", "0") or 0)   # #join-via-payment
SOL_WALLET = os.getenv("SIGMA_SOL_WALLET", "").strip()                      # receiving address - .env only
SOL_RPC = os.getenv("SIGMA_SOL_RPC", "https://api.mainnet-beta.solana.com").strip()
PAY_SESSION_MIN = 30          # quote valid this long
PAY_LATE_GRACE_H = 24         # a payment that lands after expiry still matches within this window
PAY_MATCH_TOL = 0.000005      # SOL - exact-amount match tolerance (wallets send 9-decimal exact; quotes are 0.00001 apart)
PAY_FEE_SLACK = 0.02          # SOL - exchange withdrawals arrive short by their fee; accept if it points to ONE open quote
PAY_MIN_SOL = 0.005           # ignore dust below this (no alert, just a log line)
PAY_VALUE_FLOOR = 0.95        # received SOL must still be worth >= 95% of the quoted USD at the time it lands
PAY_POLL_SEC = 30
# tokens accepted on Solana. SOL = native; the stables are SPL mints (mainnet). Amounts for stables are USD + unique cents.
PAY_TOKENS = {
    "SOL":  {"mint": None, "decimals": 9, "stable": False, "tol": 0.000005, "slack": 0.02,  "min": 0.005, "emoji": "\U0001F7E3"},
    "USDC": {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6, "stable": True, "tol": 0.0005, "slack": 0.0, "min": 0.5, "emoji": "\U0001F4B5"},
    "USDT": {"mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "decimals": 6, "stable": True, "tol": 0.0005, "slack": 0.0, "min": 0.5, "emoji": "\U0001F4B5"},
}

FREE_ALERT_LIMIT = 5      # max active alerts for non-pro members

ALERT_CHECK_MIN = 3       # how often (minutes) to check alert prices

LIQ_CHANNEL_ID = 1535755722678341733  # #liquidations feed

# ---------------- SIGMA CHART THEME ----------------
SG_OBS = "#0A0C10"    # canvas

SG_GRA = "#141A22"    # panels

SG_CARD = "#1A222C"   # raised

SG_SLATE = "#2A3644"  # grid/borders

SG_ASH = "#8593A6"    # muted text

SG_PAPER = "#EEF3F8"  # primary text

SG_CYAN = "#22D3C5"   # primary accent

SG_CYAND = "#0E8F87"

SG_AMBER = "#E8590C"  # Scient accent

SG_LONG = "#16C784"   # semantic profit only

SG_SHORT = "#EA3943"  # semantic loss only

# ---------------------------------------------------
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

DISCORD_INVITE = "https://discord.gg/SigmaTrading"

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

EMA_COLORS = ["#E8590C", "#FAC775", SG_CYAN, "#5B9CFF"]

ANALYST_ROLE_NAME = "Analyst"

PING_ROLE_ID = 1525861312729452704

X_PING_ROLE_ID = 1525861448088031462

EDIT_WINDOW_MIN = 1440  # minutes after posting during which /setup edit is allowed (24h)

# ---- X auto-feed config ----
X_AUTO_USERNAME = "Crypto_Scient"

X_POLL_MINUTES = 30

X_AUTO_ENABLED = True

ANALYSTS = {
    "scient":  {"color": "#1C4E80", "ping_role_id": 1481692018211291186, "user_ids": [249880856993202187]},
    "owais":   {"color": "#7C3AED", "ping_role_id": 1498738610118066286, "user_ids": [1120017600026513468]},
    "94":      {"color": "#2E7D32", "ping_role_id": 1493498310558748742, "user_ids": [1268246432197120090]},
}

JOURNAL_FILE = _ROOT.joinpath("trades.json")

BOARD_FILE = _ROOT.joinpath("board.json")

SPOT_FILE = _ROOT.joinpath("spot_plays.json")

SPOT_BOARD_FILE = _ROOT.joinpath("spot_board.json")

XSEEN_FILE = _ROOT.joinpath("x_posted.json")

IST = timezone(timedelta(hours=5, minutes=30))

NAVY = discord.Color.from_str("#1C4E80")

GREEN = discord.Color.from_str("#2E7D32")

RED = discord.Color.from_str("#C62828")

BLUE = discord.Color.from_str(SG_CYAN)

GOLD = discord.Color.from_str("#C9A227")

GREY = discord.Color.light_grey()

DGREY = discord.Color.dark_grey()

# Discord allows 25 choices per option - keep this list at 25 max.
FRAMEWORKS = [
    # levels / auction
    "FRVP / POC", "Value Area (VAH/VAL)", "HVN / LVN", "Anchored VWAP",
    # ranges
    "Range (sweep-reclaim)", "Deviation Reclaim", "Range Breakout + Retest", "S/R Flip",
    # structure / SMC
    "BOS / MSS", "CHoCH", "Order Block", "FVG", "OTE (0.62-0.79)", "Liquidity Sweep",
    # cycles / phases
    "AMD", "Wyckoff Accumulation", "Wyckoff Distribution",
    # exhaustion / momentum
    "Three Drives", "RSI Divergence", "Fib Pocket (0.75/0.786)",
    # trend / patterns
    "EMA Cross", "Trendline Break", "Chart Pattern (H&S, wedge, flag)", "Higher-TF Level",
    "Other",
]

assert len(FRAMEWORKS) <= 25, "Discord caps choices at 25"

SPOT_STATUSES = ["WATCHING", "ACCUMULATING", "HOLDING", "TRIMMED", "DISTRIBUTING"]

ANALYST_CHOICES = [app_commands.Choice(name=k.capitalize(), value=k) for k in ANALYSTS.keys()]

RESULTS_CHANNEL_ID = 1540681895812005928       # results-board

INVALIDATIONS_CHANNEL_ID = 0                   # off (feature cut)

RECAP_CHANNEL_ID = 1500920688515616922         # monthly-recap

RESULTS_POLL_MIN = 2

RECAP_DAY = 0                                  # Monday

RECAP_UTC = dt_time(hour=4, minute=30, tzinfo=timezone.utc)  # 10:00 IST

# Only these analysts appear on the public results board (quant/Terminal entries
# in trades.json are excluded). Empty set = allow everyone.
RESULTS_ANALYST_IDS = {
    249880856993202187,   # Scient
    1120017600026513468,  # Owais
    1268246432197120090,  # 94
}

SIGMA_BG = "#0A0C10"; SIGMA_CARD = "#141A22"; SIGMA_SLATE = "#2A3644"

SIGMA_BG = "#0A0C10"; SIGMA_CARD = "#141A22"; SIGMA_SLATE = "#2A3644"

SIGMA_BG = "#0A0C10"; SIGMA_CARD = "#141A22"; SIGMA_SLATE = "#2A3644"

SIGMA_CYAN = "#22D3C5"; SIGMA_AMBER = "#E8590C"; SIGMA_PAPER = "#EEF3F8"

SIGMA_CYAN = "#22D3C5"; SIGMA_AMBER = "#E8590C"; SIGMA_PAPER = "#EEF3F8"

SIGMA_CYAN = "#22D3C5"; SIGMA_AMBER = "#E8590C"; SIGMA_PAPER = "#EEF3F8"

SIGMA_ASH = "#8593A6"; SIGMA_GREEN = "#16C784"; SIGMA_RED = "#EA3943"

SIGMA_ASH = "#8593A6"; SIGMA_GREEN = "#16C784"; SIGMA_RED = "#EA3943"

SIGMA_ASH = "#8593A6"; SIGMA_GREEN = "#16C784"; SIGMA_RED = "#EA3943"

SIGMA_EMBED_CYAN = discord.Color.from_str(SIGMA_CYAN)

RESULTS_FILE = _ROOT.joinpath("results_board.json")

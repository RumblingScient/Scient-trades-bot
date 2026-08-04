# scient_trades_bot.py
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
from discord.ext import tasks
import os, json, re, hashlib, aiohttp
from pathlib import Path
from datetime import datetime, timezone, timedelta

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
        m1 = " ✓" if t.get("entry1_filled") else ""
        m2 = " ✓" if t.get("entry2_filled") else ""
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
        return "MOVED TO BREAKEVEN" + pct_txt
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
            return base + f" · edited {_ed.strftime('%d/%m %I:%M %p')}"
        except Exception:
            return base + " · edited"
    if t.get("edited"):
        return base + " · edited"
    return base


def build_embed(t: dict, image_url: str = None) -> discord.Embed:
    is_long = t["direction"] == "LONG"
    closed = t.get("closed")
    result = t.get("result")
    try:
        color = discord.Color.from_str(t.get("analyst_color") or "#1C4E80")
    except Exception:
        color = NAVY
    arrow = "🟢 LONG" if is_long else "🔴 SHORT"
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
    title = f"{prefix}🟢 SPOT | {p['pair'].upper()}"
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
            d = "🟢 L" if t["direction"] == "LONG" else "🔴 S"
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
            lines.append(f"🪙 **{p['pair'].upper()}** - zone `{p['dca_zone']}`{avg} - {spot_status_line(p)} - [view]({jump_url(p)})")
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
    print(f"Logged in as {bot.user} - commands synced.")


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
            "**X Updates** - get pinged when new X posts are shared"
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
    entry="Entry price (Entry 1 if DCA)",
    stop_loss="SL price",
    risk="Account risk (just a number = %, e.g. 1 shows as 1%)",
    entry_type="Market (filled now), Limit single, or Limit DCA (two entries)",
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
async def trade(interaction: discord.Interaction, pair: str, direction: app_commands.Choice[str], entry: str, stop_loss: str, risk: str, entry_type: app_commands.Choice[str], entry2: str = None, framework: app_commands.Choice[str] = None, framework2: app_commands.Choice[str] = None, chart: discord.Attachment = None, tp1: str = None, timeframe: str = None, setup_detail: str = None, tp2: str = None, tp3: str = None, notes: str = None):
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
    size_pct="% of position closed - REQUIRED for TP and Partial TP events (e.g. 25)",
    price="Fill price - REQUIRED for Partial TP and Closed (remaining position)",
    note="Optional note",
)
@app_commands.choices(event=[
    app_commands.Choice(name="Entry 1 Filled", value="EF1"),
    app_commands.Choice(name="DCA Entry Filled (Entry 2)", value="EF2"),
    app_commands.Choice(name="TP1 Hit", value="TP1"),
    app_commands.Choice(name="TP2 Hit", value="TP2"),
    app_commands.Choice(name="TP3 Hit", value="TP3"),
    app_commands.Choice(name="Partial TP (custom price)", value="PTP"),
    app_commands.Choice(name="Moved to Breakeven", value="BE"),
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
        desc = "Moved to breakeven"
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
            d = "🟢 L" if t["direction"] == "LONG" else "🔴 S"
            res = t.get("result", "?")
            r = t.get("result_r")
            rtxt = f" ({r:+g}R)" if isinstance(r, (int, float)) else ""
            emoji = {"WIN": "✅", "LOSS": "❌", "BE": "➖", "INVALID": "🚫"}.get(res, "")
            lines.append(f"{emoji} {d} **{t['pair'].upper()}**" + (f" - {tf(t)}" if tf(t) else "") + f" - {res}{rtxt} - {t.get('analyst_name', '')} - [view]({jump_url(t)})")
        embed.add_field(name="Futures", value="\n".join(lines)[:1024], inline=False)
    if sclosed:
        lines = []
        for p in sclosed:
            res = p.get("result", "?")
            pct = f" ({p['result_pct']})" if p.get("result_pct") else ""
            emoji = {"WIN": "✅", "LOSS": "❌", "BE": "➖", "INVALID": "🚫"}.get(res, "")
            lines.append(f"{emoji} 🪙 **{p['pair'].upper()}** - {res}{pct} - {p.get('analyst_name', '')} - [view]({jump_url(p)})")
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
    arrow = "🟢" if chg >= 0 else "🔴"
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
            lines.append(f"**Margin @ {lev:g}x:** ${fnum(margin)} ⚠️ exceeds your account size")
        else:
            lines.append(f"**Margin @ {lev:g}x:** ${fnum(margin)} ({margin / acc * 100:.1f}% of account)")
    embed = discord.Embed(title="Position Size Calculator", color=NAVY)
    embed.description = "\n".join(lines)
    embed.set_footer(text="Scient Lounge - risk first, always")
    await interaction.followup.send(embed=embed, ephemeral=True)


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
    embed.add_field(name="Avg R", value=(f"{avg_r:+.2f}R" if avg_r is not None else "-"), inline=True)
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

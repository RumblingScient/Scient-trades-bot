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

EDIT_WINDOW_MIN = 10  # minutes after posting during which /edit is allowed

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


def parse_r(val):
    if val is None:
        return None
    cleaned = "".join(c for c in str(val) if c in "0123456789.-")
    try:
        return float(cleaned)
    except ValueError:
        return None


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
    return bool(t.get("entry1_filled"))


def entry_display(t: dict, marks: bool = True) -> str:
    s = str(t.get("entry"))
    if marks and not t.get("closed"):
        s += " (filled)" if t.get("entry1_filled") else " (pending)"
    return s


def full_status(t: dict) -> str:
    if t.get("closed"):
        r = t.get("result_r")
        rtxt = f" ({r:+g}R)" if isinstance(r, (int, float)) else ""
        return {
            "WIN": f"CLOSED - WIN{rtxt}",
            "LOSS": f"CLOSED - LOSS{rtxt}",
            "BE": "CLOSED - BREAKEVEN",
            "INVALID": "INVALIDATED",
        }.get(t.get("result"), "CLOSED")
    if t.get("tp3_hit"):
        return "TP3 HIT - runner"
    if t.get("tp2_hit"):
        return "TP2 HIT - trailing"
    if t.get("tp1_hit"):
        return "TP1 HIT - in profit"
    if t.get("be"):
        return "MOVED TO BREAKEVEN"
    if any_entry_filled(t):
        return "ACTIVE"
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
        f" | **R:R:** {t.get('rr') or '-'}"
    )

    tps = []
    for key, hit in (("tp1", "tp1_hit"), ("tp2", "tp2_hit"), ("tp3", "tp3_hit")):
        if t.get(key):
            tps.append(f"{t[key]}" + (" \u2705" if t.get(hit) else ""))
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
@app_commands.describe(pair="e.g. BTC/USDT", direction="Long or Short", entry="Entry price (or a range for DCA, e.g. 64000 - 62000 (50/50))", stop_loss="SL", risk="Account risk (just a number = %, e.g. 1 shows as 1%)", rr="Risk:Reward", entry_type="Limit (pending) or Market (filled now)", framework="Setup framework (optional)", framework2="Second framework (optional)", chart="Chart image (optional)", tp1="Take profit 1 (optional)", timeframe="e.g. 4H (optional)", setup_detail="Extra specifics (optional)", tp2="TP2 (optional)", tp3="TP3 (optional)", notes="Reasoning (optional, posted in the trade thread)")
@app_commands.choices(
    direction=[app_commands.Choice(name="Long", value="LONG"), app_commands.Choice(name="Short", value="SHORT")],
    framework=[app_commands.Choice(name=f, value=f) for f in FRAMEWORKS],
    framework2=[app_commands.Choice(name=f, value=f) for f in FRAMEWORKS],
    entry_type=[app_commands.Choice(name="Limit - pending fill", value="LIMIT"), app_commands.Choice(name="Market - filled now", value="MARKET")],
)
async def trade(interaction: discord.Interaction, pair: str, direction: app_commands.Choice[str], entry: str, stop_loss: str, risk: str, rr: str, entry_type: app_commands.Choice[str], framework: app_commands.Choice[str] = None, framework2: app_commands.Choice[str] = None, chart: discord.Attachment = None, tp1: str = None, timeframe: str = None, setup_detail: str = None, tp2: str = None, tp3: str = None, notes: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message(f"Only members with the **{ANALYST_ROLE_NAME}** role can post setups.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    channel = bot.get_channel(TRADES_CHANNEL_ID)
    if channel is None:
        await interaction.followup.send("Trades channel not found - check TRADES_CHANNEL_ID.", ephemeral=True)
        return
    is_market = entry_type and entry_type.value == "MARKET"
    akey, acfg = resolve_analyst(interaction.user)
    frameworks = [f.value for f in (framework, framework2) if f]
    t = {
        "analyst_id": interaction.user.id, "analyst_name": interaction.user.display_name,
        "analyst_avatar": interaction.user.display_avatar.url, "analyst_key": akey,
        "analyst_color": analyst_color_hex(interaction.user),
        "pair": pair, "direction": direction.value, "timeframe": timeframe,
        "framework": frameworks[0] if frameworks else None, "frameworks": frameworks, "setup_detail": setup_detail,
        "entry": entry, "sl": stop_loss, "entry_type": entry_type.value,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "rr": rr, "risk": risk, "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entry1_filled": bool(is_market),
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False, "sl_hit": False, "be": False,
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
    stop_loss="Corrected SL / invalidation (optional)",
    risk="Corrected risk / allocation (optional)",
    rr="Corrected R:R - futures only (optional)",
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
    entry_type=[app_commands.Choice(name="Limit - pending fill", value="LIMIT"), app_commands.Choice(name="Market - filled now", value="MARKET")],
)
@app_commands.autocomplete(trade=editable_any_ac)
async def edit(interaction: discord.Interaction, trade: str, pair: str = None, direction: app_commands.Choice[str] = None, entry: str = None, stop_loss: str = None, risk: str = None, rr: str = None, entry_type: app_commands.Choice[str] = None, framework: app_commands.Choice[str] = None, framework2: app_commands.Choice[str] = None, chart: discord.Attachment = None, tp1: str = None, tp2: str = None, tp3: str = None, timeframe: str = None, setup_detail: str = None, notes: str = None):
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
        if stop_loss is not None:
            t["sl"] = stop_loss; changes.append("SL")
        if risk is not None:
            t["risk"] = risk; changes.append("risk")
        if rr is not None:
            t["rr"] = rr; changes.append("R:R")
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


@bot.tree.command(name="update", description="Update a running trade")
@app_commands.describe(trade="Pick an open trade", event="What happened", note="Optional note")
@app_commands.choices(event=[
    app_commands.Choice(name="Entry Filled (activate trade)", value="EF"),
    app_commands.Choice(name="TP1 Hit", value="TP1"),
    app_commands.Choice(name="TP2 Hit", value="TP2"),
    app_commands.Choice(name="TP3 Hit", value="TP3"),
    app_commands.Choice(name="SL Hit (closes as loss)", value="SL"),
    app_commands.Choice(name="Moved to Breakeven", value="BE"),
])
@app_commands.autocomplete(trade=open_trades_ac)
async def update(interaction: discord.Interaction, trade: str, event: app_commands.Choice[str], note: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message("Analysts only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    data = load_trades()
    t = data.get(trade)
    if not t:
        await interaction.followup.send("Trade not found.", ephemeral=True)
        return
    feed = {
        "EF": ("Entry Filled", BLUE, "Entry filled - position live."),
        "TP1": ("TP1 Hit", GREEN, "Take profit 1 reached."),
        "TP2": ("TP2 Hit", GREEN, "Take profit 2 reached."),
        "TP3": ("TP3 Hit", GREEN, "Take profit 3 reached."),
        "SL": ("Stopped Out", RED, "Stop loss hit - closed as loss."),
        "BE": ("Moved to Breakeven", GREY, "Stop moved to breakeven."),
    }
    thread_map = {"EF": "Entry filled", "TP1": "TP1 hit", "TP2": "TP2 hit", "TP3": "TP3 hit", "SL": "Stopped out", "BE": "Moved to breakeven"}
    if event.value == "EF":
        t["entry1_filled"] = True
    elif event.value == "TP1":
        t["tp1_hit"] = True; t["entry1_filled"] = True
    elif event.value == "TP2":
        t["tp2_hit"] = True; t["tp1_hit"] = True; t["entry1_filled"] = True
    elif event.value == "TP3":
        t["tp3_hit"] = True; t["tp2_hit"] = True; t["tp1_hit"] = True; t["entry1_filled"] = True
    elif event.value == "SL":
        t["sl_hit"] = True; t["closed"] = True; t["result"] = "LOSS"; t["closed_at"] = datetime.now(timezone.utc).isoformat()
        if note:
            t["close_note"] = note
    elif event.value == "BE":
        t["be"] = True
    data[trade] = t
    save_trades(data)
    await refresh_and_edit(t)
    await refresh_board()
    title, color, line = feed[event.value]
    if note:
        line += f"\n> {note}"
    await post_update_feed(t, title, color, line)
    await thread_note(t, f"**{thread_map[event.value]}**" + (f" - {note}" if note else ""))
    await interaction.followup.send(f"Updated: {thread_map[event.value]}", ephemeral=True)


@bot.tree.command(name="close", description="Close a trade")
@app_commands.describe(trade="Pick an open trade", result="Outcome", result_r="Realized R", note="Closing note")
@app_commands.choices(result=[
    app_commands.Choice(name="Win", value="WIN"),
    app_commands.Choice(name="Loss", value="LOSS"),
    app_commands.Choice(name="Breakeven", value="BE"),
    app_commands.Choice(name="Invalidated", value="INVALID"),
])
@app_commands.autocomplete(trade=open_trades_ac)
async def close(interaction: discord.Interaction, trade: str, result: app_commands.Choice[str], result_r: str = None, note: str = None):
    if not is_analyst(interaction):
        await interaction.response.send_message("Analysts only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    data = load_trades()
    t = data.get(trade)
    if not t:
        await interaction.followup.send("Trade not found.", ephemeral=True)
        return
    t["closed"] = True
    t["result"] = result.value
    t["result_r"] = parse_r(result_r)
    t["closed_at"] = datetime.now(timezone.utc).isoformat()
    if note:
        t["close_note"] = note
    data[trade] = t
    save_trades(data)
    await refresh_and_edit(t)
    await refresh_board()
    rtxt = f" ({t['result_r']:+g}R)" if isinstance(t["result_r"], (int, float)) else ""
    feed = {
        "WIN": (f"Closed - Win{rtxt}", GREEN, "Trade closed in profit."),
        "LOSS": (f"Closed - Loss{rtxt}", RED, "Trade closed at a loss."),
        "BE": ("Closed - Breakeven", GREY, "Trade closed flat."),
        "INVALID": ("Invalidated", DGREY, "Setup invalidated before trigger."),
    }
    title, color, line = feed[result.value]
    if note:
        line += f"\n> {note}"
    await post_update_feed(t, title, color, line)
    await thread_note(t, f"**Closed - {result.value}{rtxt}**" + (f" - {note}" if note else ""))
    await interaction.followup.send(f"Closed: {result.value}{rtxt}", ephemeral=True)


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

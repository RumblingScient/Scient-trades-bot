# scient_trades_bot.py
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
from discord.ext import tasks
import os, json, re, hashlib, aiohttp
from pathlib import Path
from datetime import datetime, timezone

_env_file = Path(__file__).with_name(".env")
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

BOT_TOKEN = os.getenv("SCIENT_BOT_TOKEN", "PASTE_TOKEN_HERE")
TWITTERAPIS_KEY = os.getenv("TWITTERAPIS_KEY", "")
GUILD_ID = 1524447325714518068
TRADES_CHANNEL_ID = 1524447387974897665
TRADE_UPDATES_CHANNEL_ID = 1525464605835530302
OPEN_BOARD_CHANNEL_ID = 1525468999695995001
X_FEED_CHANNEL_ID = 1525470955810328696
ANALYST_ROLE_NAME = "Analyst"
PING_ROLE_ID = 1524499302502891570
X_PING_ROLE_ID = 1525602499460071515

# ---- X auto-feed config ----
X_AUTO_USERNAME = "Crypto_Scient"
X_POLL_MINUTES = 30
X_AUTO_ENABLED = True

ANALYSTS = {
    "scient":  {"color": "#1C4E80", "ping_role_id": 1525464714291970289, "user_ids": []},
    "owais":   {"color": "#7C3AED", "ping_role_id": None, "user_ids": []},
    "94":      {"color": "#2E7D32", "ping_role_id": None, "user_ids": []},
    "michael": {"color": "#C0392B", "ping_role_id": None, "user_ids": []},
    "delta":   {"color": "#F1C40F", "ping_role_id": None, "user_ids": []},
    "gaijin":  {"color": "#14B8A6", "ping_role_id": None, "user_ids": []},
}

JOURNAL_FILE = Path(__file__).with_name("trades.json")
BOARD_FILE = Path(__file__).with_name("board.json")
XSEEN_FILE = Path(__file__).with_name("x_posted.json")
NAVY = discord.Color.from_str("#1C4E80")
GREEN = discord.Color.from_str("#2E7D32")
RED = discord.Color.from_str("#C62828")
BLUE = discord.Color.from_str("#378ADD")
GREY = discord.Color.light_grey()
DGREY = discord.Color.dark_grey()
FRAMEWORKS = [
    "FRVP / POC", "AMD", "Wyckoff Accumulation", "RSI Divergence",
    "BOS / MSS", "Fib Pocket (0.75/0.786)", "Range (sweep-reclaim)",
    "Three Drives", "Deviation Reclaim", "EMA Cross", "Other",
]
ANALYST_CHOICES = [app_commands.Choice(name=k.capitalize(), value=k) for k in ANALYSTS.keys()]

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def load_trades() -> dict:
    if JOURNAL_FILE.exists():
        try:
            return json.loads(JOURNAL_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_trades(data: dict):
    JOURNAL_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_board() -> dict:
    if BOARD_FILE.exists():
        try:
            return json.loads(BOARD_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_board(d: dict):
    BOARD_FILE.write_text(json.dumps(d, indent=2))


def load_xseen() -> dict:
    if XSEEN_FILE.exists():
        try:
            return json.loads(XSEEN_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_xseen(d: dict):
    XSEEN_FILE.write_text(json.dumps(d, indent=2))


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


def fix_x_link(url: str) -> str:
    url = url.strip()
    return re.sub(r"https?://(www\.)?(twitter|x)\.com", "https://fxtwitter.com", url, flags=re.I)


def has_ladder(t: dict) -> bool:
    return bool(t.get("entry2"))


def any_entry_filled(t: dict) -> bool:
    return bool(t.get("entry1_filled") or t.get("entry2_filled"))


def entry_display(t: dict, marks: bool = True) -> str:
    e1 = t.get("entry")
    e2 = t.get("entry2")
    w1 = t.get("weight1")
    w2 = t.get("weight2")
    f1 = t.get("entry1_filled")
    f2 = t.get("entry2_filled")
    if not e2:
        s = str(e1)
        if marks:
            s += " OK" if f1 else " ..."
        return s
    p1 = f"{e1}" + (f" ({w1}%)" if w1 else "")
    p2 = f"{e2}" + (f" ({w2}%)" if w2 else "")
    if marks:
        p1 += " OK" if f1 else " []"
        p2 += " OK" if f2 else " []"
    return f"{p1} + {p2}"


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
        if has_ladder(t) and not (t.get("entry1_filled") and t.get("entry2_filled")):
            filled_w = t.get("weight1") if t.get("entry1_filled") else t.get("weight2")
            wtxt = f" ({filled_w}%)" if filled_w else ""
            return f"ACTIVE - partial{wtxt}"
        return "ACTIVE - full position" if has_ladder(t) else "ACTIVE"
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
        if has_ladder(t) and not (t.get("entry1_filled") and t.get("entry2_filled")):
            return "Partial"
        return "Active"
    return "Pending"


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


def build_embed(t: dict, image_url: str = None) -> discord.Embed:
    is_long = t["direction"] == "LONG"
    closed = t.get("closed")
    result = t.get("result")
    try:
        color = discord.Color.from_str(t.get("analyst_color") or "#1C4E80")
    except Exception:
        color = NAVY
    arrow = "LONG" if is_long else "SHORT"
    prefix = ""
    if closed:
        prefix = {"WIN": "[WIN] ", "LOSS": "[LOSS] ", "BE": "[BE] ", "INVALID": "[INV] "}.get(result, "")
    elif not any_entry_filled(t):
        prefix = "[PENDING] "
    embed = discord.Embed(title=f"{prefix}{arrow} | {t['pair'].upper()} | {t['timeframe'].upper()}", color=color)
    if t.get("created_at"):
        try:
            embed.timestamp = datetime.fromisoformat(t["created_at"])
        except Exception:
            pass
    sl_mark = " (hit)" if t.get("sl_hit") else ""
    tp1_mark = " OK" if t.get("tp1_hit") else ""
    tp2_mark = " OK" if t.get("tp2_hit") else ""
    tp3_mark = " OK" if t.get("tp3_hit") else ""
    fw = t.get("framework", "-")
    if t.get("setup_detail"):
        fw = f"{fw} - {t['setup_detail']}"
    embed.add_field(name="Setup", value=fw, inline=False)
    laddered = has_ladder(t)
    embed.add_field(name="Entry" + (" (laddered)" if laddered else ""), value=entry_display(t), inline=not laddered)
    embed.add_field(name="Stop Loss", value=f"{t['sl']}{sl_mark}", inline=True)
    embed.add_field(name="Risk", value=t.get("risk") or "-", inline=True)
    if laddered:
        embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="TP1", value=f"{t['tp1']}{tp1_mark}", inline=True)
    embed.add_field(name="TP2", value=(f"{t['tp2']}{tp2_mark}" if t.get("tp2") else "-"), inline=True)
    embed.add_field(name="TP3", value=(f"{t['tp3']}{tp3_mark}" if t.get("tp3") else "-"), inline=True)
    embed.add_field(name="R:R", value=t.get("rr") or "-", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.add_field(name="Status", value=full_status(t), inline=False)
    if t.get("notes"):
        embed.add_field(name="Reasoning", value=t["notes"][:1024], inline=False)
    if t.get("close_note"):
        embed.add_field(name="Closing Note", value=t["close_note"][:1024], inline=False)
    embed.set_author(name=t["analyst_name"], icon_url=t.get("analyst_avatar") or None)
    embed.set_footer(text="Scient Lounge - Trade Setups")
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
            d = "L" if t["direction"] == "LONG" else "S"
            e = entry_display(t, marks=False)
            lines.append(f"[{d}] **{t['pair'].upper()}** - {t['timeframe'].upper()} - entry `{e}` - {short_status(t)} - [view]({jump_url(t)})")
        embed.add_field(name=f"{name} ({len(trades)})", value="\n".join(lines)[:1024], inline=False)
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
@app_commands.describe(pair="e.g. BTC/USDT", direction="Long or Short", framework="Setup framework", entry="Entry / first bid", stop_loss="SL", tp1="Take profit 1", timeframe="e.g. 4H", entry_type="Limit or Market", entry2="Second bid (laddered)", weight1="First bid %", weight2="Second bid %", setup_detail="Extra specifics", tp2="TP2", tp3="TP3", rr="Risk:Reward", risk="Account risk", notes="Reasoning", chart="Chart image")
@app_commands.choices(
    direction=[app_commands.Choice(name="Long", value="LONG"), app_commands.Choice(name="Short", value="SHORT")],
    framework=[app_commands.Choice(name=f, value=f) for f in FRAMEWORKS],
    entry_type=[app_commands.Choice(name="Limit - pending fill", value="LIMIT"), app_commands.Choice(name="Market - filled now", value="MARKET")],
)
async def trade(interaction: discord.Interaction, pair: str, direction: app_commands.Choice[str], framework: app_commands.Choice[str], entry: str, stop_loss: str, tp1: str, timeframe: str, entry_type: app_commands.Choice[str] = None, entry2: str = None, weight1: int = None, weight2: int = None, setup_detail: str = None, tp2: str = None, tp3: str = None, rr: str = None, risk: str = None, notes: str = None, chart: discord.Attachment = None):
    if not is_analyst(interaction):
        await interaction.response.send_message(f"Only members with the **{ANALYST_ROLE_NAME}** role can post setups.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    channel = bot.get_channel(TRADES_CHANNEL_ID)
    if channel is None:
        await interaction.followup.send("Trades channel not found - check TRADES_CHANNEL_ID.", ephemeral=True)
        return
    if entry2 and weight1 and weight2 and (weight1 + weight2 != 100):
        await interaction.followup.send(f"Weights should add up to 100% (you entered {weight1} + {weight2} = {weight1 + weight2}).", ephemeral=True)
        return
    is_market = entry_type and entry_type.value == "MARKET"
    akey, acfg = resolve_analyst(interaction.user)
    t = {
        "analyst_id": interaction.user.id, "analyst_name": interaction.user.display_name,
        "analyst_avatar": interaction.user.display_avatar.url, "analyst_key": akey,
        "analyst_color": analyst_color_hex(interaction.user),
        "pair": pair, "direction": direction.value, "timeframe": timeframe,
        "framework": framework.value, "setup_detail": setup_detail,
        "entry": entry, "entry2": entry2, "weight1": weight1, "weight2": weight2, "sl": stop_loss,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "rr": rr, "risk": risk, "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entry1_filled": bool(is_market), "entry2_filled": bool(is_market and entry2),
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False, "sl_hit": False, "be": False,
        "closed": False, "result": None, "result_r": None, "close_note": None,
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
    except discord.HTTPException:
        t["thread_id"] = None
    data = load_trades()
    data[str(msg.id)] = t
    save_trades(data)
    await refresh_board()
    await interaction.followup.send(f"Setup posted in {channel.mention} ({msg.jump_url})", ephemeral=True)


async def open_trades_ac(interaction: discord.Interaction, current: str):
    data = load_trades()
    is_admin = interaction.user.guild_permissions.administrator
    out = []
    for mid, t in data.items():
        if t.get("closed"):
            continue
        if not is_admin and t.get("analyst_id") != interaction.user.id:
            continue
        label = f"{t['pair'].upper()} {t['direction']} {t['timeframe']} - {short_status(t)}"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=mid))
    return out[:25]


async def refresh_and_edit(t: dict):
    channel = bot.get_channel(t["channel_id"])
    msg = await channel.fetch_message(t["message_id"])
    image_url = msg.attachments[0].url if msg.attachments else None
    await msg.edit(embed=build_embed(t, image_url=image_url))
    return msg


async def thread_note(t: dict, text: str):
    if t.get("thread_id"):
        try:
            th = bot.get_channel(t["thread_id"]) or await bot.fetch_channel(t["thread_id"])
            await th.send(text)
        except Exception:
            pass


async def post_update_feed(t: dict, title: str, color: discord.Color, line: str):
    if not TRADE_UPDATES_CHANNEL_ID:
        return
    ch = bot.get_channel(TRADE_UPDATES_CHANNEL_ID)
    if ch is None:
        return
    e = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    e.description = f"**{t['pair'].upper()} {t['direction']} - {t['timeframe'].upper()}**\n{line}\n[View original call]({jump_url(t)})"
    e.set_author(name=t["analyst_name"], icon_url=t.get("analyst_avatar") or None)
    e.set_footer(text="Scient Lounge - Trade Updates")
    await ch.send(embed=e)


@bot.tree.command(name="update", description="Update a running trade")
@app_commands.describe(trade="Pick an open trade", event="What happened", note="Optional note")
@app_commands.choices(event=[
    app_commands.Choice(name="Entry Filled (single/full)", value="EF"),
    app_commands.Choice(name="Entry 1 Filled (ladder)", value="E1"),
    app_commands.Choice(name="Entry 2 Filled (ladder)", value="E2"),
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
        "E1": ("Entry 1 Filled", BLUE, "First bid filled - partial position."),
        "E2": ("Entry 2 Filled", BLUE, "Second bid filled - full position."),
        "TP1": ("TP1 Hit", GREEN, "Take profit 1 reached."),
        "TP2": ("TP2 Hit", GREEN, "Take profit 2 reached."),
        "TP3": ("TP3 Hit", GREEN, "Take profit 3 reached."),
        "SL": ("Stopped Out", RED, "Stop loss hit - closed as loss."),
        "BE": ("Moved to Breakeven", GREY, "Stop moved to breakeven."),
    }
    thread_map = {"EF": "Entry filled", "E1": "Entry 1 filled", "E2": "Entry 2 filled", "TP1": "TP1 hit", "TP2": "TP2 hit", "TP3": "TP3 hit", "SL": "Stopped out", "BE": "Moved to breakeven"}
    if event.value == "EF":
        t["entry1_filled"] = True
        if t.get("entry2"):
            t["entry2_filled"] = True
    elif event.value == "E1":
        t["entry1_filled"] = True
    elif event.value == "E2":
        t["entry2_filled"] = True
    elif event.value == "TP1":
        t["tp1_hit"] = True; t["entry1_filled"] = True
    elif event.value == "TP2":
        t["tp2_hit"] = True; t["tp1_hit"] = True; t["entry1_filled"] = True
    elif event.value == "TP3":
        t["tp3_hit"] = True; t["tp2_hit"] = True; t["tp1_hit"] = True; t["entry1_filled"] = True
    elif event.value == "SL":
        t["sl_hit"] = True; t["closed"] = True; t["result"] = "LOSS"; t["closed_at"] = datetime.now(timezone.utc).isoformat()
    elif event.value == "BE":
        t["be"] = True
    if note:
        t["close_note"] = note
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


@bot.tree.command(name="stats", description="Trade journal scorecard")
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

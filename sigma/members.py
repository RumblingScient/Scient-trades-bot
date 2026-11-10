"""sigma.members - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import ALUMNI_ROLE_ID, ANALYSTS, ANALYST_CHOICES, ANALYST_ROLE_NAME, GOLD, GREY, GUILD_ID, NAVY, RED, NEWS_PING_ROLE_ID, PING_ROLE_ID, PRO_ROLE_IDS, SUB_REMINDER_DAYS, SUB_ROLE_ID, X_PING_ROLE_ID
from sigma.core import bot
from sigma.ops import ops_alert
from sigma.storage import load_subs, save_subs, PAY_LOCK, SUBS_LOCK


def csv_safe(v):
    """Neutralise spreadsheet formula injection: a cell that starts with = + - @ tab or CR is executed by Excel /
    Sheets when the file is opened. Member names and trade notes are user-supplied, so every text cell goes through this."""
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def member_is_pro(member) -> bool:
    try:
        return bool({r.id for r in member.roles} & PRO_ROLE_IDS) or member.guild_permissions.administrator
    except Exception:
        return False

def has_analyst_role(member) -> bool:
    """Role id when configured (SIGMA_ANALYST_ROLE_ID) - a mod with Manage Roles can create a second role *named*
    Analyst, so the name is only the fallback for servers that haven't set the id."""
    from sigma.config import ANALYST_ROLE_ID
    roles = getattr(member, "roles", None) or []
    if ANALYST_ROLE_ID:
        return any(getattr(r, "id", None) == ANALYST_ROLE_ID for r in roles)
    return any(getattr(r, "name", "") == ANALYST_ROLE_NAME for r in roles)


def is_analyst(interaction: discord.Interaction) -> bool:
    if getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.administrator:
        return True
    return has_analyst_role(interaction.user)

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
        title="Welcome to Sigma Trading \U0001F44B",
        color=NAVY,
        description=(
            "Glad to have you here. Sigma Trading is a trading community built around "
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
        name="\U0001F513 Want the full picture?",
        value=(
            "Members with **full access** get live analyst trade setups, entry/SL/targets, "
            "the full trade journal with verified results, priority tools, and the breaking-news wire. "
            "Check the upgrade options in the server whenever you're ready - no pressure."
        ),
        inline=False,
    )
    embed.set_footer(text="Sigma Trading - setups, not signals")
    return embed

def build_pro_dm() -> discord.Embed:
    embed = discord.Embed(
        title="You're in. Full access unlocked \U0001F680",
        color=GOLD,
        description=(
            "Welcome to the full Sigma Trading experience. Here's exactly what you now have access to - "
            "take two minutes to set yourself up so you don't miss anything."
        ),
    )
    embed.add_field(
        name="\U0001F4C8 Live Analyst Setups",
        value=(
            "Every setup our analysts take - futures and spot - is posted in **#trades** with "
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
        name="\U0001F4CA Full Sigma Terminal",
        value=(
            "**#quant-terminal**: `/chart` `/levels` `/funding` `/oi` `/vol` `/heatmap` `/dominance` "
            "`/rs` `/exitwatch` and more. Type `/help` for the full list."
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001F4F0 News",
        value="The daily news digest and market brief keep you on top of what matters. Watch **#news-wire**.",
        inline=False,
    )
    embed.set_footer(text="Sigma Trading - welcome aboard")
    return embed

@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    try:
        from sigma.services import subs as subsvc
        s = load_subs().get(str(member.id))
        dl = subsvc.days_left(s) if s else None
        if dl is not None and dl >= 0:          # 0 days left is still active - `or -1` used to drop that case
            await _sub_grant_role(member.guild, member.id)
            print(f"[subs] rejoin - Pro restored for {member} ({member.id})")
    except Exception as e:
        print(f"[subs] rejoin check error {member.id}: {e}")
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

# (moved to /admin - registered in sigma.commands_admin)
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
    embed.set_footer(text="Sigma Trading - you're in control of your pings")
    await interaction.channel.send(embed=embed, view=FollowPanel())
    await interaction.response.send_message("Follow panel posted.", ephemeral=True)

def _plan_label(k):
    from sigma.services import plans as plansvc
    return plansvc.all().get(k, {}).get("label", k)

async def plan_ac(interaction: discord.Interaction, current: str):
    from sigma.services import plans as plansvc
    cur = (current or "").lower()
    return [app_commands.Choice(name=p["label"][:100], value=k) for k, p in plansvc.all().items()
            if cur in p["label"].lower() or cur in k][:25]

async def _sub_grant_role(guild: discord.Guild, uid: int) -> bool:
    """True only when the member holds the Pro role afterwards. A missing role / member or a Discord error is False
    AND an ops alert - a paid member without the role is the one failure that must never be silent."""
    why = ""
    try:
        member = guild.get_member(uid) or await guild.fetch_member(uid)
        role = guild.get_role(SUB_ROLE_ID) if guild else None
        if role is None:
            why = f"Pro role {SUB_ROLE_ID} not found in the server"
        elif member is None:
            why = "member is not in the server"
        else:
            if role not in member.roles:
                await member.add_roles(role, reason="Subscription granted")
            return True
    except Exception as e:
        why = f"{type(e).__name__}: {e}"
    print(f"[subs] grant role error {uid}: {why}", flush=True)
    await ops_alert(f"Pro role NOT granted to <@{uid}> - {why}. Add it by hand or fix the role and re-grant from /admin desk.", key=f"role:{uid}")
    return False

async def _sub_remove_role(guild: discord.Guild, uid: int) -> bool:
    """True when the member no longer holds the Pro role (or left the server). False = still has it, try again."""
    try:
        role = guild.get_role(SUB_ROLE_ID) if guild else None
        if role is None:
            print(f"[subs] remove role error {uid}: Pro role {SUB_ROLE_ID} not found", flush=True)
            return False
        try:
            member = guild.get_member(uid) or await guild.fetch_member(uid)
        except discord.NotFound:
            return True                          # left the server - nothing to remove
        if member and role in member.roles:
            await member.remove_roles(role, reason="Subscription expired/revoked")
        return True
    except Exception as e:
        print(f"[subs] remove role error {uid}: {e}", flush=True)
        return False

# (moved to /admin - registered in sigma.commands_admin)
@app_commands.describe(member="Who gets Pro", plan="Which plan", note="Optional note, e.g. tx hash or payment ref",
                       expires="Import from the old bot: set the exact expiry, e.g. 2026-11-15 (no revenue is recorded)")
@app_commands.autocomplete(plan=plan_ac)
async def grant_cmd(interaction: discord.Interaction, member: discord.Member, plan: str, note: str = None, expires: str = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    from sigma.payments import activate
    from sigma.errors import UserError
    try:
        rec = await activate(member.id, plan, by=interaction.user.display_name, note=note or "", expires_at=expires)
    except UserError as e:
        await interaction.followup.send(e.message, ephemeral=True)
        return
    expires = datetime.fromisoformat(rec["expires"])
    await interaction.followup.send(
        f"\u2705 **{member.display_name}** -> {_plan_label(plan)}\n"
        f"Expires: **{'lifetime' if rec.get('days', 0) >= 36500 else expires.strftime('%d %b %Y')}** - logged in mod-log.",
        ephemeral=True,
    )

# (moved to /admin - registered in sigma.commands_admin)
@app_commands.describe(member="Whose subscription to revoke")
async def revoke_cmd(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    from sigma.services import subs as subsvc
    from sigma.errors import UserError
    from sigma.payments import mod_log
    async with SUBS_LOCK:
        subs = load_subs()
        try:
            rec = subsvc.revoke(subs, member.id)
        except UserError as e:
            await interaction.followup.send(e.message, ephemeral=True)
            return
        save_subs(subs)
    await _archive_lapsed(str(member.id), rec, "revoked")
    await _sub_remove_role(interaction.guild, member.id)
    await mod_log(f"**Revoked** {_plan_label(rec.get('plan'))} from {member.display_name} (`{member.id}`) by {interaction.user.display_name}", color=RED)
    await interaction.followup.send(f"Subscription revoked for **{member.display_name}** - role removed, logged in mod-log.", ephemeral=True)

# (moved to /admin - registered in sigma.commands_admin)
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
        flag = "\U0001F7E2" if days_left > max(SUB_REMINDER_DAYS) else ("\U0001F7E1" if days_left >= 0 else "\U0001F534")
        rows.append((days_left, f"{flag} **{s.get('name','?')}** - {_plan_label(s.get('plan'))} - {days_left}d left"))
    rows.sort(key=lambda r: r[0])
    embed = discord.Embed(title="\U0001F4B3 Subscriptions", color=NAVY, timestamp=now)
    embed.description = "\n".join(r[1] for r in rows[:30])
    embed.add_field(name="Total", value=f"{len(subs)} active | ${total_rev} lifetime recorded", inline=False)
    embed.set_footer(text="Sigma Trading - subscription system")
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
            f"**{_plan_label(s.get('plan'))}**\n"
            f"Expires: **{exp.strftime('%d %b %Y')}** ({days_left} days left)",
            ephemeral=True,
        )
    except Exception:
        await interaction.followup.send("Couldn't read your subscription record - ping an admin.", ephemeral=True)

async def _archive_lapsed(uid: str, rec: dict, why: str):
    """Keep expired / revoked records (for revenue + churn) in payments.json - subs.json only holds active ones."""
    from sigma.storage import load_payments, save_payments
    async with PAY_LOCK:
        st = load_payments()
        rec = dict(rec); rec.update({"uid": uid, "lapsed_at": datetime.now(timezone.utc).isoformat(), "why": why})
        st.setdefault("lapsed", []).append(rec)
        st["lapsed"] = st["lapsed"][-500:]
        save_payments(st)


async def _expire_one(guild, uid: str, s: dict, now) -> bool:
    """Role off -> record out (saved) -> archive -> DM + mod-log. The DM comes last so a crash mid-way can't repeat it
    daily; a failed role removal keeps the record (flagged) so the next run tries again."""
    from sigma.services import subs as subsvc
    from sigma.payments import mod_log
    if not await _sub_remove_role(guild, int(uid)):
        async with SUBS_LOCK:
            subs = load_subs()
            if uid in subs:
                subs[uid]["expire_failed_at"] = now.isoformat()
                save_subs(subs)
        await ops_alert(f"Expiry: couldn't remove the Pro role from <@{uid}> ({s.get('name')}) - record kept, retrying tomorrow. "
                        "Check the bot's Manage Roles permission / role order.", key=f"expire:{uid}")
        return False
    if ALUMNI_ROLE_ID:
        try:
            m = guild.get_member(int(uid)) or await guild.fetch_member(int(uid))
            role = guild.get_role(ALUMNI_ROLE_ID)
            if m and role and role not in m.roles:
                await m.add_roles(role, reason="Subscription lapsed")
        except Exception:
            pass
    async with SUBS_LOCK:
        subs = load_subs()
        rec = subs.pop(uid, None)
        save_subs(subs)
    await _archive_lapsed(uid, rec or s, "expired")
    try:
        user = bot.get_user(int(uid)) or await bot.fetch_user(int(uid))
        await user.send(
            "Your **Sigma Pro** access has ended. It's been great having you in the full lounge - "
            "renew any time from the payment channel in the server to jump back in. \U0001F91D"
        )
    except Exception:
        pass
    await mod_log(f"**Expired** {_plan_label(s.get('plan'))} - {s.get('name')} (`{uid}`)" + (" · Alumni role set" if ALUMNI_ROLE_ID else ""), color=GREY)
    print(f"[subs] expired + removed: {s.get('name')} ({uid})", flush=True)
    return True


async def _remind_one(uid: str, s: dict, th: int, dl: int, now):
    """Mark + save before the DM: a crash between the two must not re-send, a failed DM just costs one reminder."""
    from sigma.services import subs as subsvc
    async with SUBS_LOCK:
        subs = load_subs()
        rec = subs.get(uid)
        if not rec:
            return
        subsvc.mark_reminded(rec, th, SUB_REMINDER_DAYS)
        save_subs(subs)
    try:
        exp = subsvc.expiry(s)
        user = bot.get_user(int(uid)) or await bot.fetch_user(int(uid))
        await user.send(
            f"Heads up - your **Sigma Pro** expires in **{max(dl, 0)} day(s)** "
            f"({exp.strftime('%d %b %Y')}). Renew from the payment channel in the server to keep uninterrupted access. \U0001F514"
        )
    except Exception:
        pass
    print(f"[subs] reminder ({th}d) sent: {s.get('name')} ({uid})", flush=True)


async def subs_check_once(now=None) -> dict:
    """One expiry / reminder sweep. Each member is its own unit of work: one bad record or one Discord error
    is logged and skipped, never aborts the run for everyone after it."""
    from sigma.services import subs as subsvc
    out = {"expired": 0, "reminded": 0, "failed": 0}
    subs = load_subs()
    if not subs:
        return out
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return out
    now = now or datetime.now(timezone.utc)
    for uid, s in list(subs.items()):
        try:
            dl = subsvc.days_left(s, now)
            if dl is None:
                continue
            if dl < 0:
                if await _expire_one(guild, uid, s, now):
                    out["expired"] += 1
                else:
                    out["failed"] += 1
                continue
            th = subsvc.due_reminder(s, SUB_REMINDER_DAYS, now)
            if th is not None:
                await _remind_one(uid, s, th, dl, now)
                out["reminded"] += 1
        except Exception as e:
            out["failed"] += 1
            print(f"[subs] check error for {uid}: {e!r}", flush=True)
            await ops_alert(f"subs check failed for <@{uid}>: `{type(e).__name__}: {str(e)[:160]}`", key=f"subs:{uid}")
    return out


@tasks.loop(time=dt_time(hour=4, minute=30, tzinfo=timezone.utc))  # 10:00 AM IST daily
async def subs_check_loop():
    await subs_check_once()

@subs_check_loop.before_loop
async def before_subs_check():
    await bot.wait_until_ready()

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

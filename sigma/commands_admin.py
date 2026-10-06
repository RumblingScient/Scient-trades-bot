"""sigma.commands_admin - the /admin group. Hidden from members (default_permissions = administrator).

    /admin health [view]        bot / results board / news wire
    /admin terminal_check       smoke-test every terminal data source
    /admin board                rebuild the open-positions board
    /admin results action       sync | rebuild
    /admin override ...         correct a wrongly closed trade
    /admin recap kind [days]    week | month | journal
    /admin tg action [text]     brief | digest | custom
    /admin panel kind           follow | help
    /admin grant / revoke / subs

The bodies stay in the modules they belong to (health, results, recaps, telegram, members ...);
this module only builds the tree. Each body still checks administrator itself, so nothing here
can be reached by a non-admin even if Discord's permission overrides are misconfigured.
"""
import discord
from discord import app_commands
from datetime import datetime

from sigma.core import bot
from sigma.health import health_cmd
from sigma.commands_terminal import terminal_check_cmd
from sigma.commands_trades import board_cmd
from sigma.results import results_rebuild_cmd, results_override_cmd, results_sync_cmd
from sigma.recaps import journal_month_cmd, recap_now_cmd, results_debug_cmd, recap_month_cmd
from sigma.telegram import tg_send_cmd
from sigma.news import news_status
from sigma.members import setup_follow_panel, grant_cmd, revoke_cmd, subs_cmd
from sigma.commands_help import setup_help_panel
from sigma.config import SOL_WALLET
from sigma.payments import post_panel, members_embed, MembersCSVView, refresh_panel, mod_log
from sigma.services import plans as plansvc
from sigma.errors import UserError
from sigma.storage import load_payments, save_payments
from sigma.members import plan_ac
from sigma.config import GOLD, NAVY

admin = app_commands.Group(
    name="admin",
    description="Admin tools - health, results board, recaps, Telegram, subscriptions",
    guild_only=True,
    default_permissions=discord.Permissions(administrator=True),
)


def _sub(name: str, description: str, callback):
    """Register an existing (decorated) coroutine as /admin <name> without touching its body."""
    admin.add_command(app_commands.Command(name=name, description=description, callback=callback))


@admin.command(name="health", description="Bot health - loops, feed, data files, errors. Or the results board / news wire")
@app_commands.describe(view="Blank = the bot. Results = why the board isn't updating. News = the news wire")
@app_commands.choices(view=[
    app_commands.Choice(name="Bot - loops, feeds, data, errors", value="bot"),
    app_commands.Choice(name="Results board - watcher, pending, permissions", value="results"),
    app_commands.Choice(name="News wire - connection and last message", value="news"),
])
async def admin_health(interaction: discord.Interaction, view: app_commands.Choice[str] = None):
    v = view.value if view else "bot"
    if v == "results":
        await results_debug_cmd(interaction)
    elif v == "news":
        await news_status(interaction)
    else:
        await health_cmd(interaction)


_sub("terminal_check", "Smoke-test every data source the terminal depends on", terminal_check_cmd)
_sub("board", "Rebuild the open-positions board", board_cmd)


@admin.command(name="results", description="Results board maintenance - sync missed closes, or wipe and rebuild")
@app_commands.describe(action="Sync = post any closed trades the board missed. Rebuild = wipe and re-post everything")
@app_commands.choices(action=[
    app_commands.Choice(name="Sync - post whatever the board missed", value="sync"),
    app_commands.Choice(name="Rebuild - wipe the board and re-post the full log", value="rebuild"),
])
async def admin_results(interaction: discord.Interaction, action: app_commands.Choice[str]):
    if action.value == "rebuild":
        await results_rebuild_cmd(interaction)
    else:
        await results_sync_cmd(interaction)


_sub("override", "Fix a wrongly closed trade - journal, original card and board all update", results_override_cmd)


@admin.command(name="recap", description="Post a recap now - weekly image, monthly recap, or per-analyst journal cards")
@app_commands.describe(kind="Which recap", days="Month / journal: last N days instead of the last complete month")
@app_commands.choices(kind=[
    app_commands.Choice(name="Week - the weekly recap image", value="week"),
    app_commands.Choice(name="Month - server monthly recap", value="month"),
    app_commands.Choice(name="Journal - one card per analyst", value="journal"),
])
async def admin_recap(interaction: discord.Interaction, kind: app_commands.Choice[str], days: int = 0):
    if kind.value == "week":
        await recap_now_cmd(interaction)
    elif kind.value == "journal":
        await journal_month_cmd(interaction, days=days)
    else:
        await recap_month_cmd(interaction, days=(days or 30))


_sub("tg", "Send an update to Telegram now - daily brief, news digest, or a custom message", tg_send_cmd)


@admin.command(name="panel", description="Post a pinned panel in this channel - follow buttons, command guide, or the payment plans")
@app_commands.describe(kind="Which panel")
@app_commands.choices(kind=[
    app_commands.Choice(name="Follow - analyst ping buttons", value="follow"),
    app_commands.Choice(name="Help - the public command guide", value="help"),
    app_commands.Choice(name="Pay - plan dropdown for #join-via-payment", value="pay"),
])
async def admin_panel(interaction: discord.Interaction, kind: app_commands.Choice[str]):
    if kind.value == "help":
        await setup_help_panel(interaction)
    elif kind.value == "pay":
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        if not SOL_WALLET:
            await interaction.response.send_message("Set `SIGMA_SOL_WALLET` in .env first - the panel would hand out an empty address.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await post_panel(interaction.channel)
        await interaction.followup.send("Payment panel posted and pinned.", ephemeral=True)
    else:
        await setup_follow_panel(interaction)


@admin.command(name="members", description="Members dashboard - overview, full member list, revenue, payments")
@app_commands.describe(view="Blank = overview")
@app_commands.choices(view=[
    app_commands.Choice(name="Overview", value="overview"),
    app_commands.Choice(name="Members - every active Pro with plan and expiry (+ CSV)", value="members"),
    app_commands.Choice(name="Revenue - this month, last month, all time, by plan, run-rate", value="revenue"),
    app_commands.Choice(name="Payments - open quotes, last paid, unmatched", value="payments"),
])
async def admin_members(interaction: discord.Interaction, view: app_commands.Choice[str] = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    v = view.value if view else "overview"
    kw = {"view": MembersCSVView()} if v == "members" else {}
    await interaction.followup.send(embed=members_embed(interaction.guild, v), ephemeral=True, **kw)


_sub("grant", "Grant or extend a Pro subscription", grant_cmd)
_sub("revoke", "Revoke a Pro subscription", revoke_cmd)
_sub("subs", "Active subscriptions overview", subs_cmd)


def _admin_only(interaction) -> bool:
    return bool(interaction.user.guild_permissions.administrator)


def _plans_embed() -> discord.Embed:
    e = discord.Embed(title="Plans", color=NAVY)
    rows = []
    for k, p in plansvc.all().items():
        eff, why = plansvc.effective_price(p)
        flag = "" if p.get("enabled", True) else " · **disabled**"
        dur = "lifetime" if p["days"] >= plansvc.LIFETIME_DAYS else f"{p['days']}d"
        seats = f" · seats {p['seats']}" if p.get("seats") else ""
        disc = f" · ~~${p['price']:,.0f}~~ **${eff:,.0f}** ({', '.join(why)})" if why else f" · ${p['price']:,.0f}"
        rows.append(f"`{k}` **{p['short']}**{disc} · {dur}{seats}{flag}")
    e.description = "\n".join(rows) or "no plans"
    e.set_footer(text="key is what you pass to edit / discount / disable · panel re-renders automatically")
    return e


@admin.command(name="plans", description="Plans - list, add, edit price/days/name/seats, discount with end date, disable, remove")
@app_commands.describe(
    action="What to do",
    key="Plan key, e.g. 1month, 6months, lifetime (List shows them). For Add: a new key like launch_week",
    name="Add/Edit: display name, e.g. Launch Week",
    price="Add/Edit: USD, e.g. 70",
    days="Add/Edit: access days, e.g. 30, or `lifetime`",
    seats="Add/Edit: cap on members (0 = unlimited)",
    discount="Discount: percent off, e.g. 20. 0 removes it",
    until="Discount: last day, e.g. 2026-10-15 or 15 Oct. Blank = no end",
    position="Edit: order in the panel (0 = first)",
)
@app_commands.choices(action=[
    app_commands.Choice(name="List", value="list"),
    app_commands.Choice(name="Add a plan", value="add"),
    app_commands.Choice(name="Edit a plan (name / price / days / seats / position)", value="edit"),
    app_commands.Choice(name="Discount - % off a plan, optional end date", value="discount"),
    app_commands.Choice(name="Disable - hide from the panel, keep existing members", value="disable"),
    app_commands.Choice(name="Enable", value="enable"),
    app_commands.Choice(name="Remove - delete the plan", value="remove"),
])
@app_commands.autocomplete(key=plan_ac)
async def admin_plans(interaction: discord.Interaction, action: app_commands.Choice[str], key: str = None, name: str = None,
                      price: str = None, days: str = None, seats: int = None, discount: str = None, until: str = None, position: int = None):
    if not _admin_only(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    a = action.value
    try:
        if a == "list":
            await interaction.followup.send(embed=_plans_embed(), ephemeral=True)
            return
        if not key:
            raise UserError("`key` is required - run List to see them.")
        if a == "add":
            if not (name and price and days):
                raise UserError("Add needs `name`, `price` and `days`.")
            p = plansvc.add(key, name, price, days, seats)
            msg = f"Added **{p['short']}** - ${p['price']:,.0f}, {p['days']} days."
        elif a == "edit":
            p, ch = plansvc.edit(key, name=name, price=price, days=days, seats=seats, order=position)
            msg = f"Updated **{p['short']}**: {', '.join(ch)}."
        elif a == "discount":
            if discount in (None, ""):
                raise UserError("Give `discount` (percent, 0 removes).")
            p = plansvc.set_discount(key, discount, until)
            eff, why = plansvc.effective_price(p)
            msg = (f"**{p['short']}** now ${eff:,.0f} ({', '.join(why)})" + (f" until {until}" if until and p.get('discount_pct') else "")) if why else f"Discount removed from **{p['short']}**."
        elif a in ("disable", "enable"):
            p, _ = plansvc.edit(key, enabled=(a == "enable"))
            msg = f"**{p['short']}** {'enabled' if a == 'enable' else 'disabled'}."
        else:
            p = plansvc.remove(key)
            msg = f"Removed **{p['short']}**. Existing members on it keep their access until expiry."
    except UserError as e:
        await interaction.followup.send(e.message, ephemeral=True)
        return
    ok = await refresh_panel()
    await mod_log(f"**Plans** - {interaction.user.display_name}: {msg}")
    await interaction.followup.send(msg + ("\nPanel updated." if ok else "\n(No payment panel posted yet - `/admin panel kind:pay`.)"),
                                    embed=_plans_embed(), ephemeral=True)


def _promos_embed() -> discord.Embed:
    e = discord.Embed(title="Promo & referral codes", color=NAVY)
    rows = []
    for c, r in plansvc.promos().items():
        off = f"{r['pct']:g}% off" if r.get("pct") else (f"${r['usd']:g} off" if r.get("usd") else "no discount")
        own = f" · ref **{r.get('owner_name') or r.get('owner_id')}** {r.get('share_pct', 0):g}%" if r.get("owner_id") is not None else ""
        lim = []
        if r.get("uses_left") is not None:
            lim.append(f"{r['uses_left']} left")
        if r.get("until"):
            try:
                lim.append("till " + datetime.fromisoformat(r["until"]).strftime("%d %b"))
            except Exception:
                pass
        if r.get("plans"):
            lim.append("only " + ", ".join(r["plans"]))
        rows.append(f"`{c}` {off}{own} · used {r.get('used', 0)}" + (f" · {' · '.join(lim)}" if lim else ""))
    e.description = "\n".join(rows) or "no codes"
    return e


def _payouts_embed() -> discord.Embed:
    st = load_payments()
    summ = plansvc.referral_summary(st)
    e = discord.Embed(title="Referral payouts", color=GOLD)
    rows = []
    for oid, o in sorted(summ.items(), key=lambda x: -x[1]["owed"]):
        rows.append(f"**{o['name'] or oid}** (`{oid}`) · {', '.join(o['codes'])} · {o['members']} member(s) · brought ${o['usd']:,.0f} · "
                    f"**owed ${o['owed']:,.2f}** · paid ${o['settled']:,.2f}")
    e.description = "\n".join(rows) or "no referral payments yet"
    e.set_footer(text="Settle: /admin promo action:Settle owner:@analyst note:<tx> - marks everything owed as paid")
    return e


@admin.command(name="promo", description="Promo & referral codes - add, remove, list, referral payouts, settle")
@app_commands.describe(
    action="What to do",
    code="The code, e.g. SIGMA20 or SIGMA-OWAIS",
    pct="Percent off, e.g. 20 (leave blank for a pure referral code)",
    usd="Dollars off, e.g. 20 - instead of pct",
    uses="How many times it can be used (blank = unlimited)",
    until="Last day, e.g. 2026-10-31 or 31 Oct",
    plan="Only valid for this plan (blank = all)",
    owner="Referral: the analyst/member who earns a share on every payment with this code",
    share="Referral: % of each payment that goes to the owner, e.g. 20",
    note="Settle: payout reference, e.g. the SOL tx",
)
@app_commands.choices(action=[
    app_commands.Choice(name="List codes", value="list"),
    app_commands.Choice(name="Add code (discount and/or referral)", value="add"),
    app_commands.Choice(name="Remove code", value="remove"),
    app_commands.Choice(name="Payouts - what each referrer brought and is owed", value="payouts"),
    app_commands.Choice(name="Settle - mark an owner's owed share as paid out", value="settle"),
])
@app_commands.autocomplete(plan=plan_ac)
async def admin_promo(interaction: discord.Interaction, action: app_commands.Choice[str], code: str = None, pct: str = None,
                      usd: str = None, uses: int = None, until: str = None, plan: str = None, owner: discord.Member = None,
                      share: str = None, note: str = None):
    if not _admin_only(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    a = action.value
    try:
        if a == "list":
            await interaction.followup.send(embed=_promos_embed(), ephemeral=True); return
        if a == "payouts":
            await interaction.followup.send(embed=_payouts_embed(), ephemeral=True); return
        if a == "settle":
            if owner is None:
                raise UserError("Settle needs `owner`.")
            st = load_payments()
            total = plansvc.settle(st, owner.id, note or "")
            save_payments(st)
            await mod_log(f"**Payout settled** ${total:,.2f} -> {owner.display_name} (`{owner.id}`) by {interaction.user.display_name}" + (f" · {note}" if note else ""), color=GOLD)
            await interaction.followup.send(f"Settled **${total:,.2f}** for {owner.display_name}.", embed=_payouts_embed(), ephemeral=True); return
        if not code:
            raise UserError("`code` is required.")
        if a == "add":
            r = plansvc.promo_add(code, pct=pct, usd=usd, uses=uses, until=until, plan=plan,
                                  owner_id=(owner.id if owner else None), owner_name=(owner.display_name if owner else None), share_pct=share)
            off = f"{r['pct']:g}% off" if r.get("pct") else (f"${r['usd']:g} off" if r.get("usd") else "no discount")
            msg = f"Code **{r['code']}** added - {off}" + (f", {r['share_pct']:g}% of each payment to {r['owner_name']}" if r.get("owner_id") is not None else "") + "."
        else:
            r = plansvc.promo_remove(code)
            msg = f"Code **{r['code']}** removed."
    except UserError as e:
        await interaction.followup.send(e.message, ephemeral=True)
        return
    await refresh_panel()
    await mod_log(f"**Promo** - {interaction.user.display_name}: {msg}")
    await interaction.followup.send(msg, embed=_promos_embed(), ephemeral=True)


bot.tree.add_command(admin)

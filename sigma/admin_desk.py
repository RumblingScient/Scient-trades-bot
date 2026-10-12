"""sigma.admin_desk - /admin desk: the members, plans and promo console on one private panel.

Row 0  tabs       Overview · Members · Revenue · Payments · Unrecorded
Row 1  sections   Plans · Promos · Payouts · Export CSV · Refresh
Row 2  member     Discord user picker -> that member's card (plan, expiry, paid, history)
Row 3  context    plan picker (Plans / member card) or promo picker (Promos)
Row 4  actions    what applies to the current context (Grant / Extend · Revoke · Note · Edit · Discount · ...)

Every write goes through the same services the slash commands used (sigma.services.plans / subs,
sigma.payments.activate) and lands in the mod-log. Admin only - the panel refuses anyone else.
"""
import discord
from datetime import datetime, timezone

from sigma.logging_setup import print
from sigma.config import ANALYST_ROLE_NAME, GOLD, NAVY, RED, SUB_ROLE_ID
from sigma.core import bot
from sigma.errors import UserError
from sigma.storage import load_subs, save_subs, load_payments, save_payments
from sigma.services import plans as plansvc
from sigma.services import subs as subsvc
from sigma.desk import Form

PANEL_TIMEOUT = 14 * 60
TABS = [("overview", "Overview"), ("members", "Members"), ("revenue", "Revenue"), ("payments", "Payments"), ("unrecorded", "Unrecorded")]


def _is_admin(interaction: discord.Interaction) -> bool:
    return bool(interaction.user.guild_permissions.administrator)


def _now():
    return datetime.now(timezone.utc)


def unrecorded_members(guild) -> list:
    """Pro-role holders with no subscription record (bots excluded). [(member, is_staff)]"""
    if not guild or not SUB_ROLE_ID:
        return []
    role = guild.get_role(SUB_ROLE_ID)
    subs = load_subs()
    out = []
    for m in (role.members if role else []):
        if getattr(m, "bot", False) or str(m.id) in subs:
            continue
        staff = any(getattr(r, "name", "") == ANALYST_ROLE_NAME for r in m.roles) or bool(getattr(getattr(m, "guild_permissions", None), "administrator", False))
        out.append((m, staff))
    return out


def _plan_short(key):
    p = plansvc.all().get(key) or {}
    return p.get("short") or key or "?"


# ----------------------------------------------------------------------------- embeds

def plans_embed() -> discord.Embed:
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
    e.set_footer(text="Pick a plan below to edit, discount, disable or remove · the payment panel re-renders by itself")
    return e


def promos_embed() -> discord.Embed:
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
    e.set_footer(text="Add a code below · pick one to remove it")
    return e


def payouts_embed() -> discord.Embed:
    st = load_payments()
    summ = plansvc.referral_summary(st)
    e = discord.Embed(title="Referral payouts", color=GOLD)
    rows = []
    for oid, o in sorted(summ.items(), key=lambda x: -x[1]["owed"]):
        rows.append(f"**{o['name'] or oid}** (`{oid}`) · {', '.join(o['codes'])} · {o['members']} member(s) · brought ${o['usd']:,.0f} · "
                    f"**owed ${o['owed']:,.2f}** · paid ${o['settled']:,.2f}")
    e.description = "\n".join(rows) or "no referral payments yet"
    e.set_footer(text="Settle marks everything an owner is owed as paid · payouts themselves are manual")
    return e


def member_embed(guild: discord.Guild, uid: int) -> discord.Embed:
    subs = load_subs()
    rec = subs.get(str(uid))
    member = guild.get_member(uid) if guild else None
    name = member.display_name if member else (rec or {}).get("name") or str(uid)
    e = discord.Embed(title=f"{name}", color=NAVY)
    e.description = f"<@{uid}> · `{uid}`" + ("" if member else " · **not in the server**")
    has_role = bool(member and SUB_ROLE_ID and any(r.id == SUB_ROLE_ID for r in member.roles))
    if rec:
        dl = subsvc.days_left(rec)
        life = int(rec.get("days") or 0) >= plansvc.LIFETIME_DAYS
        try:
            exp = datetime.fromisoformat(rec["expires"]).strftime("%d %b %Y")
        except Exception:
            exp = "?"
        paid = sum(float(h.get("usd") or 0) for h in rec.get("history", []))
        e.add_field(name="Plan", value=_plan_short(rec.get("plan")), inline=True)
        e.add_field(name="Expires", value=("lifetime" if life else f"{exp} ({dl}d)"), inline=True)
        e.add_field(name="Paid", value=f"${paid:,.0f}", inline=True)
        e.add_field(name="Started", value=(rec.get("started") or "")[:10] or "-", inline=True)
        e.add_field(name="Pro role", value="yes" if has_role else "**missing**", inline=True)
        e.add_field(name="Reminders sent", value=", ".join(f"{d}d" for d in (rec.get("reminded") or [])) or "none", inline=True)
        hist = rec.get("history") or []
        if hist:
            lines = [f"{(h.get('at') or '')[:10]} · {_plan_short(h.get('plan'))} · ${float(h.get('usd') or 0):,.0f} · {h.get('by') or '?'}"
                     + (f" · `{str(h['tx'])[:10]}…`" if h.get("tx") else "") for h in hist[-6:]]
            e.add_field(name="History", value="\n".join(lines)[:1024], inline=False)
        if rec.get("note"):
            e.add_field(name="Note", value=rec["note"][:1024], inline=False)
    else:
        st = load_payments()
        past = [l for l in (st.get("lapsed") or []) if str(l.get("uid")) == str(uid)]
        e.add_field(name="Subscription", value="**none on record**" + (" · holds the Pro role" if has_role else ""), inline=False)
        if past:
            l = past[-1]
            e.add_field(name="Last record", value=f"{_plan_short(l.get('plan'))} · expired {(l.get('expires') or '')[:10]} · {l.get('why', '')}", inline=False)
    e.set_footer(text="Pick a plan below, then Grant / Extend · Revoke removes the role and archives the record")
    return e


# ----------------------------------------------------------------------------- the panel

class AdminDesk(discord.ui.View):
    def __init__(self, origin: discord.Interaction):
        super().__init__(timeout=PANEL_TIMEOUT)
        self.origin = origin
        self.guild = origin.guild
        self.tab = "overview"
        self.section = None          # None | "plans" | "promos" | "payouts" | "member"
        self.uid = None
        self.plan_key = None
        self.promo_code = None
        self.last = ""
        self.pending = None
        self.rebuild()

    # ---- rendering
    def embed(self) -> discord.Embed:
        from sigma.payments import members_embed
        try:
            if self.section == "member" and self.uid:
                e = member_embed(self.guild, self.uid)
            elif self.section == "plans":
                e = plans_embed()
            elif self.section == "promos":
                e = promos_embed()
            elif self.section == "payouts":
                e = payouts_embed()
            else:
                e = members_embed(self.guild, self.tab)
        except Exception as ex:
            print(f"[admin-desk] embed error: {ex!r}", flush=True)
            e = discord.Embed(title="Admin desk", description="Couldn't render this view - check the logs.", color=RED)
        if self.last:
            e.add_field(name="Last", value=self.last[:1000], inline=False)
        return e

    def rebuild(self):
        self.clear_items()
        if self.pending:
            label, _fn = self.pending
            self._btn(f"\u2714 Confirm: {label}"[:80], self.on_confirm, discord.ButtonStyle.success, row=0)
            self._btn("\u2716 Cancel", self.on_cancel, discord.ButtonStyle.secondary, row=0)
            return
        for key, label in TABS:
            active = self.section is None and self.tab == key
            b = discord.ui.Button(label=label, style=(discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary), row=0)
            b.callback = self._tab_cb(key)
            self.add_item(b)
        for label, cb, sec in (("Plans", self.on_plans, "plans"), ("Promos", self.on_promos, "promos"), ("Payouts", self.on_payouts, "payouts")):
            b = discord.ui.Button(label=label, style=(discord.ButtonStyle.primary if self.section == sec else discord.ButtonStyle.secondary), row=1)
            b.callback = cb
            self.add_item(b)
        b = discord.ui.Button(label="Export CSV", style=discord.ButtonStyle.secondary, row=1); b.callback = self.on_csv; self.add_item(b)
        b = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, row=1); b.callback = self.on_refresh; self.add_item(b)
        us = discord.ui.UserSelect(placeholder="Pick a member - view, grant, extend, revoke", row=2, min_values=1, max_values=1)
        us.callback = self.on_user
        self.add_item(us)
        if self.section == "member":
            self._plan_select(row=3, placeholder="Plan to grant / extend…")
            rec = load_subs().get(str(self.uid))
            self._btn("Grant / Extend" if rec else "Grant", self.on_grant, discord.ButtonStyle.success, row=4, disabled=not self.plan_key)
            self._btn("Revoke", self.on_revoke, discord.ButtonStyle.danger, row=4, disabled=not rec)
            self._btn("Add note", self.on_note, discord.ButtonStyle.secondary, row=4, disabled=not rec)
            self._btn("Close card", self.on_close_card, discord.ButtonStyle.secondary, row=4)
        elif self.section == "plans":
            self._plan_select(row=3, placeholder="Pick a plan…", include_disabled=True)
            p = plansvc.all().get(self.plan_key) if self.plan_key else None
            self._btn("Edit", self.on_plan_edit, discord.ButtonStyle.primary, row=4, disabled=not p)
            self._btn("Discount", self.on_plan_discount, discord.ButtonStyle.secondary, row=4, disabled=not p)
            self._btn(("Enable" if (p and not p.get("enabled", True)) else "Disable"), self.on_plan_toggle, discord.ButtonStyle.secondary, row=4, disabled=not p)
            self._btn("Remove", self.on_plan_remove, discord.ButtonStyle.danger, row=4, disabled=not p)
            self._btn("Add plan", self.on_plan_add, discord.ButtonStyle.success, row=4)
        elif self.section == "promos":
            codes = list(plansvc.promos().keys())
            if codes:
                sel = discord.ui.Select(placeholder="Pick a code…", row=3, min_values=1, max_values=1,
                                        options=[discord.SelectOption(label=c[:100], value=c, default=(c == self.promo_code)) for c in codes[:25]])
                sel.callback = self.on_promo_pick
                self.add_item(sel)
            self._btn("Add code", self.on_promo_add, discord.ButtonStyle.success, row=4)
            self._btn("Remove selected", self.on_promo_remove, discord.ButtonStyle.danger, row=4, disabled=not self.promo_code)
        elif self.section == "payouts":
            self._btn("Settle an owner", self.on_settle, discord.ButtonStyle.primary, row=4)
        elif self.section is None and self.tab == "unrecorded":
            self._plan_select(row=3, placeholder="Plan for the bulk grant\u2026")
            n = len([1 for _m, staff in unrecorded_members(self.guild) if not staff])
            self._btn(f"Grant to all unrecorded ({n})", self.on_bulk_grant, discord.ButtonStyle.success, row=4, disabled=(not self.plan_key or n == 0))
            self._btn(f"Remove Pro role from all unrecorded ({n})", self.on_bulk_remove, discord.ButtonStyle.danger, row=4, disabled=(n == 0))

    def _plan_select(self, row, placeholder, include_disabled=False):
        opts = []
        for k, p in plansvc.all().items():
            if not include_disabled and not p.get("enabled", True):
                continue
            eff, _why = plansvc.effective_price(p)
            dur = "lifetime" if p["days"] >= plansvc.LIFETIME_DAYS else f"{p['days']}d"
            opts.append(discord.SelectOption(label=f"{p['short']} - ${eff:,.0f} / {dur}"[:100], value=k, default=(k == self.plan_key)))
        if not opts:
            return
        sel = discord.ui.Select(placeholder=placeholder, row=row, min_values=1, max_values=1, options=opts[:25])
        sel.callback = self.on_plan_pick
        self.add_item(sel)

    def _btn(self, label, cb, style, row, disabled=False):
        b = discord.ui.Button(label=label, style=style, row=row, disabled=disabled)
        b.callback = cb
        self.add_item(b)

    def _tab_cb(self, key):
        async def cb(interaction):
            self.tab, self.section = key, None
            await self._show(interaction)
        return cb

    # ---- plumbing
    async def _show(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()
        self.rebuild()
        emb = self.embed()
        try:
            await interaction.edit_original_response(embed=emb, view=self)
        except Exception:
            try:
                await self.origin.edit_original_response(embed=emb, view=self)
            except Exception as ex:
                print(f"[admin-desk] refresh failed: {ex}", flush=True)

    async def _apply(self, interaction: discord.Interaction, fn):
        """Defer, run fn() -> reply text, re-render. UserError -> Last line."""
        if not interaction.response.is_done():
            await interaction.response.defer()
        if not _is_admin(interaction):
            self.last = "⚠ Admins only."
            await self._show(interaction); return
        try:
            self.last = await fn()
        except UserError as e:
            self.last = f"⚠ {e.message}"
        except Exception as ex:
            print(f"[admin-desk] action error: {ex!r}", flush=True)
            self.last = "⚠ Something broke - check the logs."
        await self._show(interaction)

    async def _ask(self, interaction, label, what, run):
        self.pending = (label, run)
        self.last = f"**{label}** - {what}\nPress Confirm to apply, Cancel to leave it."
        await self._show(interaction)

    async def on_confirm(self, interaction):
        if not self.pending:
            await self._show(interaction); return
        _l, run = self.pending
        self.pending = None
        await run(interaction)

    async def on_cancel(self, interaction):
        self.pending = None
        self.last = "Cancelled - nothing changed."
        await self._show(interaction)

    async def _log(self, text, color=None):
        from sigma.payments import mod_log
        try:
            await mod_log(text, color=color) if color else await mod_log(text)
        except Exception:
            pass

    # ---- sections
    async def on_plans(self, interaction):
        self.section = "plans"; await self._show(interaction)

    async def on_promos(self, interaction):
        self.section = "promos"; await self._show(interaction)

    async def on_payouts(self, interaction):
        self.section = "payouts"; await self._show(interaction)

    async def on_refresh(self, interaction):
        self.last = ""; await self._show(interaction)

    async def on_close_card(self, interaction):
        self.section, self.uid = None, None; await self._show(interaction)

    async def on_csv(self, interaction):
        from sigma.payments import members_csv
        if not _is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await interaction.response.send_message(file=discord.File(members_csv(load_subs()), filename="sigma_members.csv"), ephemeral=True)

    # ---- member card
    async def on_user(self, interaction):
        vals = interaction.data.get("values") or []
        if not vals:
            await self._show(interaction); return
        self.uid = int(vals[0])
        self.section = "member"
        self.last = ""
        await self._show(interaction)

    async def on_plan_pick(self, interaction):
        vals = interaction.data.get("values") or []
        self.plan_key = vals[0] if vals else None
        await self._show(interaction)

    async def on_grant(self, interaction):
        uid, plan = self.uid, self.plan_key
        rec = load_subs().get(str(uid))
        p = plansvc.all().get(plan) or {}
        who = interaction.user.display_name

        async def ok(i, v):
            async def run():
                from sigma.payments import activate
                usd = None
                if v["usd"]:
                    try:
                        usd = float(v["usd"].replace("$", "").replace(",", ""))
                    except ValueError:
                        raise UserError("Paid must be a number, e.g. 100.")
                r = await activate(uid, plan, by=who, note=v["note"] or "", usd=usd, expires_at=(v["expires"] or None))
                exp = datetime.fromisoformat(r["expires"])
                return (f"{'Extended' if rec else 'Granted'} {_plan_short(plan)} -> <@{uid}> · expires "
                        + ("lifetime" if r.get("days", 0) >= plansvc.LIFETIME_DAYS else exp.strftime("%d %b %Y")))
            await self._apply(i, run)
        try:
            cur_exp = datetime.fromisoformat(rec["expires"]).strftime("%d %b %Y") if rec else None
        except Exception:
            cur_exp = None
        await interaction.response.send_modal(Form(f"{'Extend' if rec else 'Grant'} - {p.get('short', plan)}", [
            ("expires", "Expiry date (blank = plan length" + (f", from {cur_exp}" if cur_exp else "") + ")", "", False, "e.g. 2026-11-15 or 15 Nov"),
            ("usd", "Paid (USD, blank = plan price)", "", False, "0 for a free grant"),
            ("note", "Note (optional)", "", False, "why - shown in mod-log"),
        ], ok))

    async def on_revoke(self, interaction):
        uid = self.uid
        who = interaction.user.display_name

        async def ok(i, v):
            async def run():
                from sigma.members import _archive_lapsed, _sub_remove_role
                subs = load_subs()
                rec = subsvc.revoke(subs, uid)
                save_subs(subs)
                _archive_lapsed(str(uid), rec, "revoked" + (f" - {v['note']}" if v["note"] else ""))
                await _sub_remove_role(self.guild, uid)
                await self._log(f"**Revoked** {_plan_short(rec.get('plan'))} from <@{uid}> (`{uid}`) by {who}" + (f" · {v['note']}" if v["note"] else ""), color=RED)
                return f"Revoked <@{uid}> - role removed, record archived."
            await self._apply(i, run)
        await interaction.response.send_modal(Form("Revoke - are you sure?", [
            ("note", "Reason (optional) - this removes the Pro role now", "", False, "e.g. refund / chargeback"),
        ], ok))

    async def on_note(self, interaction):
        uid = self.uid
        rec = load_subs().get(str(uid)) or {}

        async def ok(i, v):
            async def run():
                subs = load_subs()
                r = subs.get(str(uid))
                if not r:
                    raise UserError("No subscription on record.")
                r["note"] = v["note"][:120]
                save_subs(subs)
                return "Note saved."
            await self._apply(i, run)
        await interaction.response.send_modal(Form("Member note", [("note", "Note", rec.get("note") or "", False, "")], ok))

    # ---- plans
    async def on_plan_edit(self, interaction):
        key = self.plan_key
        p = plansvc.all().get(key) or {}
        who = interaction.user.display_name

        async def ok(i, v):
            async def run():
                from sigma.payments import refresh_panel
                pp, ch = plansvc.edit(key, name=(v["name"] or None), price=(v["price"] or None), days=(v["days"] or None),
                                      seats=(int(v["seats"]) if v["seats"].strip() else None),
                                      order=(int(v["position"]) if v["position"].strip() else None))
                if not ch:
                    raise UserError("Nothing changed.")
                await refresh_panel()
                msg = f"Updated **{pp['short']}**: {', '.join(ch)}."
                await self._log(f"**Plans** - {who}: {msg}")
                return msg
            await self._apply(i, run)
        await interaction.response.send_modal(Form(f"Edit plan - {p.get('short', key)}", [
            ("name", "Display name", p.get("short", ""), False, ""),
            ("price", "Price (USD)", f"{p.get('price', 0):g}", False, ""),
            ("days", "Access days (or lifetime)", ("lifetime" if p.get("days", 0) >= plansvc.LIFETIME_DAYS else str(p.get("days", ""))), False, ""),
            ("seats", "Seat cap (blank = unlimited, 0 clears)", (str(p["seats"]) if p.get("seats") else ""), False, ""),
            ("position", "Order in the panel (0 = first)", "", False, ""),
        ], ok))

    async def on_plan_discount(self, interaction):
        key = self.plan_key
        p = plansvc.all().get(key) or {}
        who = interaction.user.display_name

        async def ok(i, v):
            async def run():
                from sigma.payments import refresh_panel
                pp = plansvc.set_discount(key, v["pct"] or "0", v["until"] or None)
                eff, why = plansvc.effective_price(pp)
                await refresh_panel()
                msg = (f"**{pp['short']}** now ${eff:,.0f} ({', '.join(why)})" + (f" until {v['until']}" if v["until"] and pp.get("discount_pct") else "")) if why else f"Discount removed from **{pp['short']}**."
                await self._log(f"**Plans** - {who}: {msg}")
                return msg
            await self._apply(i, run)
        await interaction.response.send_modal(Form(f"Discount - {p.get('short', key)}", [
            ("pct", "Percent off (0 removes)", (f"{p['discount_pct']:g}" if p.get("discount_pct") else ""), True, "e.g. 20"),
            ("until", "Last day (blank = no end)", "", False, "e.g. 2026-10-31 or 31 Oct"),
        ], ok))

    async def on_plan_toggle(self, interaction):
        key = self.plan_key
        who = interaction.user.display_name
        p0 = plansvc.all().get(key) or {}
        turning_on = not p0.get("enabled", True)

        async def run():
            from sigma.payments import refresh_panel
            p = plansvc.all().get(key)
            if not p:
                raise UserError("Pick a plan first.")
            on = not p.get("enabled", True)
            pp, _ = plansvc.edit(key, enabled=on)
            await refresh_panel()
            msg = f"**{pp['short']}** {'enabled' if on else 'disabled'}."
            await self._log(f"**Plans** - {who}: {msg}")
            return msg
        await self._ask(interaction, f"{'Enable' if turning_on else 'Disable'} {p0.get('short', key)}",
                        "shows it on the payment panel again." if turning_on else "hides it from the payment panel - existing members keep access.",
                        lambda i: self._apply(i, run))

    async def on_plan_remove(self, interaction):
        key = self.plan_key
        who = interaction.user.display_name

        async def ok(i, v):
            async def run():
                from sigma.payments import refresh_panel
                if v["confirm"].strip().lower() != key.lower():
                    raise UserError(f"Type the plan key `{key}` to confirm.")
                p = plansvc.remove(key)
                self.plan_key = None
                await refresh_panel()
                msg = f"Removed **{p['short']}**. Existing members on it keep their access until expiry."
                await self._log(f"**Plans** - {who}: {msg}")
                return msg
            await self._apply(i, run)
        await interaction.response.send_modal(Form(f"Remove plan {key}", [("confirm", f"Type {key} to confirm", "", True, key)], ok))

    async def on_plan_add(self, interaction):
        who = interaction.user.display_name

        async def ok(i, v):
            async def run():
                from sigma.payments import refresh_panel
                p = plansvc.add(v["key"], v["name"], v["price"], v["days"], (int(v["seats"]) if v["seats"].strip() else None))
                self.plan_key = v["key"]
                await refresh_panel()
                msg = f"Added **{p['short']}** - ${p['price']:,.0f}, {'lifetime' if p['days'] >= plansvc.LIFETIME_DAYS else str(p['days']) + ' days'}."
                await self._log(f"**Plans** - {who}: {msg}")
                return msg
            await self._apply(i, run)
        await interaction.response.send_modal(Form("Add a plan", [
            ("key", "Key (short id, e.g. launch_week)", "", True, ""),
            ("name", "Display name", "", True, "e.g. Launch Week"),
            ("price", "Price (USD)", "", True, "e.g. 70"),
            ("days", "Access days (or lifetime)", "", True, "e.g. 30"),
            ("seats", "Seat cap (blank = unlimited)", "", False, ""),
        ], ok))

    # ---- promos
    async def on_promo_pick(self, interaction):
        vals = interaction.data.get("values") or []
        self.promo_code = vals[0] if vals else None
        await self._show(interaction)

    async def on_promo_add(self, interaction):
        who = interaction.user.display_name

        async def ok(i, v):
            async def run():
                from sigma.payments import refresh_panel
                disc = v["discount"].strip()
                pct = usd = None
                if disc:
                    if disc.endswith("%"):
                        pct = disc[:-1]
                    elif disc.startswith("$"):
                        usd = disc[1:]
                    else:
                        pct = disc
                uses = until = None
                for part in [x.strip() for x in v["limits"].split("/") if x.strip()]:
                    if part.isdigit():
                        uses = int(part)
                    else:
                        until = part
                owner_id = owner_name = share = None
                ref = v["referral"].strip()
                if ref:
                    bits = ref.replace("<@", "").replace(">", "").split()
                    if not bits or not bits[0].isdigit():
                        raise UserError("Referral: `<user id> <share %>`, e.g. `123456789 20`.")
                    owner_id = int(bits[0])
                    share = bits[1] if len(bits) > 1 else "0"
                    m = self.guild.get_member(owner_id) if self.guild else None
                    owner_name = m.display_name if m else str(owner_id)
                r = plansvc.promo_add(v["code"], pct=pct, usd=usd, uses=uses, until=until, plan=(v["plan"] or None),
                                      owner_id=owner_id, owner_name=owner_name, share_pct=share)
                self.promo_code = r["code"]
                await refresh_panel()
                off = f"{r['pct']:g}% off" if r.get("pct") else (f"${r['usd']:g} off" if r.get("usd") else "no discount")
                msg = f"Code **{r['code']}** added - {off}" + (f", {r['share_pct']:g}% of each payment to {r['owner_name']}" if r.get("owner_id") is not None else "") + "."
                await self._log(f"**Promo** - {who}: {msg}")
                return msg
            await self._apply(i, run)
        await interaction.response.send_modal(Form("Add a promo / referral code", [
            ("code", "Code", "", True, "e.g. SIGMA20 or SIGMA-OWAIS"),
            ("discount", "Discount: 20% or $20 (blank = pure referral)", "", False, ""),
            ("limits", "Uses / last day (optional)", "", False, "e.g. 10 / 31 Oct"),
            ("plan", "Only for this plan key (optional)", "", False, "e.g. 1month"),
            ("referral", "Referral owner: <user id> <share %>", "", False, "e.g. 123456789012345678 20"),
        ], ok))

    async def on_promo_remove(self, interaction):
        code = self.promo_code
        who = interaction.user.display_name

        async def run():
            from sigma.payments import refresh_panel
            if not code:
                raise UserError("Pick a code first.")
            r = plansvc.promo_remove(code)
            self.promo_code = None
            await refresh_panel()
            msg = f"Code **{r['code']}** removed."
            await self._log(f"**Promo** - {who}: {msg}")
            return msg
        await self._ask(interaction, f"Remove code {code}", "the code stops working immediately; past payments with it stay recorded.",
                        lambda i: self._apply(i, run))

    # ---- unrecorded bulk tools (staff - Analyst role / admins - are always skipped)
    async def on_bulk_grant(self, interaction):
        plan = self.plan_key
        who = interaction.user.display_name
        targets = [m for m, staff in unrecorded_members(self.guild) if not staff]
        p = plansvc.all().get(plan) or {}

        async def ok(i, v):
            async def run():
                from sigma.payments import activate
                if v["confirm"].strip().upper() != "GRANT":
                    raise UserError("Type GRANT to confirm.")
                done, failed = 0, []
                for m in targets:
                    try:
                        await activate(m.id, plan, by=who, note=(v["note"] or "bulk grant - unrecorded Pro holder"), usd=0.0,
                                       expires_at=(v["expires"] or None))
                        done += 1
                    except Exception as ex:
                        failed.append(f"{m.display_name}: {ex}")
                await self._log(f"**Bulk grant** by {who}: {_plan_short(plan)} -> {done} unrecorded member(s)"
                                + (f" \u00b7 expires {v['expires']}" if v["expires"] else "") + (f" \u00b7 {len(failed)} failed" if failed else ""))
                return f"Granted {_plan_short(plan)} to {done} member(s)." + (f" Failed: {'; '.join(failed[:3])}" if failed else "")
            await self._apply(i, run)
        await interaction.response.send_modal(Form(f"Grant {p.get('short', plan)} to {len(targets)} members", [
            ("expires", "Expiry date (blank = plan length from today)", "", False, "e.g. 2026-12-31 or 31 Dec"),
            ("note", "Note (optional)", "referral / manual - bulk", False, ""),
            ("confirm", "Type GRANT to confirm", "", True, "GRANT"),
        ], ok))

    async def on_bulk_remove(self, interaction):
        who = interaction.user.display_name
        targets = [m for m, staff in unrecorded_members(self.guild) if not staff]

        async def ok(i, v):
            async def run():
                from sigma.members import _sub_remove_role
                if v["confirm"].strip().upper() != "REMOVE":
                    raise UserError("Type REMOVE to confirm.")
                done = 0
                for m in targets:
                    try:
                        if await _sub_remove_role(self.guild, m.id):
                            done += 1
                    except Exception:
                        pass
                await self._log(f"**Bulk role removal** by {who}: Pro role removed from {done} unrecorded member(s)" + (f" \u00b7 {v['note']}" if v["note"] else ""), color=RED)
                return f"Removed the Pro role from {done} member(s)."
            await self._apply(i, run)
        await interaction.response.send_modal(Form(f"Remove Pro role from {len(targets)} members", [
            ("note", "Reason (optional)", "expired on the old bot", False, ""),
            ("confirm", "Type REMOVE to confirm", "", True, "REMOVE"),
        ], ok))

    # ---- payouts
    async def on_settle(self, interaction):
        who = interaction.user.display_name

        async def ok(i, v):
            async def run():
                oid = v["owner"].strip().replace("<@", "").replace(">", "")
                if not oid.isdigit():
                    raise UserError("Owner must be a Discord user id (shown in the payouts card).")
                st = load_payments()
                total = plansvc.settle(st, int(oid), v["note"] or "")
                save_payments(st)
                m = self.guild.get_member(int(oid)) if self.guild else None
                nm = m.display_name if m else oid
                await self._log(f"**Payout settled** ${total:,.2f} -> {nm} (`{oid}`) by {who}" + (f" · {v['note']}" if v["note"] else ""), color=GOLD)
                return f"Settled **${total:,.2f}** for {nm}."
            await self._apply(i, run)
        await interaction.response.send_modal(Form("Settle a referral payout", [
            ("owner", "Owner user id (from the card)", "", True, ""),
            ("note", "Payout reference (optional)", "", False, "e.g. the SOL tx"),
        ], ok))

    async def on_timeout(self):
        try:
            await self.origin.edit_original_response(content="Admin desk closed (14 min). Run /admin desk again.", embed=None, view=None)
        except Exception:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        print(f"[admin-desk] view error: {error!r}", flush=True)
        self.last = "⚠ Something broke on that click."
        try:
            await self._show(interaction)
        except Exception:
            pass


async def open_admin_desk(interaction: discord.Interaction):
    if not _is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    view = AdminDesk(interaction)
    await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

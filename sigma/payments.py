"""sigma.payments - SOL payments to a fixed wallet, unique amount per session, verified on chain.

Member: dropdown in #join-via-payment -> private quote (wallet, exact SOL, 30 min) -> pays ->
the watcher sees the transfer on chain, matches the amount to the session -> role + DM + mod-log.
Admin: /admin panel kind:pay (post the panel), /admin members (dashboard), /admin grant with a
tx note for anything that didn't match (the watcher alerts the ops channel on unmatched payments).

Everything that touches the subs record goes through sigma.services.subs - same path as /admin grant.
"""
import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import tasks

from sigma.logging_setup import log, print
from sigma.config import (GOLD, GREEN, GREY, GUILD_ID, MOD_LOG_CHANNEL_ID, NAVY, OPS_CHANNEL_ID, PAYMENT_CHANNEL_ID,
                          PAY_FEE_SLACK, PAY_LATE_GRACE_H, PAY_MATCH_TOL, PAY_MIN_SOL, PAY_POLL_SEC, PAY_SESSION_MIN, PAY_VALUE_FLOOR, SOL_RPC, SOL_WALLET,
                          SUB_REMINDER_DAYS)
from sigma.core import bot
from sigma.ops import HEARTBEAT, ops_alert
from sigma.http import http
from sigma.storage import load_payments, save_payments, load_subs, save_subs
from sigma.errors import UserError
from sigma.services import subs as subsvc
from sigma.services import plans as plansvc
from sigma.members import _sub_grant_role

LAMPORTS = 1_000_000_000
LIFETIME_DAYS = plansvc.LIFETIME_DAYS


# ----------------------------------------------------------------------------- pure helpers

def _now():
    return datetime.now(timezone.utc)


def quote_amount(usd: float, sol_price: float, taken: set) -> float:
    """SOL amount for `usd`, with a unique 5th-decimal tail so two open quotes never collide."""
    if not sol_price or sol_price <= 0:
        raise UserError("Couldn't fetch the SOL price - try again in a minute.")
    base = round(usd / sol_price, 3)
    for _ in range(200):
        amt = round(base + random.randint(1, 999) * 0.00001, 5)
        if all(abs(amt - t) > PAY_MATCH_TOL * 2 for t in taken):
            return amt
    raise UserError("Too many open quotes right now - try again in a minute.")


def received_sol(tx: dict, wallet: str) -> float | None:
    """Net SOL that `wallet` gained in a jsonParsed transaction (None if the wallet isn't in it)."""
    try:
        keys = tx["transaction"]["message"]["accountKeys"]
        idx = next(i for i, k in enumerate(keys) if (k.get("pubkey") if isinstance(k, dict) else k) == wallet)
        meta = tx["meta"]
        if meta.get("err"):
            return None
        return (meta["postBalances"][idx] - meta["preBalances"][idx]) / LAMPORTS
    except Exception:
        return None


def _live(sessions: dict, now):
    for sid, s in sessions.items():
        if s.get("paid") or s.get("underpaid"):
            continue
        try:
            created = datetime.fromisoformat(s["created"])
        except Exception:
            continue
        if now - created <= timedelta(hours=PAY_LATE_GRACE_H):
            yield sid, s, created


def match_session(sessions: dict, amount: float, now=None) -> tuple[str | None, str]:
    """(session id, how). how = 'exact' | 'fee' | ''.
    exact: quoted amount within PAY_MATCH_TOL. fee: no exact hit, but exactly ONE live quote is short by
    0 < quoted - received <= PAY_FEE_SLACK (exchange withdrawal fee). Two candidates = ambiguous = no match."""
    now = now or _now()
    best = None
    for sid, s, created in _live(sessions, now):
        if abs(float(s["amount"]) - amount) <= PAY_MATCH_TOL:
            if best is None or created > datetime.fromisoformat(sessions[best]["created"]):
                best = sid
    if best:
        return best, "exact"
    cands = [sid for sid, s, _ in _live(sessions, now) if 0 < float(s["amount"]) - amount <= PAY_FEE_SLACK]
    if len(cands) == 1:
        return cands[0], "fee"
    return None, ""


def session_open(s: dict, now=None) -> bool:
    try:
        return (now or _now()) - datetime.fromisoformat(s["created"]) <= timedelta(minutes=PAY_SESSION_MIN)
    except Exception:
        return False


def prune(state: dict, now=None):
    now = now or _now()
    keep = {}
    for sid, s in (state.get("sessions") or {}).items():
        try:
            age = now - datetime.fromisoformat(s["created"])
        except Exception:
            continue
        if s.get("paid") or age <= timedelta(hours=PAY_LATE_GRACE_H):
            keep[sid] = s
    state["sessions"] = keep
    state["seen"] = (state.get("seen") or [])[-500:]
    return state


# ----------------------------------------------------------------------------- chain + price

async def sol_price() -> float | None:
    try:
        async with http() as s:
            async with s.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "SOLUSDT"}, timeout=10) as r:
                if r.status == 200:
                    return float((await r.json())["price"])
    except Exception as e:
        print(f"[pay] price error: {e}", flush=True)
    return None


async def _rpc(method: str, params: list):
    async with http() as s:
        async with s.post(SOL_RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20) as r:
            if r.status != 200:
                raise RuntimeError(f"rpc {method} http {r.status}")
            j = await r.json()
            if "error" in j:
                raise RuntimeError(f"rpc {method}: {j['error']}")
            return j.get("result")


async def recent_signatures(limit: int = 25) -> list:
    res = await _rpc("getSignaturesForAddress", [SOL_WALLET, {"limit": limit}]) or []
    return [x["signature"] for x in res if not x.get("err")]


async def fetch_tx(sig: str) -> dict | None:
    return await _rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])


# ----------------------------------------------------------------------------- side effects

async def mod_log(text: str, color=GREY):
    cid = MOD_LOG_CHANNEL_ID or OPS_CHANNEL_ID
    ch = bot.get_channel(cid) if cid else None
    if ch is None:
        log.info(f"[modlog] {text}")
        return
    try:
        await ch.send(embed=discord.Embed(description=text, color=color, timestamp=_now()))
    except Exception as e:
        log.warning(f"[modlog] send failed: {e}")


async def activate(uid: int, plan: str, by: str, tx: str = None, usd: float = None, note: str = "", expires_at=None) -> dict:
    """Grant (or extend) + role + DM + mod-log. Used by the chain watcher and by /admin grant."""
    guild = bot.get_guild(GUILD_ID)
    member = None
    if guild:
        try:
            member = guild.get_member(uid) or await guild.fetch_member(uid)
        except Exception:
            member = None
    name = member.display_name if member else str(uid)
    subs = load_subs()
    rec = subsvc.grant(subs, uid, name, plan, by, note=note, tx=tx, usd=(0.0 if expires_at else usd), expires_at=expires_at)
    save_subs(subs)
    ok = await _sub_grant_role(guild, uid) if guild else False
    p = plansvc.get(plan)
    exp = datetime.fromisoformat(rec["expires"])
    if member:
        try:
            e = discord.Embed(
                title="Sigma Pro activated",
                description=(f"**{p['label']}** is live.\n**Access until:** "
                             + ("lifetime" if p["days"] >= LIFETIME_DAYS else exp.strftime("%d %b %Y"))
                             + (f"\n\nTx: `{tx[:20]}...`" if tx else "")
                             + f"\n\nRenewal reminders come {', '.join(str(d) for d in SUB_REMINDER_DAYS)} days before it ends."),
                color=GOLD)
            await member.send(embed=e)
        except Exception:
            pass
    await mod_log(f"**{'Imported' if expires_at else 'Granted'}** {p['label']} -> {name} (`{uid}`) by {by}"
                  + (f" · expires {exp.strftime('%d %b %Y')}" if expires_at else "")
                  + (f" · tx `{tx[:12]}...`" if tx else "") + (f" · {note}" if note else "")
                  + (" · role FAILED" if not ok else ""), color=GREEN)
    return rec


# ----------------------------------------------------------------------------- member UI

# server emotes (upload sigma/assets/emotes/plan_*.png with these exact names); unicode fallback if missing
PLAN_EMOTES = {"1month": ("plan_1m", "\U0001F539"), "3months": ("plan_3m", "\U0001F538"), "6months": ("plan_6m", "\U0001F536"),
               "1year": ("plan_1y", "\U0001F7E2"), "lifetime": ("plan_life", "\U0001F48E")}



def _banner_path():
    from sigma.config import _ROOT
    return _ROOT / "sigma" / "assets" / "pay_banner.png"


def plan_emote(key: str) -> str:
    from sigma.cards import emo
    name, fb = PLAN_EMOTES.get(key, ("plan_1m", "\u2b50"))
    return emo(name, fb)


def _guild_emoji(name: str):
    try:
        g = bot.get_guild(GUILD_ID)
        return discord.utils.get(g.emojis, name=name) if g else None
    except Exception:
        return None


def panel_embed() -> discord.Embed:
    from sigma.config import SUB_ROLE_ID
    subs = load_subs()
    e = discord.Embed(title="Sigma Pro - choose your plan", color=NAVY)
    role = f"<@&{SUB_ROLE_ID}>" if SUB_ROLE_ID else "**Sigma Pro**"
    lines = [f"{plan_emote(k)} {plansvc.describe(p, subsvc.seats_left(subs, k))}" for k, p in plansvc.all(enabled_only=True).items()]
    e.description = (f"Every plan unlocks {role}: live futures and spot setups, the full terminal, results board and monthly journals.\n\n"
                     + "\n".join(lines)
                     + "\n\nPick a plan below - you get a private message with the wallet and the exact SOL amount. "
                       "Send it and access switches on by itself once it lands on chain."
                     + ("\nHave a promo code? Pick a plan first, then tap **Promo code** on your quote." if plansvc.promos() else ""))
    try:
        if bot.user and bot.user.display_avatar:
            e.set_thumbnail(url=bot.user.display_avatar.url)
    except Exception:
        pass
    if _banner_path().exists():
        e.set_image(url="attachment://pay_banner.png")
    try:
        e.set_footer(text="Sigma Trading · payments in SOL · we never DM you first, never ask for keys",
                     icon_url=(bot.user.display_avatar.url if bot.user else None))
    except Exception:
        e.set_footer(text="Sigma Trading · payments in SOL")
    return e


class PlanSelect(discord.ui.Select):
    def __init__(self):
        subs = load_subs()
        opts = []
        for k, p in plansvc.all(enabled_only=True).items():
            left = subsvc.seats_left(subs, k)
            eff, why = plansvc.effective_price(p)
            desc = (f"${eff:,.0f}" + (f" (was ${p['price']:,.0f})" if why else "")) + ("" if p["days"] >= LIFETIME_DAYS else f" · {p['days']} days")
            if left is not None:
                desc += f" · {left} seats left" if left else " · sold out"
            em = _guild_emoji(PLAN_EMOTES.get(k, ("plan_1m", ""))[0]) or PLAN_EMOTES.get(k, ("", "\u2b50"))[1]
            opts.append(discord.SelectOption(label=p["short"][:100], value=k, description=desc[:100], emoji=em))
        if not opts:
            opts.append(discord.SelectOption(label="No plans available", value="none"))
        super().__init__(placeholder="Select a plan...", options=opts, custom_id="sigma:pay:plan", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if self.values[0] == "none":
            await interaction.followup.send("No plans are open right now.", ephemeral=True)
            return
        try:
            emb, view = await new_quote(interaction.user, self.values[0])
            await interaction.followup.send(embed=emb, view=view, ephemeral=True)
        except UserError as e:
            await interaction.followup.send(e.message, ephemeral=True)


class PaymentPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PlanSelect())


class QuoteView(discord.ui.View):
    def __init__(self, sid: str):
        super().__init__(timeout=None)
        self.sid = sid
        b = discord.ui.Button(label="I've paid - check now", style=discord.ButtonStyle.primary, custom_id=f"sigma:pay:check:{sid}")
        b.callback = self.check
        self.add_item(b)
        if plansvc.promos():
            pb = discord.ui.Button(label="Promo code", style=discord.ButtonStyle.secondary, custom_id=f"sigma:pay:promo:{sid}")
            pb.callback = self.promo
            self.add_item(pb)

    async def promo(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PromoModal(self.sid))

    async def check(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        st = load_payments()
        s = (st.get("sessions") or {}).get(self.sid)
        if not s or s.get("user_id") != interaction.user.id:
            await interaction.followup.send("That quote isn't yours or has expired - pick a plan again.", ephemeral=True)
            return
        found = await _watch_once()
        s = load_payments().get("sessions", {}).get(self.sid) or {}
        if s.get("paid"):
            await interaction.followup.send("Payment found - your access is live. Check your DMs.", ephemeral=True)
        elif s.get("underpaid"):
            await interaction.followup.send(f"Your transfer landed but is worth less than the plan (~${s.get('worth_usd', 0):,.0f} vs ${s['usd']:,.0f}) - "
                                            "an admin has been notified and will sort it out with you.", ephemeral=True)
        elif not session_open(s):
            await interaction.followup.send("This quote has expired. If you already sent SOL it will still be matched for 24h - "
                                            "otherwise pick a plan again for a fresh amount.", ephemeral=True)
        else:
            await interaction.followup.send("Nothing on chain yet. Solana confirms in seconds - if you just sent it, wait a moment and tap again."
                                            + (f" ({found} new transfer(s) seen, none matched this amount.)" if found else ""), ephemeral=True)


class PromoModal(discord.ui.Modal, title="Promo code"):
    code = discord.ui.TextInput(label="Code", placeholder="e.g. SIGMA20", max_length=24)

    def __init__(self, sid: str):
        super().__init__()
        self.sid = sid

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            emb, view = await apply_promo(interaction.user, self.sid, str(self.code.value))
            await interaction.followup.send(embed=emb, view=view, ephemeral=True)
        except UserError as e:
            await interaction.followup.send(e.message, ephemeral=True)


async def apply_promo(user, sid: str, code: str):
    st = prune(load_payments())
    s = (st.get("sessions") or {}).get(sid)
    if not s or s.get("user_id") != user.id or s.get("paid"):
        raise UserError("That quote isn't yours or is already paid - pick a plan again.")
    promo = plansvc.promo_check(code, s["plan"])
    p = plansvc.get(s["plan"])
    usd, why = plansvc.effective_price(p, promo)
    if usd <= 0:
        raise UserError("That code would make the plan free - ping an admin to grant it directly.")
    price = s.get("sol_price") or await sol_price()
    taken = {float(x["amount"]) for k, x in st["sessions"].items() if not x.get("paid") and k != sid}
    s.update({"usd": usd, "amount": quote_amount(usd, price, taken), "promo": promo["code"], "why": why,
              "created": _now().isoformat()})     # fresh 30 min on the new amount
    save_payments(st)
    await log_quote(s, sid, f"Promo applied - {promo['code']}")
    return quote_embed(s, sid), QuoteView(sid)


async def new_quote(user: discord.abc.User, plan: str):
    if not SOL_WALLET:
        raise UserError("Payments aren't configured yet - ping an admin.")
    p = plansvc.all(enabled_only=True).get(plan)
    if not p:
        raise UserError("That plan isn't open right now.")
    subs = load_subs()
    if p.get("seats") is not None and subsvc.seats_left(subs, plan) <= 0:
        raise UserError(f"{p['label']} is sold out.")
    price = await sol_price()
    st = prune(load_payments())
    sessions = st.setdefault("sessions", {})
    # one open quote per user per plan - reuse it instead of minting another amount
    for sid, s in sessions.items():
        if s.get("user_id") == user.id and s.get("plan") == plan and not s.get("paid") and session_open(s):
            return quote_embed(s, sid), QuoteView(sid)
    taken = {float(s["amount"]) for s in sessions.values() if not s.get("paid")}
    usd, why = plansvc.effective_price(p)
    amt = quote_amount(usd, price, taken)
    sid = uuid.uuid4().hex[:12]
    sessions[sid] = {"user_id": user.id, "user": str(user), "plan": plan, "usd": usd, "list_usd": p["price"], "why": why,
                     "sol_price": price, "amount": amt, "created": _now().isoformat(), "paid": False}
    save_payments(st)
    await log_quote(sessions[sid], sid, "Plan selected")
    return quote_embed(sessions[sid], sid), QuoteView(sid)


async def log_quote(s: dict, sid: str, title: str):
    """Mod-log card for a quote - same shape as the old Payment Bot's 'Plan selected' entry."""
    p = plansvc.all().get(s["plan"], {})
    e = discord.Embed(title=title, color=GOLD, timestamp=_now())
    e.add_field(name="User", value=f"<@{s['user_id']}>", inline=True)
    e.add_field(name="Plan", value=p.get("label", s["plan"]), inline=True)
    e.add_field(name="Price", value=f"${s['usd']:,.2f} ({s['amount']:.5f} SOL)" + (f"\n{', '.join(s['why'])}" if s.get("why") else ""), inline=True)
    e.add_field(name="Session", value=f"`{sid}`", inline=False)
    cid = MOD_LOG_CHANNEL_ID or OPS_CHANNEL_ID
    ch = bot.get_channel(cid) if cid else None
    if ch is None:
        log.info(f"[modlog] {title} {s.get('user')} {s['plan']} {s['amount']:.5f} SOL {sid}")
        return
    try:
        await ch.send(embed=e)
    except Exception as ex:
        log.warning(f"[modlog] quote log failed: {ex}")


def quote_embed(s: dict, sid: str) -> discord.Embed:
    p = plansvc.get(s["plan"])
    exp = datetime.fromisoformat(s["created"]) + timedelta(minutes=PAY_SESSION_MIN)
    e = discord.Embed(title=f"{p['label']} - payment", color=GOLD)
    price_line = f"plan `${s['usd']:,.0f}`" + (f" (list ${s.get('list_usd', p['price']):,.0f} - {', '.join(s['why'])})" if s.get("why") else "")
    e.description = (f"Send the **exact amount** of SOL to the wallet below.\n\n"
                     f"**Wallet (Solana):**\n```{SOL_WALLET}```\n"
                     f"**Amount to send:**\n```{s['amount']:.5f} SOL```\n"
                     f"SOL price `${s['sol_price']:,.2f}` · {price_line} · quote expires <t:{int(exp.timestamp())}:R>\n\n"
                     f"The amount's last digits identify your payment - don't round it. "
                     f"Sending from an exchange? Its withdrawal fee comes out of the amount - add it on top so the full amount lands. "
                     f"Access switches on automatically within a minute of the transfer landing; you'll get a DM.")
    e.set_footer(text=f"Quote {sid}")
    return e


# ----------------------------------------------------------------------------- watcher

_watch_lock = asyncio.Lock()


async def _watch_once() -> int:
    """Pull new transfers, match them to quotes, activate. Returns number of new incoming transfers seen.
    Serialised: the 30s loop and the 'check now' button can never process the same signature twice."""
    if not SOL_WALLET:
        return 0
    async with _watch_lock:
        return await _watch_once_inner()


async def _watch_once_inner() -> int:
    st = prune(load_payments())
    seen = set(st.get("seen") or [])
    try:
        sigs = await recent_signatures()
    except Exception as e:
        print(f"[pay] rpc error: {e}", flush=True)
        return 0
    new = [s for s in sigs if s not in seen]
    if not new:
        return 0
    found = 0
    for sig in reversed(new):              # oldest first
        try:
            tx = await fetch_tx(sig)
        except Exception as e:
            print(f"[pay] tx fetch error {sig[:12]}: {e}", flush=True)
            continue
        seen.add(sig)
        st["seen"] = list(seen)
        if not tx:
            continue
        amt = received_sol(tx, SOL_WALLET)
        if amt is None or amt <= 0:
            continue
        if amt < PAY_MIN_SOL:
            print(f"[pay] dust ignored {amt:.6f} SOL {sig[:12]}", flush=True)
            save_payments(st)
            continue
        found += 1
        sid, how = match_session(st.get("sessions", {}), amt)
        if sid is None:
            st.setdefault("unmatched", []).append({"sig": sig, "amount": amt, "at": _now().isoformat()})
            st["unmatched"] = st["unmatched"][-50:]
            save_payments(st)
            await ops_alert(f"unmatched SOL payment: {amt:.5f} SOL - https://solscan.io/tx/{sig} - resolve with /admin grant (tx in note)",
                            key=f"pay:unmatched:{sig[:12]}")
            continue
        s = st["sessions"][sid]
        # value gate: a late or fee-short payment must still be worth >= PAY_VALUE_FLOOR of the quoted USD *now*
        px_now = await sol_price()
        worth = amt * px_now if px_now else amt * float(s.get("sol_price") or 0)
        if worth < float(s["usd"]) * PAY_VALUE_FLOOR:
            s.update({"underpaid": True, "tx": sig, "received": amt, "worth_usd": round(worth, 2)})
            st.setdefault("unmatched", []).append({"sig": sig, "amount": amt, "at": _now().isoformat(), "session": sid, "worth_usd": round(worth, 2)})
            save_payments(st)
            await ops_alert(f"UNDERPAID: <@{s['user_id']}> sent {amt:.5f} SOL (~${worth:,.0f}) for {s['plan']} quoted ${s['usd']:,.0f} - "
                            f"https://solscan.io/tx/{sig} - not activated; /admin grant if you accept it", key=f"pay:under:{sig[:12]}")
            continue
        s.update({"paid": True, "tx": sig, "paid_at": _now().isoformat(), "received": amt, "match": how})
        save_payments(st)
        if s.get("promo"):
            plansvc.promo_consume(s["promo"])
            pr = plansvc.promos().get(s["promo"])
            row = plansvc.record_referral(st, pr, s, sig)
            if row:
                save_payments(st)
                await mod_log(f"**Referral** `{row['code']}` -> {row.get('owner_name') or row['owner_id']} earns ${row['share_usd']:,.2f} "
                              f"({row['share_pct']:g}% of ${row['usd']:,.0f}) from {s.get('user')}", color=GOLD)
        try:
            await activate(int(s["user_id"]), s["plan"], by="chain", tx=sig, usd=s["usd"])
        except Exception as e:
            print(f"[pay] activate failed {sid}: {e}", flush=True)
            await ops_alert(f"payment matched but activation failed for <@{s['user_id']}> ({s['plan']}): {e} - tx {sig}", key=f"pay:act:{sid}")
    save_payments(st)
    return found


@tasks.loop(seconds=PAY_POLL_SEC)
async def payment_watch_loop():
    HEARTBEAT["payments"] = __import__("time").time()
    await _watch_once()


@payment_watch_loop.before_loop
async def _before_pay():
    await bot.wait_until_ready()


# ----------------------------------------------------------------------------- admin bits

async def post_panel(channel: discord.abc.Messageable) -> discord.Message:
    files = [discord.File(_banner_path(), filename="pay_banner.png")] if _banner_path().exists() else []
    msg = await channel.send(embed=panel_embed(), view=PaymentPanel(), files=files)
    try:
        await msg.pin()
    except Exception:
        pass
    st = load_payments()
    st["panel"] = {"channel_id": channel.id, "message_id": msg.id}
    save_payments(st)
    return msg


async def refresh_panel() -> bool:
    """Re-render the pinned panel after a plan / promo change. False if there's no panel yet."""
    st = load_payments()
    pn = st.get("panel") or {}
    if not pn.get("message_id"):
        return False
    try:
        ch = bot.get_channel(pn["channel_id"]) or await bot.fetch_channel(pn["channel_id"])
        msg = await ch.fetch_message(pn["message_id"])
        await msg.edit(embed=panel_embed(), view=PaymentPanel())
        return True
    except Exception as e:
        print(f"[pay] panel refresh failed: {e}", flush=True)
        return False


def _plan_short(k):
    return plansvc.all().get(k, {}).get("short", k)


def members_embed(guild: discord.Guild, view: str = "overview") -> discord.Embed:
    subs = load_subs()
    st = load_payments()
    lapsed = st.get("lapsed") or []
    now = _now()
    sm = subsvc.summary(subs, now)
    rv = subsvc.revenue(subs, lapsed, now)
    e = discord.Embed(color=NAVY, timestamp=now)
    if view == "members":
        rows = []
        for uid, s in subs.items():
            dl = subsvc.days_left(s, now)
            if dl is None:
                continue
            life = int(s.get("days") or 0) >= LIFETIME_DAYS or s.get("plan") == "lifetime"
            exp = "lifetime" if life else f"{dl}d"
            rows.append((0 if life else dl, f"{s.get('name', uid)} · {_plan_short(s.get('plan'))} · {exp}"))
        rows.sort(key=lambda r: (r[0] == 0, r[0]))
        e.title = f"Members - active Pro ({len(rows)})"
        chunk, cur, n = [], [], 0
        for _, line in rows:
            if n + len(line) + 1 > 1000:
                chunk.append("\n".join(cur)); cur, n = [], 0
            cur.append(line); n += len(line) + 1
        if cur:
            chunk.append("\n".join(cur))
        for i, c in enumerate(chunk[:6]):
            e.add_field(name=("Soonest expiry first" if i == 0 else "\u200b"), value=c, inline=False)
        if not rows:
            e.description = "No active subscriptions on record."
        e.set_footer(text="Sigma Trading - members · CSV via the button")
        return e
    if view == "revenue":
        e.title = "Revenue (USD recorded at grant)"
        e.add_field(name="This month", value=f"${rv['this_month']:,.0f}", inline=True)
        e.add_field(name="Last month", value=f"${rv['last_month']:,.0f}", inline=True)
        e.add_field(name="All time", value=f"${rv['all']:,.0f}", inline=True)
        bp = "\n".join(f"{_plan_short(k)}: ${v:,.0f}" for k, v in sorted(rv["by_plan"].items(), key=lambda x: -x[1]))
        e.add_field(name="By plan", value=bp or "-", inline=True)
        bm = "\n".join(f"{k}: ${v:,.0f}" for k, v in rv["by_month"].items())
        e.add_field(name="By month (last 6)", value=bm or "-", inline=True)
        e.add_field(name="New paying this month", value=str(rv["new_this_month"]), inline=True)
        _pl = plansvc.all()
        mrr = sum(float(s.get("price") or _pl[s["plan"]]["price"]) / (int(s.get("days") or _pl[s["plan"]]["days"]) / 30) for s in subs.values()
                  if s.get("plan") in _pl and int(s.get("days") or _pl[s["plan"]]["days"]) < LIFETIME_DAYS)
        e.add_field(name="Run-rate (active plans / 30d)", value=f"${mrr:,.0f} / month", inline=False)
        e.set_footer(text="Sigma Trading - revenue · figures come from grant history, not the chain")
        return e
    if view == "payments":
        sess = st.get("sessions") or {}
        opn = [s for s in sess.values() if not s.get("paid") and session_open(s)]
        paid = sorted([s for s in sess.values() if s.get("paid")], key=lambda s: s.get("paid_at", ""), reverse=True)[:10]
        unm = (st.get("unmatched") or [])[-10:]
        e.title = "Payments"
        e.add_field(name=f"Open quotes ({len(opn)})", value=("\n".join(f"{s.get('user')} · {_plan_short(s['plan'])} · {s['amount']:.5f} SOL" for s in opn) or "none"), inline=False)
        e.add_field(name="Last paid", value=("\n".join(f"{s.get('user')} · {_plan_short(s['plan'])} · {s.get('received', s['amount']):.5f} SOL · <t:{int(datetime.fromisoformat(s['paid_at']).timestamp())}:R>" for s in paid) or "none"), inline=False)
        e.add_field(name=f"Unmatched ({len(st.get('unmatched') or [])})",
                    value=("\n".join(f"{u['amount']:.5f} SOL · [tx](https://solscan.io/tx/{u['sig']}) · <t:{int(datetime.fromisoformat(u['at']).timestamp())}:R>" for u in reversed(unm)) or "none"), inline=False)
        e.add_field(name="Wallet", value=(f"`{SOL_WALLET}`" if SOL_WALLET else "NOT SET"), inline=False)
        e.set_footer(text="Sigma Trading - payments · unmatched = resolve with /admin grant, tx in note")
        return e
    if view == "unrecorded":
        from sigma.config import SUB_ROLE_ID
        role = guild.get_role(SUB_ROLE_ID) if SUB_ROLE_ID else None
        holders = [m for m in (role.members if role else []) if not m.bot]
        missing = [m for m in holders if str(m.id) not in subs]
        e.title = f"Pro role without a record ({len(missing)} of {len(holders)})"
        e.description = ("These members have the Pro role but no subscription record - the old bot tracked them. "
                         "Import each with `/admin grant member plan expires:<date from the old bot>` so expiry and reminders work here.\n\n"
                         + ("\n".join(f"{m.mention} · {m.display_name}" for m in missing[:40]) if missing else "none - everyone is on record"))
        if len(missing) > 40:
            e.set_footer(text=f"+{len(missing) - 40} more")
        return e
    # overview
    e.title = "Members - overview"
    total = guild.member_count or len(guild.members)
    bots = sum(1 for m in guild.members if m.bot)
    e.add_field(name="Server", value=f"{total - bots} humans", inline=True)
    e.add_field(name="Pro active", value=str(sm["active"]), inline=True)
    _lt = plansvc.all().get("lifetime", {})
    e.add_field(name="Lifetime seats", value=(f"{sm['lifetime_seats_left']} of {_lt.get('seats')} left" if sm["lifetime_seats_left"] is not None and _lt.get("seats") else "-"), inline=True)
    e.add_field(name="Revenue", value=f"this month ${rv['this_month']:,.0f} · last ${rv['last_month']:,.0f} · all time ${rv['all']:,.0f}", inline=False)
    by = " · ".join(f"{_plan_short(k)} {v}" for k, v in sorted(sm["by_plan"].items(), key=lambda x: -x[1]))
    e.add_field(name="By plan", value=by or "-", inline=False)
    exp = sm["expiring_7d"]
    e.add_field(name=f"Expiring in 7d ({len(exp)})", value=("\n".join(f"{n} - {d}d" for d, n in exp[:12]) if exp else "none"), inline=False)
    recent_lapsed = [l for l in lapsed if (now - datetime.fromisoformat(l["lapsed_at"])) <= timedelta(days=30)]
    e.add_field(name="Lapsed last 30d", value=(f"{len(recent_lapsed)} · " + ", ".join(l.get("name", "?") for l in recent_lapsed[-8:]) if recent_lapsed else "0"), inline=False)
    sess = st.get("sessions") or {}
    opn = [s for s in sess.values() if not s.get("paid") and session_open(s)]
    paid24 = [s for s in sess.values() if s.get("paid") and (now - datetime.fromisoformat(s["paid_at"])) <= timedelta(hours=24)]
    e.add_field(name="Payments", value=f"{len(opn)} open quote(s) · {len(paid24)} paid in 24h · {len(st.get('unmatched') or [])} unmatched", inline=False)
    e.set_footer(text="Sigma Trading - members · view: members / revenue / payments for detail")
    return e


def members_csv(subs: dict):
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_id", "name", "plan", "started", "expires", "paid_usd_total", "last_tx"])
    for uid, s in subs.items():
        hist = s.get("history", [])
        w.writerow([uid, s.get("name"), s.get("plan"), (s.get("started") or "")[:10], (s.get("expires") or "")[:10],
                    sum(float(h.get("usd") or h.get("price") or 0) for h in hist), (hist[-1].get("tx") or "") if hist else ""])
    out = io.BytesIO(buf.getvalue().encode("utf-8-sig")); out.seek(0)
    return out


class MembersCSVView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        b = discord.ui.Button(label="Download CSV", style=discord.ButtonStyle.secondary)
        b.callback = self.send
        self.add_item(b)

    async def send(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        await interaction.response.send_message(file=discord.File(members_csv(load_subs()), filename="sigma_members.csv"), ephemeral=True)


# ----------------------------------------------------------------------------- import from the old Payment Bot

import re as _re

_PLAN_DAYS = (("lifetime", 36500), ("year", 365), ("6month", 180), ("3month", 90), ("month", 30))


def parse_old_plan(label: str):
    """'Sigma Pro $500/6months' -> (days, usd). None if it doesn't look like a plan."""
    if not label:
        return None
    low = label.lower().replace(" ", "")
    m = _re.search(r"\$([\d,]+)", label)
    usd = float(m.group(1).replace(",", "")) if m else None
    for key, days in _PLAN_DAYS:
        if key in low:
            return days, usd
    return None


def plan_key_for_days(days: int) -> str | None:
    best = None
    for k, p in plansvc.all().items():
        if p["days"] == days:
            return k
        if best is None or abs(p["days"] - days) < abs(plansvc.all()[best]["days"] - days):
            best = k
    return best


def _parse_old_card(msg: discord.Message):
    """One Payment Bot 'Plan selected' embed -> (user_id, days, usd, when) or None."""
    for e in msg.embeds:
        if (e.title or "").strip().lower() != "plan selected":
            continue
        uid = plan = None
        for f in e.fields:
            if f.name.lower() == "user":
                m = _re.search(r"<@!?(\d+)>", f.value or "")
                uid = int(m.group(1)) if m else None
            elif f.name.lower() == "plan":
                plan = f.value
        pd = parse_old_plan(plan or "")
        if uid and pd:
            return uid, pd[0], pd[1], msg.created_at
    return None


async def scan_old_bot(guild: discord.Guild, log_channel: discord.TextChannel, old_bot_name: str = "Payment Bot") -> dict:
    """Build an import preview: {uid: {name, days, usd, start, expires, source}} for Pro-role holders without a record."""
    from sigma.config import SUB_ROLE_ID
    subs = load_subs()
    role = guild.get_role(SUB_ROLE_ID)
    holders = {m.id: m for m in (role.members if role else []) if not m.bot and str(m.id) not in subs}
    if not holders:
        return {"rows": {}, "skipped": [], "cards": 0, "grants": 0}
    # 1) every 'Plan selected' card, newest first per user
    selected = {}
    cards = 0
    async for msg in log_channel.history(limit=None, oldest_first=False):
        if not msg.author.bot or old_bot_name.lower() not in (msg.author.display_name or msg.author.name).lower():
            continue
        r = _parse_old_card(msg)
        if not r:
            continue
        cards += 1
        uid, days, usd, when = r
        selected.setdefault(uid, []).append((when, days, usd))
    # 2) audit log: when did the old bot give the Pro role (confirmed payment)
    grants = {}
    try:
        async for entry in guild.audit_logs(action=discord.AuditLogAction.member_role_update, limit=None):
            if not entry.user or not entry.user.bot or old_bot_name.lower() not in (entry.user.display_name or entry.user.name).lower():
                continue
            after_roles = getattr(entry.after, "roles", None) or []
            if role and any(r.id == role.id for r in after_roles):
                tid = entry.target.id if entry.target else None
                if tid and (tid not in grants or entry.created_at > grants[tid]):
                    grants[tid] = entry.created_at
    except discord.Forbidden:
        pass
    rows, skipped = {}, []
    for uid, m in holders.items():
        sel = sorted(selected.get(uid, []), reverse=True)
        g = grants.get(uid)
        if g and sel:
            before = [x for x in sel if x[0] <= g + timedelta(minutes=5)]
            when, days, usd = (before[0] if before else sel[0])
            start, source = g, "audit log + plan card"
        elif sel:
            when, days, usd = sel[0]
            start, source = when, "plan card only (assumed paid - holds the role)"
        elif g:
            start, days, usd, source = g, 30, None, "audit log only - plan unknown, assumed monthly"
        else:
            skipped.append((uid, m.display_name))
            continue
        exp = start + timedelta(days=days)
        rows[uid] = {"name": m.display_name, "days": days, "usd": usd, "start": start.isoformat(), "expires": exp.isoformat(),
                     "plan": plan_key_for_days(days), "source": source, "already_expired": exp < _now()}
    st = load_payments()
    st["import_preview"] = {str(k): v for k, v in rows.items()}
    save_payments(st)
    return {"rows": rows, "skipped": skipped, "cards": cards, "grants": len(grants)}


def import_preview_embed(res: dict) -> discord.Embed:
    rows = res["rows"]
    e = discord.Embed(title=f"Import preview - {len(rows)} member(s)", color=NAVY)
    e.description = (f"Read {res['cards']} plan cards and {res['grants']} role grants from the old bot. "
                     f"Run `/admin import action:Apply` to create these records (no revenue is counted).")
    lines = []
    for uid, r in sorted(rows.items(), key=lambda x: x[1]["expires"]):
        exp = datetime.fromisoformat(r["expires"])
        flag = " \u26a0 already past expiry - will NOT import, decide by hand" if r["already_expired"] else ""
        est = "" if r["source"].startswith("audit log +") else " (est.)"
        lines.append(f"<@{uid}> · {plansvc.all().get(r['plan'], {}).get('short', r['days'])} · expires {exp.strftime('%d %b %Y')}{est}{flag}")
    for i in range(0, len(lines), 15):
        e.add_field(name="\u200b" if i else "Members", value="\n".join(lines[i:i + 15])[:1024], inline=False)
    if res["skipped"]:
        e.add_field(name=f"No trace found ({len(res['skipped'])}) - import by hand with /admin grant expires:",
                    value="\n".join(f"<@{u}> · {n}" for u, n in res["skipped"][:20])[:1024], inline=False)
    e.set_footer(text="(est.) = plan card only or plan unknown - check the ones that matter")
    return e


async def apply_import(by: str) -> tuple[int, int]:
    st = load_payments()
    rows = st.get("import_preview") or {}
    done = skipped = 0
    for uid, r in rows.items():
        if r.get("already_expired") or not r.get("plan"):
            skipped += 1
            continue
        try:
            await activate(int(uid), r["plan"], by=by, note=f"imported from old bot ({r['source']})",
                           expires_at=datetime.fromisoformat(r["expires"]))
            done += 1
        except Exception as ex:
            print(f"[import] {uid} failed: {ex}", flush=True)
            skipped += 1
    st = load_payments()
    st["import_preview"] = {}
    save_payments(st)
    return done, skipped

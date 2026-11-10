"""sigma.payments - SOL payments to a fixed wallet, unique amount per session, verified on chain.

Member: dropdown in #join-via-payment -> private quote (wallet, exact SOL, 30 min) -> pays ->
the watcher sees the transfer on chain, matches the amount to the session -> role + DM + mod-log.
Admin: /admin panel kind:pay (post the panel), /admin desk (members dashboard, grant with a tx note for
anything that didn't match - the watcher alerts the ops channel on unmatched payments).

Everything that touches the subs record goes through sigma.services.subs - same path as the desk's Grant.
"""
import asyncio
import random
import time as _time
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import tasks

from sigma.logging_setup import log, print
from sigma.config import (GOLD, GREEN, GREY, GUILD_ID, MOD_LOG_CHANNEL_ID, NAVY, OPS_CHANNEL_ID, PAYMENT_CHANNEL_ID,
                          PAY_FEE_SLACK, PAY_LATE_GRACE_H, PAY_MATCH_TOL, PAY_MIN_SOL, PAY_POLL_SEC, PAY_SESSION_MIN, PAY_TOKENS, PAY_VALUE_FLOOR, SOL_RPC, SOL_WALLET,
                          SUB_REMINDER_DAYS)
from sigma.core import bot
from sigma.ops import HEARTBEAT, ops_alert
from sigma.http import http
from sigma.storage import load_payments, save_payments, load_subs, save_subs, PAY_LOCK, SUBS_LOCK
from sigma.errors import UserError
from sigma.services import subs as subsvc
from sigma.services import plans as plansvc
from sigma.members import _sub_grant_role, csv_safe

LAMPORTS = 1_000_000_000
LIFETIME_DAYS = plansvc.LIFETIME_DAYS
SEEN_CAP = 5000                # processed signatures kept (oldest evicted first - never random)
ACTIVATE_RETRIES = 10          # watcher passes a paid-but-not-activated session is retried before we give up
CHECK_COOLDOWN_S = 15          # 'I've paid - check now' per user
SIG_PAGES = 8                  # getSignaturesForAddress pages per address when catching up after downtime
RPC_ALERT_AFTER_S = 600        # continuous RPC failure before the ops channel hears about it
RPC_429_SKIP = 4               # passes skipped after a 429 (2 min at the 30s poll)


# ----------------------------------------------------------------------------- pure helpers

def _now():
    return datetime.now(timezone.utc)


def quote_amount(usd: float, sol_price: float, taken: set, token: str = "SOL") -> float:
    """Amount for `usd` in `token`, with a unique tail so two open quotes never collide.
    SOL: 5th-decimal tail. Stables: USD + unique cents (100.37), falling back to 3 decimals when cents run out."""
    tk = PAY_TOKENS[token]
    tol = tk["tol"]
    if tk["stable"]:
        base = round(usd, 2)
        if all(abs(base - t) > tol * 2 for t in taken):
            return base                                   # the round number, whenever it's free
        for k in range(1, 100):                           # someone else holds it right now: smallest free cents tail
            amt = round(base + k * 0.01, 2)
            if all(abs(amt - t) > tol * 2 for t in taken):
                return amt
        for k in range(1, 1000):
            amt = round(base + k * 0.001, 3)
            if all(abs(amt - t) > tol * 2 for t in taken):
                return amt
        raise UserError("Too many open quotes right now - try again in a minute.")
    if not sol_price or sol_price <= 0:
        raise UserError("Couldn't fetch the SOL price - try again in a minute.")
    base = round(usd / sol_price, 3)
    for _ in range(200):
        amt = round(base + random.randint(1, 999) * 0.00001, 5)
        if all(abs(amt - t) > tol * 2 for t in taken):
            return amt
    raise UserError("Too many open quotes right now - try again in a minute.")


def fmt_amt(amount: float, token: str = "SOL") -> str:
    return f"{amount:.5f} SOL" if token == "SOL" else f"{amount:.2f} {token}" if abs(amount * 100 - round(amount * 100)) < 1e-6 else f"{amount:.3f} {token}"


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


def received_token(tx: dict, wallet: str, mint: str) -> float | None:
    """Net amount of SPL `mint` that `wallet` (as token-account owner) gained in a jsonParsed transaction."""
    try:
        meta = tx["meta"]
        if meta.get("err"):
            return None
        def tot(key):
            return sum(float(b["uiTokenAmount"]["uiAmountString"] or 0) for b in (meta.get(key) or [])
                       if b.get("mint") == mint and b.get("owner") == wallet)
        post, pre = tot("postTokenBalances"), tot("preTokenBalances")
        if post == 0 and pre == 0 and not any(b.get("mint") == mint and b.get("owner") == wallet for b in (meta.get("postTokenBalances") or [])):
            return None
        return round(post - pre, 6)
    except Exception:
        return None


def received_all(tx: dict, wallet: str) -> list:
    """[(token, amount)] for every accepted token this tx moved into the wallet."""
    out = []
    a = received_sol(tx, wallet)
    if a is not None and a > 0:
        out.append(("SOL", a))
    for tk, cfg in PAY_TOKENS.items():
        if cfg["mint"]:
            b = received_token(tx, wallet, cfg["mint"])
            if b is not None and b > 0:
                out.append((tk, b))
    return out


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


def match_session(sessions: dict, amount: float, now=None, token: str = "SOL") -> tuple[str | None, str]:
    """(session id, how). how = 'exact' | 'fee' | ''. Only quotes in the same token are considered.
    exact: quoted amount within the token's tolerance. fee: no exact hit, but exactly ONE live quote is short by
    0 < quoted - received <= the token's slack (exchange withdrawal fee, SOL only). Two candidates = ambiguous = no match."""
    now = now or _now()
    tk = PAY_TOKENS.get(token, PAY_TOKENS["SOL"])
    best = None
    for sid, s, created in _live(sessions, now):
        if s.get("token", "SOL") != token:
            continue
        if abs(float(s["amount"]) - amount) <= tk["tol"]:
            if best is None or created > datetime.fromisoformat(sessions[best]["created"]):
                best = sid
    if best:
        return best, "exact"
    if tk["slack"] > 0:
        cands = [sid for sid, s, _ in _live(sessions, now)
                 if s.get("token", "SOL") == token and 0 < float(s["amount"]) - amount <= tk["slack"]]
        if len(cands) == 1:
            return cands[0], "fee"
    return None, ""


def match_by_value(sessions: dict, worth_usd: float, token: str, now=None, band: float = 0.06) -> str | None:
    """Last resort when the amount tail was lost (sender rounded it, exchange took a fee): exactly ONE live quote
    in this token whose USD price is within `band` of what landed. Two candidates = ambiguous = None."""
    now = now or _now()
    cands = [sid for sid, s, _ in _live(sessions, now)
             if s.get("token", "SOL") == token and abs(float(s["usd"]) - worth_usd) <= float(s["usd"]) * band]
    return cands[0] if len(cands) == 1 else None


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
    # ordered, oldest first, so eviction drops what the RPC stopped returning long ago - a set() here evicted at
    # random and let a months-old signature come back as "new"
    state["seen"] = list(dict.fromkeys(state.get("seen") or []))[-SEEN_CAP:]
    return state


# ----------------------------------------------------------------------------- chain + price

_PRICE_CACHE = {"px": None, "at": 0.0}
_PRICE_FAILS = 0
PRICE_CACHE_MAX_S = 300
PRICE_JUMP_MAX = 0.20          # a source that disagrees with the last-good price by more than this is lying or broken


async def _price_from(name: str, url: str, params: dict, pick) -> float | None:
    try:
        async with http() as s:
            async with s.get(url, params=params, timeout=10) as r:
                if r.status == 200:
                    v = float(pick(await r.json()))
                    return v if v > 0 else None
                print(f"[pay] price {name} http {r.status}", flush=True)
    except Exception as e:
        print(f"[pay] price {name} error: {e}", flush=True)
    return None


_PRICE_SOURCES = (
    ("binance", "https://api.binance.com/api/v3/ticker/price", {"symbol": "SOLUSDT"}, lambda j: j["price"]),
    ("coinbase", "https://api.coinbase.com/v2/prices/SOL-USD/spot", {}, lambda j: j["data"]["amount"]),
    ("coingecko", "https://api.coingecko.com/api/v3/simple/price", {"ids": "solana", "vs_currencies": "usd"}, lambda j: j["solana"]["usd"]),
)


async def sol_price() -> float | None:
    """Binance -> Coinbase -> CoinGecko, first sane answer wins. Sane = within PRICE_JUMP_MAX of the last good price
    (one exchange printing a bad tick must not mint a quote at half price). Last good price (<= 5 min old) is the
    final fallback; two total failures in a row ping ops."""
    global _PRICE_FAILS
    last, at = _PRICE_CACHE["px"], _PRICE_CACHE["at"]
    for name, url, params, pick in _PRICE_SOURCES:
        v = await _price_from(name, url, params, pick)
        if v is None:
            continue
        if last and abs(v - last) / last > PRICE_JUMP_MAX:
            print(f"[pay] price {name} {v:.2f} rejected - {abs(v - last) / last:.0%} from last good {last:.2f}", flush=True)
            await ops_alert(f"SOL price from {name} is ${v:,.2f}, last good ${last:,.2f} - ignored that source", key="price:jump")
            continue
        _PRICE_CACHE.update({"px": v, "at": _time.time()})
        _PRICE_FAILS = 0
        return v
    if last and _time.time() - at <= PRICE_CACHE_MAX_S:
        print(f"[pay] all price sources failed - using cached {last:.2f}", flush=True)
        return last
    _PRICE_FAILS += 1
    if _PRICE_FAILS >= 2:
        await ops_alert("SOL price: every source (Binance, Coinbase, CoinGecko) failed twice in a row - quotes are refused until one answers.", key="price")
    return None


class RpcRateLimited(RuntimeError):
    pass


async def _rpc(method: str, params: list):
    async with http() as s:
        async with s.post(SOL_RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20) as r:
            if r.status == 429:
                raise RpcRateLimited(f"rpc {method} http 429")
            if r.status != 200:
                raise RuntimeError(f"rpc {method} http {r.status}")
            j = await r.json()
            if "error" in j:
                raise RuntimeError(f"rpc {method}: {j['error']}")
            return j.get("result")


_TOKEN_ACCOUNTS: dict = {}       # mint -> [token account address]; refreshed every ~10 min
_TOKEN_ACCOUNTS_AT: float = 0.0


async def token_accounts() -> list:
    """Every token account the wallet owns for an accepted stablecoin mint. SPL transfers reference the
    token account, not the owner, so getSignaturesForAddress(wallet) alone never sees a USDC / USDT payment
    sent from an exchange (wallet apps that add an idempotent ATA-create do reference the owner - exchanges don't)."""
    global _TOKEN_ACCOUNTS_AT
    import time as _t
    if _t.time() - _TOKEN_ACCOUNTS_AT < 600 and _TOKEN_ACCOUNTS:
        return [a for lst in _TOKEN_ACCOUNTS.values() for a in lst]
    out = {}
    for tk, cfg in PAY_TOKENS.items():
        if not cfg["mint"]:
            continue
        try:
            res = await _rpc("getTokenAccountsByOwner", [SOL_WALLET, {"mint": cfg["mint"]}, {"encoding": "jsonParsed"}]) or {}
            out[cfg["mint"]] = [v["pubkey"] for v in (res.get("value") or [])]
        except Exception as e:
            print(f"[pay] token account lookup failed for {tk}: {e}", flush=True)
            if cfg["mint"] in _TOKEN_ACCOUNTS:
                out[cfg["mint"]] = _TOKEN_ACCOUNTS[cfg["mint"]]
    if out:
        _TOKEN_ACCOUNTS.clear(); _TOKEN_ACCOUNTS.update(out); _TOKEN_ACCOUNTS_AT = _t.time()
    return [a for lst in out.values() for a in lst]


BLOCK_TIMES: dict = {}         # signature -> blockTime (unix s) from the last getSignaturesForAddress sweep


async def _signatures_for(addr: str, seen, limit: int, cutoff: int) -> list:
    """Newest-first, paged with `before=` until a page contains something we already processed or something older
    than the grace window - so a payment that landed while the bot was down is still found, however many
    transactions came after it. The first page is always scanned in full (retries live there)."""
    out, before = [], None
    for _ in range(SIG_PAGES):
        opts = {"limit": limit}
        if before:
            opts["before"] = before
        res = await _rpc("getSignaturesForAddress", [addr, opts]) or []
        if not res:
            break
        reached = False
        for x in res:
            sig = x["signature"]
            bt = x.get("blockTime")
            if bt:
                BLOCK_TIMES[sig] = int(bt)
            if sig in seen:
                reached = True
                continue
            if bt and int(bt) < cutoff:
                reached = True               # still returned: the watcher marks it seen (or retries it) itself
            if not x.get("err"):
                out.append(sig)
        if reached or len(res) < limit:
            break
        before = res[-1]["signature"]
    return out


async def recent_signatures(seen=(), limit: int = 25) -> list:
    """Newest-first signatures touching the wallet OR any of its stablecoin token accounts (deduped, order kept).
    `seen`: signatures already processed - paging stops once a page reaches one. BLOCK_TIMES is refreshed."""
    addrs = [SOL_WALLET] + [a for a in await token_accounts() if a != SOL_WALLET]
    seen = set(seen or ())
    cutoff = int(_time.time()) - PAY_LATE_GRACE_H * 3600
    BLOCK_TIMES.clear()
    have, out = set(), []
    for addr in addrs:
        try:
            res = await _signatures_for(addr, seen, limit, cutoff)
        except Exception as e:
            if addr == SOL_WALLET:
                raise
            print(f"[pay] signatures for token account {addr[:8]} failed: {e}", flush=True)
            continue
        for sig in res:
            if sig not in have:
                have.add(sig); out.append(sig)
    return out


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
    """Grant (or extend) + role + DM + mod-log. Used by the chain watcher and by /admin desk (Grant / Extend, bulk grant, import)."""
    guild = bot.get_guild(GUILD_ID)
    member = None
    if guild:
        try:
            member = guild.get_member(uid) or await guild.fetch_member(uid)
        except Exception:
            member = None
    name = member.display_name if member else str(uid)
    async with SUBS_LOCK:
        subs = load_subs()
        rec = subsvc.grant(subs, uid, name, plan, by, note=note, tx=tx,
                           usd=(usd if usd is not None else (0.0 if expires_at else None)), expires_at=expires_at)
        save_subs(subs)
    if guild:
        ok = await _sub_grant_role(guild, uid)
    else:
        ok = False
        await ops_alert(f"Pro role NOT granted to <@{uid}> - the bot isn't in guild {GUILD_ID}. Add it by hand.", key=f"role:{uid}")
    p = plansvc.get(plan)
    exp = subsvc.expiry(rec)
    life = int(rec.get("days") or p["days"]) >= LIFETIME_DAYS
    if member:
        try:
            until = "lifetime" if life else exp.strftime("%d %b %Y")
            if ok:
                body = f"**{p['label']}** is live.\n**Access until:** {until}"
            else:
                # never tell someone their access is live when the role didn't land - say what's actually true
                body = (f"Payment received for **{p['label']}** - your access is being set up and an admin has been pinged. "
                        f"You'll have the Pro role shortly.\n**Access until:** {until}")
            e = discord.Embed(
                title="Sigma Pro - membership moved to the new bot" if expires_at else "Sigma Pro activated",
                description=(body + (f"\n\nTx: `{tx[:20]}...`" if tx else "")
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
    e.description = (f"Unlocks {role} - live setups, terminal, results board, journals.\n\n"
                     + "\n".join(lines)
                     + "\n\nPick a plan, pay in **SOL** (Solana network), send the exact amount. Access switches on by itself."
                     + ("\nPromo code? Tap **Promo code** on your quote." if plansvc.promos() else ""))
    try:
        if bot.user and bot.user.display_avatar:
            e.set_thumbnail(url=bot.user.display_avatar.url)
    except Exception:
        pass
    if _banner_path().exists():
        e.set_image(url="attachment://pay_banner.png")
    try:
        e.set_footer(text="Sigma Trading · we never DM you first, never ask for keys",
                     icon_url=(bot.user.display_avatar.url if bot.user else None))
    except Exception:
        e.set_footer(text="Sigma Trading · payments in SOL")
    return e


class SafeView(discord.ui.View):
    """Every dropdown / button error: one ephemeral line to the member, full trace to the log + ops channel."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        import traceback as _tb
        log.error(f"[ui] {type(item).__name__} failed: {error}\n" + "".join(_tb.format_exception(error)))
        try:
            await ops_alert(f"UI error in {type(item).__name__}: `{type(error).__name__}: {str(error)[:160]}`", key=f"ui:{type(item).__name__}:{type(error).__name__}")
        except Exception:
            pass
        msg = "Something broke on that click - it's logged and the admin has been pinged. Try again in a minute."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


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
        plan = self.values[0]
        p = plansvc.all(enabled_only=True).get(plan)
        if not p:
            await interaction.followup.send("That plan isn't open right now.", ephemeral=True)
            return
        eff, _ = plansvc.effective_price(p)
        from sigma.config import ACCEPTED_TOKENS
        if len(ACCEPTED_TOKENS) == 1:
            try:
                emb, view = await new_quote(interaction.user, plan, ACCEPTED_TOKENS[0])
                await interaction.followup.send(embed=emb, view=view, ephemeral=True)
            except UserError as e:
                await interaction.followup.send(e.message, ephemeral=True)
            return
        await interaction.followup.send(f"**{p['short']}** · ${eff:,.0f} · Solana", view=TokenPick(plan), ephemeral=True)


class TokenSelect(discord.ui.Select):
    def __init__(self, plan: str):
        self.plan = plan
        from sigma.config import ACCEPTED_TOKENS
        opts = [discord.SelectOption(label=tk, value=tk) for tk in ACCEPTED_TOKENS]  # plain on purpose - no icons, no blurbs
        super().__init__(placeholder="Pay with", options=opts, custom_id=f"sigma:pay:token:{plan}", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            emb, view = await new_quote(interaction.user, self.plan, self.values[0])
            await interaction.followup.send(embed=emb, view=view, ephemeral=True)
        except UserError as e:
            await interaction.followup.send(e.message, ephemeral=True)


class TokenPick(SafeView):
    def __init__(self, plan: str):
        super().__init__(timeout=None)
        self.add_item(TokenSelect(plan))


class PaymentPanel(SafeView):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PlanSelect())


class QuoteView(SafeView):
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
        now = _time.time()
        async with PAY_LOCK:
            st = load_payments()
            s = (st.get("sessions") or {}).get(self.sid)
            if not s or s.get("user_id") != interaction.user.id:
                await interaction.followup.send("That quote isn't yours or has expired - pick a plan again.", ephemeral=True)
                return
            wait = CHECK_COOLDOWN_S - (now - float(s.get("last_check") or 0))
            if wait > 0 and not s.get("paid"):
                # every tap is a full RPC sweep - one member hammering the button must not rate-limit the watcher for everyone
                await interaction.followup.send(f"Checking too fast - the chain is polled every {PAY_POLL_SEC}s anyway. Try again in {int(wait) + 1}s.", ephemeral=True)
                return
            s["last_check"] = now
            save_payments(st)
        found = await _watch_once()
        s = load_payments().get("sessions", {}).get(self.sid) or {}
        if s.get("paid"):
            if s.get("activated") is False:
                await interaction.followup.send("Payment found - your access is being set up and an admin has been pinged. Check your DMs.", ephemeral=True)
            else:
                await interaction.followup.send("Payment found - your access is live. Check your DMs.", ephemeral=True)
        elif s.get("underpaid"):
            await interaction.followup.send(f"Your transfer landed but is worth less than the plan (~${s.get('worth_usd', 0):,.0f} vs ${s['usd']:,.0f}) - "
                                            "an admin has been notified and will sort it out with you.", ephemeral=True)
        elif found is None:
            await interaction.followup.send("Couldn't reach Solana right now - this isn't about your transfer. "
                                            "The watcher keeps retrying every 30s and will match it by itself; try again in a minute.", ephemeral=True)
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
    """Re-price an open quote with a code. Same code twice = no-op (no new amount, no mod-log line); a different
    code replaces the first. The quote's clock is NOT reset - a code must not be a way to keep a stale SOL price alive."""
    async with PAY_LOCK:
        st = prune(load_payments())
        s = (st.get("sessions") or {}).get(sid)
        if not s or s.get("user_id") != user.id or s.get("paid"):
            raise UserError("That quote isn't yours or is already paid - pick a plan again.")
        if not session_open(s):
            raise UserError("This quote has expired - pick a plan again, then add the code.")
        promo = plansvc.promo_check(code, s["plan"], user_id=user.id)
        if s.get("promo") == promo["code"]:
            return quote_embed(s, sid), QuoteView(sid)
        p = plansvc.get(s["plan"])
        usd, why = plansvc.effective_price(p, promo)
        if usd <= 0:
            raise UserError("That code would make the plan free - ping an admin to grant it directly.")
        token = s.get("token", "SOL")
        price = s.get("sol_price") or (await sol_price() if token == "SOL" else 1.0)
        taken = {float(x["amount"]) for k, x in st["sessions"].items() if not x.get("paid") and k != sid and x.get("token", "SOL") == token}
        s.update({"usd": usd, "amount": quote_amount(usd, price, taken, token), "promo": promo["code"], "why": why})
        save_payments(st)
    await log_quote(s, sid, f"Promo applied - {promo['code']}")
    return quote_embed(s, sid), QuoteView(sid)


async def new_quote(user: discord.abc.User, plan: str, token: str = "SOL"):
    if not SOL_WALLET:
        raise UserError("Payments aren't configured yet - ping an admin.")
    if token not in PAY_TOKENS or not PAY_TOKENS[token].get("accept", True):
        raise UserError("We take payment in SOL only - pick a plan again.")
    p = plansvc.all(enabled_only=True).get(plan)
    if not p:
        raise UserError("That plan isn't open right now.")
    subs = load_subs()
    if p.get("seats") is not None and subsvc.seats_left(subs, plan) <= 0:
        raise UserError(f"{p['label']} is sold out.")
    price = await sol_price() if token == "SOL" else 1.0
    async with PAY_LOCK:
        st = prune(load_payments())
        sessions = st.setdefault("sessions", {})
        # one open quote per user per plan per token - reuse it instead of minting another amount
        for sid, s in sessions.items():
            if s.get("user_id") == user.id and s.get("plan") == plan and s.get("token", "SOL") == token and not s.get("paid") and session_open(s):
                return quote_embed(s, sid), QuoteView(sid)
        taken = {float(s["amount"]) for s in sessions.values() if not s.get("paid") and s.get("token", "SOL") == token}
        usd, why = plansvc.effective_price(p)
        amt = quote_amount(usd, price, taken, token)
        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"user_id": user.id, "user": str(user), "plan": plan, "token": token, "usd": usd, "list_usd": p["price"], "why": why,
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
    e.add_field(name="Price", value=f"${s['usd']:,.2f} ({fmt_amt(s['amount'], s.get('token', 'SOL'))})" + (f"\n{', '.join(s['why'])}" if s.get("why") else ""), inline=True)
    e.add_field(name="Session", value=f"`{sid}`", inline=False)
    cid = MOD_LOG_CHANNEL_ID or OPS_CHANNEL_ID
    ch = bot.get_channel(cid) if cid else None
    if ch is None:
        log.info(f"[modlog] {title} {s.get('user')} {s['plan']} {fmt_amt(s['amount'], s.get('token', 'SOL'))} {sid}")
        return
    try:
        await ch.send(embed=e)
    except Exception as ex:
        log.warning(f"[modlog] quote log failed: {ex}")


def quote_embed(s: dict, sid: str) -> discord.Embed:
    p = plansvc.get(s["plan"])
    exp = datetime.fromisoformat(s["created"]) + timedelta(minutes=PAY_SESSION_MIN)
    e = discord.Embed(title=f"{p['label']} - pay with {s.get('token', 'SOL')}", color=GOLD)
    price_line = f"plan `${s['usd']:,.0f}`" + (f" (list ${s.get('list_usd', p['price']):,.0f} - {', '.join(s['why'])})" if s.get("why") else "")
    token = s.get("token", "SOL")
    if token == "SOL":
        head = f"Send the **exact amount** of SOL to the wallet below."
        rate = f"SOL price `${s['sol_price']:,.2f}` · "
        tail = (f"The amount's last digits identify your payment - don't round it. "
                f"Sending from an exchange? Its withdrawal fee comes out of the amount - add it on top so the full amount lands. ")
    else:
        head = (f"Send the **exact amount** of **{token} on the Solana network (SPL)** to the wallet below. "
                f"\u26a0 Not Ethereum, not BSC, not Tron - {token} sent on another network is lost.")
        rate = ""
        cents = " - the cents identify your payment, don't round them. " if abs(s["amount"] - round(s["amount"])) > 1e-9 else ". "
        tail = (f"Send exactly `{fmt_amt(s['amount'], token)}`{cents}"
                f"From an exchange: pick network **Solana (SOL)** for the withdrawal; the fee is charged separately, so the full amount lands. ")
    e.description = (f"{head}\n\n"
                     f"**Wallet (Solana):**\n```{SOL_WALLET}```\n"
                     f"**Amount to send:**\n```{fmt_amt(s['amount'], token)}```\n"
                     f"{rate}{price_line} · quote expires <t:{int(exp.timestamp())}:R>\n\n"
                     f"{tail}"
                     f"Access switches on automatically within a minute of the transfer landing; you'll get a DM.")
    e.set_footer(text=f"Quote {sid}")
    return e


# ----------------------------------------------------------------------------- watcher

_RPC = {"fail_since": 0.0, "alerted": False, "skip": 0}     # watcher-level RPC health (see _rpc_failed / _rpc_ok)


async def _watch_once() -> int | None:
    """Pull new transfers, match them to quotes, activate. Returns the number of new incoming transfers seen,
    or None when the chain couldn't be read this pass (so callers can say 'RPC down', not 'nothing on chain').
    Serialised under PAY_LOCK: the 30s loop and the 'check now' button can never process the same signature twice."""
    if not SOL_WALLET:
        return 0
    if _RPC["skip"] > 0:
        _RPC["skip"] -= 1                 # 429 backoff: sit out a few passes instead of hammering the endpoint
        return None
    async with PAY_LOCK:
        return await _watch_once_inner()


async def _rpc_failed(e: Exception):
    now = _time.time()
    if isinstance(e, RpcRateLimited):
        _RPC["skip"] = RPC_429_SKIP
    if not _RPC["fail_since"]:
        _RPC["fail_since"] = now
    if now - _RPC["fail_since"] >= RPC_ALERT_AFTER_S and not _RPC["alerted"]:
        _RPC["alerted"] = True
        await ops_alert(f"Solana RPC has been failing for {int((now - _RPC['fail_since']) // 60)} min - payments are NOT being matched. "
                        f"Last error: `{str(e)[:160]}`. Check SIGMA_SOL_RPC / the provider.", key="pay:rpc")


def _rpc_ok():
    if _RPC["alerted"]:
        log.info("[pay] rpc recovered")
    _RPC.update({"fail_since": 0.0, "alerted": False})
    HEARTBEAT["payments"] = _time.time()   # only a pass that actually read the chain counts as alive


def _mark_seen(st: dict, seen: list, sig: str):
    if sig not in seen:
        seen.append(sig)
    st["seen"] = seen[-SEEN_CAP:]


async def _retry_activations(st: dict):
    """Sessions paid on chain whose activate() blew up (Discord hiccup, sold-out seat, file error). Bounded so a
    permanently broken one ends in a loud alert instead of a daily grant attempt forever."""
    for sid, s in list((st.get("sessions") or {}).items()):
        if not (s.get("paid") and s.get("activated") is False) or s.get("activate_gave_up"):
            continue
        tries = int(s.get("activate_tries") or 0)
        if tries >= ACTIVATE_RETRIES:
            s["activate_gave_up"] = True
            save_payments(st)
            await ops_alert(f"GIVING UP activating <@{s['user_id']}> ({s['plan']}) after {tries} tries - paid tx {s.get('tx')}. "
                            f"Grant by hand from /admin desk.", key=f"pay:act:giveup:{sid}")
            continue
        await _after_paid(st, s, sid, s.get("tx"))


async def _watch_once_inner() -> int | None:
    st = prune(load_payments())
    seen = list(st.get("seen") or [])
    seen_set = set(seen)
    await _retry_activations(st)
    try:
        sigs = await recent_signatures(seen_set)
    except Exception as e:
        print(f"[pay] rpc error: {e}", flush=True)
        await _rpc_failed(e)
        return None
    _rpc_ok()
    retry = st.get("retry") or {}
    cutoff = _time.time() - PAY_LATE_GRACE_H * 3600
    new, skipped = [], 0
    for sig in sigs:
        if sig in seen_set:
            continue
        bt = BLOCK_TIMES.get(sig)
        if bt and bt < cutoff and sig not in retry:
            _mark_seen(st, seen, sig); seen_set.add(sig); skipped += 1   # older than any quote it could match - never worth a fetch
            continue
        new.append(sig)
    if not new:
        if skipped:
            save_payments(st)
        return 0
    found = 0
    for sig in reversed(new):              # oldest first
        try:
            tx = await fetch_tx(sig)
        except Exception as e:
            print(f"[pay] tx fetch error {sig[:12]}: {e}", flush=True)
            continue
        if not tx:
            # public RPCs sometimes answer null for a fresh signature - try again next pass, give up after a while
            tries = st.setdefault("retry", {})
            tries[sig] = tries.get(sig, 0) + 1
            if tries[sig] >= 20:
                tries.pop(sig, None); _mark_seen(st, seen, sig)
                await ops_alert(f"payment watcher: transaction {sig} never loaded from the RPC after 20 tries - check it on solscan: https://solscan.io/tx/{sig}", key=f"pay:null:{sig[:12]}")
            save_payments(st)
            continue
        st.get("retry", {}).pop(sig, None)
        _mark_seen(st, seen, sig)
        for token, amt in received_all(tx, SOL_WALLET):
            if amt < PAY_TOKENS[token]["min"]:
                print(f"[pay] dust ignored {amt:.6f} {token} {sig[:12]}", flush=True)
                continue
            found += 1
            await _settle_transfer(st, sig, token, amt)
        save_payments(st)
    save_payments(st)
    return found


async def _settle_transfer(st: dict, sig: str, token: str, amt: float):
    """One incoming transfer -> match a quote, value-gate it, activate. Mutates st (caller saves)."""
    shown = fmt_amt(amt, token)
    accepted = PAY_TOKENS.get(token, {}).get("accept", True)
    sid, how = match_session(st.get("sessions", {}), amt, token=token) if accepted else (None, "")
    px_now = await sol_price() if token == "SOL" else None
    if sid is None and not accepted:
        # we only sell in SOL, but the money is here: if exactly one open quote is worth this much, honour it
        sid = match_by_value(st.get("sessions", {}), amt, "SOL")
        how = "value" if sid else ""
        if sid:
            await ops_alert(f"{shown} received (we only offer SOL) - matched by value to <@{st['sessions'][sid]['user_id']}> "
                            f"{st['sessions'][sid]['plan']} and activated. https://solscan.io/tx/{sig}", key=f"pay:stable:{sig[:12]}")
    if sid is None:
        # the unique tail is gone (rounded amount / exchange fee) - fall back to value, if it points at exactly one quote
        worth_guess = amt * px_now if (token == "SOL" and px_now) else (amt if token != "SOL" else None)
        if worth_guess is not None:
            sid = match_by_value(st.get("sessions", {}), worth_guess, token)
            how = "value" if sid else ""
    if sid is not None and accepted and how in ("fee", "value"):
        # a non-exact match is a judgement call - the admin should see every one, an exact tail match stays silent
        s0 = st["sessions"][sid]
        open_q = [f"<@{x['user_id']}> {x['plan']} {fmt_amt(float(x['amount']), x.get('token', 'SOL'))} (${x['usd']:,.0f})"
                  for _sid, x, _c in _live(st.get("sessions", {}), _now()) if _sid != sid]
        await ops_alert(f"{how.upper()} match: {shown} received -> <@{s0['user_id']}> {s0['plan']} quoted {fmt_amt(float(s0['amount']), token)} "
                        f"(${s0['usd']:,.0f}). https://solscan.io/tx/{sig}\nOther open quotes: {', '.join(open_q) or 'none'}",
                        key=f"pay:{how}:{sig[:12]}")
    if sid is None:
        st.setdefault("unmatched", []).append({"sig": sig, "amount": amt, "token": token, "at": _now().isoformat()})
        st["unmatched"] = st["unmatched"][-50:]
        save_payments(st)
        open_q = [f"<@{x['user_id']}> {x['plan']} ${x['usd']:,.0f}" for _sid, x, _c in _live(st.get("sessions", {}), _now())]
        await ops_alert(f"unmatched payment: {shown} - https://solscan.io/tx/{sig}\n"
                        f"Open quotes right now: {', '.join(open_q) or 'none'}\n"
                        f"Grant from /admin desk (member -> plan -> Grant, put the tx in the note).",
                        key=f"pay:unmatched:{sig[:12]}")
        return
    s = st["sessions"][sid]
    # value gate: a late, fee-short or value-matched payment must still be worth >= PAY_VALUE_FLOOR of the quoted USD *now*
    if token == "SOL":
        worth = amt * px_now if px_now else amt * float(s.get("sol_price") or 0)
    else:
        worth = amt
    if worth < float(s["usd"]) * PAY_VALUE_FLOOR:
        s.update({"underpaid": True, "tx": sig, "received": amt, "worth_usd": round(worth, 2)})
        st.setdefault("unmatched", []).append({"sig": sig, "amount": amt, "token": token, "at": _now().isoformat(), "session": sid, "worth_usd": round(worth, 2)})
        save_payments(st)
        await ops_alert(f"UNDERPAID: <@{s['user_id']}> sent {shown} (~${worth:,.0f}) for {s['plan']} quoted ${s['usd']:,.0f} - "
                        f"https://solscan.io/tx/{sig} - not activated; grant from /admin desk if you accept it", key=f"pay:under:{sig[:12]}")
        try:
            u = bot.get_user(int(s["user_id"])) or await bot.fetch_user(int(s["user_id"]))
            await u.send(f"We saw your transfer of {shown} (~${worth:,.0f}), but the {s['plan']} plan was quoted at ${s['usd']:,.0f}. "
                         "An admin has been notified and will sort it out with you - no need to send again.")
        except Exception:
            pass
        return
    # paid is recorded first so a crash here can never match the same transfer twice; activated flips only after
    # activate() returned, and the watcher retries anything left in between
    s.update({"paid": True, "tx": sig, "paid_at": _now().isoformat(), "received": amt, "match": how, "activated": False})
    save_payments(st)
    await _after_paid(st, s, sid, sig)


async def _after_paid(st: dict, s: dict, sid: str, sig: str):
    if s.get("promo") and not s.get("promo_done"):
        plansvc.promo_consume(s["promo"])
        pr = plansvc.promos().get(s["promo"])
        row = plansvc.record_referral(st, pr, s, sig)
        s["promo_done"] = True                  # consumed once even if activation below needs another pass
        save_payments(st)
        if row:
            await mod_log(f"**Referral** `{row['code']}` -> {row.get('owner_name') or row['owner_id']} earns ${row['share_usd']:,.2f} "
                          f"({row['share_pct']:g}% of ${row['usd']:,.0f}) from {s.get('user')}", color=GOLD)
    try:
        await activate(int(s["user_id"]), s["plan"], by="chain", tx=sig, usd=s["usd"])
        s["activated"] = True
        s.pop("activate_error", None)
        save_payments(st)
    except Exception as e:
        s["activate_tries"] = int(s.get("activate_tries") or 0) + 1
        s["activate_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        save_payments(st)
        print(f"[pay] activate failed {sid} (try {s['activate_tries']}): {e}", flush=True)
        await ops_alert(f"payment matched but activation failed for <@{s['user_id']}> ({s['plan']}): {e} - tx {sig}"
                        f" - retrying next pass ({s['activate_tries']}/{ACTIVATE_RETRIES})", key=f"pay:act:{sid}")


@tasks.loop(seconds=PAY_POLL_SEC)
async def payment_watch_loop():
    if not SOL_WALLET:
        HEARTBEAT["payments"] = _time.time()   # nothing to watch - the loop itself is still alive
    await _watch_once()                        # heartbeat otherwise comes from a pass that read the chain


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
    async with PAY_LOCK:
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
        # Discord caps an embed at 6000 chars in total - stop well under it and say how many rows didn't fit
        budget, used, chunk, cur, n, shown = 5500, len(e.title) + 120, [], [], 0, 0
        for _, line in rows:
            if used + len(line) + 1 > budget:
                break
            if n + len(line) + 1 > 1000:
                chunk.append("\n".join(cur)); cur, n = [], 0
            cur.append(line); n += len(line) + 1; used += len(line) + 1; shown += 1
        if cur:
            chunk.append("\n".join(cur))
        for i, c in enumerate(chunk):
            e.add_field(name=("Soonest expiry first" if i == 0 else "\u200b"), value=c, inline=False)
        if shown < len(rows):
            e.add_field(name="\u200b", value=f"**+{len(rows) - shown} more** - use Export CSV for the full list", inline=False)
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
        e.add_field(name=f"Open quotes ({len(opn)})", value=("\n".join(f"{s.get('user')} · {_plan_short(s['plan'])} · {fmt_amt(s['amount'], s.get('token', 'SOL'))}" for s in opn) or "none"), inline=False)
        e.add_field(name="Last paid", value=("\n".join(f"{s.get('user')} · {_plan_short(s['plan'])} · {fmt_amt(s.get('received', s['amount']), s.get('token', 'SOL'))} · <t:{int(datetime.fromisoformat(s['paid_at']).timestamp())}:R>" for s in paid) or "none"), inline=False)
        e.add_field(name=f"Unmatched ({len(st.get('unmatched') or [])})",
                    value=("\n".join(f"{fmt_amt(u['amount'], u.get('token', 'SOL'))} · [tx](https://solscan.io/tx/{u['sig']}) · <t:{int(datetime.fromisoformat(u['at']).timestamp())}:R>" for u in reversed(unm)) or "none"), inline=False)
        e.add_field(name="Wallet", value=(f"`{SOL_WALLET}`" if SOL_WALLET else "NOT SET"), inline=False)
        e.set_footer(text="Sigma Trading - payments · unmatched = resolve from /admin desk (member -> plan -> Grant, tx in note)")
        return e
    if view == "unrecorded":
        from sigma.config import SUB_ROLE_ID
        role = guild.get_role(SUB_ROLE_ID) if SUB_ROLE_ID else None
        holders = [m for m in (role.members if role else []) if not m.bot]
        missing = [m for m in holders if str(m.id) not in subs]
        e.title = f"Pro role without a record ({len(missing)} of {len(holders)})"
        e.description = ("These members have the Pro role but no subscription record - the old bot tracked them. "
                         "Import each from `/admin desk` (pick the member -> plan -> Grant, set the expiry from the old bot) so expiry and reminders work here.\n\n"
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
        w.writerow([csv_safe(c) for c in (uid, s.get("name"), s.get("plan"), (s.get("started") or "")[:10], (s.get("expires") or "")[:10],
                    sum(float(h.get("usd") or h.get("price") or 0) for h in hist), (hist[-1].get("tx") or "") if hist else "")])
    out = io.BytesIO(buf.getvalue().encode("utf-8-sig")); out.seek(0)
    return out


class MembersCSVView(SafeView):
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


def parse_csv_export(text: str) -> dict:
    """The old Payment Bot's subscriber CSV -> {rows, lapsed, skipped}. Columns used: Discord User ID, Username,
    Display Name, Status, Current Plan, Started, Expires, Total Paid (USD). Active -> import rows (preview),
    expired -> archive rows (revenue + churn history)."""
    import csv, io
    rows, lapsed, skipped = {}, [], []
    reader = csv.DictReader(io.StringIO(text))

    def when(v):
        v = (v or "").strip().replace(" UTC", "")
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(v, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return None
    for r in reader:
        uid = (r.get("Discord User ID") or "").strip()
        if not uid.isdigit():
            continue
        name = (r.get("Display Name") or "").strip() or (r.get("Username") or "").strip() or uid
        parsed = parse_old_plan(r.get("Current Plan") or "")
        start, exp = when(r.get("Started")), when(r.get("Expires"))
        try:
            usd = float((r.get("Total Paid (USD)") or "0").replace(",", "") or 0)
        except ValueError:
            usd = 0.0
        if not parsed or exp is None:
            skipped.append((uid, name))
            continue
        days, _plan_usd = parsed
        plan = plan_key_for_days(days)
        active = (r.get("Status") or "").strip().lower() == "active" and exp > _now()
        if active:
            rows[uid] = {"name": name, "days": days, "usd": usd, "start": (start or _now()).isoformat(), "expires": exp.isoformat(),
                         "plan": plan, "source": "csv export", "already_expired": False}
        else:
            lapsed.append({"uid": uid, "name": name, "plan": plan, "price": plansvc.all().get(plan, {}).get("price"), "days": days,
                           "started": (start or exp).isoformat(), "expires": exp.isoformat(), "reminded": [], "note": "old bot csv",
                           "history": [{"plan": plan, "price": plansvc.all().get(plan, {}).get("price"), "usd": usd,
                                        "at": (start or exp).isoformat(), "by": "old bot", "tx": None}],
                           "lapsed_at": exp.isoformat(), "why": "expired (old bot)"})
    st = load_payments()
    st["import_preview"] = dict(rows)
    st["import_lapsed"] = lapsed
    save_payments(st)
    return {"rows": rows, "lapsed": lapsed, "skipped": skipped, "cards": 0, "grants": 0, "source": "csv"}


def import_preview_embed(res: dict) -> discord.Embed:
    rows = res["rows"]
    e = discord.Embed(title=f"Import preview - {len(rows)} member(s)", color=NAVY)
    if res.get("source") == "csv":
        total = sum(float(r.get("usd") or 0) for r in rows.values()) + sum(float(l["history"][0]["usd"] or 0) for l in res.get("lapsed", []))
        e.description = (f"CSV export: **{len(rows)} active** members to import, **{len(res.get('lapsed', []))} expired** go to the archive "
                         f"(revenue history ${total:,.0f}, churn). Run `/admin import action:Apply` to create the records.")
    else:
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
        e.add_field(name=f"No trace found ({len(res['skipped'])}) - import by hand from /admin desk (Grant with an expiry)",
                    value="\n".join(f"<@{u}> · {n}" for u, n in res["skipped"][:20])[:1024], inline=False)
    e.set_footer(text="(est.) = plan card only or plan unknown - check the ones that matter")
    return e


async def apply_import(by: str) -> tuple[int, int, int]:
    """Create records from the last preview. Returns (imported, skipped, archived)."""
    st = load_payments()
    rows = st.get("import_preview") or {}
    pending_lapsed = st.get("import_lapsed") or []
    done = skipped = 0
    for uid, r in rows.items():
        if r.get("already_expired") or not r.get("plan"):
            skipped += 1
            continue
        try:
            await activate(int(uid), r["plan"], by=by, note=f"imported from old bot ({r['source']})",
                           usd=r.get("usd"), expires_at=datetime.fromisoformat(r["expires"]))
            done += 1
        except Exception as ex:
            print(f"[import] {uid} failed: {ex}", flush=True)
            skipped += 1
    archived = 0
    async with PAY_LOCK:
        st = load_payments()
        if pending_lapsed:
            have = {(l.get("uid"), l.get("expires")) for l in (st.get("lapsed") or [])}
            for l in pending_lapsed:
                if (l.get("uid"), l.get("expires")) in have:
                    continue
                st.setdefault("lapsed", []).append(l)
                archived += 1
            st["lapsed"] = st["lapsed"][-1000:]
        st["import_preview"] = {}
        st["import_lapsed"] = []
        save_payments(st)
    return done, skipped, archived

"""Payments: quote amounts, chain parsing, matching, subscription service, watcher end to end (RPC mocked)."""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from sigma.errors import UserError
from sigma.services import subs as subsvc

WALLET = "3WvX9mqBAkbzwBtx7bRWF2vEA4KLq7fsHJjw3HRC1xnd"


def _tx(received_sol: float, wallet=WALLET, err=None):
    """jsonParsed getTransaction shape: wallet is account index 1, sender index 0."""
    lam = int(round(received_sol * 1_000_000_000))
    return {"transaction": {"message": {"accountKeys": [{"pubkey": "SenderXYZ"}, {"pubkey": wallet}, {"pubkey": "11111111111111111111111111111111"}]}},
            "meta": {"err": err, "preBalances": [5_000_000_000, 1_000_000_000, 1], "postBalances": [5_000_000_000 - lam - 5000, 1_000_000_000 + lam, 1]}}


# ---------------------------------------------------------------- pure

def test_quote_amount_unique_and_close_to_price(bot_mod):
    pay = bot_mod
    taken = set()
    for _ in range(50):
        a = pay.quote_amount(100, 121.09, taken)
        assert abs(a - 100 / 121.09) < 0.011 and all(abs(a - t) > pay.PAY_MATCH_TOL * 2 - 1e-12 for t in taken)
        taken.add(a)
    with pytest.raises(UserError):
        pay.quote_amount(100, 0, set())


def test_received_sol_parses_balance_delta(bot_mod):
    assert bot_mod.received_sol(_tx(8.25832), WALLET) == pytest.approx(8.25832)
    assert bot_mod.received_sol(_tx(1.0, wallet="OtherWallet"), WALLET) is None
    assert bot_mod.received_sol(_tx(1.0, err={"InstructionError": [0, "x"]}), WALLET) is None


def test_match_session_tolerance_and_grace(bot_mod):
    now = datetime.now(timezone.utc)
    sess = {"a": {"amount": 8.25832, "created": (now - timedelta(minutes=5)).isoformat(), "paid": False},
            "b": {"amount": 8.25840, "created": (now - timedelta(minutes=5)).isoformat(), "paid": False},
            "old": {"amount": 1.11111, "created": (now - timedelta(hours=30)).isoformat(), "paid": False},
            "done": {"amount": 2.22222, "created": now.isoformat(), "paid": True}}
    assert bot_mod.match_session(sess, 8.25832, now) == ("a", "exact")
    assert bot_mod.match_session(sess, 8.25840, now) == ("b", "exact")
    assert bot_mod.match_session(sess, 8.25841, now) == (None, "")  # 0.00001 over b: not exact, not short -> no match
    assert bot_mod.match_session(sess, 8.25836, now) == ("b", "fee") # short of b only (a is below it): one candidate
    assert bot_mod.match_session(sess, 1.11111, now) == (None, "")  # past the 24h grace
    assert bot_mod.match_session(sess, 2.22222, now) == (None, "")  # already paid
    late = {"l": {"amount": 3.0, "created": (now - timedelta(hours=5)).isoformat(), "paid": False}}
    assert bot_mod.match_session(late, 3.0, now) == ("l", "exact")       # expired quote, inside grace


def test_prune_keeps_paid_and_recent(bot_mod):
    now = datetime.now(timezone.utc)
    st = {"sessions": {"new": {"created": now.isoformat(), "paid": False},
                       "stale": {"created": (now - timedelta(hours=48)).isoformat(), "paid": False},
                       "paidold": {"created": (now - timedelta(days=9)).isoformat(), "paid": True}},
          "seen": list(range(800))}
    bot_mod.prune(st, now)
    assert set(st["sessions"]) == {"new", "paidold"} and len(st["seen"]) == 500


# ---------------------------------------------------------------- subs service

def test_grant_extends_from_current_expiry_and_logs_history():
    subs = {}
    r1 = subsvc.grant(subs, 1, "A", "1month", by="admin")
    e1 = datetime.fromisoformat(r1["expires"])
    assert (e1 - datetime.now(timezone.utc)).days in (29, 30) and r1["reminded"] == []
    r2 = subsvc.grant(subs, 1, "A", "3months", by="chain", tx="sig1", usd=270)
    e2 = datetime.fromisoformat(r2["expires"])
    assert (e2 - e1).days == 90 and len(r2["history"]) == 2 and r2["history"][-1]["tx"] == "sig1"
    assert r2["started"] == r1["started"]


def test_lifetime_seats_cap():
    subs = {str(i): {"plan": "lifetime", "expires": "2099-01-01T00:00:00+00:00"} for i in range(25)}
    assert subsvc.seats_left(subs, "lifetime") == 0
    with pytest.raises(UserError, match="sold out"):
        subsvc.grant(subs, 999, "Z", "lifetime", by="admin")
    subsvc.grant(subs, "3", "C", "lifetime", by="admin")     # existing holder re-granted: fine
    assert subsvc.seats_left(subs, "1month") is None


def test_reminders_fire_once_per_threshold():
    now = datetime.now(timezone.utc)
    rec = {"expires": (now + timedelta(days=2, hours=12)).isoformat(), "reminded": []}
    assert subsvc.due_reminder(rec, (7, 3, 1), now) == 3          # under 3d -> the 3-day one fires
    subsvc.mark_reminded(rec, 3, (7, 3, 1))
    assert rec["reminded"] == [3, 7] and subsvc.due_reminder(rec, (7, 3, 1), now) is None   # 7 settled too, no second ping
    rec["expires"] = (now + timedelta(hours=10)).isoformat()
    assert subsvc.due_reminder(rec, (7, 3, 1), now) == 1
    old = {"expires": (now + timedelta(days=2)).isoformat(), "reminded": True}   # pre-upgrade record
    assert subsvc.due_reminder(old, (7, 3, 1), now) == 3
    assert subsvc.revoke({"5": {"plan": "1month"}}, 5)["plan"] == "1month"
    with pytest.raises(UserError):
        subsvc.revoke({}, 5)


# ---------------------------------------------------------------- watcher end to end

def test_watcher_matches_and_activates(bot_mod, monkeypatch):
    pay = bot_mod
    pay_mod = __import__("sigma.payments", fromlist=["x"])
    now = datetime.now(timezone.utc)
    state = {"sessions": {"s1": {"user_id": 42, "user": "msk", "plan": "1year", "usd": 1000, "sol_price": 121.09,
                                 "amount": 8.25832, "created": now.isoformat(), "paid": False}}, "seen": ["oldsig"]}
    monkeypatch.setattr(pay_mod, "SOL_WALLET", WALLET)
    monkeypatch.setattr(pay_mod, "load_payments", lambda: state)
    monkeypatch.setattr(pay_mod, "save_payments", lambda d: state.update(d))
    txs = {"sigA": _tx(8.25832), "sigB": _tx(0.5), "oldsig": _tx(9.9)}
    async def _sigs(limit=25): return ["sigB", "sigA", "oldsig"]
    async def _tx_fetch(sig): return txs[sig]
    acts, alerts = [], []
    async def _activate(uid, plan, by, tx=None, usd=None, note=""): acts.append((uid, plan, by, tx, usd))
    async def _alert(msg, key=None): alerts.append(msg)
    monkeypatch.setattr(pay_mod, "recent_signatures", _sigs)
    monkeypatch.setattr(pay_mod, "fetch_tx", _tx_fetch)
    monkeypatch.setattr(pay_mod, "activate", _activate)
    monkeypatch.setattr(pay_mod, "ops_alert", _alert)
    async def _px(): return 121.09
    monkeypatch.setattr(pay_mod, "sol_price", _px)
    found = asyncio.run(pay_mod._watch_once())
    assert found == 2
    assert acts == [(42, "1year", "chain", "sigA", 1000)]
    assert state["sessions"]["s1"]["paid"] and state["sessions"]["s1"]["tx"] == "sigA"
    assert len(state["unmatched"]) == 1 and state["unmatched"][0]["amount"] == pytest.approx(0.5) and "unmatched" in alerts[0]
    assert set(state["seen"]) >= {"sigA", "sigB", "oldsig"}
    # second pass: nothing new, nothing re-activated
    acts.clear()
    assert asyncio.run(pay_mod._watch_once()) == 0 and acts == []


def test_panel_and_quote_embed_render(bot_mod, monkeypatch):
    pay_mod = __import__("sigma.payments", fromlist=["x"])
    monkeypatch.setattr(pay_mod, "SOL_WALLET", WALLET)
    monkeypatch.setattr(pay_mod, "load_subs", lambda: {})
    e = pay_mod.panel_embed()
    assert "$1,000" in e.description and "25 of 25 seats" in e.description
    v = pay_mod.PaymentPanel()
    assert v.children[0].custom_id == "sigma:pay:plan" and len(v.children[0].options) == 5
    q = pay_mod.quote_embed({"plan": "6months", "usd": 500, "sol_price": 121.09, "amount": 4.12934,
                             "created": datetime.now(timezone.utc).isoformat()}, "abc")
    assert WALLET in q.description and "4.12934 SOL" in q.description and "$500" in q.description


def test_revenue_and_dashboard_views(bot_mod, monkeypatch):
    now = datetime.now(timezone.utc)
    this_m = now.strftime("%Y-%m")
    subs = {"1": {"name": "A", "plan": "1month", "started": now.isoformat(), "expires": (now + timedelta(days=20)).isoformat(),
                  "history": [{"plan": "1month", "usd": 100, "at": now.isoformat()}]},
            "2": {"name": "B", "plan": "lifetime", "started": "2026-08-01T00:00:00+00:00", "expires": "2099-01-01T00:00:00+00:00",
                  "history": [{"plan": "lifetime", "usd": 1999, "at": "2026-08-01T00:00:00+00:00"}]}}
    lapsed = [{"name": "C", "plan": "3months", "started": "2026-07-01T00:00:00+00:00", "lapsed_at": (now - timedelta(days=3)).isoformat(),
               "history": [{"plan": "3months", "usd": 270, "at": "2026-07-01T00:00:00+00:00"}]}]
    rv = subsvc.revenue(subs, lapsed, now)
    assert rv["all"] == 2369 and rv["this_month"] == 100 and rv["by_plan"]["lifetime"] == 1999 and rv["new_this_month"] == 1
    assert this_m in rv["by_month"]
    pay_mod = __import__("sigma.payments", fromlist=["x"])
    monkeypatch.setattr(pay_mod, "load_subs", lambda: subs)
    monkeypatch.setattr(pay_mod, "load_payments", lambda: {"lapsed": lapsed, "sessions": {}, "unmatched": []})
    import types
    guild = types.SimpleNamespace(member_count=120, members=[types.SimpleNamespace(bot=True)] * 3)
    ov = pay_mod.members_embed(guild, "overview")
    assert "117 humans" in ov.fields[0].value and "24 of 25" in ov.fields[2].value and "Lapsed last 30d" in [f.name for f in ov.fields]
    mem = pay_mod.members_embed(guild, "members")
    assert ("A · Monthly · 20d" in mem.fields[0].value or "A · Monthly · 19d" in mem.fields[0].value) and "B · Lifetime · lifetime" in mem.fields[0].value
    rev = pay_mod.members_embed(guild, "revenue")
    assert "$2,369" in rev.fields[2].value and "/ month" in rev.fields[-1].value
    csv_txt = pay_mod.members_csv(subs).read().decode("utf-8-sig")
    assert csv_txt.splitlines()[0].startswith("user_id,name,plan") and "1999" in csv_txt


# ---------------------------------------------------------------- plans + promos + referrals

@pytest.fixture
def plans_store(monkeypatch):
    from sigma.services import plans as plansvc
    state = {}
    monkeypatch.setattr(plansvc, "_load", lambda: state)
    monkeypatch.setattr(plansvc, "_save", lambda d: state.update(d))
    return plansvc


def test_plans_seed_edit_add_disable(plans_store):
    pl = plans_store
    assert list(pl.all()) == ["1month", "3months", "6months", "1year", "lifetime"]
    assert pl.get("6months")["price"] == 500 and pl.get("1year")["price"] == 1000 and pl.get("lifetime")["seats"] == 25
    p, ch = pl.edit("1month", price="120", name="Monthly Pro")
    assert p["price"] == 120 and "price $120" in ch and p["label"] == "Sigma Pro - Monthly Pro ($120)"
    p = pl.add("launch_week", "Launch Week", "70", "30", seats=10)
    assert p["key"] == "launch_week" and p["seats"] == 10 and list(pl.all())[-1] == "launch_week"
    with pytest.raises(UserError, match="already exists"):
        pl.add("launch_week", "x", "1", "1")
    pl.edit("launch_week", enabled=False)
    assert "launch_week" not in pl.all(enabled_only=True) and "launch_week" in pl.all()
    p = pl.add("founder2", "Founder II", "2500", "lifetime")
    assert p["days"] == pl.LIFETIME_DAYS and p["label"] == "Founder II ($2,500)"
    with pytest.raises(UserError):
        pl.edit("nope", price="1")
    pl.remove("founder2")
    assert "founder2" not in pl.all()


def test_plan_discount_and_describe(plans_store):
    pl = plans_store
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    p = pl.set_discount("1month", "20", "2026-10-15")
    eff, why = pl.effective_price(p, None, now)
    assert eff == 80 and why == ["20% off"]
    assert "~~$100~~ **$80** (20% off till 15 Oct) / 30 days" in pl.describe(p, None, now)
    eff, why = pl.effective_price(p, None, datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert eff == 100 and why == []                       # expired discount
    p = pl.set_discount("1month", "0")
    assert p["discount_pct"] == 0 and pl.describe(p) == "**Monthly** - $100 / 30 days"
    with pytest.raises(UserError):
        pl.set_discount("1month", "150")
    with pytest.raises(UserError, match="Date"):
        pl.set_discount("1month", "10", "someday")


def test_promo_rules_and_stacking(plans_store):
    pl = plans_store
    pl.set_discount("1year", "10")
    r = pl.promo_add("sigma20", pct="20", uses="2", plan="1year")
    assert r["code"] == "SIGMA20" and r["uses_left"] == 2
    with pytest.raises(UserError, match="doesn't apply"):
        pl.promo_check("SIGMA20", "1month")
    promo = pl.promo_check("sigma20", "1year")
    eff, why = pl.effective_price(pl.get("1year"), promo)
    assert eff == 720 and why == ["10% off", "code SIGMA20 -20%"]      # 1000 -> 900 -> 720
    pl.promo_consume("SIGMA20"); pl.promo_consume("SIGMA20")
    with pytest.raises(UserError, match="fully used"):
        pl.promo_check("SIGMA20", "1year")
    pl.promo_add("ten", usd="10", until="2020-01-01")
    with pytest.raises(UserError, match="expired"):
        pl.promo_check("TEN", "1month")
    with pytest.raises(UserError):
        pl.promo_add("both", pct="5", usd="5")
    with pytest.raises(UserError):
        pl.promo_add("empty")
    with pytest.raises(UserError, match="doesn't exist"):
        pl.promo_check("NOPE", "1month")


def test_referral_attribution_and_settle(plans_store):
    pl = plans_store
    r = pl.promo_add("sigma-owais", owner_id=7, owner_name="Owais", share_pct="25")    # pure referral, no discount
    assert r["pct"] is None and r["share_pct"] == 25
    eff, why = pl.effective_price(pl.get("1year"), pl.promo_check("SIGMA-OWAIS", "1year"))
    assert eff == 1000 and why == []
    st = {}
    row = pl.record_referral(st, r, {"user_id": 1, "user": "m1", "plan": "1year", "usd": 1000}, "sigA")
    pl.record_referral(st, r, {"user_id": 2, "user": "m2", "plan": "1month", "usd": 100}, "sigB")
    assert row["share_usd"] == 250 and len(st["referrals"]) == 2
    assert pl.record_referral(st, {"code": "X", "owner_id": None}, {}, "s") is None
    summ = pl.referral_summary(st)[7]
    assert summ["members"] == 2 and summ["usd"] == 1100 and summ["owed"] == 275 and summ["settled"] == 0
    assert pl.settle(st, 7, "tx123") == 275
    summ = pl.referral_summary(st, 7)[7]
    assert summ["owed"] == 0 and summ["settled"] == 275
    with pytest.raises(UserError):
        pl.settle(st, 7)


def test_quote_uses_effective_price_and_promo(bot_mod, plans_store, monkeypatch):
    pay_mod = __import__("sigma.payments", fromlist=["x"])
    pl = plans_store
    pl.set_discount("6months", "20")
    pl.promo_add("extra", usd="50")
    state = {"sessions": {}}
    monkeypatch.setattr(pay_mod, "SOL_WALLET", WALLET)
    monkeypatch.setattr(pay_mod, "load_payments", lambda: state)
    monkeypatch.setattr(pay_mod, "save_payments", lambda d: state.update(d))
    monkeypatch.setattr(pay_mod, "load_subs", lambda: {})
    async def _price(): return 100.0
    monkeypatch.setattr(pay_mod, "sol_price", _price)
    import types
    logged = []
    async def _lq(s, sid, title): logged.append(title)
    monkeypatch.setattr(pay_mod, "log_quote", _lq)
    user = types.SimpleNamespace(id=5, __str__=lambda s: "u5")
    emb, view = asyncio.run(pay_mod.new_quote(user, "6months"))
    assert logged == ["Plan selected"]
    sid, s = next(iter(state["sessions"].items()))
    assert s["usd"] == 400 and s["list_usd"] == 500 and 3.99 < s["amount"] < 4.02 and "list $500" in emb.description
    emb2, _ = asyncio.run(pay_mod.apply_promo(user, sid, "extra"))
    s = state["sessions"][sid]
    assert s["usd"] == 350 and s["promo"] == "EXTRA" and 3.49 < s["amount"] < 3.52 and "code EXTRA" in emb2.description
    assert logged[-1] == "Promo applied - EXTRA"
    with pytest.raises(UserError):
        asyncio.run(pay_mod.apply_promo(user, sid, "nope"))
    assert len(pay_mod.PaymentPanel().children[0].options) == 5
    assert "~~$500~~ **$400**" in pay_mod.panel_embed().description



# ---------------------------------------------------------------- penetration scenarios

def _env(pay_mod, monkeypatch, sessions, sol_price=121.09):
    state = {"sessions": sessions, "seen": []}
    acts, alerts = [], []
    monkeypatch.setattr(pay_mod, "SOL_WALLET", WALLET)
    monkeypatch.setattr(pay_mod, "load_payments", lambda: state)
    monkeypatch.setattr(pay_mod, "save_payments", lambda d: state.update(d))
    async def _activate(uid, plan, by, tx=None, usd=None, note=""): acts.append((uid, plan, tx))
    async def _alert(msg, key=None): alerts.append(msg)
    async def _px(): return sol_price
    monkeypatch.setattr(pay_mod, "activate", _activate)
    monkeypatch.setattr(pay_mod, "ops_alert", _alert)
    monkeypatch.setattr(pay_mod, "sol_price", _px)
    return state, acts, alerts


def _sess(uid, amount, usd, minutes_ago=5, plan="1year", sol_price=121.09):
    return {"user_id": uid, "user": f"u{uid}", "plan": plan, "usd": usd, "sol_price": sol_price, "amount": amount,
            "created": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(), "paid": False}


def test_pentest_replay_same_tx_twice_activates_once(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, _ = _env(pay, monkeypatch, {"s": _sess(1, 8.25832, 1000)})
    async def _sigs(limit=25): return ["sigA"]
    async def _tx(sig): return _tx_(sig)
    def _tx_(sig): return globals()["_tx"](8.25832)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once()); asyncio.run(pay._watch_once()); asyncio.run(pay._watch_once())
    assert acts == [(1, "1year", "sigA")]


def test_pentest_concurrent_watchers_serialise(bot_mod, monkeypatch):
    """Loop tick + 'check now' at the same instant must not double-grant."""
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, _ = _env(pay, monkeypatch, {"s": _sess(1, 8.25832, 1000)})
    async def _sigs(limit=25):
        await asyncio.sleep(0.01)
        return ["sigA"]
    async def _tx(sig):
        await asyncio.sleep(0.01)
        return globals()["_tx"](8.25832)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    async def both():
        await asyncio.gather(pay._watch_once(), pay._watch_once(), pay._watch_once())
    asyncio.run(both())
    assert len(acts) == 1


def test_pentest_late_payment_after_sol_dump_is_not_activated(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    # quoted at $121, pays 20h later when SOL is $95 -> 8.258 SOL ~ $784 for a $1000 plan
    state, acts, alerts = _env(pay, monkeypatch, {"s": _sess(1, 8.25832, 1000, minutes_ago=20 * 60)}, sol_price=95.0)
    async def _sigs(limit=25): return ["sigA"]
    async def _tx(sig): return globals()["_tx"](8.25832)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once())
    assert acts == [] and state["sessions"]["s"]["underpaid"] and "UNDERPAID" in alerts[0]
    # the same session can't be matched again by a second transfer either
    assert pay.match_session(state["sessions"], 8.25832) == (None, "")


def test_pentest_exchange_fee_short_matches_only_when_unambiguous(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    sessions = {"a": _sess(1, 8.25832, 1000), "b": _sess(2, 0.82611, 100, plan="1month")}
    state, acts, alerts = _env(pay, monkeypatch, sessions)
    async def _sigs(limit=25): return ["sigA"]
    async def _tx(sig): return globals()["_tx"](8.25832 - 0.001)      # Binance-style: fee taken out of the amount
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once())
    assert acts == [(1, "1year", "sigA")] and state["sessions"]["a"]["match"] == "fee"
    # two live quotes within the slack window of the received amount -> ambiguous -> unmatched, alert
    sessions = {"a": _sess(1, 8.25832, 1000), "c": _sess(3, 8.26500, 1000)}
    state, acts, alerts = _env(pay, monkeypatch, sessions)
    async def _sigs2(limit=25): return ["sigB"]
    async def _tx2(sig): return globals()["_tx"](8.2570)
    monkeypatch.setattr(pay, "recent_signatures", _sigs2)
    monkeypatch.setattr(pay, "fetch_tx", _tx2)
    asyncio.run(pay._watch_once())
    assert acts == [] and len(state["unmatched"]) == 1 and "unmatched" in alerts[0]
    # short by more than the slack -> never a fee case
    assert pay.match_session({"a": _sess(1, 8.25832, 1000)}, 8.20) == (None, "")


def test_pentest_dust_is_ignored_silently(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, alerts = _env(pay, monkeypatch, {"s": _sess(1, 8.25832, 1000)})
    async def _sigs(limit=25): return ["d1", "d2", "d3"]
    async def _tx(sig): return globals()["_tx"](0.000001)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    assert asyncio.run(pay._watch_once()) == 0
    assert alerts == [] and acts == [] and set(state["seen"]) == {"d1", "d2", "d3"}


def test_pentest_quote_cannot_be_hijacked_or_duplicated(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state = {"sessions": {}}
    monkeypatch.setattr(pay, "SOL_WALLET", WALLET)
    monkeypatch.setattr(pay, "load_payments", lambda: state)
    monkeypatch.setattr(pay, "save_payments", lambda d: state.update(d))
    monkeypatch.setattr(pay, "load_subs", lambda: {})
    async def _px(): return 100.0
    monkeypatch.setattr(pay, "sol_price", _px)
    async def _lq(s, sid, title): pass
    monkeypatch.setattr(pay, "log_quote", _lq)
    import types
    u1 = types.SimpleNamespace(id=1, __str__=lambda s: "u1"); u2 = types.SimpleNamespace(id=2, __str__=lambda s: "u2")
    asyncio.run(pay.new_quote(u1, "1month")); asyncio.run(pay.new_quote(u1, "1month")); asyncio.run(pay.new_quote(u1, "1month"))
    assert len(state["sessions"]) == 1                      # spamming the dropdown mints ONE quote per user per plan
    sid = next(iter(state["sessions"]))
    with pytest.raises(UserError, match="isn't yours"):     # another user can't attach a promo to it
        asyncio.run(pay.apply_promo(u2, sid, "ANY"))
    asyncio.run(pay.new_quote(u2, "1month"))
    a1, a2 = (float(s["amount"]) for s in state["sessions"].values())
    assert abs(a1 - a2) > pay.PAY_MATCH_TOL * 2             # two users, same plan, distinct amounts


def test_pentest_amount_space_cannot_be_exhausted_cheaply(bot_mod):
    pay = __import__("sigma.payments", fromlist=["x"])
    taken = set()
    for _ in range(300):                                     # 300 open $100 quotes at the same price still find room
        taken.add(pay.quote_amount(100, 121.09, taken))
    assert len(taken) == 300


def test_pentest_bad_rpc_payloads_do_not_crash_or_activate(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, alerts = _env(pay, monkeypatch, {"s": _sess(1, 8.25832, 1000)})
    payloads = [None, {}, {"transaction": {}}, {"transaction": {"message": {"accountKeys": []}}, "meta": {}},
                {"transaction": {"message": {"accountKeys": [{"pubkey": WALLET}]}}, "meta": {"err": None, "preBalances": [5], "postBalances": []}},
                {"transaction": {"message": {"accountKeys": [{"pubkey": WALLET}]}}, "meta": {"err": {"x": 1}, "preBalances": [0], "postBalances": [10**12]}}]
    async def _sigs(limit=25): return [f"p{i}" for i in range(len(payloads))]
    async def _tx(sig): return payloads[int(sig[1:])]
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    assert asyncio.run(pay._watch_once()) == 0 and acts == [] and alerts == []
    async def _boom(limit=25): raise RuntimeError("rpc down")
    monkeypatch.setattr(pay, "recent_signatures", _boom)
    assert asyncio.run(pay._watch_once()) == 0


def test_pentest_admin_gates_on_sensitive_views(bot_mod):
    tree = bot_mod.bot.tree
    admin = next(c for c in tree.get_commands() if c.name == "admin")
    assert admin.default_permissions.administrator
    import inspect
    for name in ("members", "plans", "promo", "grant", "revoke", "panel"):
        src = inspect.getsource(next(s for s in admin.commands if s.name == name).callback)
        assert "administrator" in src or "_admin_only" in src, name
    pay = __import__("sigma.payments", fromlist=["x"])
    assert "administrator" in inspect.getsource(pay.MembersCSVView.send)

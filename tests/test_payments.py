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
          "seen": list(range(6000))}
    bot_mod.prune(st, now)
    assert set(st["sessions"]) == {"new", "paidold"} and len(st["seen"]) == 5000
    assert st["seen"][0] == 1000 and st["seen"][-1] == 5999          # oldest evicted first, order kept


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
    async def _boom(seen=(), limit=25): raise RuntimeError("rpc down")
    monkeypatch.setattr(pay, "recent_signatures", _boom)
    assert asyncio.run(pay._watch_once()) is None          # None = chain unreadable, not "nothing on chain"


def test_pentest_admin_gates_on_sensitive_views(bot_mod):
    tree = bot_mod.bot.tree
    admin = next(c for c in tree.get_commands() if c.name == "admin")
    assert admin.default_permissions.administrator
    import inspect
    for name in ("desk", "panel", "import"):
        src = inspect.getsource(next(s for s in admin.commands if s.name == name).callback)
        assert "administrator" in src or "_admin_only" in src, name
    pay = __import__("sigma.payments", fromlist=["x"])
    assert "administrator" in inspect.getsource(pay.MembersCSVView.send)
    desk = __import__("sigma.admin_desk", fromlist=["x"])
    assert "_is_admin" in inspect.getsource(desk.open_admin_desk) and "_is_admin" in inspect.getsource(desk.AdminDesk._apply)
    assert "_is_admin" in inspect.getsource(desk.AdminDesk.on_csv)


def test_grant_with_explicit_expiry_for_import():
    subs = {}
    r = subsvc.grant(subs, 9, "Old", "1month", by="admin", expires_at="2099-11-15", usd=0.0)
    assert r["expires"].startswith("2099-11-15") and r["history"][-1]["usd"] == 0.0
    with pytest.raises(UserError, match="past"):
        subsvc.grant({}, 9, "Old", "1month", by="admin", expires_at="2020-01-01")
    with pytest.raises(UserError, match="Date"):
        subsvc.grant({}, 9, "Old", "1month", by="admin", expires_at="someday")


def test_panel_has_visuals(bot_mod, plans_store, monkeypatch):
    pay_mod = __import__("sigma.payments", fromlist=["x"])
    monkeypatch.setattr(pay_mod, "load_subs", lambda: {})
    e = pay_mod.panel_embed()
    assert e.image.url == "attachment://pay_banner.png" and pay_mod._banner_path().exists()
    assert "<@&" in e.description and "Monthly" in e.description
    opts = pay_mod.PaymentPanel().children[0].options
    assert all(o.emoji is not None for o in opts)


def test_old_bot_card_parsing_and_plan_mapping(bot_mod, plans_store):
    pay = __import__("sigma.payments", fromlist=["x"])
    assert pay.parse_old_plan("Sigma Pro $500/6months") == (180, 500)
    assert pay.parse_old_plan("Sigma Pro $100/Monthly") == (30, 100)
    assert pay.parse_old_plan("Sigma Pro $1000/yearly") == (365, 1000)
    assert pay.parse_old_plan("Sigma Pro $270/3months") == (90, 270)
    assert pay.parse_old_plan("hello") is None
    assert pay.plan_key_for_days(180) == "6months" and pay.plan_key_for_days(365) == "1year" and pay.plan_key_for_days(31) == "1month"
    import types
    now = datetime.now(timezone.utc)
    f = [types.SimpleNamespace(name="User", value="<@42>"), types.SimpleNamespace(name="Plan", value="Sigma Pro $270/3months"),
         types.SimpleNamespace(name="Session", value="x")]
    msg = types.SimpleNamespace(embeds=[types.SimpleNamespace(title="Plan selected", fields=f)], created_at=now)
    assert pay._parse_old_card(msg) == (42, 90, 270, now)
    msg2 = types.SimpleNamespace(embeds=[types.SimpleNamespace(title="Something else", fields=f)], created_at=now)
    assert pay._parse_old_card(msg2) is None


def test_import_apply_uses_preview_and_skips_expired(bot_mod, plans_store, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    now = datetime.now(timezone.utc)
    state = {"import_preview": {
        "1": {"name": "A", "plan": "3months", "expires": (now + timedelta(days=40)).isoformat(), "source": "audit log + plan card", "already_expired": False},
        "2": {"name": "B", "plan": "1month", "expires": (now - timedelta(days=2)).isoformat(), "source": "plan card only", "already_expired": True},
    }}
    monkeypatch.setattr(pay, "load_payments", lambda: state)
    monkeypatch.setattr(pay, "save_payments", lambda d: state.update(d))
    calls = []
    async def _act(uid, plan, by, tx=None, usd=None, note="", expires_at=None): calls.append((uid, plan, expires_at is not None))
    monkeypatch.setattr(pay, "activate", _act)
    done, skipped, archived = asyncio.run(pay.apply_import("admin"))
    assert (done, skipped, archived) == (1, 1, 0) and calls == [(1, "3months", True)] and state["import_preview"] == {}


CSV_SAMPLE = """Discord User ID,Username,Display Name,In Server,Status,Current Plan,Started,Expires,Days Left,How Granted,Granted By,Total Paid (USD),Paid Purchases,Total Grants,First Seen
962384339688108032,mika867372,NOTmichele bro,yes,active,Sigma Pro $500/6months,2026-09-30 10:02 UTC,2999-03-29 10:02 UTC,169,crypto payment,,600.00,2,2,2026-09-03 09:03 UTC
918145305826959380,baap5467,Cryptokabaap,yes,active,Sigma Pro $100/Monthly,2026-10-08 00:49 UTC,2999-11-07 00:49 UTC,27,manual (/addsub),pyouki,0.00,0,1,2026-10-08 00:49 UTC
1308358731679137843,thhb_0_09201,Thhb,yes,expired,Scient Pro - $250/3 Months,2026-05-29 15:47 UTC,2026-08-27 15:47 UTC,0,crypto payment,,350.00,2,2,2026-04-13 12:55 UTC
249880856993202187,scient7793,Scient,yes,expired,Scient Pro - $100/Monthly,2026-03-24 13:56 UTC,2026-04-23 13:56 UTC,0,crypto payment,,1.99,1,1,2026-03-24 10:30 UTC
notanid,x,,yes,active,Sigma Pro $100/Monthly,2026-10-08 00:49 UTC,2999-11-07 00:49 UTC,27,manual,,0,0,1,
"""


def test_csv_export_import_active_and_archive(bot_mod, plans_store, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state = {}
    monkeypatch.setattr(pay, "load_payments", lambda: state)
    monkeypatch.setattr(pay, "save_payments", lambda d: state.update(d))
    res = pay.parse_csv_export(CSV_SAMPLE)
    assert set(res["rows"]) == {"962384339688108032", "918145305826959380"}
    r = res["rows"]["962384339688108032"]
    assert r["plan"] == "6months" and r["usd"] == 600.0 and r["expires"].startswith("2999-03-29") and not r["already_expired"]
    assert [l["uid"] for l in res["lapsed"]] == ["1308358731679137843", "249880856993202187"]
    assert res["lapsed"][0]["plan"] == "3months" and res["lapsed"][0]["history"][0]["usd"] == 350.0 and res["lapsed"][0]["why"].startswith("expired")
    assert res["skipped"] == []
    e = pay.import_preview_embed(res)
    assert "2 active" in e.description and "2 expired" in e.description and "$952" in e.description
    calls = []
    async def _act(uid, plan, by, tx=None, usd=None, note="", expires_at=None): calls.append((uid, plan, usd))
    monkeypatch.setattr(pay, "activate", _act)
    done, skipped, archived = asyncio.run(pay.apply_import("admin"))
    assert (done, skipped, archived) == (2, 0, 2) and calls[0] == (962384339688108032, "6months", 600.0)
    assert len(state["lapsed"]) == 2 and state["import_lapsed"] == [] and state["import_preview"] == {}
    # re-applying the same archive rows doesn't duplicate them
    pay.parse_csv_export(CSV_SAMPLE)
    _d, _s, archived2 = asyncio.run(pay.apply_import("admin"))
    assert archived2 == 0 and len(state["lapsed"]) == 2


# ---------------------------------------------------------------- USDC / USDT on Solana

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"


def _spl_tx(amount: float, mint=USDC, owner=WALLET, pre=250.0, err=None):
    """jsonParsed shape for an SPL transfer: the wallet owns a token account for `mint`; its balance rises by `amount`."""
    def bal(v):
        return {"accountIndex": 2, "mint": mint, "owner": owner,
                "uiTokenAmount": {"uiAmount": v, "uiAmountString": f"{v:.6f}", "decimals": 6, "amount": str(int(round(v * 1e6)))}}
    return {"transaction": {"message": {"accountKeys": [{"pubkey": "SenderXYZ"}, {"pubkey": "SenderTokenAcct"}, {"pubkey": "OurTokenAcct"},
                                                        {"pubkey": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}]}},
            "meta": {"err": err, "preBalances": [5_000_000_000, 2_039_280, 2_039_280, 1], "postBalances": [4_999_995_000, 2_039_280, 2_039_280, 1],
                     "preTokenBalances": [bal(pre), {"accountIndex": 1, "mint": mint, "owner": "SenderXYZ", "uiTokenAmount": {"uiAmount": 900, "uiAmountString": "900", "decimals": 6, "amount": "900000000"}}],
                     "postTokenBalances": [bal(pre + amount), {"accountIndex": 1, "mint": mint, "owner": "SenderXYZ", "uiTokenAmount": {"uiAmount": 900 - amount, "uiAmountString": f"{900 - amount:.6f}", "decimals": 6, "amount": str(int(round((900 - amount) * 1e6)))}}]}}


def test_spl_parsing(bot_mod):
    pay = __import__("sigma.payments", fromlist=["x"])
    assert pay.received_token(_spl_tx(100.0), WALLET, USDC) == pytest.approx(100.0)
    assert pay.received_token(_spl_tx(100.0), WALLET, USDT) is None                 # wrong mint
    assert pay.received_token(_spl_tx(100.0, owner="Someone"), WALLET, USDC) is None  # not our token account
    assert pay.received_token(_spl_tx(100.0, err={"x": 1}), WALLET, USDC) is None
    assert pay.received_all(_spl_tx(100.0), WALLET) == [("USDC", 100.0)]            # SOL delta is 0 -> not listed
    assert pay.received_all(_spl_tx(50.5, mint=USDT), WALLET) == [("USDT", 50.5)]
    assert pay.received_all(_tx(1.25), WALLET) == [("SOL", 1.25)]


def test_stable_amounts_round_first_then_cents(bot_mod):
    pay = __import__("sigma.payments", fromlist=["x"])
    taken = set()
    a = pay.quote_amount(100, 1.0, taken, "USDC"); taken.add(a)
    assert a == 100.0
    b = pay.quote_amount(100, 1.0, taken, "USDC"); taken.add(b)
    assert b == 100.01                                           # second concurrent quote: smallest cents tail
    c = pay.quote_amount(100, 1.0, set(), "USDT")
    assert c == 100.0                                            # tokens are separate namespaces (caller passes per-token taken)
    assert pay.quote_amount(270, 1.0, set(), "USDT") == 270.0
    assert pay.fmt_amt(100.0, "USDC") == "100.00 USDC" and pay.fmt_amt(100.01, "USDT") == "100.01 USDT" and pay.fmt_amt(0.82611, "SOL") == "0.82611 SOL"


def test_match_is_per_token_and_stables_allow_exchange_fee_slack(bot_mod):
    pay = __import__("sigma.payments", fromlist=["x"])
    now = datetime.now(timezone.utc)
    sess = {"u": {"amount": 100.0, "token": "USDC", "created": now.isoformat(), "paid": False},
            "t": {"amount": 100.0, "token": "USDT", "created": now.isoformat(), "paid": False},
            "s": {"amount": 100.0, "token": "SOL", "created": now.isoformat(), "paid": False}}
    assert pay.match_session(sess, 100.0, now, token="USDC") == ("u", "exact")
    assert pay.match_session(sess, 100.0, now, token="USDT") == ("t", "exact")
    assert pay.match_session(sess, 100.0, now, token="SOL") == ("s", "exact")
    assert pay.match_session(sess, 99.0, now, token="USDC") == ("u", "fee")       # exchange withdrawal fee taken out of the amount
    assert pay.match_session(sess, 97.0, now, token="USDC") == (None, "")         # short by more than the slack -> not a fee case
    assert pay.match_session({"s": sess["s"]}, 99.99, now, token="SOL") == ("s", "fee")
    legacy = {"old": {"amount": 1.5, "created": now.isoformat(), "paid": False}}  # pre-upgrade session has no token key
    assert pay.match_session(legacy, 1.5, now, token="SOL") == ("old", "exact")
    assert pay.match_session(legacy, 1.5, now, token="USDC") == (None, "")


def test_usdc_payment_end_to_end(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    sessions = {"u": {**_sess(7, 100.0, 100, plan="1month"), "token": "USDC", "sol_price": 1.0},
                "s": _sess(8, 0.82611, 100, plan="1month")}
    state, acts, alerts = _env(pay, monkeypatch, sessions)
    txs = {"sigU": _spl_tx(100.0), "sigS": _tx(0.82611), "sigT": _spl_tx(100.0, mint=USDT)}
    async def _sigs(limit=25): return ["sigT", "sigS", "sigU"]
    async def _tx_fetch(sig): return txs[sig]
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx_fetch)
    found = asyncio.run(pay._watch_once())
    assert found == 3
    # SOL is the only offered token. The USDC transfer (oldest, processed first) is worth exactly one open SOL quote
    # -> activated by value with an alert; the SOL transfer then finds that quote already paid -> unmatched; USDT -> unmatched.
    assert acts == [(8, "1month", "sigU")]
    assert state["sessions"]["s"]["paid"] and state["sessions"]["s"]["match"] == "value"
    assert "we only offer SOL" in alerts[0] and [u["token"] for u in state["unmatched"]] == ["SOL", "USDT"]


def test_quote_flow_with_token(bot_mod, plans_store, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state = {"sessions": {}}
    monkeypatch.setattr(pay, "SOL_WALLET", WALLET)
    monkeypatch.setattr(pay, "load_payments", lambda: state)
    monkeypatch.setattr(pay, "save_payments", lambda d: state.update(d))
    monkeypatch.setattr(pay, "load_subs", lambda: {})
    async def _px(): raise AssertionError("stables must not fetch the SOL price")
    monkeypatch.setattr(pay, "sol_price", _px)
    async def _lq(s, sid, title): pass
    monkeypatch.setattr(pay, "log_quote", _lq)
    import types
    u = types.SimpleNamespace(id=5, __str__=lambda s: "u5")
    with pytest.raises(UserError, match="SOL only"):
        asyncio.run(pay.new_quote(u, "3months", "USDT"))         # stables are no longer offered
    with pytest.raises(UserError):
        asyncio.run(pay.new_quote(u, "3months", "DOGE"))
    assert state["sessions"] == {}
    tp = pay.TokenPick("3months")
    assert [o.value for o in tp.children[0].options] == ["SOL"] and tp.children[0].custom_id == "sigma:pay:token:3months"


def test_token_picker_is_plain(bot_mod):
    """Plain labels only - a non-emoji character in a SelectOption makes Discord reject the whole message."""
    pay = __import__("sigma.payments", fromlist=["x"])
    opts = pay.TokenPick("1month").children[0].options
    assert [o.label for o in opts] == ["SOL"]
    assert all(o.emoji is None and not o.description for o in opts)


def test_views_surface_errors(bot_mod):
    pay = __import__("sigma.payments", fromlist=["x"])
    for cls in (pay.PaymentPanel, pay.QuoteView, pay.TokenPick, pay.MembersCSVView):
        assert issubclass(cls, pay.SafeView)


def test_rounded_amount_matches_by_value_only_when_one_quote_fits(bot_mod, monkeypatch):
    """Sender rounded the quoted 0.82611 SOL to 0.83 -> the unique tail is gone -> match on USD value, one candidate only."""
    pay = __import__("sigma.payments", fromlist=["x"])
    sessions = {"b": _sess(2, 0.82611, 100, plan="1month")}
    state, acts, alerts = _env(pay, monkeypatch, sessions)
    async def _sigs(limit=25): return ["sigR"]
    async def _tx(sig): return globals()["_tx"](0.83)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once())
    assert acts == [(2, "1month", "sigR")] and state["sessions"]["b"]["match"] == "value"
    # two $100 quotes open -> ambiguous -> unmatched, and the alert names both so the admin can resolve it
    sessions = {"b": _sess(2, 0.82611, 100, plan="1month"), "c": _sess(3, 0.82744, 100, plan="1month")}
    state, acts, alerts = _env(pay, monkeypatch, sessions)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once())
    assert acts == [] and len(state["unmatched"]) == 1 and "<@2>" in alerts[0] and "<@3>" in alerts[0]


def test_null_tx_from_rpc_is_retried_not_dropped(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    sessions = {"b": _sess(2, 0.82611, 100, plan="1month")}
    state, acts, alerts = _env(pay, monkeypatch, sessions)
    calls = {"n": 0}
    async def _sigs(limit=25): return ["sigN"]
    async def _tx(sig):
        calls["n"] += 1
        return None if calls["n"] < 3 else globals()["_tx"](0.82611)     # RPC serves null twice, then the real tx
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once()); asyncio.run(pay._watch_once())
    assert acts == [] and "sigN" not in state.get("seen", []) and state["retry"]["sigN"] == 2
    asyncio.run(pay._watch_once())
    assert acts == [(2, "1month", "sigN")] and "sigN" in state["seen"] and "sigN" not in state["retry"]


# ---------------------------------------------------------------- audit fixes: watcher durability

def test_seen_is_ordered_and_no_signature_is_processed_twice(bot_mod, monkeypatch):
    """600+ signatures through a 25-newest window with a small seen cap: every tx fetched exactly once, eviction oldest-first."""
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, alerts = _env(pay, monkeypatch, {})
    monkeypatch.setattr(pay, "SEEN_CAP", 100)
    chain = []                                              # newest first
    fetched = []
    async def _sigs(seen=(), limit=25): return chain[:25]
    async def _tx(sig):
        fetched.append(sig)
        return globals()["_tx"](0.000001)                   # dust: no alerts, no activation, just bookkeeping
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    n = 0
    for _pass in range(32):
        for _ in range(20):
            chain.insert(0, f"sig{n:04d}"); n += 1
        asyncio.run(pay._watch_once())
    assert n == 640 and len(fetched) == 640 and len(set(fetched)) == 640
    assert state["seen"] == [f"sig{i:04d}" for i in range(540, 640)]      # newest 100, in order
    assert acts == [] and alerts == []


def test_old_signatures_are_skipped_without_a_fetch_unless_in_retry(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, alerts = _env(pay, monkeypatch, {"s": _sess(1, 8.25832, 1000)})
    import time
    pay.BLOCK_TIMES.clear()
    pay.BLOCK_TIMES.update({"ancient": int(time.time()) - 3 * 86400, "fresh": int(time.time()) - 60, "retried": int(time.time()) - 3 * 86400})
    state["retry"] = {"retried": 2}
    fetched = []
    async def _sigs(seen=(), limit=25): return ["fresh", "ancient", "retried"]
    async def _tx(sig):
        fetched.append(sig)
        return globals()["_tx"](8.25832 if sig == "fresh" else 0.1)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once())
    pay.BLOCK_TIMES.clear()
    assert sorted(fetched) == ["fresh", "retried"] and "ancient" in state["seen"]
    assert acts == [(1, "1year", "fresh")]


def test_recent_signatures_pages_until_a_seen_signature(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    import time
    now = int(time.time())
    # 70 txs newest first; the bot last saw 'sig40' (29 newer ones landed while it was down)
    chain = [{"signature": f"sig{i:02d}", "blockTime": now - i * 60, "err": None} for i in range(70)]
    calls = []
    async def _rpc(method, params):
        assert method == "getSignaturesForAddress"
        opts = params[1]; calls.append(opts.get("before"))
        start = 0 if not opts.get("before") else next(i for i, x in enumerate(chain) if x["signature"] == opts["before"]) + 1
        return chain[start:start + opts["limit"]]
    async def _accts(): return []
    monkeypatch.setattr(pay, "SOL_WALLET", WALLET)
    monkeypatch.setattr(pay, "_rpc", _rpc)
    monkeypatch.setattr(pay, "token_accounts", _accts)
    out = asyncio.run(pay.recent_signatures({"sig40", "sig41"}))
    # page 2 reached sig40 -> no page 3; the page itself is scanned in full (unseen-but-older = retry material, the watcher's blockTime gate drops the rest)
    assert out == [f"sig{i:02d}" for i in range(40)] + [f"sig{i}" for i in range(42, 50)] and calls == [None, "sig24"]
    assert pay.BLOCK_TIMES["sig00"] == now
    # nothing seen at all (fresh install): capped at SIG_PAGES pages, old ones cut by the grace window
    calls.clear()
    chain2 = [{"signature": f"x{i:03d}", "blockTime": now - i * 3600 * 2, "err": None} for i in range(400)]  # 2h apart
    async def _rpc2(method, params):
        opts = params[1]; calls.append(opts.get("before"))
        start = 0 if not opts.get("before") else next(i for i, x in enumerate(chain2) if x["signature"] == opts["before"]) + 1
        return chain2[start:start + opts["limit"]]
    monkeypatch.setattr(pay, "_rpc", _rpc2)
    out = asyncio.run(pay.recent_signatures(set()))
    assert len(calls) == 1 and len(out) == 25                 # first page already crosses the 24h cutoff -> no page 2
    pay.BLOCK_TIMES.clear()


def test_activation_failure_is_retried_then_gives_up(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, alerts = _env(pay, monkeypatch, {"s": _sess(1, 8.25832, 1000)})
    boom = {"n": 2}
    async def _activate(uid, plan, by, tx=None, usd=None, note=""):
        if boom["n"] > 0:
            boom["n"] -= 1; raise RuntimeError("discord 503")
        acts.append((uid, plan, tx))
    monkeypatch.setattr(pay, "activate", _activate)
    async def _sigs(seen=(), limit=25): return ["sigA"]
    async def _tx(sig): return globals()["_tx"](8.25832)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once())
    s = state["sessions"]["s"]
    assert s["paid"] and s["activated"] is False and s["activate_tries"] == 1 and "activation failed" in alerts[-1] and acts == []
    asyncio.run(pay._watch_once())                          # retried at the top of the next pass, fails again
    assert s["activate_tries"] == 2 and acts == []
    asyncio.run(pay._watch_once())
    assert acts == [(1, "1year", "sigA")] and s["activated"] is True and "activate_error" not in s
    asyncio.run(pay._watch_once())
    assert len(acts) == 1                                     # activated sessions are never touched again
    # permanent failure: bounded, ends in a give-up alert, no further attempts
    state2, acts2, alerts2 = _env(pay, monkeypatch, {"p": {**_sess(2, 1.0, 100, plan="1month"), "paid": True, "activated": False, "tx": "sigP"}})
    async def _never(uid, plan, by, tx=None, usd=None, note=""): raise RuntimeError("sold out")
    monkeypatch.setattr(pay, "activate", _never)
    async def _none(seen=(), limit=25): return []
    monkeypatch.setattr(pay, "recent_signatures", _none)
    for _ in range(pay.ACTIVATE_RETRIES + 3):
        asyncio.run(pay._watch_once())
    p = state2["sessions"]["p"]
    assert p["activate_tries"] == pay.ACTIVATE_RETRIES and p["activate_gave_up"] and any("GIVING UP" in a for a in alerts2)


def test_grant_is_idempotent_on_tx_and_keeps_lifetime():
    subs = {}
    r1 = subsvc.grant(subs, 1, "A", "1month", by="chain", tx="sigX", usd=100)
    r2 = subsvc.grant(subs, 1, "A", "1month", by="chain", tx="sigX", usd=100)      # activation retry after a crash
    assert r2 is r1 and len(r2["history"]) == 1 and r2["expires"] == r1["expires"]
    subsvc.grant(subs, 1, "A", "1month", by="chain", tx="sigY", usd=100)
    assert len(subs["1"]["history"]) == 2
    # lifetime member buys a month (or admin extends): payment recorded, plan / days untouched
    subsvc.grant(subs, 2, "L", "lifetime", by="admin")
    r = subsvc.grant(subs, 2, "L", "1month", by="chain", tx="sigZ", usd=100)
    assert r["plan"] == "lifetime" and r["days"] == subsvc.plansvc.LIFETIME_DAYS and len(r["history"]) == 2 and r["history"][-1]["tx"] == "sigZ"
    # naive expiry on a hand-edited record is read as UTC instead of crashing comparisons
    subs["3"] = {"plan": "1month", "expires": "2099-01-01T00:00:00", "history": []}
    assert subsvc.days_left(subs["3"]) > 0 and subs["3"]["expires"].endswith("+00:00")


def test_fee_and_value_matches_alert_exact_stays_silent(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, alerts = _env(pay, monkeypatch, {"a": _sess(1, 8.25832, 1000), "b": _sess(2, 0.82611, 100, plan="1month")})
    async def _sigs(seen=(), limit=25): return ["sigFee"]
    async def _tx(sig): return globals()["_tx"](8.25832 - 0.001)
    monkeypatch.setattr(pay, "recent_signatures", _sigs)
    monkeypatch.setattr(pay, "fetch_tx", _tx)
    asyncio.run(pay._watch_once())
    assert acts == [(1, "1year", "sigFee")] and len(alerts) == 1
    a = alerts[0]
    assert a.startswith("FEE match") and "8.25732 SOL" in a and "<@1>" in a and "8.25832 SOL" in a and "$1,000" in a and "<@2>" in a
    state, acts, alerts = _env(pay, monkeypatch, {"b": _sess(2, 0.82611, 100, plan="1month")})
    async def _tx2(sig): return globals()["_tx"](0.83)
    monkeypatch.setattr(pay, "fetch_tx", _tx2)
    asyncio.run(pay._watch_once())
    assert alerts and alerts[0].startswith("VALUE match") and "<@2>" in alerts[0]
    state, acts, alerts = _env(pay, monkeypatch, {"a": _sess(1, 8.25832, 1000)})
    async def _tx3(sig): return globals()["_tx"](8.25832)
    monkeypatch.setattr(pay, "fetch_tx", _tx3)
    asyncio.run(pay._watch_once())
    assert acts and alerts == []                              # exact: silent


class _FakeResp:
    def __init__(self, status, body): self.status, self._b = status, body
    async def json(self): return self._b
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _FakeHTTP:
    """Maps a host substring -> (status, json) or an Exception; counts calls."""
    def __init__(self, routes): self.routes, self.calls = routes, []
    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        for k, v in self.routes.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return _FakeResp(*v)
        raise RuntimeError("no route")
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def test_sol_price_fallback_chain_cache_and_sanity(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    alerts = []
    async def _alert(msg, key=None): alerts.append(key)
    monkeypatch.setattr(pay, "ops_alert", _alert)
    pay._PRICE_CACHE.update({"px": None, "at": 0.0}); pay._PRICE_FAILS = 0
    # binance down, coinbase answers
    h = _FakeHTTP({"binance": (503, {}), "coinbase": (200, {"data": {"amount": "120.50"}}), "coingecko": (200, {"solana": {"usd": 119.0}})})
    monkeypatch.setattr(pay, "http", lambda: h)
    assert asyncio.run(pay.sol_price()) == 120.5 and [u for u in h.calls if "coingecko" in u] == []
    # binance back but printing a 40% jump -> rejected, coinbase wins
    h = _FakeHTTP({"binance": (200, {"price": "70.0"}), "coinbase": (200, {"data": {"amount": "121.00"}})})
    monkeypatch.setattr(pay, "http", lambda: h)
    assert asyncio.run(pay.sol_price()) == 121.0 and "price:jump" in alerts
    # everything down: cached last-good (fresh) is served, no alert on the first total failure
    alerts.clear()
    h = _FakeHTTP({"binance": RuntimeError("dns"), "coinbase": (500, {}), "coingecko": RuntimeError("timeout")})
    monkeypatch.setattr(pay, "http", lambda: h)
    assert asyncio.run(pay.sol_price()) == 121.0 and alerts == []
    # cache too old -> None; second consecutive total failure -> ops alert
    pay._PRICE_CACHE["at"] -= 10 * 60
    assert asyncio.run(pay.sol_price()) is None and alerts == []
    assert asyncio.run(pay.sol_price()) is None and alerts == ["price"]
    pay._PRICE_CACHE.update({"px": None, "at": 0.0}); pay._PRICE_FAILS = 0


def test_rpc_outage_alerts_after_ten_minutes_and_429_backs_off(bot_mod, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, alerts = _env(pay, monkeypatch, {})
    pay._RPC.update({"fail_since": 0.0, "alerted": False, "skip": 0})
    pay.HEARTBEAT.pop("payments", None)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(pay._time, "time", lambda: clock["t"])
    async def _down(seen=(), limit=25): raise RuntimeError("rpc down")
    monkeypatch.setattr(pay, "recent_signatures", _down)
    assert asyncio.run(pay._watch_once()) is None and alerts == [] and "payments" not in pay.HEARTBEAT
    clock["t"] += 9 * 60
    asyncio.run(pay._watch_once())
    assert alerts == []
    clock["t"] += 2 * 60
    asyncio.run(pay._watch_once()); asyncio.run(pay._watch_once())
    assert len(alerts) == 1 and "failing for 11 min" in alerts[0]         # once, not every pass
    async def _ok(seen=(), limit=25): return []
    monkeypatch.setattr(pay, "recent_signatures", _ok)
    assert asyncio.run(pay._watch_once()) == 0 and pay.HEARTBEAT["payments"] == clock["t"] and pay._RPC["fail_since"] == 0
    # 429: the next RPC_429_SKIP passes are skipped entirely (no RPC call)
    calls = {"n": 0}
    async def _limited(seen=(), limit=25):
        calls["n"] += 1; raise pay.RpcRateLimited("rpc http 429")
    monkeypatch.setattr(pay, "recent_signatures", _limited)
    asyncio.run(pay._watch_once())
    for _ in range(pay.RPC_429_SKIP):
        assert asyncio.run(pay._watch_once()) is None
    assert calls["n"] == 1
    asyncio.run(pay._watch_once())
    assert calls["n"] == 2
    pay._RPC.update({"fail_since": 0.0, "alerted": False, "skip": 0})


class _Follow:
    def __init__(self): self.msgs = []
    async def send(self, msg=None, **kw): self.msgs.append(msg)


class _Resp:
    async def defer(self, **kw): pass
    def is_done(self): return True


def test_check_now_button_cooldown_and_rpc_down_wording(bot_mod, monkeypatch):
    import types
    pay = __import__("sigma.payments", fromlist=["x"])
    state, acts, alerts = _env(pay, monkeypatch, {"q": _sess(5, 1.0, 100, plan="1month")})
    clock = {"t": 2_000_000.0}
    monkeypatch.setattr(pay._time, "time", lambda: clock["t"])
    pay._RPC.update({"fail_since": 0.0, "alerted": False, "skip": 0})
    sweeps = {"n": 0}
    async def _down(seen=(), limit=25):
        sweeps["n"] += 1; raise RuntimeError("rpc down")
    monkeypatch.setattr(pay, "recent_signatures", _down)
    def _it(uid):
        return types.SimpleNamespace(user=types.SimpleNamespace(id=uid), response=_Resp(), followup=_Follow())
    v = pay.QuoteView("q")
    it = _it(5); asyncio.run(v.check(it))
    assert "Couldn't reach Solana" in it.followup.msgs[0] and sweeps["n"] == 1
    it = _it(5); asyncio.run(v.check(it))
    assert "too fast" in it.followup.msgs[0] and sweeps["n"] == 1            # 15s cooldown: no second sweep
    it = _it(6); asyncio.run(v.check(it))
    assert "isn't yours" in it.followup.msgs[0]
    clock["t"] += 16
    async def _empty(seen=(), limit=25): return []
    monkeypatch.setattr(pay, "recent_signatures", _empty)
    it = _it(5); asyncio.run(v.check(it))
    assert it.followup.msgs[0].startswith("Nothing on chain yet")
    pay._RPC.update({"fail_since": 0.0, "alerted": False, "skip": 0})


def test_promo_owner_cannot_self_refer_and_apply_is_once_per_code(bot_mod, plans_store, monkeypatch):
    pay = __import__("sigma.payments", fromlist=["x"])
    pl = plans_store
    pl.promo_add("sigma-owais", pct="10", owner_id=7, owner_name="Owais", share_pct="25")
    pl.promo_add("ten", usd="10")
    with pytest.raises(UserError, match="own referral code"):
        pl.promo_check("SIGMA-OWAIS", "1year", user_id=7)
    assert pl.promo_check("SIGMA-OWAIS", "1year", user_id=8)["code"] == "SIGMA-OWAIS"
    state = {"sessions": {}}
    monkeypatch.setattr(pay, "SOL_WALLET", WALLET)
    monkeypatch.setattr(pay, "load_payments", lambda: state)
    monkeypatch.setattr(pay, "save_payments", lambda d: state.update(d))
    monkeypatch.setattr(pay, "load_subs", lambda: {})
    async def _px(): return 100.0
    monkeypatch.setattr(pay, "sol_price", _px)
    logged = []
    async def _lq(s, sid, title): logged.append(title)
    monkeypatch.setattr(pay, "log_quote", _lq)
    import types
    owner = types.SimpleNamespace(id=7, __str__=lambda s: "owais")
    asyncio.run(pay.new_quote(owner, "1year"))
    sid = next(iter(state["sessions"]))
    created = state["sessions"][sid]["created"]
    with pytest.raises(UserError, match="own referral"):
        asyncio.run(pay.apply_promo(owner, sid, "sigma-owais"))
    asyncio.run(pay.apply_promo(owner, sid, "ten"))
    s = state["sessions"][sid]
    assert s["usd"] == 990 and s["promo"] == "TEN" and s["created"] == created          # clock not reset
    amt = s["amount"]
    asyncio.run(pay.apply_promo(owner, sid, "TEN"))                                     # same code again: no-op
    assert s["amount"] == amt and logged.count("Promo applied - TEN") == 1
    pl.promo_add("twenty", usd="20")
    asyncio.run(pay.apply_promo(owner, sid, "twenty"))                                  # a different code replaces it
    assert s["usd"] == 980 and s["promo"] == "TWENTY" and logged[-1] == "Promo applied - TWENTY"


def test_csv_cells_are_formula_safe(bot_mod):
    from sigma.members import csv_safe
    assert csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)" and csv_safe("+1") == "'+1" and csv_safe("-x") == "'-x" and csv_safe("@a") == "'@a"
    assert csv_safe("\tx") == "'\tx" and csv_safe("plain") == "plain" and csv_safe(-1.5) == -1.5 and csv_safe(None) is None
    pay = __import__("sigma.payments", fromlist=["x"])
    txt = pay.members_csv({"1": {"name": "=cmd|' /C calc'!A0", "plan": "1month", "history": []}}).read().decode("utf-8-sig")
    assert "'=cmd" in txt
    ct = __import__("sigma.commands_trades", fromlist=["x"])
    t = {"pair": "=BTC", "direction": "LONG", "entry": "100", "sl": "95", "tp1": "110", "closed": False, "created_at": "2026-10-01"}
    full = ct.build_trades_csv([t], "@scient").read().decode("utf-8-sig").splitlines()[1]
    assert full.startswith("2026-10-01,'@scient,'=BTC") and ",100," in full and ",95," in full
    pub = ct.build_trades_csv([t], "scient", pro=False).read().decode("utf-8-sig").splitlines()[1]
    assert "100" not in pub and "95" not in pub and "110" not in pub and ",OPEN," in pub         # open trade: no levels for non-Pro
    closed = ct.build_trades_csv([{**t, "closed": True, "result": "WIN"}], "scient", pro=False).read().decode("utf-8-sig").splitlines()[1]
    assert ",100," in closed


def test_open_and_stats_csv_are_pro_only(bot_mod, monkeypatch):
    import types
    ct = __import__("sigma.commands_trades", fromlist=["x"])
    from sigma.config import PRO_ROLE_IDS
    def _it(role_ids=(), admin=False):
        u = types.SimpleNamespace(id=1, display_name="x", roles=[types.SimpleNamespace(id=r, name="r") for r in role_ids],
                                  guild_permissions=types.SimpleNamespace(administrator=admin))
        f = _Follow()
        return types.SimpleNamespace(user=u, response=types.SimpleNamespace(defer=f.send, send_message=f.send), followup=f), f
    it, f = _it()
    asyncio.run(ct.open_cmd.callback(it))
    assert f.msgs[-1] == ct.PRO_ONLY
    it, f = _it(role_ids=(next(iter(PRO_ROLE_IDS)),))
    monkeypatch.setattr(ct, "build_combined_board_embed", lambda: "BOARD")
    asyncio.run(ct.open_cmd.callback(it))
    assert f.msgs[-1] is None and "BOARD" not in f.msgs            # embed= kwarg: not a Pro-only refusal
    v = ct.StatsCSVView([], "scient")
    it, f = _it()
    asyncio.run(v.send_csv(it))
    assert f.msgs == [ct.PRO_ONLY]
    it, f = _it(admin=True)
    asyncio.run(v.send_csv(it))
    assert f.msgs == [None]                                        # file sent (file= kwarg)


def test_help_text_points_at_admin_desk(bot_mod):
    import inspect
    pay = __import__("sigma.payments", fromlist=["x"])
    ca = __import__("sigma.commands_admin", fromlist=["x"])
    for mod in (pay, ca):
        src = inspect.getsource(mod)
        assert "/admin grant" not in src and "/admin members" not in src, mod.__name__


# ---------------------------------------------------------------- audit fixes: roles, expiry loop, rejoin

class _Role:
    def __init__(self, rid): self.id = rid


class _Member:
    def __init__(self, uid, roles=(), fail=None):
        self.id, self.roles, self.fail, self.display_name, self.sent = uid, list(roles), fail, f"m{uid}", []
    async def add_roles(self, role, reason=None):
        if self.fail: raise self.fail
        self.roles.append(role)
    async def remove_roles(self, role, reason=None):
        if self.fail: raise self.fail
        self.roles = [r for r in self.roles if r is not role]
    async def send(self, *a, **kw): self.sent.append(kw.get("embed") or (a[0] if a else None))


class _Guild:
    def __init__(self, members, roles):
        self._m, self._r = {m.id: m for m in members}, {r.id: r for r in roles}
    def get_member(self, uid): return self._m.get(uid)
    async def fetch_member(self, uid):
        import discord
        if uid in self._m: return self._m[uid]
        raise discord.NotFound(types_resp(), "no")
    def get_role(self, rid): return self._r.get(rid)


def types_resp():
    import types
    return types.SimpleNamespace(status=404, reason="nf")


class _Bot:
    def __init__(self, guild): self.g, self.users = guild, {}
    def get_guild(self, gid): return self.g
    def get_user(self, uid): return self.users.get(uid)
    async def fetch_user(self, uid): return self.users[uid]


def test_role_grant_failure_alerts_and_dm_says_being_set_up(bot_mod, monkeypatch):
    import sigma.members as mem
    pay = __import__("sigma.payments", fromlist=["x"])
    alerts = []
    async def _alert(msg, key=None): alerts.append((key, msg))
    monkeypatch.setattr(mem, "ops_alert", _alert)
    monkeypatch.setattr(pay, "ops_alert", _alert)
    pro = _Role(mem.SUB_ROLE_ID)
    m = _Member(5)
    assert asyncio.run(mem._sub_grant_role(_Guild([m], [pro]), 5)) is True and m.roles == [pro]
    # role missing from the server -> False + alert (used to return True)
    assert asyncio.run(mem._sub_grant_role(_Guild([m], []), 5)) is False and alerts[-1][0] == "role:5" and "not found" in alerts[-1][1]
    assert asyncio.run(mem._sub_remove_role(_Guild([m], []), 5)) is False
    import discord
    bad = _Member(6, fail=discord.Forbidden(types_resp(), "no perms"))
    assert asyncio.run(mem._sub_grant_role(_Guild([bad], [pro]), 6)) is False and alerts[-1][0] == "role:6"
    # activate(): record saved, DM is honest about the missing role, mod-log flags it
    subs = {}
    monkeypatch.setattr(pay, "load_subs", lambda: subs)
    monkeypatch.setattr(pay, "save_subs", lambda d: subs.update(d))
    logs = []
    async def _log(t, color=None): logs.append(t)
    monkeypatch.setattr(pay, "mod_log", _log)
    monkeypatch.setattr(pay, "bot", _Bot(_Guild([bad], [pro])))
    rec = asyncio.run(pay.activate(6, "1month", by="chain", tx="sigQ", usd=100))
    assert "6" in subs and bad.sent and "being set up" in bad.sent[-1].description and "admin has been pinged" in bad.sent[-1].description
    assert "is live" not in bad.sent[-1].description and "role FAILED" in logs[-1]
    ok = _Member(7)
    monkeypatch.setattr(pay, "bot", _Bot(_Guild([ok], [pro])))
    asyncio.run(pay.activate(7, "1month", by="chain", tx="sigR", usd=100))
    assert "is live" in ok.sent[-1].description and ok.roles == [pro]


def test_expiry_loop_is_per_member_saves_before_dm_and_keeps_record_on_role_failure(bot_mod, monkeypatch):
    import sigma.members as mem
    from sigma.services import subs as subsvc
    now = datetime.now(timezone.utc)
    pro = _Role(mem.SUB_ROLE_ID)
    import discord
    gone = _Member(1, roles=[pro])                                                    # expired, clean path
    stuck = _Member(2, roles=[pro], fail=discord.Forbidden(types_resp(), "no perms"))  # expired, role removal fails
    soon = _Member(3, roles=[pro])                                                    # 2 days left -> reminder
    naive = _Member(4, roles=[pro])                                                   # naive expiry string
    guild = _Guild([gone, stuck, soon, naive], [pro])
    fake_bot = _Bot(guild); fake_bot.users = {m.id: m for m in (gone, stuck, soon, naive)}
    subs = {"1": {"name": "gone", "plan": "1month", "expires": (now - timedelta(days=1)).isoformat(), "reminded": [], "history": []},
            "2": {"name": "stuck", "plan": "1month", "expires": (now - timedelta(days=1)).isoformat(), "reminded": [], "history": []},
            "junk": {"name": "junk", "plan": "1month", "expires": "not a date", "history": []},
            "3": {"name": "soon", "plan": "1month", "expires": (now + timedelta(days=2, hours=5)).isoformat(), "reminded": [], "history": []},
            "4": {"name": "naive", "plan": "1month", "expires": (now - timedelta(days=1)).replace(tzinfo=None).isoformat(), "reminded": [], "history": []}}
    saves, archived, logs, alerts = [], [], [], []
    monkeypatch.setattr(mem, "bot", fake_bot)
    monkeypatch.setattr(mem, "load_subs", lambda: subs)
    def _save(d):
        if d is not subs:
            subs.clear(); subs.update(d)
        saves.append(sorted(d))
    monkeypatch.setattr(mem, "save_subs", _save)
    async def _archive(uid, rec, why): archived.append((uid, why))
    monkeypatch.setattr(mem, "_archive_lapsed", _archive)
    async def _alert(msg, key=None): alerts.append(key)
    monkeypatch.setattr(mem, "ops_alert", _alert)
    pay = __import__("sigma.payments", fromlist=["x"])
    async def _log(t, color=None): logs.append(t)
    monkeypatch.setattr(pay, "mod_log", _log)
    # make the DM to 'gone' blow up: the record must already be gone from the file when that happens
    async def _dm_boom(*a, **kw):
        assert "1" not in subs, "expired DM sent before the record was saved"
        raise RuntimeError("dm closed")
    gone.send = _dm_boom
    out = asyncio.run(mem.subs_check_once(now))
    assert out == {"expired": 2, "reminded": 1, "failed": 1}
    assert "1" not in subs and "4" not in subs and gone.roles == [] and [a[0] for a in archived] == ["1", "4"]
    assert "2" in subs and subs["2"]["expire_failed_at"] and stuck.roles == [pro] and "expire:2" in alerts     # retried tomorrow
    assert subs["3"]["reminded"] == [3, 7] and soon.sent and "2 day(s)" in soon.sent[-1]
    assert "junk" in subs                                                                 # bad record skipped, loop went on
    assert sum("Expired" in l for l in logs) == 2
    # second run: nothing repeats except the stuck one
    out = asyncio.run(mem.subs_check_once(now))
    assert out == {"expired": 0, "reminded": 0, "failed": 1} and len(soon.sent) == 1


def test_rejoin_restores_role_with_zero_days_left(bot_mod, monkeypatch):
    import sigma.members as mem
    now = datetime.now(timezone.utc)
    pro = _Role(mem.SUB_ROLE_ID)
    m = _Member(9); m.bot = False; m.guild = _Guild([m], [pro])
    subs = {"9": {"plan": "1month", "expires": (now + timedelta(hours=6)).isoformat()}}    # days_left == 0: still active today
    monkeypatch.setattr(mem, "load_subs", lambda: subs)
    monkeypatch.setattr(mem, "build_join_dm", lambda: "welcome")
    asyncio.run(mem.on_member_join(m))
    assert m.roles == [pro]
    m2 = _Member(10); m2.bot = False; m2.guild = _Guild([m2], [pro])
    subs["10"] = {"plan": "1month", "expires": (now - timedelta(hours=6)).isoformat()}
    asyncio.run(mem.on_member_join(m2))
    assert m2.roles == []

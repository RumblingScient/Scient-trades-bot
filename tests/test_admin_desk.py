"""/admin desk - the members / plans / promos panel, driven with fake interactions."""
import asyncio
import types
import pytest


class FakePerms:
    def __init__(self, admin): self.administrator = admin


class FakeUser:
    def __init__(self, uid=1, admin=True):
        self.id = uid
        self.display_name = "Scient"
        self.guild_permissions = FakePerms(admin)
        self.roles = []


class FakeMember:
    def __init__(self, uid, name, roles=()):
        self.id, self.display_name = uid, name
        self.roles = [types.SimpleNamespace(id=r) for r in roles]


class FakeGuild:
    def __init__(self, members, pro=()):
        self._m = {m.id: m for m in members}
        self._pro = list(pro)
    def get_member(self, uid): return self._m.get(uid)
    def get_role(self, rid): return types.SimpleNamespace(members=self._pro, id=rid)
    member_count = 10
    @property
    def members(self): return list(self._m.values())


class FakeResponse:
    def __init__(self): self.sent = []; self.deferred = False; self.modal = None; self.kw = {}
    async def send_message(self, msg=None, **kw): self.sent.append(msg); self.kw = kw
    async def send_modal(self, modal): self.modal = modal
    async def defer(self, **kw): self.deferred = True
    def is_done(self): return self.deferred or bool(self.sent) or self.modal is not None


class FakeInteraction:
    def __init__(self, user, guild, values=None):
        self.user, self.guild = user, guild
        self.response = FakeResponse()
        self.data = {"values": values or []}
        self.edits = []
    async def edit_original_response(self, **kw): self.edits.append(kw)


@pytest.fixture
def env(bot_mod, monkeypatch):
    from sigma.services import plans as plansvc
    import sigma.admin_desk as ad
    import sigma.payments as pay
    import sigma.members as mem
    pstate = {}
    monkeypatch.setattr(plansvc, "_load", lambda: pstate)
    monkeypatch.setattr(plansvc, "_save", lambda d: pstate.update(d))
    subs = {"22": {"name": "Owais", "plan": "1month", "price": 100, "days": 30, "started": "2026-10-01T00:00:00+00:00",
                   "expires": "2099-11-01T00:00:00+00:00", "reminded": [], "note": "",
                   "history": [{"plan": "1month", "price": 100, "usd": 100, "at": "2026-10-01T00:00:00+00:00", "by": "chain", "tx": "abc"}]}}
    payments = {"lapsed": []}
    monkeypatch.setattr(ad, "load_subs", lambda: subs)
    monkeypatch.setattr(ad, "save_subs", lambda d: subs.update(d))
    monkeypatch.setattr(ad, "load_payments", lambda: payments)
    monkeypatch.setattr(ad, "save_payments", lambda d: payments.update(d))
    monkeypatch.setattr(pay, "load_subs", lambda: subs)
    monkeypatch.setattr(pay, "load_payments", lambda: payments)
    logs, acts, roles = [], [], []
    async def _log(text, color=None): logs.append(text)
    async def _refresh(): return True
    async def _activate(uid, plan, by, tx=None, usd=None, note="", expires_at=None):
        acts.append((uid, plan, usd, expires_at, note))
        from sigma.services import subs as subsvc
        return subsvc.grant(subs, uid, "x", plan, by, note=note, usd=usd, expires_at=expires_at)
    async def _remove_role(guild, uid): roles.append(("remove", uid)); return True
    monkeypatch.setattr(pay, "mod_log", _log)
    monkeypatch.setattr(pay, "refresh_panel", _refresh)
    monkeypatch.setattr(pay, "activate", _activate)
    monkeypatch.setattr(mem, "_sub_remove_role", _remove_role)
    async def _archive(uid, rec, why): payments["lapsed"].append({"uid": uid, "why": why, **rec})
    monkeypatch.setattr(mem, "_archive_lapsed", _archive)
    pro = [FakeMember(22, "Owais", roles=(mem.SUB_ROLE_ID,)), FakeMember(44, "OldPaid", roles=(mem.SUB_ROLE_ID,)),
           FakeMember(55, "Ref", roles=(mem.SUB_ROLE_ID,)), FakeMember(66, "Analyst", roles=(mem.SUB_ROLE_ID,))]
    pro[3].roles.append(types.SimpleNamespace(id=1, name="Analyst"))
    for m in pro:
        m.bot = False; m.guild_permissions = FakePerms(False)
    guild = FakeGuild(pro + [FakeMember(33, "Newbie")], pro=pro)
    return {"ad": ad, "subs": subs, "payments": payments, "logs": logs, "acts": acts, "roles": roles, "guild": guild, "plans": plansvc}


def _open(env, admin=True):
    it = FakeInteraction(FakeUser(admin=admin), env["guild"])
    asyncio.run(env["ad"].open_admin_desk(it))
    return it, it.response.kw.get("view")


def _btn(view, label):
    import discord
    return next(i for i in view.children if isinstance(i, discord.ui.Button) and i.label == label)


def _labels(view):
    import discord
    return [i.label for i in view.children if isinstance(i, discord.ui.Button)]


def _select(view, row):
    import discord
    return next(i for i in view.children if isinstance(i, (discord.ui.Select, discord.ui.UserSelect)) and i.row == row)


def _confirm(view, env):
    import discord
    b = next(i for i in view.children if isinstance(i, discord.ui.Button) and i.label.startswith("\u2714 Confirm"))
    asyncio.run(b.callback(FakeInteraction(FakeUser(), env["guild"])))


def _submit(it, env, vals):
    modal = it.response.modal
    assert modal is not None, "expected a popup"
    it2 = FakeInteraction(it.user, env["guild"])
    asyncio.run(modal.on_ok(it2, {k: vals.get(k, "") for k in modal.inputs}))
    return it2


def test_gate_and_tabs(env):
    it, view = _open(env, admin=False)
    assert view is None and it.response.sent[0] == "Admins only."
    it, view = _open(env)
    assert view is not None and it.response.kw["ephemeral"] is True
    assert _labels(view)[:5] == ["Overview", "Members", "Revenue", "Payments", "Unrecorded"]
    assert _labels(view)[5:] == ["Plans", "Promos", "Payouts", "Export CSV", "Refresh"]
    it2 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Members").callback(it2))
    assert view.tab == "members" and it2.edits and "Members" in it2.edits[-1]["embed"].title


def test_member_card_grant_extend_revoke_note(env):
    it, view = _open(env)
    user_sel = _select(view, 2)
    asyncio.run(user_sel.callback(FakeInteraction(FakeUser(), env["guild"], values=["22"])))
    assert view.section == "member" and view.uid == 22
    e = view.embed()
    assert e.title == "Owais" and any(f.name == "Plan" and f.value == "Monthly" for f in e.fields)
    assert _btn(view, "Grant / Extend").disabled and not _btn(view, "Revoke").disabled
    # pick a plan -> extend popup -> service
    asyncio.run(_select(view, 3).callback(FakeInteraction(FakeUser(), env["guild"], values=["3months"])))
    assert view.plan_key == "3months" and not _btn(view, "Grant / Extend").disabled
    it2 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Grant / Extend").callback(it2))
    assert "Extend" in it2.response.modal.title
    _submit(it2, env, {"expires": "", "usd": "250", "note": "paid by hand"})
    assert env["acts"][-1][:3] == (22, "3months", 250.0) and env["subs"]["22"]["plan"] == "3months"
    assert view.last.startswith("Extended Quarterly") or view.last.startswith("Extended")
    # note
    it3 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Add note").callback(it3))
    _submit(it3, env, {"note": "VIP"})
    assert env["subs"]["22"]["note"] == "VIP"
    # revoke -> role removed, archived, logged
    it4 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Revoke").callback(it4))
    _submit(it4, env, {"note": "refund"})
    assert "22" not in env["subs"] and env["roles"] == [("remove", 22)] and env["payments"]["lapsed"][-1]["why"].startswith("revoked")
    assert any("Revoked" in l for l in env["logs"])
    # a member with no record: Grant only
    asyncio.run(user_sel.callback(FakeInteraction(FakeUser(), env["guild"], values=["33"])))
    assert "Grant" in _labels(view) and _btn(view, "Revoke").disabled
    assert any(f.value.startswith("**none on record**") for f in view.embed().fields)


def test_plans_section_edit_discount_toggle_add_remove(env):
    it, view = _open(env)
    asyncio.run(_btn(view, "Plans").callback(FakeInteraction(FakeUser(), env["guild"])))
    assert view.section == "plans" and view.embed().title == "Plans"
    assert _btn(view, "Edit").disabled and not _btn(view, "Add plan").disabled
    asyncio.run(_select(view, 3).callback(FakeInteraction(FakeUser(), env["guild"], values=["1month"])))
    it2 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Edit").callback(it2))
    assert it2.response.modal.inputs["price"].default == "100"
    _submit(it2, env, {"name": "", "price": "120", "days": "", "seats": "", "position": ""})
    assert env["plans"].get("1month")["price"] == 120 and "price $120" in view.last
    it3 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Discount").callback(it3))
    _submit(it3, env, {"pct": "25", "until": ""})
    assert env["plans"].get("1month")["discount_pct"] == 25 and "$90" in view.last
    asyncio.run(_btn(view, "Disable").callback(FakeInteraction(FakeUser(), env["guild"])))
    assert env["plans"].get("1month")["enabled"] is True and view.pending           # waits for Confirm
    _confirm(view, env)
    assert env["plans"].get("1month")["enabled"] is False and "Enable" in _labels(view)
    it4 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Add plan").callback(it4))
    _submit(it4, env, {"key": "launch", "name": "Launch Week", "price": "70", "days": "30", "seats": ""})
    assert env["plans"].get("launch")["price"] == 70 and view.plan_key == "launch"
    it5 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Remove").callback(it5))
    _submit(it5, env, {"confirm": "wrong"})
    assert "launch" in env["plans"].all() and view.last.startswith("⚠ Type the plan key")
    it6 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Remove").callback(it6))
    _submit(it6, env, {"confirm": "launch"})
    assert "launch" not in env["plans"].all() and view.plan_key is None
    assert sum("Plans" in l for l in env["logs"]) == 5


def test_promos_add_remove_and_settle(env):
    it, view = _open(env)
    asyncio.run(_btn(view, "Promos").callback(FakeInteraction(FakeUser(), env["guild"])))
    assert _btn(view, "Remove selected").disabled
    it2 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Add code").callback(it2))
    _submit(it2, env, {"code": "SIGMA-OWAIS", "discount": "10%", "limits": "", "plan": "", "referral": "22 20"})
    r = env["plans"].promos()["SIGMA-OWAIS"]
    assert r["pct"] == 10 and r["owner_id"] == 22 and r["owner_name"] == "Owais" and r["share_pct"] == 20
    assert view.promo_code == "SIGMA-OWAIS" and not _btn(view, "Remove selected").disabled
    it3 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Add code").callback(it3))
    _submit(it3, env, {"code": "TEN", "discount": "$10", "limits": "5 / 31 Dec", "plan": "1month", "referral": ""})
    r = env["plans"].promos()["TEN"]
    assert r["usd"] == 10 and r["uses_left"] == 5 and r["plans"] == ["1month"] and r.get("until")
    asyncio.run(_btn(view, "Remove selected").callback(FakeInteraction(FakeUser(), env["guild"])))
    _confirm(view, env)
    assert "TEN" not in env["plans"].promos() and "SIGMA-OWAIS" in env["plans"].promos()
    # payouts + settle
    asyncio.run(_btn(view, "Payouts").callback(FakeInteraction(FakeUser(), env["guild"])))
    assert view.embed().title == "Referral payouts"
    it4 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Settle an owner").callback(it4))
    _submit(it4, env, {"owner": "<@22>", "note": "tx1"})
    assert view.last.startswith("\u26a0 Nothing owed")           # no referral payments yet -> service refuses, nothing logged
    _submit(it4, env, {"owner": "abc", "note": ""})
    assert "user id" in view.last


def test_unrecorded_bulk_grant_and_remove(env):
    ad = env["ad"]
    assert [(m.id, staff) for m, staff in ad.unrecorded_members(env["guild"])] == [(44, False), (55, False), (66, True)]
    it, view = _open(env)
    asyncio.run(_btn(view, "Unrecorded").callback(FakeInteraction(FakeUser(), env["guild"])))
    assert _btn(view, "Grant to all unrecorded (2)").disabled and not _btn(view, "Remove Pro role from all unrecorded (2)").disabled
    asyncio.run(_select(view, 3).callback(FakeInteraction(FakeUser(), env["guild"], values=["lifetime"])))
    it2 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Grant to all unrecorded (2)").callback(it2))
    _submit(it2, env, {"expires": "", "note": "referral", "confirm": "nope"})
    assert view.last.startswith("\u26a0 Type GRANT") and env["acts"] == []
    _submit(it2, env, {"expires": "2099-01-01", "note": "referral", "confirm": "grant"})
    assert [a[0] for a in env["acts"]] == [44, 55] and all(a[2] == 0.0 and a[3] == "2099-01-01" for a in env["acts"])
    assert "44" in env["subs"] and "55" in env["subs"] and "66" not in env["subs"]
    assert any("Bulk grant" in l for l in env["logs"]) and "Grant to all unrecorded (0)" in _labels(view)
    # remove path on whoever is still unrecorded (none now) -> button disabled; simulate with a fresh holder
    env["guild"]._pro.append(FakeMember(77, "Ghost", roles=(ad.SUB_ROLE_ID,)))
    env["guild"]._pro[-1].bot = False; env["guild"]._pro[-1].guild_permissions = FakePerms(False)
    view.rebuild()
    it3 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Remove Pro role from all unrecorded (1)").callback(it3))
    _submit(it3, env, {"note": "", "confirm": "REMOVE"})
    assert env["roles"][-1] == ("remove", 77) and any("Bulk role removal" in l for l in env["logs"])


# ---------------------------------------------------------------- audit fixes

def test_settle_confirms_with_owed_total_then_stamps_rows(env):
    env["payments"]["referrals"] = [
        {"code": "SIGMA-OWAIS", "owner_id": 22, "owner_name": "Owais", "user_id": 1, "user": "m1", "plan": "1year", "usd": 1000,
         "share_pct": 25, "share_usd": 250.0, "tx": "sigA", "at": "2026-10-01T00:00:00+00:00", "settled": None},
        {"code": "SIGMA-OWAIS", "owner_id": 22, "owner_name": "Owais", "user_id": 2, "user": "m2", "plan": "1month", "usd": 100,
         "share_pct": 25, "share_usd": 25.0, "tx": "sigB", "at": "2026-10-02T00:00:00+00:00", "settled": None}]
    it, view = _open(env)
    asyncio.run(_btn(view, "Payouts").callback(FakeInteraction(FakeUser(), env["guild"])))
    it2 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Settle an owner").callback(it2))
    _submit(it2, env, {"owner": "22", "note": "tx9"})
    assert view.pending and "Settle $275.00 for Owais" in view.last           # nothing stamped yet
    assert all(r["settled"] is None for r in env["payments"]["referrals"]) and not any("Payout settled" in l for l in env["logs"])
    _confirm(view, env)
    assert view.last == "Settled **$275.00** for Owais." and all(r["settled"] for r in env["payments"]["referrals"])
    assert any("Payout settled" in l and "$275.00" in l for l in env["logs"]) and view.pending is None
    # cancel path leaves the rows alone
    for r in env["payments"]["referrals"]:
        r["settled"] = None
    it3 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Settle an owner").callback(it3))
    _submit(it3, env, {"owner": "<@22>", "note": ""})
    asyncio.run(_btn(view, "✖ Cancel").callback(FakeInteraction(FakeUser(), env["guild"])))
    assert all(r["settled"] is None for r in env["payments"]["referrals"]) and view.last.startswith("Cancelled")


def test_members_tab_caps_embed_size_with_overflow_marker(env):
    import sigma.payments as pay
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    for i in range(400):
        env["subs"][str(1000 + i)] = {"name": f"member-with-a-fairly-long-display-name-{i:03d}", "plan": "1month", "days": 30,
                                      "expires": (now + __import__("datetime").timedelta(days=1 + i % 29)).isoformat(), "history": []}
    e = pay.members_embed(env["guild"], "members")
    total = len(e.title or "") + sum(len(f.name) + len(f.value) for f in e.fields) + len(e.footer.text or "")
    assert total <= 5500 and all(len(f.value) <= 1024 for f in e.fields)
    marker = next(f.value for f in e.fields if "more" in f.value)
    shown = sum(f.value.count("\n") + 1 for f in e.fields if "more" not in f.value)
    assert marker == f"**+{401 - shown} more** - use Export CSV for the full list" and "Export CSV" in marker


def test_free_text_ints_become_user_errors_and_generic_errors_alert(env, monkeypatch):
    it, view = _open(env)
    asyncio.run(_btn(view, "Plans").callback(FakeInteraction(FakeUser(), env["guild"])))
    asyncio.run(_select(view, 3).callback(FakeInteraction(FakeUser(), env["guild"], values=["1month"])))
    it2 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Edit").callback(it2))
    _submit(it2, env, {"name": "", "price": "", "days": "", "seats": "ten", "position": ""})
    assert view.last == "⚠ Seats must be a whole number, got `ten`." and env["plans"].get("1month").get("seats") is None
    it3 = FakeInteraction(FakeUser(), env["guild"])
    asyncio.run(_btn(view, "Add plan").callback(it3))
    _submit(it3, env, {"key": "x", "name": "X", "price": "1", "days": "1", "seats": "1.5"})
    assert view.last.startswith("⚠ Seats must be a whole number") and "x" not in env["plans"].all()
    # an unexpected exception inside an action -> Last line + ops alert (not just a log line)
    alerts = []
    async def _alert(msg, key=None): alerts.append((key, msg))
    monkeypatch.setattr(env["ad"], "ops_alert", _alert)
    async def _boom(): raise KeyError("plans.json")
    asyncio.run(view._apply(FakeInteraction(FakeUser(), env["guild"]), _boom))
    assert view.last == "⚠ Something broke - check the logs." and alerts and alerts[0][0] == "desk:KeyError" and "plans.json" in alerts[0][1]


def test_revoke_and_note_take_the_subs_lock(bot_mod):
    import inspect
    import sigma.admin_desk as ad
    assert "SUBS_LOCK" in inspect.getsource(ad.AdminDesk.on_revoke) and "SUBS_LOCK" in inspect.getsource(ad.AdminDesk.on_note)
    assert "PAY_LOCK" in inspect.getsource(ad.AdminDesk.on_settle)
    import sigma.payments as pay, sigma.members as mem
    assert "SUBS_LOCK" in inspect.getsource(pay.activate) and "PAY_LOCK" in inspect.getsource(pay.new_quote)
    assert "PAY_LOCK" in inspect.getsource(pay.apply_promo) and "PAY_LOCK" in inspect.getsource(pay._watch_once)
    assert "PAY_LOCK" in inspect.getsource(mem._archive_lapsed) and not hasattr(pay, "_watch_lock")

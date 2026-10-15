"""The Manage panel (sigma.desk) and /setup edit end to end with a fake Interaction:
gate -> button / popup -> service -> save -> card refresh -> board -> feed -> thread -> panel re-render."""
import asyncio
import types
import pytest


class FakePerms:
    def __init__(self, admin): self.administrator = admin


class FakeRole:
    def __init__(self, name): self.name = name


class FakeUser:
    def __init__(self, uid=1, admin=False, analyst=True):
        self.id = uid
        self.display_name = "Scient"
        self.guild_permissions = FakePerms(admin)
        self.roles = [FakeRole("Analyst")] if analyst else []
        self.display_avatar = types.SimpleNamespace(url=None)


class FakeResponse:
    def __init__(self): self.sent = []; self.deferred = False; self.modal = None; self.kw = {}
    async def send_message(self, msg=None, **kw): self.sent.append(msg); self.kw = kw
    async def send_modal(self, modal): self.modal = modal
    async def defer(self, **kw): self.deferred = True
    def is_done(self): return self.deferred or bool(self.sent) or self.modal is not None


class FakeFollowup:
    def __init__(self): self.sent = []
    async def send(self, msg=None, **kw): self.sent.append(msg)


class FakeInteraction:
    def __init__(self, user, command=None, values=None):
        self.user = user
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.command = command
        self.data = {"values": values or []}
        self.edits = []
    async def edit_original_response(self, **kw): self.edits.append(kw)


@pytest.fixture
def env(bot_mod, monkeypatch):
    """In-memory stores + silenced Discord side effects. Returns a dict to inspect."""
    from sigma.services import store
    import sigma.actions as ac
    import sigma.desk as desk
    trades = {"11": {"direction": "LONG", "entry": "100", "sl": "95", "initial_sl": "95", "tp1": "110", "tp2": "120", "tp_split": [50, 50],
                     "fills": [], "closed": False, "analyst_id": 1, "analyst_name": "Scient", "pair": "BTC",
                     "channel_id": 1, "message_id": 11, "entry1_filled": False, "tp1_hit": False, "tp2_hit": False, "be": False,
                     "created_at": "2099-01-01T00:00:00+00:00"}}
    spots = {"22": {"kind": "spot", "pair": "FET", "dca_zone": "1.2 - 1.0", "avg_entry": "1.1", "invalidation": "0.9", "t1": "1.5", "t2": "2.0",
                    "tp_split": [50, 50], "t1_hit": False, "t2_hit": False, "t3_hit": False, "sells": [],
                    "buys": [{"price": 1.1, "pct": None}], "closed": False, "analyst_id": 2, "analyst_name": "Owais",
                    "channel_id": 1, "message_id": 22, "status": "ACCUMULATING", "zone_filled": False,
                    "created_at": "2099-01-01T00:00:00+00:00"}}
    calls = {"feed": [], "thread": [], "card": 0, "board": 0, "results": 0}
    monkeypatch.setattr(store, "load_trades", lambda: trades)
    monkeypatch.setattr(store, "load_spot", lambda: spots)
    monkeypatch.setattr(store, "save_trades", lambda d: trades.update(d))
    monkeypatch.setattr(store, "save_spot", lambda d: spots.update(d))

    async def _card(rec, spot_mode=False): calls["card"] += 1
    async def _board(): calls["board"] += 1
    async def _feed(rec, title, color, line, footer=None): calls["feed"].append((title, line, footer))
    async def _thread(rec, text): calls["thread"].append(text)
    async def _results(): calls["results"] += 1
    for mod in (ac,):
        monkeypatch.setattr(mod, "refresh_and_edit", _card)
        monkeypatch.setattr(mod, "refresh_board", _board)
        monkeypatch.setattr(mod, "post_update_feed", _feed)
        monkeypatch.setattr(mod, "thread_note", _thread)
        monkeypatch.setattr(mod, "_results_watch_tick", _results)
    return {"trades": trades, "spots": spots, "calls": calls, "desk": desk, "ac": ac}


def _panel(env, kind, key, user):
    """Open the Manage panel as `user`; returns (interaction, view)."""
    it = FakeInteraction(user)
    asyncio.run(env["desk"].open_panel(it, kind, key))
    return it, it.response.kw.get("view")


def _button(view, label):
    import discord
    return next(i for i in view.children if isinstance(i, discord.ui.Button) and i.label == label)


def _select(view):
    import discord
    return next(i for i in view.children if isinstance(i, discord.ui.Select))


def _confirm(view, user=None):
    """Instant actions park behind Confirm - press it (as the given user)."""
    import discord
    b = next(i for i in view.children if isinstance(i, discord.ui.Button) and i.label.startswith("\u2714 Confirm"))
    it = FakeInteraction(user or FakeUser(uid=1))
    asyncio.run(b.callback(it))
    return it


def _click(view, label, user=None):
    """Click a button; if it asks for confirmation, confirm it too."""
    it = FakeInteraction(user or FakeUser(uid=1))
    asyncio.run(_button(view, label).callback(it))
    if view.pending:
        return _confirm(view, user)
    return it


def _pick(view, value, user=None):
    """Pick a More... option; confirm if asked."""
    it = FakeInteraction(user or FakeUser(uid=1), values=[value])
    asyncio.run(_select(view).callback(it))
    if view.pending:
        return _confirm(view, user)
    return it


def _submit(it, vals):
    """The popup the last click opened, answered with vals."""
    modal = it.response.modal
    assert modal is not None, "expected a popup"
    it2 = FakeInteraction(it.user)
    asyncio.run(modal.on_ok(it2, {k: vals.get(k, "") for k in modal.inputs}))
    return it2


# ----------------------------------------------------------------------------- gate

def test_panel_gate(env):
    it, view = _panel(env, "fut", "11", FakeUser(uid=5, analyst=False))
    assert view is None and "Analyst controls" in it.response.sent[0]
    it, view = _panel(env, "fut", "11", FakeUser(uid=2))          # another analyst
    assert view is None and "Only the analyst who posted" in it.response.sent[0]
    it, view = _panel(env, "fut", "11", FakeUser(uid=99, admin=True))
    assert view is not None and it.response.kw.get("ephemeral") is True
    it, view = _panel(env, "fut", "404", FakeUser(uid=1))
    assert view is None and "isn't in the log" in it.response.sent[0]


# ----------------------------------------------------------------------------- futures

def test_futures_buttons_follow_state(env):
    it, view = _panel(env, "fut", "11", FakeUser(uid=1))
    labels = [b.label for b in view.children if hasattr(b, "label")]
    assert labels == ["Entry filled", "TP hit", "Stop to entry", "Trail stop", "Stopped out", "Close", "Edit", "Replace chart"]
    assert _button(view, "TP hit").disabled and _button(view, "Stop to entry").disabled and not _button(view, "Entry filled").disabled
    it2 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "Entry filled").callback(it2))
    t = env["trades"]["11"]
    assert not t["entry1_filled"] and view.pending and it2.edits              # parked behind Confirm, nothing changed yet
    assert [b.label for b in view.children][0].startswith("\u2714 Confirm: Entry filled")
    asyncio.run(_button(view, "\u2716 Cancel").callback(FakeInteraction(FakeUser(uid=1))))
    assert not view.pending and not t["entry1_filled"] and view.last.startswith("Cancelled")
    it2 = _click(view, "Entry filled")
    assert t["entry1_filled"] and it2.edits and env["calls"]["feed"][-1][0] == "Entry filled"
    assert _button(view, "Entry filled").disabled and not _button(view, "TP hit").disabled    # panel re-rendered


def test_futures_tp_be_trail_close_flow(env):
    it, view = _panel(env, "fut", "11", FakeUser(uid=1))
    _click(view, "Entry filled")
    # TP hit popup is pre-filled with the next preset + planned %
    it2 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "TP hit").callback(it2))
    m = it2.response.modal
    assert m.inputs["price"].default == "110" and m.inputs["pct"].default == "50"
    _submit(it2, {"price": "110", "pct": "", "note": "clean"})
    t = env["trades"]["11"]
    assert t["tp1_hit"] and t["fills"][0] == {"price": 110.0, "pct": 50.0, "label": "TP1"}
    assert env["calls"]["feed"][-1][0] == "TP1 reached" and "> clean" in env["calls"]["feed"][-1][1]
    # stop to entry - one click, shows on the panel and the card data
    _click(view, "Stop to entry")
    assert t["be"] and t["sl"] == "100" and t["initial_sl"] == "95" and _button(view, "Stop to entry").disabled
    assert "at entry" in view.embed().description
    # trail above entry - R:R unchanged
    it3 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "Trail stop").callback(it3))
    _submit(it3, {"level": "105", "note": ""})
    assert t["sl"] == "105" and not t["be"] and env["calls"]["feed"][-1][0] == "Stop moved"
    from sigma.calculations import display_rr
    assert display_rr(t) == "4.0"
    # discretionary TP below the next preset -> PTP needs pct
    it4 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "TP hit").callback(it4))
    _submit(it4, {"price": "115", "pct": "", "note": ""})
    assert len(t["fills"]) == 1 and view.last.startswith("⚠ **% is required**")
    _submit(it4, {"price": "115", "pct": "25", "note": ""})
    assert len(t["fills"]) == 2 and t["fills"][1]["label"] == "TP2"
    # close the rest
    it5 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "Close").callback(it5))
    assert "25% still open" in str(it5.response.modal.inputs["price"].label)
    _submit(it5, {"price": "120", "note": "done"})
    assert t["closed"] and t["result"] == "WIN" and env["calls"]["results"] == 1
    assert t["result_r"] == pytest.approx((0.5 * 110 + 0.25 * 115 + 0.25 * 120 - 100) / 5, abs=1e-6)
    # closed panel: only Reopen is offered
    assert [o.value for o in _select(view).options] == ["reopen"]


def test_futures_stopped_out_and_more(env):
    it, view = _panel(env, "fut", "11", FakeUser(uid=1))
    _click(view, "Entry filled")
    it2 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "Stopped out").callback(it2))
    assert it2.response.modal.inputs["price"].default == "95"
    _submit(it2, {"price": "", "note": ""})
    t = env["trades"]["11"]
    assert t["closed"] and t["result"] == "LOSS" and t["sl_hit"] and t["result_r"] == -1.0


def test_more_menu_invalidated_undo_track(env):
    it, view = _panel(env, "fut", "11", FakeUser(uid=1))
    vals = [o.value for o in _select(view).options]
    assert vals == ["ci", "ul", "fix", "track", "rtp"]
    _pick(view, "track")
    assert env["trades"]["11"]["watch_disabled"] is True and "OFF" in view.last
    _pick(view, "ul")
    assert view.last.startswith("\u26a0 Nothing to undo")
    _pick(view, "ci")
    t = env["trades"]["11"]
    assert t["closed"] and t["result"] == "INVALID" and env["calls"]["feed"][-1][0] == "Invalidated"


def test_fix_a_fill_from_panel(env):
    it, view = _panel(env, "fut", "11", FakeUser(uid=1))
    _click(view, "Entry filled")
    it2 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "TP hit").callback(it2))
    _submit(it2, {"price": "110", "pct": "50", "note": ""})
    it3 = FakeInteraction(FakeUser(uid=1), values=["fix"])
    asyncio.run(_select(view).callback(it3))
    assert "`1.` 50% @ 110" in view.last and it3.response.modal is not None
    _submit(it3, {"item": "1", "price": "", "pct": "", "what": "tp"})
    t = env["trades"]["11"]
    assert t["fills"] == [] and not t["tp1_hit"] and env["calls"]["thread"][-1].startswith("**Correction** - removed take profit #1")


def test_edit_popup_changes_only_what_changed(env):
    it, view = _panel(env, "fut", "11", FakeUser(uid=1))
    it2 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "Edit").callback(it2))
    m = it2.response.modal
    assert m.inputs["targets"].default == "110 / 120" and m.inputs["stop"].default == "95"
    _submit(it2, {"entry": "100", "stop": "94", "targets": "110 / 120 / 130", "risk_tf": "1 / 4H", "context": ""})
    t = env["trades"]["11"]
    assert t["sl"] == "94" and t["initial_sl"] == "94" and t["tp3"] == "130" and t["timeframe"] == "4H"
    assert env["calls"]["thread"][-1].startswith("**Edited** - corrected: stop, risk, TP1, TP2, TP3, TP4, timeframe")
    # outside the window a non-admin is refused, an admin is not
    t["created_at"] = "2020-01-01T00:00:00+00:00"
    it3 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "Edit").callback(it3))
    assert it3.response.modal is None and "Edit window" in view.last
    it4 = FakeInteraction(FakeUser(uid=99, admin=True))
    asyncio.run(_button(view, "Edit").callback(it4))
    assert it4.response.modal is not None


# ----------------------------------------------------------------------------- spot

def test_spot_panel_flow(env):
    it, view = _panel(env, "spot", "22", FakeUser(uid=2))
    labels = [b.label for b in view.children if hasattr(b, "label")]
    assert labels == ["Buy", "Zone filled", "Sold", "Close", "Edit", "Replace chart"]
    it2 = FakeInteraction(FakeUser(uid=2))
    asyncio.run(_button(view, "Buy").callback(it2))
    _submit(it2, {"price": "1.0", "pct": "", "note": ""})
    p = env["spots"]["22"]
    assert p["buys"][-1]["price"] == 1.0 and p["avg_entry"] == "1.05" and env["calls"]["feed"][-1][0] == "Buy filled"
    _pick(view, "st:HOLDING", FakeUser(uid=2))
    assert p["status"] == "HOLDING" and env["calls"]["feed"][-1][0] == "Status updated"
    it3 = FakeInteraction(FakeUser(uid=2))
    asyncio.run(_button(view, "Sold").callback(it3))
    assert it3.response.modal.inputs["price"].default == "1.5"
    _submit(it3, {"price": "1.5", "pct": "", "note": ""})
    assert p["t1_hit"] and p["sells"][0]["pct"] == 50.0
    it4 = FakeInteraction(FakeUser(uid=2))
    asyncio.run(_button(view, "Close").callback(it4))
    _submit(it4, {"price": "1.3", "note": ""})
    assert p["closed"] and p["result"] == "WIN" and env["calls"]["feed"][-1][2] == "Sigma Trading - Spot Plays"


def test_other_analyst_blocked_at_action_time(env):
    """Panel opened as admin, but the record's owner check still runs on every action (uid swap mid-panel)."""
    it, view = _panel(env, "spot", "22", FakeUser(uid=99, admin=True))
    asyncio.run(_button(view, "Zone filled").callback(FakeInteraction(FakeUser(uid=1))))
    it2 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "Zone filled").callback(it2))
    _submit(it2, {"avg": "", "note": ""})
    assert not env["spots"]["22"]["zone_filled"] and "Only the analyst who posted" in view.last


# ----------------------------------------------------------------------------- /setup edit (rare fields) + autocomplete

def test_setup_edit_targets_and_framework(bot_mod, env, monkeypatch):
    import sigma.commands_setup as cs
    g = next(c for c in bot_mod.bot.tree.get_commands() if c.name == "setup")
    cmd = next(s for s in g.commands if s.name == "edit")

    class Msg:
        attachments = []
        async def edit(self, **kw): pass

    class Ch:
        async def fetch_message(self, mid): return Msg()
    monkeypatch.setattr(cs.bot, "get_channel", lambda cid: Ch())
    async def _board(): env["calls"]["board"] += 1
    monkeypatch.setattr(cs, "refresh_board", _board)
    it = FakeInteraction(FakeUser(uid=1), command=cmd)
    kw = {p.name: None for p in cmd.parameters}
    kw.update(trade="f:11", targets="111 / 121", framework="FVG + Order Block")
    asyncio.run(cmd.callback(it, **kw))
    t = env["trades"]["11"]
    assert t["tp1"] == "111" and t["tp2"] == "121" and t["tp3"] == "" and t["frameworks"] == ["FVG", "Order Block"]
    assert it.followup.sent[-1].startswith("Updated (framework, TP1, TP2, TP3, TP4)")
    it = FakeInteraction(FakeUser(uid=1), command=cmd)
    kw.update(targets=None, framework="Nope")
    asyncio.run(cmd.callback(it, **kw))
    assert it.followup.sent[-1].startswith("Unknown framework")


def test_open_autocomplete_filters_to_own_trades(bot_mod, env):
    import sigma.commands_setup as cs
    it = FakeInteraction(FakeUser(uid=1))
    own = asyncio.run(cs.open_any_ac(it, ""))
    assert [c.value for c in own] == ["f:11"]
    it = FakeInteraction(FakeUser(uid=99, admin=True))
    allv = asyncio.run(cs.open_any_ac(it, ""))
    assert sorted(c.value for c in allv) == ["f:11", "s:22"]


def test_parse_targets(env):
    from sigma.actions import parse_targets
    from sigma.errors import UserError
    assert parse_targets("104000 / 108,000, 112000", 4) == ["104000", "108,000", "112000"]
    assert parse_targets(".5 / .75", 3) == [".5", ".75"]
    assert parse_targets("0.0000037 0.00000395", 3) == ["0.0000037", "0.00000395"]
    with pytest.raises(UserError, match="Up to 3"):
        parse_targets("1/2/3/4", 3)
    with pytest.raises(UserError, match="isn't a price"):
        parse_targets("1 / abc", 3)


def test_replace_chart_from_panel(env, monkeypatch):
    """Replace chart: the card is re-uploaded with the new image, record marked edited, thread noted."""
    import sigma.actions as ac
    edits = []

    class Msg:
        async def edit(self, **kw): edits.append(kw)

    class Ch:
        async def fetch_message(self, mid): return Msg()
    monkeypatch.setattr(ac.bot, "get_channel", lambda cid: Ch())

    class Att:
        filename = "new.png"; content_type = "image/png"
        async def to_file(self):
            import discord, io
            return discord.File(io.BytesIO(b"x"), filename="new.png")
    it, view = _panel(env, "fut", "11", FakeUser(uid=1))
    assert "Replace chart" in [b.label for b in view.children if hasattr(b, "label")]
    it2 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(_button(view, "Replace chart").callback(it2))
    assert isinstance(it2.response.modal, env["desk"].ChartForm)
    it3 = FakeInteraction(FakeUser(uid=1))
    asyncio.run(it2.response.modal.on_ok(it3, Att()))
    t = env["trades"]["11"]
    assert edits and edits[-1]["attachments"][0].filename == "new.png" and edits[-1]["embed"].image.url == "attachment://new.png"
    assert t["edited"] and view.last == "Chart replaced on the card." and env["calls"]["thread"][-1] == "**Chart replaced**"
    # not an image -> refused, nothing edited
    class Bad(Att):
        content_type = "application/pdf"
    asyncio.run(it2.response.modal.on_ok(FakeInteraction(FakeUser(uid=1)), Bad()))
    assert view.last.startswith("\u26a0 That isn't an image") and len(edits) == 1

"""/setup handlers end to end with a fake Interaction: gate -> service -> save -> refresh -> feed -> reply."""
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
    def __init__(self): self.sent = []; self.deferred = False
    async def send_message(self, msg, **kw): self.sent.append(msg)
    async def defer(self, **kw): self.deferred = True
    def is_done(self): return self.deferred or bool(self.sent)


class FakeFollowup:
    def __init__(self): self.sent = []
    async def send(self, msg=None, **kw): self.sent.append(msg)


class FakeInteraction:
    def __init__(self, user, command=None):
        self.user = user
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.command = command


@pytest.fixture
def env(bot_mod, monkeypatch):
    """In-memory stores + silenced Discord side effects. Returns a dict to inspect."""
    from sigma.services import store
    import sigma.commands_setup as cs
    trades = {"11": {"direction": "LONG", "entry": "100", "sl": "95", "tp1": "110", "tp2": "120", "tp_split": [50, 50],
                     "fills": [], "closed": False, "analyst_id": 1, "analyst_name": "Scient", "pair": "BTC",
                     "channel_id": 1, "message_id": 11, "entry1_filled": False, "tp1_hit": False, "tp2_hit": False, "be": False}}
    spots = {"22": {"kind": "spot", "pair": "FET", "avg_entry": "1.1", "invalidation": "0.9", "t1": "1.5", "t2": "2.0",
                    "tp_split": [50, 50], "t1_hit": False, "t2_hit": False, "t3_hit": False, "sells": [],
                    "buys": [{"price": 1.1, "pct": None}], "closed": False, "analyst_id": 2, "analyst_name": "Owais",
                    "channel_id": 1, "message_id": 22, "status": "ACCUMULATING", "zone_filled": False}}
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
    monkeypatch.setattr(cs, "refresh_and_edit", _card)
    monkeypatch.setattr(cs, "refresh_board", _board)
    monkeypatch.setattr(cs, "post_update_feed", _feed)
    monkeypatch.setattr(cs, "thread_note", _thread)
    monkeypatch.setattr(cs, "_results_watch_tick", _results)
    return {"trades": trades, "spots": spots, "calls": calls, "cs": cs}


def _sub(bot_mod, name):
    g = next(c for c in bot_mod.bot.tree.get_commands() if c.name == "setup")
    return next(s for s in g.commands if s.name == name)


def _choice(value):
    return types.SimpleNamespace(value=value, name=value)


def test_update_preset_tp_flow(bot_mod, env):
    cmd = _sub(bot_mod, "update")
    it = FakeInteraction(FakeUser(uid=1), command=cmd)
    asyncio.run(cmd.callback(it, trade="f:11", event=_choice("TPN"), price=None, tp_pct=None, note="clean"))
    t = env["trades"]["11"]
    assert it.response.deferred and t["tp1_hit"] and t["fills"][0]["pct"] == 50.0
    assert env["calls"]["card"] == 1 and env["calls"]["board"] == 1
    assert env["calls"]["feed"][0][0] == "TP1 reached" and "> clean" in env["calls"]["feed"][0][1]
    assert it.followup.sent[-1].startswith("Updated: TP1 reached")


def test_update_status_only_on_spot(bot_mod, env):
    cmd = _sub(bot_mod, "update")
    it = FakeInteraction(FakeUser(uid=2), command=cmd)
    asyncio.run(cmd.callback(it, trade="s:22", event=None, price=None, tp_pct=None, status=_choice("HOLDING"), note=None))
    assert env["spots"]["22"]["status"] == "HOLDING" and env["calls"]["feed"][0][0] == "Status updated"
    it = FakeInteraction(FakeUser(uid=2), command=cmd)
    asyncio.run(cmd.callback(it, trade="s:22", event=None, price=None, tp_pct=None, status=None, note=None))
    assert it.followup.sent[-1].startswith("Pick an **event**")


def test_update_refuses_other_analysts_trade(bot_mod, env):
    cmd = _sub(bot_mod, "update")
    it = FakeInteraction(FakeUser(uid=1), command=cmd)
    asyncio.run(cmd.callback(it, trade="s:22", event=_choice("TPN"), price=None, tp_pct=None, note=None))
    assert "Only the analyst who posted" in it.followup.sent[-1]
    assert env["spots"]["22"]["sells"] == [] and env["calls"]["card"] == 0


def test_non_analyst_is_gated_before_defer(bot_mod, env):
    cmd = _sub(bot_mod, "close")
    it = FakeInteraction(FakeUser(uid=5, analyst=False), command=cmd)
    asyncio.run(cmd.callback(it, trade="f:11", price="120", note=None))
    assert not it.response.deferred and "Analyst" in it.response.sent[0]


def test_close_grades_and_refreshes_results(bot_mod, env):
    cmd = _sub(bot_mod, "close")
    it = FakeInteraction(FakeUser(uid=1), command=cmd)
    asyncio.run(cmd.callback(it, trade="f:11", price="120", note=None))
    t = env["trades"]["11"]
    assert t["closed"] and t["result"] == "WIN" and abs(t["result_r"] - 4.0) < 1e-9
    assert env["calls"]["results"] == 1
    assert env["calls"]["feed"][0][0].startswith("Closed - Win (+4.00R)")
    assert it.followup.sent[-1].startswith("Closed: Closed (+4.00R)")


def test_admin_can_close_spot_and_alias_adds_moved_note(bot_mod, env):
    cmds = {c.name: c for c in bot_mod.bot.tree.get_commands()}
    alias = cmds["spot_close"]
    it = FakeInteraction(FakeUser(uid=99, admin=True), command=alias)
    asyncio.run(alias.callback(it, trade="s:22", price="1.3", note=None))
    p = env["spots"]["22"]
    assert p["closed"] and p["result"] == "WIN" and abs(p["result_r"] - 1.0) < 1e-9
    assert it.followup.sent[-1].startswith("Moved to `/setup close`")
    assert env["calls"]["feed"][0][2] == "Sigma Trading - Spot Plays"


def test_user_error_is_one_plain_line(bot_mod, env):
    cmd = _sub(bot_mod, "update")
    it = FakeInteraction(FakeUser(uid=1), command=cmd)
    asyncio.run(cmd.callback(it, trade="f:11", event=_choice("PTP"), price="105", tp_pct=None, note=None))
    assert it.followup.sent[-1].startswith("**tp_pct is required**")
    assert env["trades"]["11"]["fills"] == []


def test_fix_list_and_remove(bot_mod, env):
    upd = _sub(bot_mod, "update"); fix = _sub(bot_mod, "fix")
    it = FakeInteraction(FakeUser(uid=1), command=upd)
    asyncio.run(upd.callback(it, trade="f:11", event=_choice("TPN"), price=None, tp_pct=None, note=None))
    it2 = FakeInteraction(FakeUser(uid=1), command=fix)
    asyncio.run(fix.callback(it2, trade="f:11", action=_choice("list"), item=None, price=None, pct=None, note=None))
    assert "`1.` 50% @" in it2.followup.sent[-1]
    it3 = FakeInteraction(FakeUser(uid=1), command=fix)
    asyncio.run(fix.callback(it3, trade="f:11", action=_choice("remove_tp"), item=1, price=None, pct=None, note="fat finger"))
    assert env["trades"]["11"]["fills"] == [] and not env["trades"]["11"]["tp1_hit"]
    assert env["calls"]["thread"][-1].startswith("**Correction** - removed take profit #1")


def test_open_autocomplete_filters_to_own_trades(bot_mod, env):
    cs = env["cs"]
    it = FakeInteraction(FakeUser(uid=1))
    own = asyncio.run(cs.open_any_ac(it, ""))
    assert [c.value for c in own] == ["f:11"]
    it = FakeInteraction(FakeUser(uid=99, admin=True))
    allv = asyncio.run(cs.open_any_ac(it, ""))
    assert sorted(c.value for c in allv) == ["f:11", "s:22"]

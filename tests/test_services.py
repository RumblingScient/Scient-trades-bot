"""Service layer: every trade state change, both markets, no Discord."""
import pytest
from sigma.errors import UserError
from sigma.services import trades as svc
from sigma.services import store


A = {"id": 1, "name": "Scient", "avatar": None, "key": "scient", "color": "#1C4E80"}


def fut(**kw):
    t = {"direction": "LONG", "entry": "100", "sl": "95", "tp1": "110", "tp2": "120", "tp3": "130",
         "tp_split": [50, 30, 20], "fills": [], "closed": False, "entry1_filled": False, "entry2_filled": False,
         "tp1_hit": False, "tp2_hit": False, "tp3_hit": False, "be": False}
    t.update(kw)
    return t


def spot(**kw):
    p = {"kind": "spot", "pair": "FET", "dca_zone": "1.2 - 1.0", "avg_entry": "1.1", "invalidation": "0.9",
         "t1": "1.5", "t2": "2.0", "t3": None, "tp_split": [50, 50], "t1_hit": False, "t2_hit": False, "t3_hit": False,
         "buys": [{"price": 1.1, "pct": None}], "sells": [], "status": "ACCUMULATING", "zone_filled": False, "closed": False}
    p.update(kw)
    return p


# ---------------------------------------------------------------- build

def test_build_futures_defaults_and_direction_check():
    t = svc.build_futures(analyst=A, pair="BTC", direction="LONG", entry="100", stop="95", tp1="110")
    assert t["entry_type"] == "LIMIT" and t["risk"] == "1" and t["entry2"] is None
    t = svc.build_futures(analyst=A, pair="BTC", direction="LONG", entry="100", stop="95", entry2="98", entry_split="20/80")
    assert t["entry_split"] == "20% / 80%"
    with pytest.raises(UserError, match="below entry"):
        svc.build_futures(analyst=A, pair="BTC", direction="LONG", entry="100", stop="105")
    with pytest.raises(UserError, match="above entry"):
        svc.build_futures(analyst=A, pair="BTC", direction="SHORT", entry="100", stop="95")


def test_build_futures_soft_stop_and_tp_split():
    t = svc.build_futures(analyst=A, pair="BTC", direction="SHORT", entry="100", stop="4h close above 105", tp1="90", tp2="80", tp_split="60/40")
    assert t["sl"] == "105" and t["sl_condition"] == "4h close above" and t["tp_split"] == [60, 40]
    with pytest.raises(UserError, match="tp_split"):
        svc.build_futures(analyst=A, pair="BTC", direction="SHORT", entry="100", stop="105", tp1="90", tp_split="60/40/10")


def test_build_spot_infers_play_type():
    p = svc.build_spot(analyst=A, pair="FET", zone="1.2 - 1.0", tp1="1.5")
    assert p["status"] == "ACCUMULATING" and p["avg_entry"] is None and p["buys"] == []
    p = svc.build_spot(analyst=A, pair="FET", zone="1.2 - 1.0", tp1="1.5", avg_entry="1.1")
    assert p["avg_entry"] == "1.1" and p["buys"][0]["price"] == 1.1
    p = svc.build_spot(analyst=A, pair="FET", zone="1.2 - 1.0", tp1="1.5", avg_entry="1.1", play_type="FILLED")
    assert p["status"] == "HOLDING" and p["zone_filled"]


# ---------------------------------------------------------------- events, futures

def test_futures_preset_tp_uses_plan_and_numbers():
    t = fut()
    out = svc.apply_event("fut", t, "TPN")
    assert t["tp1_hit"] and t["fills"] == [{"price": 110.0, "pct": 50.0, "label": "TP1"}]
    assert "TP1" in out.desc and out.title == "TP1 reached"
    svc.apply_event("fut", t, "TPN", price="125")          # all up to price
    assert t["tp2_hit"] and t["fills"][-1]["label"] == "TP2" and t["fills"][-1]["price"] == 125.0
    with pytest.raises(UserError, match="hasn't reached"):
        svc.apply_event("fut", t, "TPN", price="128")


def test_futures_manual_tp_replaces_next_preset_and_needs_pct():
    t = fut()
    with pytest.raises(UserError, match="tp_pct"):
        svc.apply_event("fut", t, "PTP", price="105")
    svc.apply_event("fut", t, "PTP", price="105", pct="25")
    # the manual TP takes preset slot 1 (unified ledger): TP1 counts as done, TP2 still pending
    assert t["fills"][0]["label"] == "TP1" and t["tp1_hit"] and not t["tp2_hit"]
    svc.apply_event("fut", t, "PTP", price="115", pct="25")
    assert t["fills"][1]["label"] == "TP2" and t["tp1_hit"] and t["tp2_hit"] and not t["tp3_hit"]


def test_futures_undo_and_rebuild():
    t = fut()
    svc.apply_event("fut", t, "TPN")
    svc.apply_event("fut", t, "UL")
    assert t["fills"] == [] and not t["tp1_hit"]
    with pytest.raises(UserError):
        svc.apply_event("fut", t, "UL")
    t["tp2_hit"] = True   # corrupt flag
    out = svc.apply_event("fut", t, "RTP")
    assert not t["tp2_hit"] and out.silent


def test_futures_stop_events():
    t = fut()
    svc.apply_event("fut", t, "SLU", price="4h close below 97")
    assert t["sl"] == "97" and t["sl_condition"] == "4h close below"
    with pytest.raises(UserError, match="price is required"):   # soft stop needs an exact close price
        svc.apply_event("fut", t, "SL")
    out = svc.apply_event("fut", t, "SL", price="96")
    assert out.closed and t["result"] == "LOSS" and t["sl_hit"]
    t = fut()
    svc.apply_event("fut", t, "BE")
    out = svc.apply_event("fut", t, "SL")   # BE stop = exit at entry
    assert t["result"] == "BE" and not t["sl_hit"]


def test_futures_close_grades_and_partial_math():
    t = fut()
    svc.apply_event("fut", t, "TPN")                # 50% @ 110
    out = svc.close("fut", t, "120")                # rest @ 120 -> avg 115 -> 3R
    assert out.closed and t["result"] == "WIN" and abs(t["result_r"] - 3.0) < 1e-9 and "+3.00R" in out.result_txt
    t = fut()
    with pytest.raises(UserError, match="price is required"):
        svc.close("fut", t)
    t = fut()
    svc.close("fut", t, "100.2")
    assert t["result"] == "BE"
    with pytest.raises(UserError, match="Already closed"):
        svc.close("fut", t, "100")


def test_futures_invalidated_is_an_update_event():
    t = fut()
    out = svc.apply_event("fut", t, "CI", note="never triggered")
    assert out.closed and t["result"] == "INVALID" and t["close_note"] == "never triggered"


def test_spot_only_events_refused_on_futures_and_vice_versa():
    with pytest.raises(UserError):
        svc.apply_event("spot", spot(), "EF2")
    with pytest.raises(UserError):
        svc.apply_event("spot", spot(), "BE")
    with pytest.raises(UserError):
        svc.apply_event("spot", spot(), "SL")


# ---------------------------------------------------------------- events, spot

def test_spot_buy_recalculates_average():
    p = spot(buys=[{"price": 1.2, "pct": 50}])
    out = svc.apply_event("spot", p, "EF1", price="1.0", pct="50")
    assert p["avg_entry"] == "1.1" and "avg 1.1" in out.desc
    out = svc.apply_event("spot", p, "EF1")      # no price = zone filled
    assert p["zone_filled"] and p["status"] == "HOLDING"


def test_spot_preset_tp_and_auto_close_at_100():
    p = spot()
    svc.apply_event("spot", p, "TPN")
    assert p["t1_hit"] and p["sells"][0] == {"pct": 50.0, "price": 1.5, "label": "TP1"}
    out = svc.apply_event("spot", p, "TPN", price="2.1")     # last 50% -> bag fully sold -> auto close
    assert out.closed and p["closed"] and p["result"] == "WIN"
    assert p["close_note"] == "Bag fully sold - closed automatically"
    # avg exit 1.8, entry 1.1, inv 0.9 -> R = 0.7 / 0.2 = 3.5
    assert abs(p["result_r"] - 3.5) < 1e-9


def test_spot_close_price_only_and_pct_fallback():
    p = spot()
    svc.apply_event("spot", p, "PTP", price="1.4", pct="40")
    with pytest.raises(UserError, match="still held"):
        svc.close("spot", p)
    out = svc.close("spot", p, "1.3")
    assert p["closed"] and p["result"] == "WIN" and p["sells"][-1]["label"] == "close"
    # no invalidation -> graded by % with the 0.5% band
    p = spot(invalidation=None)
    svc.close("spot", p, "1.104")
    assert p["result"] == "BE" and p["result_r"] is None
    p = spot(invalidation=None)
    svc.close("spot", p, "1.2")
    assert p["result"] == "WIN" and p["result_pct"] == "+9.1%"


def test_spot_never_filled_must_be_invalidated_not_closed():
    p = spot(avg_entry=None, buys=[])
    with pytest.raises(UserError, match="Invalidated"):
        svc.close("spot", p, "1.0")
    out = svc.apply_event("spot", p, "CI")
    assert out.closed and p["result"] == "INVALID" and p["status"] == "INVALIDATED"
    p = spot()
    with pytest.raises(UserError, match="had fills"):
        svc.apply_event("spot", p, "CI")


# ---------------------------------------------------------------- edit / fix / reopen

def test_edit_validates_direction_after_change():
    t = fut()
    ch = svc.edit("fut", t, {"stop": "97", "tp1": "111"})
    assert ch == ["stop", "TP1"] and t["sl"] == "97" and t["edited"]
    with pytest.raises(UserError, match="below entry"):
        svc.edit("fut", t, {"stop": "101"})
    with pytest.raises(UserError, match="Nothing to change"):
        svc.edit("fut", t, {})
    p = spot()
    ch = svc.edit("spot", p, {"avg_entry": "1.05", "status": "HOLDING", "tp_split": "30/70"})
    assert p["avg_entry"] == "1.05" and p["status"] == "HOLDING" and p["tp_split"] == [30, 70]


def test_fix_fill_both_markets():
    t = fut()
    svc.apply_event("fut", t, "TPN")
    svc.apply_event("fut", t, "PTP", price="125", pct="30")
    assert "`2.`" in svc.ledger_text("fut", t)
    ch = svc.fix_fill("fut", t, "fix_tp", 2, price="124", pct="20")
    assert "fixed take profit #2" in ch and t["fills"][1]["price"] == 124.0 and t["fills"][1]["pct"] == 20.0
    svc.fix_fill("fut", t, "remove_tp", 1)
    # one fill left -> it takes slot 1; its price 124 also reaches TP2; TP3 untouched
    assert len(t["fills"]) == 1 and t["tp1_hit"] and t["tp2_hit"] and not t["tp3_hit"]
    with pytest.raises(UserError, match="Pick which one"):
        svc.fix_fill("fut", t, "remove_tp", 9)
    with pytest.raises(UserError, match="setup edit"):
        svc.fix_fill("fut", t, "remove_buy", 1)
    p = spot(buys=[{"price": 1.2, "pct": 50}, {"price": 1.0, "pct": 50}])
    svc.fix_fill("spot", p, "fix_buy", 2, price="0.8")
    assert p["avg_entry"] == "1"
    svc.fix_fill("spot", p, "remove_buy", 1)
    assert p["avg_entry"] == "0.8"


def test_reopen_reverses_close():
    t = fut()
    svc.close("fut", t, "120")
    svc.reopen("fut", t)
    assert not t["closed"] and "result" not in t and t["fills"] == [] and t["watch_disabled"]
    p = spot()
    svc.close("spot", p, "1.3")
    svc.reopen("spot", p)
    assert not p["closed"] and p["sells"] == [] and "result_r" not in p
    with pytest.raises(UserError):
        svc.reopen("spot", p)


# ---------------------------------------------------------------- store ids

def test_store_ids():
    assert store.split_id("f:12") == ("fut", "12") and store.split_id("s:12") == ("spot", "12") and store.split_id("12") == (None, "12")
    assert store.make_id("spot", "5") == "s:5" and store.make_id("fut", 5) == "f:5"

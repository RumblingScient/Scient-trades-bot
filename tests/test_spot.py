import pytest

def P(**kw):
    p = {"avg_entry": "1.65", "invalidation": "1D close below 1.52", "sells": [], "buys": []}
    p.update(kw); return p

def test_spot_r_from_invalidation(bot_mod):
    assert bot_mod.spot_signed_r(P(), 2.26) == pytest.approx((2.26 - 1.65) / 0.13)

def test_spot_result_r_computed_when_missing(bot_mod):
    p = P(avg_exit="3.4", result="WIN")
    assert bot_mod.spot_result_r(p) == pytest.approx(round((3.4 - 1.65) / 0.13, 2))

def test_spot_not_graded_without_numeric_invalidation(bot_mod):
    assert bot_mod.spot_result_r(P(invalidation=None, avg_exit="3.4", result="WIN")) is None

def test_spot_invalid_has_no_r(bot_mod):
    assert bot_mod.spot_result_r(P(result="INVALID")) is None

def test_weighted_buys_and_sells(bot_mod):
    p = P(buys=[{"price": 70, "pct": 40}, {"price": 60, "pct": 60}],
          sells=[{"price": 100, "pct": 50}, {"price": 120, "pct": 50}])
    assert bot_mod.spot_weighted_entry(p) == pytest.approx(64)
    assert bot_mod.spot_weighted_exit(p) == pytest.approx(110)

def test_zone_projection_blends_logged_buys(bot_mod):
    p = P(dca_zone="65 - 52", buys=[{"price": 68, "pct": 40}])
    assert bot_mod.spot_zone_projection(p) == pytest.approx(0.4 * 68 + 0.6 * 58.5)

def test_unified_ledger_manual_replaces_preset(bot_mod):
    p = P(t1="2.05", t2="5.4", t3="8.4", tp_split=[30, 30, 40], sells=[{"pct": 20, "price": 1.9}])
    bot_mod._sync_tp_flags(p, spot=True)
    assert p["t1_hit"] and not p["t2_hit"] and not p["t3_hit"]
    txt = bot_mod.unified_tp_text(p, spot=True)
    assert "TP1" in txt and "1.9" in txt and "5.4" in txt and "2.05" not in txt

def test_parse_spot_split_rules(bot_mod):
    assert bot_mod.parse_spot_split("30/30/40", 3) == ([30.0, 30.0, 40.0], None)
    assert bot_mod.parse_spot_split("50/50", 3)[1] is None          # fewer parts ok (moonbag)
    assert bot_mod.parse_spot_split("60/60", 3)[1] is not None      # >100 rejected
    assert bot_mod.parse_spot_split("10/10/10/10", 3)[1] is not None

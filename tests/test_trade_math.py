import pytest

def T(direction="LONG", entry="100", sl="95", fills=None, **kw):
    t = {"direction": direction, "entry": entry, "sl": sl, "fills": fills or []}
    t.update(kw); return t

def test_long_r_positive_and_negative(bot_mod):
    t = T()
    assert bot_mod.signed_r(t, 110) == pytest.approx(2.0)
    assert bot_mod.signed_r(t, 95) == pytest.approx(-1.0)

def test_short_r(bot_mod):
    t = T("SHORT", "100", "105")
    assert bot_mod.signed_r(t, 90) == pytest.approx(2.0)
    assert bot_mod.signed_r(t, 105) == pytest.approx(-1.0)

def test_partial_then_close_weighted_exit(bot_mod):
    t = T(fills=[{"price": 110, "pct": 50, "label": "TP1"}])
    ae, r = bot_mod.finalize_close(t, 120)
    assert ae == pytest.approx(115) and r == pytest.approx(3.0)

def test_full_close_no_partials(bot_mod):
    ae, r = bot_mod.finalize_close(T(), 90)
    assert ae == pytest.approx(90) and r == pytest.approx(-2.0)

def test_breakeven_after_partial(bot_mod):
    ae, r = bot_mod.finalize_close(T(fills=[{"price": 103, "pct": 30, "label": "TP1"}]), 100)
    assert r == pytest.approx(0.18)

def test_dca_weighted_entry_default_and_split(bot_mod):
    d = T(entry="100", entry2="90", entry1_filled=True, entry2_filled=True, sl="85")
    assert bot_mod.entry_num(d) == pytest.approx(95)
    d["entry_split"] = "20% / 80%"
    assert bot_mod.entry_num(d) == pytest.approx(92)

def test_dca_only_first_leg_filled_uses_entry1(bot_mod):
    d = T(entry="100", entry2="90", entry1_filled=True, entry2_filled=False, sl="85")
    assert bot_mod.entry_num(d) == pytest.approx(100)

def test_tiny_and_huge_prices(bot_mod):
    tiny = T(entry="0.000012", sl="0.000010")
    assert bot_mod.signed_r(tiny, 0.000016) == pytest.approx(2.0)
    huge = T(entry="108200", sl="106800")
    assert bot_mod.signed_r(huge, 111000) == pytest.approx(2.0)

def test_parse_sl_soft_condition(bot_mod):
    assert bot_mod.parse_sl("4h close below 63000") == ("63000", "4h close below")
    assert bot_mod.parse_sl("63,000") == ("63000", None)
    assert bot_mod.parse_sl("") == (None, None)

def test_fills_pct_and_remaining(bot_mod):
    t = T(fills=[{"price": 1, "pct": 25}, {"price": 1, "pct": 30}])
    assert bot_mod.fills_pct(t) == pytest.approx(55)

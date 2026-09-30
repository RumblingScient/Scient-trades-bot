import asyncio, pytest

def run(t, candles, bot_mod):
    return asyncio.run(bot_mod._pw_process_trade("x", t, candles))

def base(**kw):
    t = {"direction": "LONG", "entry": "100", "sl": "95", "entry_type": "MARKET", "entry1_filled": True,
         "tp1": "110", "tp2": "120", "tp_split": [50, 50], "fills": []}
    t.update(kw); return t

def test_tp_hit_records_planned_pct(bot_mod):
    t = base(); ev = run(t, [(1_700_000_000_000, 111, 101, 110)], bot_mod)
    assert t["tp1_hit"] and t["fills"][0]["pct"] == 50 and not t.get("closed")

def test_hard_sl_closes_at_stop(bot_mod):
    t = base(); run(t, [(1_700_000_000_000, 101, 94, 96)], bot_mod)
    assert t["closed"] and t["result"] == "LOSS" and t["avg_exit"] == pytest.approx(95)

def test_be_uses_entry_as_stop(bot_mod):
    t = base(be=True); run(t, [(1_700_000_000_000, 101, 100, 100.5)], bot_mod)
    assert t["closed"] and t["result"] == "BE" and t["avg_exit"] == pytest.approx(100)

def test_be_not_triggered_while_above_entry(bot_mod):
    t = base(be=True); run(t, [(1_700_000_000_000, 103, 101, 102)], bot_mod)
    assert not t.get("closed")

def test_soft_sl_only_pings(bot_mod):
    t = base(sl_condition="4h close below"); ev = run(t, [(1_700_000_000_000, 101, 94, 96)], bot_mod)
    assert not t.get("closed") and any("soft" in e[0].lower() for e in ev)

def test_ambiguous_candle_tp_and_sl_pauses_instead_of_guessing(bot_mod):
    t = base(); ev = run(t, [(1_700_000_000_000, 111, 94, 100)], bot_mod)   # one candle touches TP1 (110) and SL (95)
    assert not t.get("closed") and not t.get("tp1_hit") and t.get("watch_disabled")
    assert any("Ambiguous" in e[0] for e in ev)

def test_ambiguous_entry_and_sl_pauses(bot_mod):
    t = base(entry_type="LIMIT", entry1_filled=False); ev = run(t, [(1_700_000_000_000, 101, 94, 96)], bot_mod)
    assert not t.get("closed") and not t.get("entry1_filled") and t.get("watch_disabled")

def test_limit_entry_fills_then_tp_next_candle(bot_mod):
    t = base(entry_type="LIMIT", entry1_filled=False)
    run(t, [(1_700_000_000_000, 102, 99.5, 100.2), (1_700_000_060_000, 112, 100.5, 111)], bot_mod)
    assert t["entry1_filled"] and t["tp1_hit"]

def test_short_tp_and_sl_directions(bot_mod):
    t = base(direction="SHORT", entry="100", sl="105", tp1="90", tp2="80")
    run(t, [(1_700_000_000_000, 99, 89, 90)], bot_mod); assert t["tp1_hit"]
    t2 = base(direction="SHORT", entry="100", sl="105", tp1="90", tp2="80")
    run(t2, [(1_700_000_000_000, 106, 99, 104)], bot_mod); assert t2["closed"] and t2["result"] == "LOSS"

def test_symbol_normalisation(bot_mod):
    f = bot_mod._pw_symbol
    assert f("BTCUSDT.P") == "BTCUSDT" and f("BINANCE:ETHUSDT.P") == "ETHUSDT"
    assert f("1000PEPE/USDT") == "1000PEPEUSDT" and f("btcusd") == "BTCUSDT" and f("sui") == "SUIUSDT"

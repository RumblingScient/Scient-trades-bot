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


def test_tracker_tp_and_stop_go_through_services(bot_mod):
    """Tracker fills and closes must produce exactly what /setup update would."""
    import asyncio
    from sigma.services import trades as svc
    base = 1_700_000_000_000
    mk = lambda i, hi, lo: (base + i * 60_000, hi, lo, (hi + lo) / 2)
    # TP1 (plan 50%) then hard stop -> services label + grading
    t = {"direction": "LONG", "entry": "100", "sl": "95", "tp1": "110", "tp2": "120", "tp_split": [50, 50],
         "fills": [], "closed": False, "entry_type": "MARKET", "entry1_filled": True, "entry2_filled": False,
         "tp1_hit": False, "tp2_hit": False, "be": False, "pair": "BTC"}
    ev = asyncio.run(bot_mod._pw_process_trade("1", t, [mk(0, 111, 105), mk(1, 100, 94)]))
    assert t["fills"][0] == {"price": 110.0, "pct": 50.0, "label": "TP1"} and t["tp1_hit"]
    assert t["closed"] and t["result_r"] == pytest.approx(0.5) and t["result"] == "WIN"   # avg exit 102.5 -> +0.5R
    assert t["close_note"] == "Auto-tracked stop" and t["sl_hit"]
    assert ev[0][0].startswith("TP1 reached") and ev[-1][0].startswith("Closed - Win")
    # same inputs through the command path give the same record
    u = {k: v for k, v in t.items() if k not in ("closed", "result", "result_r", "avg_exit", "closed_at", "close_note", "sl_hit")}
    u.update({"fills": [], "tp1_hit": False, "closed": False})
    svc.apply_event("fut", u, "TPN"); svc.apply_event("fut", u, "SL", note="Auto-tracked stop")
    for k in ("fills", "result", "result_r", "avg_exit", "tp1_hit", "sl_hit"):
        assert u[k] == t[k], k
    # no plan % -> level is tagged, nothing recorded, trade stays open
    t2 = {"direction": "SHORT", "entry": "100", "sl": "105", "tp1": "90", "fills": [], "closed": False,
          "entry_type": "MARKET", "entry1_filled": True, "tp1_hit": False, "be": False, "pair": "ETH"}
    ev = asyncio.run(bot_mod._pw_process_trade("2", t2, [mk(0, 95, 89)]))
    assert t2["tp1_hit"] and t2["fills"] == [] and not t2["closed"] and "tagged" in ev[0][0]


def test_rereading_the_same_candle_never_double_fires(bot_mod):
    """The cursor re-reads the forming candle every tick - every handler must be flag-guarded."""
    c = (1_700_000_000_000, 102, 99.5, 100.2)
    t = base(entry_type="LIMIT", entry1_filled=False)
    ev1 = run(t, [c], bot_mod); ev2 = run(t, [c], bot_mod)
    assert t["entry1_filled"] and len(ev1) == 1 and ev2 == []
    c = (1_700_000_060_000, 111, 101, 110)
    ev1 = run(t, [c], bot_mod); ev2 = run(t, [c], bot_mod)
    assert len(t["fills"]) == 1 and t["fills"][0]["pct"] == 50 and len(ev1) == 1 and ev2 == []
    c = (1_700_000_120_000, 101, 94, 96)
    ev1 = run(t, [c], bot_mod); ev2 = run(t, [c], bot_mod)
    assert t["closed"] and len(t["fills"]) == 1 and t["avg_exit"] == pytest.approx(102.5) and len(ev1) == 1 and ev2 == []
    # soft stop warning: once per 4h, not once per tick
    t2 = base(sl_condition="4h close below")
    c = (1_700_000_000_000, 101, 94, 96)
    ev1 = run(t2, [c], bot_mod); ev2 = run(t2, [c], bot_mod)
    assert len(ev1) == 1 and ev2 == [] and not t2.get("closed")


def test_dca_leg_two_and_stop_in_one_candle_is_ambiguous(bot_mod):
    """Entry 1 live, entry 2 pending: a candle touching both entry 2 and the stop pauses instead of booking a doubled loss."""
    t = base(entry_type="LIMIT", entry1_filled=True, entry2="97", entry2_filled=False)
    ev = run(t, [(1_700_000_000_000, 101, 94, 96)], bot_mod)
    assert not t.get("closed") and not t["entry2_filled"] and t.get("watch_disabled")
    assert any("Ambiguous" in e[0] and "the entry" in e[2] for e in ev)
    # entry 2 alone (stop untouched) still fills
    t = base(entry_type="LIMIT", entry1_filled=True, entry2="97", entry2_filled=False)
    run(t, [(1_700_000_000_000, 101, 96.5, 98)], bot_mod)
    assert t["entry2_filled"] and not t.get("watch_disabled")


def test_fully_taken_position_never_takes_the_stop_path(bot_mod):
    t = base(tp_split=[50, 50])
    run(t, [(1_700_000_000_000, 121, 101, 120)], bot_mod)   # both targets in one candle -> 100% taken -> auto-closed
    assert t["closed"] and t["result"] == "WIN" and t["close_note"].startswith("Position fully closed")
    # a legacy record: 100% in fills but never closed - a stop candle must not grade it as a stop-out
    t = base(fills=[{"price": 110, "pct": 100, "label": "TP1"}], tp1_hit=True, tp2_hit=True)
    ev = run(t, [(1_700_000_000_000, 101, 94, 96)], bot_mod)
    assert not t.get("closed") and ev == []


def test_tick_cursor_rereads_the_forming_candle_and_flags_bad_symbols(bot_mod, monkeypatch):
    import asyncio
    import sigma.tracker as tr
    c0, c1 = (1_700_000_000_000, 101, 99, 100), (1_700_000_060_000, 102, 99, 101)
    trades = {"1": base(pair="BTC", watch_ms=c0[0], watch_verified=True),
              "2": base(pair="XYZ", watch_ms=c0[0], watch_verified=True),
              "3": base(pair="XYZ", watch_ms=c0[0], watch_verified=True, watch_disabled=True)}
    feed = []
    async def klines(sym, since):
        return "BAD_SYMBOL" if sym == "XYZUSDT" else [c0, c1]
    async def announce(t, title, color, line): feed.append((t["pair"], title))
    async def noop(*a, **k): pass
    monkeypatch.setattr(tr, "_pw_klines", klines)
    monkeypatch.setattr(tr, "_pw_announce", announce)
    monkeypatch.setattr(tr, "post_update_feed", noop)
    monkeypatch.setattr(tr, "refresh_and_edit", noop)
    monkeypatch.setattr(tr, "refresh_board", noop)
    monkeypatch.setattr(tr, "load_trades", lambda: trades)
    monkeypatch.setattr(tr, "save_trades", lambda d: trades.update(d))
    tr._pw_unsupported.discard("XYZUSDT")
    asyncio.run(tr._price_watch_tick())
    assert trades["1"]["watch_ms"] == c1[0]                      # last (forming) candle is read again next tick
    assert trades["2"]["watch_disabled"] and trades["2"]["watch_reason"] == "symbol not on Binance futures"
    assert feed == [("XYZ", "Auto-tracking disabled")]           # once, for the live record only
    assert "watch_reason" not in trades["3"]
    tr._pw_unsupported.discard("XYZUSDT")

"""Boot and health: loops survive a failing boot step, self-heal backs off, the watchdog sees stale loops,
startup validation reports instead of raising, and the .env parser accepts the real-world forms."""
import asyncio
import time as _time
import pytest


@pytest.fixture
def alerts(bot_mod, monkeypatch):
    """Capture ops_alert calls (text, key) in every module that imports it."""
    got = []
    async def _alert(text, key=None, cooldown=600): got.append((text, key))
    import sigma.app as app, sigma.health as health
    monkeypatch.setattr(app, "ops_alert", _alert)
    monkeypatch.setattr(health, "ops_alert", _alert)
    return got


def test_on_ready_starts_every_loop_even_when_a_boot_step_raises(bot_mod, alerts, monkeypatch):
    import sigma.app as app, sigma.health as health, sigma.actions as actions
    async def _sync(*a, **k): pass
    async def _board(): raise RuntimeError("board channel gone")
    async def _spot(): pass
    async def _cards(): pass
    async def _catchup(): return False
    async def _never_ready(): await asyncio.Event().wait()     # loops park in before_loop like on a real boot
    monkeypatch.setattr(app.bot, "wait_until_ready", _never_ready)
    monkeypatch.setattr(app.bot.tree, "sync", _sync)
    monkeypatch.setattr(app, "refresh_board", _board)
    monkeypatch.setattr(app, "refresh_spot_board", _spot)
    monkeypatch.setattr(app, "monthly_catchup", _catchup)
    monkeypatch.setattr(app, "_validate_startup", lambda: [])
    monkeypatch.setattr(app, "LIQ_CHANNEL_ID", 0)
    monkeypatch.setattr(app, "load_payments", lambda: {})
    monkeypatch.setattr(actions, "ensure_card_views", _cards)
    for attr in ("_synced", "_card_views_started"):
        if hasattr(app.bot, attr):
            delattr(app.bot, attr)

    async def run():
        await app.on_ready()
        await asyncio.sleep(0.01)                                   # let every loop task actually start
        return [(nm, lp.is_running()) for nm, lp, on in health._loops() if on]
    states = asyncio.run(run())
    assert states and all(running for _, running in states), states
    assert any("refresh_board" in t for t, k in alerts if k == "boot:failed")
    assert any(k == "boot" and "loops running=" in t for t, k in alerts)
    assert health.BOOT["done"] and health.BOOT["sync"] == "ok" and app.bot._synced
    # a second on_ready (reconnect) does not sync again and does not fail
    calls = {"n": 0}
    async def _sync2(*a, **k): calls["n"] += 1
    monkeypatch.setattr(app.bot.tree, "sync", _sync2)
    asyncio.run(app.on_ready())
    assert calls["n"] == 0


def test_self_heal_backoff_grows_and_caps(bot_mod):
    import sigma.health as health
    health._HEAL.pop("demo", None)
    now = _time.time()
    delays = [health._heal_delay("demo", now + i) for i in range(8)]
    assert delays == [30, 60, 120, 240, 480, 960, 1800, 1800]
    # a quiet hour resets the streak
    assert health._heal_delay("demo", now + 10_000) == 30


def test_self_heal_reruns_a_time_loop_body_once_then_restarts(bot_mod, alerts, monkeypatch):
    import sigma.health as health
    async def _nosleep(_): pass
    monkeypatch.setattr(health.asyncio, "sleep", _nosleep)
    calls = {"coro": 0, "restart": 0, "err": None}
    class FakeLoop:
        time = [None]                        # a time= loop
        async def coro(self): calls["coro"] += 1
        def restart(self): calls["restart"] += 1
        def error(self, fn): calls["err"] = fn
    lp = FakeLoop()
    health._HEAL.pop("fake", None)
    health._self_heal(lp, "fake")
    asyncio.run(calls["err"](RuntimeError("boom")))
    assert calls["coro"] == 1 and calls["restart"] == 1
    assert any(k == "loop:fake" for _, k in alerts)


def test_watchdog_alerts_on_stale_heartbeat_and_restarts_dead_loop(bot_mod, alerts, monkeypatch):
    import sigma.health as health
    class FakeLoop:
        time = None; seconds = 0.0; minutes = 3.0; hours = 0.0
        def __init__(self, running): self._r = running; self.started = 0
        def is_running(self): return self._r
        def start(self): self.started += 1
    stale, dead, fresh, off = FakeLoop(True), FakeLoop(False), FakeLoop(True), FakeLoop(False)
    monkeypatch.setattr(health, "_loops", lambda: (("alerts", stale, True), ("funding", dead, True),
                                                   ("x_poll", fresh, True), ("tg_move", off, False)))
    now = _time.time()
    health.HEARTBEAT["alerts"] = now - 3 * 180 - 5
    health.HEARTBEAT["x_poll"] = now - 10
    asyncio.run(health.watchdog_loop.coro())
    keys = {k for _, k in alerts}
    assert "watchdog:alerts" in keys and "watchdog:funding" in keys
    assert "watchdog:x_poll" not in keys and "watchdog:tg_move" not in keys
    assert dead.started == 1 and off.started == 0
    assert health.BOOT["watchdog_last"] >= now


def test_validate_startup_reports_bad_wallet_without_raising(bot_mod, monkeypatch):
    import sigma.health as health
    class _Bot:
        def get_channel(self, cid): return None
        def get_guild(self, gid): return None
    monkeypatch.setattr(health, "bot", _Bot())
    monkeypatch.setattr(health, "SOL_WALLET", "0OIl-not-base58")
    probs = health._validate_startup()
    assert any("SIGMA_SOL_WALLET" in p and "base58" in p for p in probs)
    monkeypatch.setattr(health, "SOL_WALLET", "1111")                      # valid base58, wrong length
    assert any("not 32" in p for p in health._validate_startup())
    monkeypatch.setattr(health, "SOL_WALLET", "11111111111111111111111111111111")   # 32 zero bytes
    assert not any("SIGMA_SOL_WALLET" in p for p in health._validate_startup())
    monkeypatch.setattr(health, "OPS_CHANNEL_ID", 0); monkeypatch.setattr(health, "MOD_LOG_CHANNEL_ID", 0)
    assert any("SIGMA_OPS_CHANNEL_ID" in p for p in health._validate_startup())


def test_b58decode_roundtrip(bot_mod):
    import sigma.health as health
    assert health._b58decode("StV1DL6CwTryKyV") == b"hello world"
    assert health._b58decode("11") == b"\x00\x00"


def test_env_parser_quotes_export_and_ints(bot_mod, monkeypatch):
    import sigma.config as cfg
    assert cfg._parse_env_line('export SIGMA_SOL_WALLET="abc"') == ("SIGMA_SOL_WALLET", "abc")
    assert cfg._parse_env_line("KEY='v=1'") == ("KEY", "v=1")
    assert cfg._parse_env_line("  KEY = plain  ") == ("KEY", "plain")
    assert cfg._parse_env_line("# KEY=1") is None and cfg._parse_env_line("") is None and cfg._parse_env_line("novalue") is None
    assert cfg._parse_env_line('KEY="unbalanced') == ("KEY", '"unbalanced')
    monkeypatch.setenv("SIGMA_T_INT", " 42 ")
    assert cfg._env_int("SIGMA_T_INT") == 42
    monkeypatch.setenv("SIGMA_T_INT", "")
    assert cfg._env_int("SIGMA_T_INT", 7) == 7
    monkeypatch.setenv("SIGMA_T_INT", "12x")
    with pytest.raises(ValueError, match="SIGMA_T_INT"):
        cfg._env_int("SIGMA_T_INT")


def test_backup_writes_under_backup_dir_and_reports_age(bot_mod, monkeypatch, tmp_path):
    import sigma.jobs as jobs
    src = tmp_path / "root"; src.mkdir(); (src / "a.json").write_text("{}"); (src / "b.json").write_text("{}")
    monkeypatch.setattr(jobs, "_ROOT", src)
    monkeypatch.setattr(jobs, "BACKUP_DIR", tmp_path / "nested" / "backups")
    assert jobs.last_backup_age() is None
    assert jobs._backup_once() == 2
    assert len(list((tmp_path / "nested" / "backups").iterdir())) == 1
    assert jobs.last_backup_age() is not None and jobs.last_backup_age() < 5


def test_backup_loop_alerts_when_nothing_copied(bot_mod, monkeypatch, tmp_path):
    import sigma.jobs as jobs
    got = []
    async def _alert(text, key=None, cooldown=600): got.append(key)
    monkeypatch.setattr(jobs, "ops_alert", _alert)
    monkeypatch.setattr(jobs, "_ROOT", tmp_path / "empty"); (tmp_path / "empty").mkdir()
    monkeypatch.setattr(jobs, "BACKUP_DIR", tmp_path / "bk")
    asyncio.run(jobs.backup_loop.coro())
    assert got == ["backup"]


def test_binance_429_backs_off_to_bybit(bot_mod, monkeypatch):
    import sigma.market_data as md
    got = []
    async def _alert(text, key=None, cooldown=600): got.append(key)
    monkeypatch.setattr(md, "ops_alert", _alert)
    monkeypatch.setattr(md, "_binance_until", 0.0)
    calls = []
    class _Resp:
        def __init__(self, status, headers=None, body=None): self.status, self.headers, self._b = status, headers or {}, body
        async def json(self): return self._b
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class _Sess:
        def get(self, url, params=None, timeout=15):
            calls.append(url)
            if "binance" in url:
                return _Resp(429, {"Retry-After": "120"})
            return _Resp(200, body={"result": {"list": [{"lastPrice": "5"}]}})
    class _Http:
        async def __aenter__(self): return _Sess()
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(md, "http", lambda: _Http())
    assert asyncio.run(md.md_price("BTCUSDT")) == 5.0
    assert got == ["binance:429"] and md._binance_until > _time.time() + 100
    calls.clear()
    assert asyncio.run(md.md_price("BTCUSDT")) == 5.0
    assert all("bybit" in u for u in calls)      # Binance skipped entirely inside the penalty window

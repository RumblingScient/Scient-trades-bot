"""X feed: the same tweet must never be posted twice, even if the seen-file cannot be saved."""
import asyncio
import pytest
from sigma import xfeed


class _Msg:
    def __init__(self, author, content): self.author, self.content = author, content


class _Chan:
    def __init__(self):
        self.sent = []
    async def send(self, content=None, **kw):
        self.sent.append(content)
    def history(self, limit=50):
        msgs = [_Msg(xfeed.bot.user, c) for c in reversed(self.sent)]
        async def gen():
            for m in msgs[:limit]:
                yield m
        return gen()


class _Resp:
    status = 200
    def __init__(self, data): self._d = data
    async def json(self): return self._d
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Session:
    def __init__(self, data): self._d = data
    def get(self, *a, **k): return _Resp(self._d)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


@pytest.fixture
def env(monkeypatch):
    chan = _Chan()
    store = {"ids": ["1"], "initialized": True}
    saves = {"ok": True, "n": 0}
    monkeypatch.setattr(xfeed, "X_AUTO_ENABLED", True)
    monkeypatch.setattr(xfeed, "TWITTERAPIS_KEY", "k")
    monkeypatch.setattr(xfeed, "X_FEED_CHANNEL_ID", 5)
    monkeypatch.setattr(xfeed, "X_AUTO_USERNAME", "Crypto_Scient")
    monkeypatch.setattr(xfeed, "X_PING_ROLE_ID", 0)
    class _Bot:
        user = object()
        def get_channel(self, cid): return chan
    monkeypatch.setattr(xfeed, "bot", _Bot())
    monkeypatch.setattr(xfeed, "http", lambda: _Session({"tweets": [{"id": 2, "author": {"username": "Crypto_Scient"}}]}))
    monkeypatch.setattr(xfeed, "load_xseen", lambda: dict(store))
    def save(d):
        saves["n"] += 1
        if not saves["ok"]:
            raise RuntimeError("x_posted.json is marked corrupt")
        store.clear(); store.update(d)
    monkeypatch.setattr(xfeed, "save_xseen", save)
    monkeypatch.setattr(xfeed, "fix_x_link", lambda l: l)
    xfeed._POSTED.clear()
    return chan, store, saves


def test_new_tweet_posted_once_and_persisted(env):
    chan, store, saves = env
    asyncio.run(xfeed.x_poll_loop.coro())
    asyncio.run(xfeed.x_poll_loop.coro())
    assert chan.sent == ["https://x.com/Crypto_Scient/status/2"] and "2" in store["ids"] and store["ids"][-1] == "2"


def test_no_repost_when_seen_file_cannot_be_saved(env):
    chan, store, saves = env
    saves["ok"] = False
    for _ in range(6):                              # six polls, the bug's exact pattern
        asyncio.run(xfeed.x_poll_loop.coro())
    assert chan.sent == ["https://x.com/Crypto_Scient/status/2"]


def test_state_lost_but_message_in_channel_is_not_reposted(env):
    chan, store, saves = env
    chan.sent.append("https://x.com/Crypto_Scient/status/2")   # bot posted it before a restart; seen-file lost it
    xfeed._POSTED.clear()
    asyncio.run(xfeed.x_poll_loop.coro())
    assert len(chan.sent) == 1 and "2" in store["ids"]


class _StatusResp(_Resp):
    def __init__(self, status, data=None): super().__init__(data); self.status = status


def test_non_dict_body_is_ignored_not_crashed(env, monkeypatch):
    chan, store, saves = env
    monkeypatch.setattr(xfeed, "http", lambda: _Session(["not", "a", "dict"]))
    xfeed._FAIL["n"] = 0
    asyncio.run(xfeed.x_poll_loop.coro())
    assert chan.sent == [] and saves["n"] == 0 and xfeed._FAIL["n"] == 1


def test_three_failed_polls_alert_once_and_success_resets(env, monkeypatch):
    chan, store, saves = env
    got = []
    async def _alert(text, key=None, cooldown=600): got.append(key)
    monkeypatch.setattr(xfeed, "ops_alert", _alert)
    xfeed._FAIL["n"] = 0
    sess = _Session(None); sess.get = lambda *a, **k: _StatusResp(402)       # payment required: key out of credit
    monkeypatch.setattr(xfeed, "http", lambda: sess)
    for _ in range(5):
        asyncio.run(xfeed.x_poll_loop.coro())
    assert got == ["xfeed"] and chan.sent == [] and xfeed._FAIL["n"] == 5
    monkeypatch.setattr(xfeed, "http", lambda: _Session({"tweets": [{"id": 2, "author": {"username": "Crypto_Scient"}}]}))
    asyncio.run(xfeed.x_poll_loop.coro())
    assert xfeed._FAIL["n"] == 0 and "x_poll" in xfeed.HEARTBEAT and len(chan.sent) == 1

import json, tempfile, pathlib

def test_save_keeps_bak_and_refuses_after_corruption(bot_mod):
    p = pathlib.Path(tempfile.mkdtemp()) / "t.json"
    bot_mod._save(p, {"a": 1}); bot_mod._save(p, {"a": 2})
    assert json.loads(p.with_suffix(".json.bak").read_text()) == {"a": 1}
    p.write_text("{corrupt")
    assert bot_mod._load(p) == {"a": 1}                              # served from .bak ...
    assert json.loads(p.read_text()) == {"a": 1}                      # ... and restored in place
    assert p.with_suffix(".json.corrupt").read_text() == "{corrupt"   # bad copy kept for forensics
    bot_mod._save(p, {"a": 3})                                        # writes continue - no wedge
    assert bot_mod._load(p) == {"a": 3}
    # no .bak at all -> refuse writes until a human looks
    q = p.parent / "u.json"
    q.write_text("{corrupt")
    assert bot_mod._load(q) == {}
    refused = False
    try:
        bot_mod._save(q, {"a": 3})
    except RuntimeError:
        refused = True
    assert refused
    q.write_text('{"a": 9}')
    assert bot_mod._load(q) == {"a": 9} and str(q) not in bot_mod._CORRUPT   # fixed by hand -> flag clears itself
    # main file vanished (crash between renames) but .bak is there -> served and put back
    p.unlink()
    assert bot_mod._load(p) == {"a": 1} and p.exists()        # .bak = the copy taken before the last save

def test_stats_window_semantics(bot_mod):
    s, e, lab = bot_mod._stats_window(-1); assert s is not None and e is not None
    s, e, lab = bot_mod._stats_window(-2); assert "so far" in lab
    s, e, lab = bot_mod._stats_window(0); assert s is None and lab == "All time"

def test_fit_lines_respects_discord_field_cap(bot_mod):
    v = bot_mod._fit_lines([f"line {i} " + "x" * 60 for i in range(40)])
    assert len(v) <= 1024 and "more" in v

def test_every_command_within_discord_limits(bot_mod):
    for c in bot_mod.bot.tree.get_commands():
        assert len(c.name) <= 32 and len(c.description or "") <= 100, c.name
        for prm in getattr(c, "parameters", []) or []:
            assert len(prm.description or "") <= 100, (c.name, prm.name)
            assert len(getattr(prm, "choices", []) or []) <= 25, (c.name, prm.name)
    assert len(bot_mod.bot.tree.get_commands()) <= 100


def _tg_html(ids):
    return "".join(f'<div data-post="{pid}"><div class="tgme_widget_message_text">headline number {pid} long enough</div></div>' for pid in ids)


def test_tg_sources_seen_state_is_ordered_and_evicts_oldest(bot_mod, monkeypatch):
    """A set here evicted random ids, so old posts could come back as 'new' - the list keeps the oldest first."""
    import asyncio
    from sigma import telegram as tg
    pages = {"html": _tg_html([])}
    class _Resp:
        status = 200
        async def text(self): return pages["html"]
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    class _Sess:
        def get(self, *a, **k): return _Resp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
    state, digest = {}, {"items": []}
    monkeypatch.setattr(tg, "TG_NEWS_CHANNELS", ("chan",))
    monkeypatch.setattr(tg, "http", lambda: _Sess())
    monkeypatch.setattr(tg, "load_sources", lambda: dict(state))
    monkeypatch.setattr(tg, "save_sources", lambda d: (state.clear(), state.update(d)))
    monkeypatch.setattr(tg, "load_digest", lambda: dict(digest))
    monkeypatch.setattr(tg, "save_digest", lambda d: (digest.clear(), digest.update(d)))
    monkeypatch.setattr(tg, "_news_urgent", lambda t: False)
    pages["html"] = _tg_html(["chan/1", "chan/2"])
    asyncio.run(tg.tg_sources_loop.coro())                       # first run seeds, posts nothing
    assert state["chan"] == ["chan/1", "chan/2"] and digest["items"] == []
    pages["html"] = _tg_html(["chan/2", "chan/3"])
    asyncio.run(tg.tg_sources_loop.coro())
    assert state["chan"] == ["chan/1", "chan/2", "chan/3"]       # append unseen only, in page order
    assert [i["link"] for i in digest["items"]] == ["https://t.me/chan/3"]
    asyncio.run(tg.tg_sources_loop.coro())
    assert len(digest["items"]) == 1                             # nothing new -> nothing re-buffered
    pages["html"] = _tg_html([f"chan/{i}" for i in range(4, 604)])
    asyncio.run(tg.tg_sources_loop.coro())
    assert len(state["chan"]) == 500 and state["chan"][0] == "chan/104" and state["chan"][-1] == "chan/603"

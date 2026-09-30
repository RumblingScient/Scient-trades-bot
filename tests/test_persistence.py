import json, tempfile, pathlib

def test_save_keeps_bak_and_refuses_after_corruption(bot_mod):
    p = pathlib.Path(tempfile.mkdtemp()) / "t.json"
    bot_mod._save(p, {"a": 1}); bot_mod._save(p, {"a": 2})
    assert json.loads(p.with_suffix(".json.bak").read_text()) == {"a": 1}
    p.write_text("{corrupt")
    assert bot_mod._load(p) == {"a": 1}
    refused = False
    try:
        bot_mod._save(p, {"a": 3})
    except RuntimeError:
        refused = True
    assert refused
    bot_mod._CORRUPT.discard(str(p))

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

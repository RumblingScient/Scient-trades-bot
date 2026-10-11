"""Command tree shape: groups, aliases, Discord limits. Loads the whole bot with run() stubbed."""
import pytest


@pytest.fixture(scope="module")
def tree(bot_mod):
    return bot_mod.bot.tree


def _group(tree, name):
    g = next(c for c in tree.get_commands() if c.name == name)
    return g, {s.name: s for s in g.commands}


def test_setup_group(tree):
    g, subs = _group(tree, "setup")
    assert set(subs) == {"futures", "spot", "update", "spot_update", "close", "edit", "fix", "track", "reopen", "xpost", "referrals"}
    assert [p.name for p in subs["close"].parameters] == ["trade", "price", "note"]
    fut = [p.name for p in subs["futures"].parameters]
    assert fut[:7] == ["pair", "direction", "entry_type", "entry", "stop", "entry2", "entry_split"], "new-setup flow: ticker, side, entry type, entry 1, stop, entry 2"
    assert all(p.required for p in subs["futures"].parameters[:5]) and not subs["futures"].parameters[5].required
    assert [c.value for c in subs["futures"].parameters[2].choices] == ["LIMIT", "MARKET", "DCA"]
    assert [p.name for p in subs["update"].parameters] == ["trade", "event", "price", "pct", "note"]
    assert [p.name for p in subs["spot_update"].parameters] == ["trade", "event", "price", "pct", "status", "note"]
    fut_names = [c.name for c in subs["update"].parameters[1].choices]
    spot_names = [c.name for c in subs["spot_update"].parameters[1].choices]
    assert fut_names[0].startswith("Entry filled") and any(n.startswith("Stop to entry") for n in fut_names)
    assert not any("Buy filled" in n or "Zone filled" in n for n in fut_names), "spot events must not show on the futures picker"
    assert any(n.startswith("Buy filled") for n in spot_names) and any(n.startswith("Zone filled") for n in spot_names)
    assert not any(n.startswith("Stop to entry") or n.startswith("Stop hit") or n.startswith("Entry") for n in spot_names)
    assert all(len(n) <= 62 for n in fut_names + spot_names), "event names must not truncate in the picker"
    assert subs["spot_update"].parameters[0].autocomplete is not None
    assert all(len(p.parameters) <= 25 for p in subs.values())
    assert subs["update"].parameters[0].autocomplete is not None
    assert subs["reopen"].parameters[0].autocomplete is not None


def test_admin_group_hidden(tree):
    g, subs = _group(tree, "admin")
    assert g.default_permissions is not None and g.default_permissions.administrator
    assert {"health", "terminal_check", "board", "results", "override", "recap", "tg", "panel", "grant", "revoke", "subs", "members", "plans", "promo"} <= set(subs)
    assert [c.value for c in subs["recap"].parameters[0].choices] == ["week", "month", "journal"]


def test_aliases_point_to_same_callbacks(tree, bot_mod):
    cmds = {c.name: c for c in tree.get_commands()}
    g, subs = _group(tree, "setup")
    for old, sub in (("trade", "futures"), ("spot", "spot"), ("update", "update"), ("spot_update", "spot_update"),
                     ("spot_close", "close"), ("edit", "edit"), ("spot_edit", "fix"), ("track", "track"),
                     ("reopen", "reopen"), ("xpost", "xpost")):
        assert cmds[old].callback is subs[sub].callback, old
        assert cmds[old].extras["moved"].startswith("/setup ")
    for old in ("health", "recap_month", "journal_month", "tg_send"):
        assert cmds[old].extras["moved"].startswith("/admin ")
        assert cmds[old].default_permissions.administrator
    assert len(bot_mod.ALIAS_NAMES) == 14


def test_old_admin_names_gone_from_top_level(tree):
    names = {c.name for c in tree.get_commands()}
    for gone in ("results_sync", "results_rebuild", "results_debug", "results_override", "tg_digest", "tg_brief",
                 "news_status", "setup_follow_panel", "setup_help_panel", "grant", "revoke", "subs", "board",
                 "recap_now", "terminal_check"):
        assert gone not in names, gone
    assert len(names) <= 100


def test_discord_limits(tree):
    def walk(cmds):
        for c in cmds:
            yield c
            yield from walk(getattr(c, "commands", []) or [])
    for c in walk(tree.get_commands()):
        assert len(c.name) <= 32 and len(c.description or "") <= 100, c.qualified_name
        for p in getattr(c, "parameters", []) or []:
            assert len(p.description or "") <= 100, f"{c.qualified_name}.{p.name}"
            assert len(p.choices or []) <= 25
            for ch in p.choices or []:
                assert len(ch.name) <= 100, f"{c.qualified_name}.{p.name}: {ch.name}"

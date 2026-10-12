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
    assert set(subs) == {"futures", "spot", "edit", "xpost", "referrals"}, "everything after posting lives on the card's Manage button"
    fut = [p.name for p in subs["futures"].parameters]
    assert fut[:6] == ["pair", "direction", "entry_type", "entry", "stop", "targets"], "new-setup flow: ticker, side, entry type, entry, stop, targets"
    assert all(p.required for p in subs["futures"].parameters[:6]) and not subs["futures"].parameters[6].required
    assert fut[6:8] == ["entry2", "entry_split"]
    assert "framework2" not in fut and "tp1" not in fut
    assert [c.value for c in subs["futures"].parameters[2].choices] == ["LIMIT", "MARKET", "DCA"]
    spot = [p.name for p in subs["spot"].parameters]
    assert spot[:4] == ["pair", "zone", "targets", "stop"] and "tp1" not in spot
    assert [p.name for p in subs["edit"].parameters][:2] == ["trade", "pair"] and "targets" in [p.name for p in subs["edit"].parameters]
    assert all(len(p.parameters) <= 25 for p in subs.values())
    assert subs["edit"].parameters[0].autocomplete is not None


def test_admin_group_hidden(tree):
    g, subs = _group(tree, "admin")
    assert g.default_permissions is not None and g.default_permissions.administrator
    assert {"health", "terminal_check", "board", "results", "override", "recap", "tg", "panel", "grant", "revoke", "subs", "members", "plans", "promo"} <= set(subs)
    assert [c.value for c in subs["recap"].parameters[0].choices] == ["week", "month", "journal"]


def test_aliases_point_to_same_callbacks(tree, bot_mod):
    cmds = {c.name: c for c in tree.get_commands()}
    for old in ("trade", "update", "spot_update", "spot_close", "spot_edit", "track", "reopen"):
        assert old not in cmds, f"old analyst name /{old} must be gone - the Manage panel replaced it"
    for old in ("health", "recap_month", "journal_month", "tg_send"):
        assert cmds[old].extras["moved"].startswith("/admin ")
        assert cmds[old].default_permissions.administrator
    assert len(bot_mod.ALIAS_NAMES) == 4


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


def _size(d):
    n = len(d.get("name") or "") + len(d.get("description") or "")
    n += sum(_size(o) for o in (d.get("options") or []))
    n += sum(len(str(c.get("name", ""))) + len(str(c.get("value", ""))) for c in (d.get("choices") or []))
    return n


def test_total_size_under_discord_cap(tree):
    """Discord refuses the whole sync (HTTP 400 50035 'Command exceeds maximum size (8000)') when one top-level
    command's names + descriptions + choices pass 8000 chars - subcommands included. Keep headroom."""
    for c in tree.get_commands():
        assert _size(c.to_dict(tree)) <= 7600, f"/{c.name} is {_size(c.to_dict(tree))} chars - trim descriptions or move choices to autocomplete"

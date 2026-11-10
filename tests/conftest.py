"""Loads scient_trades_bot.py as a module with bot.run stubbed so pure functions can be tested.
Run from the repo root:  SCIENT_BOT_TOKEN=x python -m pytest -q
"""
import os, sys, importlib, pathlib
import pytest
os.environ.setdefault("SCIENT_BOT_TOKEN", "x")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import discord.ext.commands as commands
commands.Bot.run = lambda self, *a, **k: None


@pytest.fixture(scope="session")
def bot_mod():
    return importlib.import_module("scient_trades_bot")


@pytest.fixture(autouse=True)
def _isolated_plans(tmp_path, monkeypatch):
    """Tests must see the seed plans, never the live plans.json on a deployed box (real prices, edited plan list)."""
    from sigma.services import plans as plansvc
    import json
    p = tmp_path / "plans.json"
    state = {"plans": {}, "promos": {}}
    monkeypatch.setattr(plansvc, "_load", lambda: json.loads(p.read_text()) if p.exists() else dict(state))
    monkeypatch.setattr(plansvc, "_save", lambda d: p.write_text(json.dumps(d)))
    yield

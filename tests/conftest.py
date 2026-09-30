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

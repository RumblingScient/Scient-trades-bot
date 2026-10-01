"""sigma.aliases - the old top-level command names, kept for ONE release.

Each alias runs exactly the same callback as its new home and tells the caller where it moved.
Remove this module (and its import in bot.py / the shim) in the release after the /setup and
/admin groups go live. bot_selfcheck.py asserts the alias count so the removal is deliberate.

    /trade        -> /setup futures      /health        -> /admin health
    /spot         -> /setup spot         /recap_month   -> /admin recap kind:month
    /update       -> /setup update       /journal_month -> /admin recap kind:journal
    /spot_update  -> /setup update       /tg_send       -> /admin tg
    /spot_close   -> /setup close
    /edit         -> /setup edit
    /spot_edit    -> /setup fix
    /track        -> /setup track
    /reopen       -> /setup reopen
    /xpost        -> /setup xpost
"""
import functools
import discord                      # noqa: F401  (annotation lookups on wrapped callbacks)
from discord import app_commands    # noqa: F401

from sigma.core import bot
from sigma.commands_setup import setup
from sigma.health import health_cmd
from sigma.recaps import recap_month_cmd, journal_month_cmd
from sigma.telegram import tg_send_cmd

# old name -> (new home, /setup subcommand name)
SETUP_ALIASES = {
    "trade": ("/setup futures", "futures"),
    "spot": ("/setup spot", "spot"),
    "update": ("/setup update", "update"),
    "spot_update": ("/setup update", "update"),
    "spot_close": ("/setup close", "close"),
    "edit": ("/setup edit", "edit"),
    "spot_edit": ("/setup fix", "fix"),
    "track": ("/setup track", "track"),
    "reopen": ("/setup reopen", "reopen"),
    "xpost": ("/setup xpost", "xpost"),
}

# old name -> (new home, callback)
ADMIN_ALIASES = {
    "health": ("/admin health", health_cmd),
    "recap_month": ("/admin recap kind:month", recap_month_cmd),
    "journal_month": ("/admin recap kind:journal", journal_month_cmd),
    "tg_send": ("/admin tg", tg_send_cmd),
}

ALIAS_NAMES = tuple(SETUP_ALIASES) + tuple(ADMIN_ALIASES)


def _setup_alias(old: str, moved: str, sub: str):
    cmd = setup.get_command(sub)
    alias = app_commands.Command(name=old, description=f"Moved to {moved}"[:100], callback=cmd.callback,
                                 extras={"moved": moved})
    bot.tree.add_command(alias)


def _admin_alias(old: str, moved: str, fn):
    @functools.wraps(fn)
    async def _w(*a, **kw):
        r = await fn(*a, **kw)
        try:
            await a[0].followup.send(f"`/{old}` moved to `{moved}` - use that next time.", ephemeral=True)
        except Exception:
            pass
        return r
    cmd = app_commands.Command(name=old, description=f"(Admin) Moved to {moved}"[:100], callback=_w, extras={"moved": moved})
    cmd.default_permissions = discord.Permissions(administrator=True)
    bot.tree.add_command(cmd)


for _old, (_moved, _sub) in SETUP_ALIASES.items():
    _setup_alias(_old, _moved, _sub)
for _old, (_moved, _fn) in ADMIN_ALIASES.items():
    _admin_alias(_old, _moved, _fn)

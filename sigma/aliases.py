"""sigma.aliases - old top-level ADMIN command names, kept for one release.

    /health        -> /admin health
    /recap_month   -> /admin recap kind:month
    /journal_month -> /admin recap kind:journal
    /tg_send       -> /admin tg

The old analyst names (/trade, /update, /spot_update, /spot_close, /edit, /spot_edit, /track, /reopen,
/xpost) are gone: posting is /setup futures | spot, everything after that is the Manage button on the card.
"""
import functools
import discord                      # noqa: F401
from discord import app_commands    # noqa: F401

from sigma.core import bot
from sigma.health import health_cmd
from sigma.recaps import recap_month_cmd, journal_month_cmd
from sigma.telegram import tg_send_cmd

# old name -> (new home, callback)
ADMIN_ALIASES = {
    "health": ("/admin health", health_cmd),
    "recap_month": ("/admin recap kind:month", recap_month_cmd),
    "journal_month": ("/admin recap kind:journal", journal_month_cmd),
    "tg_send": ("/admin tg", tg_send_cmd),
}

ALIAS_NAMES = tuple(ADMIN_ALIASES)


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


for _old, (_moved, _fn) in ADMIN_ALIASES.items():
    _admin_alias(_old, _moved, _fn)

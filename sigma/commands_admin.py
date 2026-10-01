"""sigma.commands_admin - the /admin group. Hidden from members (default_permissions = administrator).

    /admin health [view]        bot / results board / news wire
    /admin terminal_check       smoke-test every terminal data source
    /admin board                rebuild the open-positions board
    /admin results action       sync | rebuild
    /admin override ...         correct a wrongly closed trade
    /admin recap kind [days]    week | month | journal
    /admin tg action [text]     brief | digest | custom
    /admin panel kind           follow | help
    /admin grant / revoke / subs

The bodies stay in the modules they belong to (health, results, recaps, telegram, members ...);
this module only builds the tree. Each body still checks administrator itself, so nothing here
can be reached by a non-admin even if Discord's permission overrides are misconfigured.
"""
import discord
from discord import app_commands

from sigma.core import bot
from sigma.health import health_cmd
from sigma.commands_terminal import terminal_check_cmd
from sigma.commands_trades import board_cmd
from sigma.results import results_rebuild_cmd, results_override_cmd, results_sync_cmd
from sigma.recaps import journal_month_cmd, recap_now_cmd, results_debug_cmd, recap_month_cmd
from sigma.telegram import tg_send_cmd
from sigma.news import news_status
from sigma.members import setup_follow_panel, grant_cmd, revoke_cmd, subs_cmd
from sigma.commands_help import setup_help_panel

admin = app_commands.Group(
    name="admin",
    description="Admin tools - health, results board, recaps, Telegram, subscriptions",
    guild_only=True,
    default_permissions=discord.Permissions(administrator=True),
)


def _sub(name: str, description: str, callback):
    """Register an existing (decorated) coroutine as /admin <name> without touching its body."""
    admin.add_command(app_commands.Command(name=name, description=description, callback=callback))


@admin.command(name="health", description="Bot health - loops, feed, data files, errors. Or the results board / news wire")
@app_commands.describe(view="Blank = the bot. Results = why the board isn't updating. News = the news wire")
@app_commands.choices(view=[
    app_commands.Choice(name="Bot - loops, feeds, data, errors", value="bot"),
    app_commands.Choice(name="Results board - watcher, pending, permissions", value="results"),
    app_commands.Choice(name="News wire - connection and last message", value="news"),
])
async def admin_health(interaction: discord.Interaction, view: app_commands.Choice[str] = None):
    v = view.value if view else "bot"
    if v == "results":
        await results_debug_cmd(interaction)
    elif v == "news":
        await news_status(interaction)
    else:
        await health_cmd(interaction)


_sub("terminal_check", "Smoke-test every data source the terminal depends on", terminal_check_cmd)
_sub("board", "Rebuild the open-positions board", board_cmd)


@admin.command(name="results", description="Results board maintenance - sync missed closes, or wipe and rebuild")
@app_commands.describe(action="Sync = post any closed trades the board missed. Rebuild = wipe and re-post everything")
@app_commands.choices(action=[
    app_commands.Choice(name="Sync - post whatever the board missed", value="sync"),
    app_commands.Choice(name="Rebuild - wipe the board and re-post the full log", value="rebuild"),
])
async def admin_results(interaction: discord.Interaction, action: app_commands.Choice[str]):
    if action.value == "rebuild":
        await results_rebuild_cmd(interaction)
    else:
        await results_sync_cmd(interaction)


_sub("override", "Fix a wrongly closed trade - journal, original card and board all update", results_override_cmd)


@admin.command(name="recap", description="Post a recap now - weekly image, monthly recap, or per-analyst journal cards")
@app_commands.describe(kind="Which recap", days="Month / journal: last N days instead of the last complete month")
@app_commands.choices(kind=[
    app_commands.Choice(name="Week - the weekly recap image", value="week"),
    app_commands.Choice(name="Month - server monthly recap", value="month"),
    app_commands.Choice(name="Journal - one card per analyst", value="journal"),
])
async def admin_recap(interaction: discord.Interaction, kind: app_commands.Choice[str], days: int = 0):
    if kind.value == "week":
        await recap_now_cmd(interaction)
    elif kind.value == "journal":
        await journal_month_cmd(interaction, days=days)
    else:
        await recap_month_cmd(interaction, days=(days or 30))


_sub("tg", "Send an update to Telegram now - daily brief, news digest, or a custom message", tg_send_cmd)


@admin.command(name="panel", description="Post a pinned panel in this channel - analyst follow buttons or the command guide")
@app_commands.describe(kind="Which panel")
@app_commands.choices(kind=[
    app_commands.Choice(name="Follow - analyst ping buttons", value="follow"),
    app_commands.Choice(name="Help - the public command guide", value="help"),
])
async def admin_panel(interaction: discord.Interaction, kind: app_commands.Choice[str]):
    if kind.value == "help":
        await setup_help_panel(interaction)
    else:
        await setup_follow_panel(interaction)


_sub("grant", "Grant or extend a Pro subscription", grant_cmd)
_sub("revoke", "Revoke a Pro subscription", revoke_cmd)
_sub("subs", "Active subscriptions overview", subs_cmd)

bot.tree.add_command(admin)

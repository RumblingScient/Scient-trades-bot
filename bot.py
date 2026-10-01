"""Entrypoint: python bot.py  (loads every sigma module so commands/loops/events register, then runs)."""
import sigma.logging_setup  # noqa: F401
import sigma.config  # noqa: F401
import sigma.core  # noqa: F401
import sigma.ops  # noqa: F401
import sigma.http  # noqa: F401
import sigma.storage  # noqa: F401
import sigma.market_data  # noqa: F401
import sigma.charts_theme  # noqa: F401
import sigma.calculations  # noqa: F401
import sigma.members  # noqa: F401
import sigma.cards  # noqa: F401
import sigma.sizer  # noqa: F401
import sigma.boards  # noqa: F401
import sigma.tracker  # noqa: F401
import sigma.news  # noqa: F401
import sigma.telegram  # noqa: F401
import sigma.alerts  # noqa: F401
import sigma.liquidations  # noqa: F401
import sigma.xfeed  # noqa: F401
import sigma.jobs  # noqa: F401
import sigma.results  # noqa: F401
import sigma.recaps  # noqa: F401
import sigma.commands_trades  # noqa: F401
import sigma.commands_terminal  # noqa: F401
import sigma.commands_help  # noqa: F401
import sigma.errors  # noqa: F401
import sigma.services.store  # noqa: F401
import sigma.services.trades  # noqa: F401
import sigma.commands_setup  # noqa: F401
import sigma.commands_admin  # noqa: F401
import sigma.aliases  # noqa: F401
import sigma.health  # noqa: F401
import sigma.app  # noqa: F401
from sigma.core import bot
from sigma.config import BOT_TOKEN

if not BOT_TOKEN or BOT_TOKEN == "PASTE_TOKEN_HERE":
    raise SystemExit("SCIENT_BOT_TOKEN is not set - refusing to start.")
bot.run(BOT_TOKEN, log_handler=None)

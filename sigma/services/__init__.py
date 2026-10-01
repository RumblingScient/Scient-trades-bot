"""sigma.services - the only place trade state changes.

store  : one API over trades.json + spot.json (ids are "f:<msg_id>" / "s:<msg_id>")
trades : open / apply_event / close / edit / fix_fill / set_tracking / reopen - pure functions
         on the record; slash commands, card buttons and the tracker all call these.
"""

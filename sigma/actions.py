"""sigma.actions - what happens AFTER a trade changes, shared by the Manage panel and the slash commands.

commit()        persist + refresh card + board + update feed + thread note + results. Returns the one-line summary.
reopen_trade()  reverse a close, delete the wrong results card, rebuild the summary.
card_view()     the components every trade card carries (member sizer + analyst Manage button).
parse_targets() "104 / 108 / 112" -> ["104", "108", "112"] for the targets field.
"""
import re
import discord

from sigma.logging_setup import print
from sigma.config import BLUE, RESULTS_CHANNEL_ID
from sigma.core import bot
from sigma.errors import UserError
from sigma.storage import load_results, save_results
from sigma.cards import post_update_feed, refresh_and_edit, thread_note
from sigma.boards import refresh_board
from sigma.results import refresh_results_summary, _results_watch_tick
from sigma.services import store
from sigma.services import trades as svc
from sigma.sizer import TradeSizerView, SpotCardView

SPOT_FOOTER = "Sigma Trading - Spot Plays"
FUT_FOOTER = "Sigma Trading - Trade Updates"


def parse_targets(text, limit: int) -> list:
    """Split a targets field on / ; or whitespace (a comma inside a number is a thousands separator,
    a trailing comma is a separator). Raises UserError on junk or too many."""
    if text in (None, ""):
        return []
    parts = [p.strip().strip(",") for p in re.split(r"[\/;\s]+", str(text).strip())]
    parts = [p for p in parts if p]
    for p in parts:
        if not re.fullmatch(r"\$?(?:\d[\d,]*)?\.?\d*", p) or not re.search(r"\d", p):
            raise UserError(f"Target `{p}` isn't a price - give numbers only, e.g. `104000 / 108000`.")
    if len(parts) > limit:
        raise UserError(f"Up to {limit} targets - you gave {len(parts)}.")
    return [p.lstrip("$") for p in parts]


def card_view(kind: str, key) -> discord.ui.View:
    return SpotCardView(key) if kind == store.SPOT else TradeSizerView(key)


async def commit(kind: str, key: str, rec: dict, data: dict, out: svc.Outcome, note: str = None) -> str:
    """Persist + refresh + announce one applied event / close. Returns the reply line."""
    spot = kind == store.SPOT
    data[key] = rec
    store.save(kind, data)
    try:
        await refresh_and_edit(rec, spot_mode=spot)
    except Exception as ex:
        print(f"[actions] card refresh failed for {rec.get('pair')}: {ex}", flush=True)
    await refresh_board()
    if not out.silent:
        line = out.line or out.desc
        if note:
            line += f"\n> {note}"
        await post_update_feed(rec, out.title or out.desc, out.color, line, footer=(SPOT_FOOTER if spot else FUT_FOOTER))
    await thread_note(rec, f"**{out.desc}{out.result_txt}**" + (f" - {note}" if note else ""))
    if out.closed:
        try:
            await _results_watch_tick()      # results card + summary now, not on the next poll
        except Exception as ex:
            print(f"[actions] results post failed: {ex}", flush=True)
    return f"{'Closed' if out.closed else 'Updated'}: {out.desc}{out.result_txt}" + (f"\n{out.extra}" if out.extra else "")


async def commit_edit(kind: str, key: str, rec: dict, data: dict, changes: list, notes: str = None) -> str:
    """Persist a card-field correction and refresh. Returns the reply line."""
    spot = kind == store.SPOT
    data[key] = rec
    store.save(kind, data)
    try:
        await refresh_and_edit(rec, spot_mode=spot)
    except Exception as ex:
        print(f"[actions] card refresh failed for {rec.get('pair')}: {ex}", flush=True)
    await refresh_board()
    changed_txt = ", ".join(changes)
    await thread_note(rec, f"**Edited** - corrected: {changed_txt}")
    if notes is not None:
        await thread_note(rec, f"**{'Thesis' if spot else 'Reasoning'} (updated):** {notes[:1900]}")
    return f"Edited: {changed_txt}"


async def commit_fix(kind: str, key: str, rec: dict, data: dict, change: str, note: str = None) -> str:
    """Persist a fill correction and refresh. Returns the reply line."""
    data[key] = rec
    store.save(kind, data)
    try:
        await refresh_and_edit(rec, spot_mode=(kind == store.SPOT))
    except Exception as ex:
        print(f"[actions] card refresh failed for {rec.get('pair')}: {ex}", flush=True)
    await refresh_board()
    await thread_note(rec, f"**Correction** - {change}" + (f" - {note}" if note else ""))
    return f"Fixed: {change}"


async def reopen_trade(kind: str, key: str, rec: dict, data: dict) -> str:
    spot = kind == store.SPOT
    svc.reopen(kind, rec)
    data[key] = rec
    store.save(kind, data)
    state = load_results()
    removed = False
    for rk in ("fut", "spot"):
        rkey = f"{rk}:{key}"
        if rkey in set(state.get("posted", [])):
            state["posted"] = [x for x in state.get("posted", []) if x != rkey]
            msg_id = (state.get("posted_msgs") or {}).pop(rkey, None)
            if msg_id and RESULTS_CHANNEL_ID:
                ch = bot.get_channel(RESULTS_CHANNEL_ID)
                if ch:
                    try:
                        m = await ch.fetch_message(msg_id)
                        await m.delete()
                    except Exception:
                        pass
            removed = True
    save_results(state)
    try:
        await refresh_and_edit(rec, spot_mode=spot)
    except Exception:
        pass
    await refresh_board()
    await refresh_results_summary(repost=True)
    await post_update_feed(rec, "Setup reopened", BLUE,
                           "Previous close was recorded in error and has been reversed. "
                           + ("The play is live again." if spot else "The setup is live again - auto-tracking is off for it (manual updates)."),
                           footer=(SPOT_FOOTER if spot else FUT_FOOTER))
    return f"Reopened {rec.get('pair', '?').upper()}." + (" Wrong results card deleted, summary rebuilt." if removed else "")


async def ensure_card_views():
    """Boot pass: every open card (and cards closed in the last 14 days, so Reopen stays reachable)
    gets the current component set (sizer + Manage) once. Each flag is written under STATE_LOCK on a
    fresh load, so a fill or tracker cursor saved meanwhile is never overwritten."""
    import asyncio
    from datetime import datetime, timezone, timedelta
    from sigma.tracker import STATE_LOCK
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    n = 0
    for kind in (store.FUT, store.SPOT):
        snapshot = store.load(kind)
        for key, rec in snapshot.items():
            if rec.get("card_view") == 2 or not rec.get("channel_id") or not rec.get("message_id"):
                continue
            if rec.get("closed") and str(rec.get("closed_at") or "") < cutoff:
                continue
            try:
                ch = bot.get_channel(rec["channel_id"]) or await bot.fetch_channel(rec["channel_id"])
                msg = await ch.fetch_message(rec["message_id"])
                await msg.edit(view=card_view(kind, key))
                async with STATE_LOCK:
                    live = store.load(kind)
                    if key in live:
                        live[key]["card_view"] = 2
                        store.save(kind, live)
                n += 1
                await asyncio.sleep(0.7)
            except Exception as ex:
                print(f"[actions] card view attach failed for {rec.get('pair')}: {ex}", flush=True)
    if n:
        print(f"[actions] Manage button attached to {n} card(s)", flush=True)

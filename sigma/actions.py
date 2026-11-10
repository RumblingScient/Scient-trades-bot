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


CHART_MAX_BYTES = 8 * 1024 * 1024       # Discord's upload cap without boosts - a bigger file fails the send anyway


async def chart_file(attachment) -> discord.File:
    """Attachment -> File named chart.<ext>. A fixed ASCII name keeps the embed's attachment:// reference
    matching whatever Discord stores (it rewrites spaces and odd characters on upload)."""
    ctype = (getattr(attachment, "content_type", "") or "").lower()
    if not ctype.startswith("image/"):
        raise UserError("The chart must be an image (PNG / JPG).")
    size = getattr(attachment, "size", None)
    if isinstance(size, (int, float)) and size > CHART_MAX_BYTES:
        raise UserError(f"That chart is {size / 1024 / 1024:.1f} MB - keep it under 8 MB.")
    f = await attachment.to_file()
    ext = (str(getattr(attachment, "filename", "") or "").rsplit(".", 1)[-1] or "png").lower()
    if ext not in ("png", "jpg", "jpeg", "webp", "gif"):
        ext = "png"
    f.filename = f"chart.{ext}"
    return f


def card_view(kind: str, key) -> discord.ui.View:
    return SpotCardView(key) if kind == store.SPOT else TradeSizerView(key)


async def _step(failed: list, name: str, rec: dict, coro):
    """One after-save step. The record is already on disk, so a Discord hiccup here must not surface as an error
    that reads like the change was lost - log it, remember the step name for the reply, carry on."""
    try:
        await coro
    except Exception as ex:
        failed.append(name)
        print(f"[actions] {name} failed for {rec.get('pair')}: {ex!r}", flush=True)


def _with_failures(summary: str, failed: list) -> str:
    if not failed:
        return summary
    return summary + f"\n⚠ Saved, but the {', '.join(failed)} update failed - press Manage again later or ping ops."


async def commit(kind: str, key: str, rec: dict, data: dict, out: svc.Outcome, note: str = None) -> str:
    """Persist + refresh + announce one applied event / close. Returns the reply line."""
    spot = kind == store.SPOT
    data[key] = rec
    store.save(kind, data)
    failed = []
    await _step(failed, "card", rec, refresh_and_edit(rec, spot_mode=spot))
    await _step(failed, "board", rec, refresh_board())
    if not out.silent:
        line = out.line or out.desc
        if note:
            line += f"\n> {note}"
        await _step(failed, "feed", rec, post_update_feed(rec, out.title or out.desc, out.color, line, footer=(SPOT_FOOTER if spot else FUT_FOOTER)))
    await _step(failed, "thread", rec, thread_note(rec, f"**{out.desc}{out.result_txt}**" + (f" - {note}" if note else "")))
    if out.closed:
        await _step(failed, "results", rec, _results_watch_tick())      # results card + summary now, not on the next poll
    summary = f"{'Closed' if out.closed else 'Updated'}: {out.desc}{out.result_txt}" + (f"\n{out.extra}" if out.extra else "")
    return _with_failures(summary, failed)


async def commit_edit(kind: str, key: str, rec: dict, data: dict, changes: list, notes: str = None) -> str:
    """Persist a card-field correction and refresh. Returns the reply line."""
    spot = kind == store.SPOT
    data[key] = rec
    store.save(kind, data)
    failed = []
    await _step(failed, "card", rec, refresh_and_edit(rec, spot_mode=spot))
    await _step(failed, "board", rec, refresh_board())
    changed_txt = ", ".join(changes)
    await _step(failed, "thread", rec, thread_note(rec, f"**Edited** - corrected: {changed_txt}"))
    if notes is not None:
        await _step(failed, "thread", rec, thread_note(rec, f"**{'Thesis' if spot else 'Reasoning'} (updated):** {notes[:1900]}"))
    return _with_failures(f"Edited: {changed_txt}", failed)


async def commit_fix(kind: str, key: str, rec: dict, data: dict, change: str, note: str = None) -> str:
    """Persist a fill correction and refresh. Returns the reply line."""
    data[key] = rec
    store.save(kind, data)
    failed = []
    await _step(failed, "card", rec, refresh_and_edit(rec, spot_mode=(kind == store.SPOT)))
    await _step(failed, "board", rec, refresh_board())
    await _step(failed, "thread", rec, thread_note(rec, f"**Correction** - {change}" + (f" - {note}" if note else "")))
    return _with_failures(f"Fixed: {change}", failed)


async def replace_chart(kind: str, key: str, rec: dict, data: dict, attachment) -> str:
    """Re-upload the card with a new image (Discord can't swap an attachment in place). Returns the reply line."""
    from datetime import datetime, timezone
    from sigma.cards import build_embed, build_spot_embed
    spot = kind == store.SPOT
    channel = bot.get_channel(rec["channel_id"]) or await bot.fetch_channel(rec["channel_id"])
    try:
        msg = await channel.fetch_message(rec["message_id"])
    except Exception:
        raise UserError("Original card not found - it may have been deleted.")
    f = await chart_file(attachment)
    rec["edited"] = True
    rec["edited_at"] = datetime.now(timezone.utc).isoformat()
    embed = (build_spot_embed if spot else build_embed)(rec)
    embed.set_image(url=f"attachment://{f.filename}")
    await msg.edit(embed=embed, attachments=[f])
    data[key] = rec
    store.save(kind, data)
    await thread_note(rec, "**Chart replaced**")
    return "Chart replaced on the card."


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
    failed = []
    await _step(failed, "card", rec, refresh_and_edit(rec, spot_mode=spot))
    await _step(failed, "board", rec, refresh_board())
    await _step(failed, "results", rec, refresh_results_summary(repost=True))
    await _step(failed, "feed", rec, post_update_feed(rec, "Setup reopened", BLUE,
                "Previous close was recorded in error and has been reversed. "
                + ("The play is live again." if spot else "The setup is live again - auto-tracking is off for it (manual updates)."),
                footer=(SPOT_FOOTER if spot else FUT_FOOTER)))
    return _with_failures(f"Reopened {rec.get('pair', '?').upper()}." + (" Wrong results card deleted, summary rebuilt." if removed else ""), failed)


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

"""sigma.results - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
from discord.ext import tasks
import os, json, re, hashlib, aiohttp, io, asyncio, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dt_time
import logging, logging.handlers, builtins, time as _time, traceback as _tb
import functools as _functools
from sigma.logging_setup import print
from sigma.config import DGREY, GREEN, GREY, INVALIDATIONS_CHANNEL_ID, IST, RED, RESULTS_ANALYST_IDS, RESULTS_CHANNEL_ID, RESULTS_POLL_MIN, SIGMA_EMBED_CYAN
from sigma.core import bot
from sigma.ops import HEARTBEAT
from sigma.storage import _results_lock, load_results, load_spot, load_trades, save_results, save_spot, save_trades
from sigma.calculations import _last_complete_month_range, _rec_r, _res_ts, fnum, spot_result_r, tf
from sigma.cards import _ac_label, entry_display, refresh_and_edit
from sigma.boards import _fit_lines


def _res_all_closed():
    def _ok(rec):
        if not RESULTS_ANALYST_IDS:
            return True
        return rec.get("analyst_id") in RESULTS_ANALYST_IDS

    out = []
    for mid, t in load_trades().items():
        if t.get("closed") and _ok(t):
            out.append(("fut", mid, t))
    for mid, p in load_spot().items():
        if p.get("closed") and _ok(p):
            out.append(("spot", mid, p))
    out.sort(key=lambda x: x[2].get("closed_at") or x[2].get("created_at") or "")
    return out

def sigma_tracking_since():
    def _ok(rec):
        return not RESULTS_ANALYST_IDS or rec.get("analyst_id") in RESULTS_ANALYST_IDS
    dates = [t.get("created_at") for t in load_trades().values() if t.get("created_at") and _ok(t)]
    dates += [p.get("created_at") for p in load_spot().values() if p.get("created_at") and _ok(p)]
    return min(dates) if dates else None

def _res_totals(entries):
    rs = [r for r in (_rec_r(k, t) for k, _, t in entries) if r is not None]
    wins = sum(1 for _, _, t in entries if t.get("result") == "WIN")
    losses = sum(1 for _, _, t in entries if t.get("result") == "LOSS")
    be = sum(1 for _, _, t in entries if t.get("result") == "BE")
    inv = sum(1 for _, _, t in entries if t.get("result") == "INVALID")
    decided = wins + losses
    return {"n": len(entries), "wins": wins, "losses": losses, "be": be, "inv": inv,
            "wr": (wins / decided * 100) if decided else 0,
            "total_r": sum(rs) if rs else 0.0, "graded": len(rs),
            "best": max(rs) if rs else None, "worst": min(rs) if rs else None}

def build_results_summary_embed() -> discord.Embed:
    entries = _res_all_closed()
    tot = _res_totals(entries)
    since = sigma_tracking_since()
    embed = discord.Embed(title="Results Board - Full Log", color=SIGMA_EMBED_CYAN,
                          timestamp=datetime.now(timezone.utc))
    head = []
    if since:
        head.append(f"**Tracking since:** <t:{_res_ts(since)}:D>")
    head.append("Every entry in this channel was logged by the bot **at the moment the setup was "
                "posted** - the `Posted` timestamp on each card is the original, not added later.")
    embed.description = "\n".join(head)
    embed.add_field(name="Setups closed", value=str(tot["n"]), inline=True)
    embed.add_field(name="Win rate", value=f"{tot['wr']:.0f}% ({tot['wins']}W / {tot['losses']}L)", inline=True)
    embed.add_field(name="Net result", value=f"{tot['total_r']:+.2f}R ({tot['graded']} graded)", inline=True)
    embed.add_field(name="Breakeven / Invalidated", value=f"{tot['be']} / {tot['inv']}", inline=True)
    if tot["best"] is not None:
        embed.add_field(name="Best / Worst", value=f"{tot['best']:+g}R / {tot['worst']:+g}R", inline=True)
    per = {}
    for k, mid, t in entries:
        per.setdefault(t.get("analyst_name", "?"), []).append((k, mid, t))
    lines = []
    for name, ent in sorted(per.items()):
        s = _res_totals(ent)
        lines.append(f"**{name}** - {s['n']} closed - {s['wr']:.0f}% WR - {s['total_r']:+.2f}R")
    if lines:
        embed.add_field(name="By analyst", value=_fit_lines(lines), inline=False)
    embed.set_footer(text="Sigma Trading - setups, not signals - wins and losses both logged - not financial advice")
    return embed

def build_result_entry_embed(kind: str, t: dict) -> discord.Embed:
    res = t.get("result", "?")
    color = {"WIN": GREEN, "LOSS": RED, "BE": GREY, "INVALID": DGREY}.get(res, GREY)
    if kind == "spot":
        _sr = spot_result_r(t)
        rtxt = (f" {_sr:+.2f}R" if _sr is not None else "") + (f" ({t['result_pct']})" if t.get("result_pct") else "")
        title = f"[{res}]{rtxt} - SPOT {t.get('pair', '?').upper()}"
    else:
        r = t.get("result_r")
        rtxt = f" {r:+.2f}R" if isinstance(r, (int, float)) else ""
        d = "LONG" if t.get("direction") == "LONG" else "SHORT"
        tfs = tf(t)
        title = f"[{res}]{rtxt} - {d} {t.get('pair', '?').upper()}" + (f" {tfs}" if tfs else "")
    embed = discord.Embed(title=title, color=color)
    if kind == "fut":
        embed.add_field(name="Entry", value=entry_display(t, marks=False) or "-", inline=True)
        embed.add_field(name="Invalidation", value=str(t.get("sl") or "-"), inline=True)
        if t.get("avg_exit") is not None:
            embed.add_field(name="Avg exit", value=fnum(t["avg_exit"]), inline=True)
    else:
        embed.add_field(name="DCA zone", value=str(t.get("dca_zone") or "-"), inline=True)
        if t.get("avg_entry"):
            embed.add_field(name="Avg entry", value=str(t["avg_entry"]), inline=True)
        if t.get("avg_exit"):
            embed.add_field(name="Avg exit", value=str(t["avg_exit"]), inline=True)
    posted = t.get("created_at")
    closed = t.get("closed_at") or posted
    embed.add_field(name="Timeline",
                    value=f"Posted <t:{_res_ts(posted)}:f>\nClosed <t:{_res_ts(closed)}:f>",
                    inline=False)
    ov = t.get("override")
    if ov:
        val = f"<t:{_res_ts(ov.get('at'))}:f> by {ov.get('by', 'owner')}"
        if ov.get("note"):
            val += f"\n{ov['note']}"
        embed.add_field(name="Corrected", value=val[:1024], inline=False)
    embed.set_author(name=t.get("analyst_name", "?"), icon_url=t.get("analyst_avatar") or None)
    embed.set_footer(text="Sigma Trading - logged at post time - not financial advice")
    return embed

def _results_csv(entries, label: str) -> discord.File:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["closed_at_utc", "posted_at_utc", "analyst", "kind", "pair", "direction",
                "timeframe", "entry", "invalidation", "avg_exit", "result", "result_r",
                "result_pct", "corrected"])
    for k, mid, t in entries:
        if k == "fut":
            w.writerow([t.get("closed_at", ""), t.get("created_at", ""),
                        t.get("analyst_name", ""), "futures", t.get("pair", "").upper(),
                        t.get("direction", ""), tf(t) or "",
                        entry_display(t, marks=False) or "", t.get("sl", ""),
                        t.get("avg_exit", ""), t.get("result", ""),
                        t.get("result_r", ""), "", "yes" if t.get("override") else ""])
        else:
            w.writerow([t.get("closed_at", ""), t.get("created_at", ""),
                        t.get("analyst_name", ""), "spot", t.get("pair", "").upper(),
                        "", "", t.get("dca_zone", ""), t.get("invalidation", ""), t.get("avg_exit", ""),
                        t.get("result", ""), (spot_result_r(t) if spot_result_r(t) is not None else ""), t.get("result_pct", ""),
                        "yes" if t.get("override") else ""])
    data = buf.getvalue().encode("utf-8")
    return discord.File(io.BytesIO(data), filename=f"sigma_results_{label}.csv")

class ResultsBoardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Last month (CSV)", style=discord.ButtonStyle.secondary,
                       custom_id="sigma_results_csv_month")
    async def csv_month(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        start, end, slug, pretty = _last_complete_month_range()
        rows = []
        for k, mid, t in _res_all_closed():
            try:
                c = datetime.fromisoformat(t["closed_at"]).astimezone(IST)
            except Exception:
                continue
            if start <= c < end:
                rows.append((k, mid, t))
        if not rows:
            await interaction.followup.send(f"No closed setups in {pretty}.", ephemeral=True)
            return
        await interaction.followup.send(
            f"**{pretty}** - {len(rows)} closed setups. Same data as the cards above, machine-readable.",
            file=_results_csv(rows, slug), ephemeral=True)

    @discord.ui.button(label="Full log (CSV)", style=discord.ButtonStyle.secondary,
                       custom_id="sigma_results_csv_all")
    async def csv_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        rows = _res_all_closed()
        if not rows:
            await interaction.followup.send("No closed setups yet.", ephemeral=True)
            return
        await interaction.followup.send(
            f"**Full log** - {len(rows)} closed setups since tracking began. "
            "Every row matches a card in this channel.",
            file=_results_csv(rows, "full_log"), ephemeral=True)

async def refresh_results_summary(repost: bool = False):
    """repost=False: edit the existing summary in place.
    repost=True: delete the old summary and send a fresh one so it is always
    the LAST message in the channel (result cards stack above it)."""
    if not RESULTS_CHANNEL_ID:
        return
    ch = bot.get_channel(RESULTS_CHANNEL_ID)
    if ch is None:
        return
    state = load_results()
    embed = build_results_summary_embed()
    msg_id = state.get("summary_message_id")
    if msg_id and not repost:
        try:
            msg = await ch.fetch_message(msg_id)
            await msg.edit(embed=embed, view=ResultsBoardView())
            return
        except (discord.NotFound, discord.HTTPException):
            pass
    if msg_id and repost:
        try:
            old_msg = await ch.fetch_message(msg_id)
            await old_msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass
    try:
        msg = await ch.send(embed=embed, view=ResultsBoardView())
    except Exception as e:
        print(f"[results] summary send error: {e}", flush=True)
        return
    try:
        await msg.pin()
        # delete the "Quant pinned a message" system notification to keep the channel clean
        async for m in ch.history(limit=5):
            if m.type == discord.MessageType.pins_add and m.author.id == bot.user.id:
                try:
                    await m.delete()
                except discord.HTTPException:
                    pass
                break
    except discord.HTTPException:
        pass
    state["summary_message_id"] = msg.id
    save_results(state)

@tasks.loop(minutes=RESULTS_POLL_MIN)
async def results_watch_loop():
    """Never let an exception kill this loop - a dead loop silently stops the board."""
    HEARTBEAT["results"] = _time.time()
    try:
        await _results_watch_tick()
    except Exception as e:
        import traceback
        print(f"[results] watcher tick error: {e}", flush=True)
        traceback.print_exc()

@results_watch_loop.error
async def _results_watch_error(*args):
    import traceback
    print("[results] watcher crashed - restarting in 60s", flush=True)
    traceback.print_exc()
    await asyncio.sleep(60)
    try:
        if not results_watch_loop.is_running():
            results_watch_loop.start()
    except Exception:
        pass

async def _results_watch_tick():
    if not RESULTS_CHANNEL_ID:
        return
    if _results_lock.locked():
        return          # a tick is already in flight - never double-post
    async with _results_lock:
        await _results_watch_tick_inner()

async def _results_watch_tick_inner():
    if not RESULTS_CHANNEL_ID:
        return
    ch = bot.get_channel(RESULTS_CHANNEL_ID)
    if ch is None:
        return
    state = load_results()
    posted = set(state.get("posted", []))
    new = [(k, mid, t) for k, mid, t in _res_all_closed() if f"{k}:{mid}" not in posted]
    if not new:
        return
    inv_ch = bot.get_channel(INVALIDATIONS_CHANNEL_ID) if INVALIDATIONS_CHANNEL_ID else None
    posted_msgs = state.get("posted_msgs", {})
    for k, mid, t in new:
        try:
            _m = await ch.send(embed=build_result_entry_embed(k, t))
            posted_msgs[f"{k}:{mid}"] = _m.id
        except Exception as e:
            import traceback
            print(f"[results] post error {mid} ({t.get('pair')}): {e}", flush=True)
            traceback.print_exc()
            continue
        if inv_ch and t.get("result") in ("LOSS", "INVALID"):
            try:
                await inv_ch.send(embed=build_result_entry_embed(k, t))
            except Exception as e:
                print(f"[results] mirror error: {e}", flush=True)
        posted.add(f"{k}:{mid}")
        state["posted"] = list(posted)
        state["posted_msgs"] = posted_msgs
        save_results(state)
        await asyncio.sleep(1.5)
    await refresh_results_summary(repost=True)
    print(f"[results] posted {len(new)} closed trade(s)", flush=True)

@results_watch_loop.before_loop
async def _before_results_watch():
    await bot.wait_until_ready()

def _sigma_week_stats(days: int = 7) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries = []
    for k, mid, t in _res_all_closed():
        try:
            d = datetime.fromisoformat(t["closed_at"])
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if d >= cutoff:
                entries.append((k, mid, t))
        except Exception:
            continue
    tot = _res_totals(entries)
    futs = [(k, m, t, _rec_r(k, t)) for k, m, t in entries if _rec_r(k, t) is not None]
    futs.sort(key=lambda x: x[3], reverse=True)

    def _line(k, t):
        d = "spot" if k == "spot" else ("long" if t.get("direction") == "LONG" else "short")
        nm = t.get("analyst_name", "")
        return f"{t.get('pair', '?').upper()} {d}" + (f", {nm}" if nm else "")

    tot["best_lines"] = [(_line(k, t), f"{r:+.1f}R") for k, _, t, r in futs[:2] if r > 0]
    tot["worst_lines"] = [(_line(k, t), f"{r:+.1f}R") for k, _, t, r in futs[-2:] if r < 0]
    end = datetime.now(IST); start = end - timedelta(days=days)
    if days == 7:
        tot["range_txt"] = f"week of {start.strftime('%d')}-{end.strftime('%d %b %Y')}"
    else:
        tot["range_txt"] = f"last {days} days \u00b7 to {end.strftime('%d %b %Y')}"
    return tot

def _sigma_range_stats(start, end):
    """Per-analyst + totals for closed setups inside [start, end) - IST datetimes."""
    entries = []
    for k, mid, t in _res_all_closed():
        try:
            d = datetime.fromisoformat(t["closed_at"])
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if start <= d.astimezone(IST) < end:
                entries.append((k, mid, t))
        except Exception:
            continue
    tot = _res_totals(entries)
    per = {}
    for k, mid, t in entries:
        per.setdefault(t.get("analyst_name", "?"), []).append((k, mid, t))
    rows = []
    for name, ent in per.items():
        s = _res_totals(ent)
        rows.append({"name": name, "n": s["n"], "w": s["wins"], "l": s["losses"],
                     "wr": s["wr"], "r": s["total_r"]})
    # fixed order: Scient first, Owais second, then everyone else by net R
    _rank = {"scient": 0, "owais": 1}
    rows.sort(key=lambda r: (_rank.get(r["name"].strip().lower(), 2), -r["r"]))
    tot["rows"] = rows
    return tot

@bot.tree.command(name="results", description="Public results scorecard - every setup logged, wins and losses")
async def results_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_results_summary_embed(), view=ResultsBoardView())

# (moved to /admin - registered in sigma.commands_admin)
async def results_rebuild_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ch = bot.get_channel(RESULTS_CHANNEL_ID)
    if ch is None:
        await interaction.followup.send("Results channel not found.", ephemeral=True)
        return
    # delete every message the bot posted in this channel (incl. the bad backfill)
    deleted = 0
    try:
        async for msg in ch.history(limit=1000):
            if msg.author.id == bot.user.id:
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(0.6)
                except discord.HTTPException:
                    continue
    except Exception as e:
        print(f"[results] rebuild purge error: {e}", flush=True)
    save_results({})  # reset state -> watcher re-backfills with the analyst filter
    await interaction.followup.send(
        f"Cleared {deleted} messages. Filtered re-backfill starts within "
        f"{RESULTS_POLL_MIN} min (analyst calls only).", ephemeral=True)
    print(f"[results] rebuild: purged {deleted}, state reset", flush=True)

async def closed_any_ac(interaction: discord.Interaction, current: str):
    """Admin autocomplete over CLOSED trades (fut + spot), newest close first."""
    if not interaction.user.guild_permissions.administrator:
        return []
    rows = []
    for mid, t in load_trades().items():
        if t.get("closed"):
            rows.append((t.get("closed_at") or "", _ac_label(t), f"f:{mid}"))
    for mid, p in load_spot().items():
        if p.get("closed"):
            rows.append((p.get("closed_at") or "", _ac_label(p, spot=True), f"s:{mid}"))
    rows.sort(key=lambda r: r[0], reverse=True)
    cur = current.lower()
    out = [app_commands.Choice(name=label[:100], value=val)
           for _, label, val in rows if cur in label.lower()]
    return out[:25]

# (moved to /admin - registered in sigma.commands_admin)
@app_commands.describe(trade="Which closed trade to correct",
                       result="Corrected result",
                       result_r="Corrected R (futures), e.g. -1 or 2.4",
                       avg_exit="Corrected average exit price",
                       result_pct="Corrected % (spot), e.g. +12%",
                       note="Why it was corrected (shown publicly on the card)")
@app_commands.choices(result=[
    app_commands.Choice(name="WIN", value="WIN"),
    app_commands.Choice(name="LOSS", value="LOSS"),
    app_commands.Choice(name="BE", value="BE"),
    app_commands.Choice(name="INVALID", value="INVALID"),
])
@app_commands.autocomplete(trade=closed_any_ac)
async def results_override_cmd(interaction: discord.Interaction, trade: str,
                               result: str = None, result_r: float = None,
                               avg_exit: float = None, result_pct: str = None,
                               note: str = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Owner/admin only.", ephemeral=True)
        return
    if result is None and result_r is None and avg_exit is None and result_pct is None:
        await interaction.response.send_message(
            "Nothing to change - pass at least one of result / result_r / avg_exit / result_pct.",
            ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    kind, _, mid = trade.partition(":")
    spot = kind == "s"
    data = load_spot() if spot else load_trades()
    t = data.get(mid)
    if not t or not t.get("closed"):
        await interaction.followup.send("Closed trade not found - pick it from the autocomplete.", ephemeral=True)
        return

    prev = {f: t.get(f) for f in ("result", "result_r", "avg_exit", "result_pct")}
    if result is not None:
        t["result"] = result
    if result_r is not None:
        t["result_r"] = round(result_r, 2)
    if avg_exit is not None:
        t["avg_exit"] = avg_exit
    if result_pct is not None and spot:
        t["result_pct"] = result_pct
    t["override"] = {"by": interaction.user.display_name,
                     "at": datetime.now(timezone.utc).isoformat(),
                     "prev": prev, "note": note}
    data[mid] = t
    (save_spot if spot else save_trades)(data)

    # original trade card in the trades channel
    orig = "ok"
    try:
        await refresh_and_edit(t, spot_mode=spot)
    except Exception as e:
        orig = f"failed ({e})"
        print(f"[results] override: original card edit failed: {e}", flush=True)

    # results-board card: edit in place if we know the message, else post corrected
    board = "no board channel"
    ch = bot.get_channel(RESULTS_CHANNEL_ID) if RESULTS_CHANNEL_ID else None
    if ch is not None:
        key = f"{'spot' if spot else 'fut'}:{mid}"
        state = load_results()
        posted_msgs = state.get("posted_msgs", {})
        embed = build_result_entry_embed("spot" if spot else "fut", t)
        msg_id = posted_msgs.get(key)
        board = "card updated"
        try:
            if msg_id:
                m = await ch.fetch_message(msg_id)
                await m.edit(embed=embed)
            else:
                m = await ch.send(embed=embed)
                posted_msgs[key] = m.id
                state["posted"] = list(set(state.get("posted", [])) | {key})
                state["posted_msgs"] = posted_msgs
                board = "corrected card posted (original pre-dated tracking of card ids)"
            save_results(state)
        except Exception as e:
            board = f"failed ({e})"
            print(f"[results] override: board edit failed: {e}", flush=True)
        await refresh_results_summary()

    changed = []
    for f in ("result", "result_r", "avg_exit", "result_pct"):
        if t.get(f) != prev.get(f):
            changed.append(f"{f}: {prev.get(f)} -> {t.get(f)}")
    await interaction.followup.send(
        "Override applied.\n" + ("\n".join(changed) if changed else "(values unchanged)") +
        f"\nOriginal card: {orig}\nResults board: {board}\n"
        "The board card now shows a public 'Corrected' timestamp - silent edits would cost more trust than the mistake.",
        ephemeral=True)

# (moved to /admin - registered in sigma.commands_admin)
async def results_sync_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not results_watch_loop.is_running():
        results_watch_loop.start()
    try:
        await _results_watch_tick()
        await refresh_results_summary()
        await interaction.followup.send("Sync done - board and summary are current.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Sync failed: `{e}`\nCheck journalctl for the traceback.", ephemeral=True)

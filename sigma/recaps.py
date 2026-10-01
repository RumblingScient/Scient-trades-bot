"""sigma.recaps - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import IST, RECAP_CHANNEL_ID, RECAP_DAY, RECAP_UTC, RESULTS_ANALYST_IDS, RESULTS_CHANNEL_ID, RESULTS_POLL_MIN, SIGMA_AMBER, SIGMA_ASH, SIGMA_BG, SIGMA_CARD, SIGMA_CYAN, SIGMA_GREEN, SIGMA_PAPER, SIGMA_RED, SIGMA_SLATE
from sigma.core import bot
from sigma.storage import load_results, load_trades
from sigma.charts_theme import _sigma_fonts
from sigma.calculations import _last_complete_month_range, _rec_r
from sigma.tracker import price_watch_loop
from sigma.results import ResultsBoardView, _res_all_closed, _res_totals, _sigma_range_stats, _sigma_week_stats, refresh_results_summary, results_watch_loop


def make_recap_image(stats: dict) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    fams = _sigma_fonts()
    fig = plt.figure(figsize=(10.8, 13.5), facecolor=SIGMA_BG)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 108); ax.set_ylim(0, 135)
    ax.axis("off"); ax.set_facecolor(SIGMA_BG)

    def box(x, y, w, h, fc=SIGMA_CARD, ec=SIGMA_SLATE):
        ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h,
                     boxstyle="round,pad=0,rounding_size=1.4",
                     facecolor=fc, edgecolor=ec, linewidth=1.2))

    box(7, 122, 9, 9, fc=SIGMA_CYAN, ec=SIGMA_CYAN)
    ax.plot([14.2, 9.0, 11.8, 9.0, 14.2], [129.2, 129.2, 126.5, 123.8, 123.8],
            color=SIGMA_BG, linewidth=3.4, solid_capstyle="butt")
    ax.text(19, 126.8, "SIGMA TRADING", color=SIGMA_PAPER, fontsize=21,
            fontweight="bold", family=fams["disp"], va="center")
    ax.text(19, 123.4, stats["range_txt"], color=SIGMA_ASH, fontsize=11,
            family=fams["mono"], va="center")
    ax.plot([7, 101], [119.5, 119.5], color=SIGMA_SLATE, linewidth=1.2)

    ax.text(7, 109, "WEEKLY", color=SIGMA_PAPER, fontsize=34, fontweight="bold", family=fams["disp"])
    ax.text(7, 101, "RECAP", color=SIGMA_CYAN, fontsize=34, fontweight="bold", family=fams["disp"])

    cells = [("SETUPS CLOSED", str(stats["n"]), SIGMA_PAPER),
             ("CLOSED GREEN", str(stats["wins"]), SIGMA_GREEN),
             ("CLOSED RED", str(stats["losses"]), SIGMA_RED),
             ("NET", f"{stats['total_r']:+.1f}R", SIGMA_CYAN)]
    for i, (label, val, col) in enumerate(cells):
        x = 7 + (i % 2) * 48.5; y = 78 - (i // 2) * 19
        box(x, y, 45.5, 16)
        ax.text(x + 3, y + 11.5, label, color=SIGMA_ASH, fontsize=10, family=fams["mono"])
        ax.text(x + 3, y + 3.5, val, color=col, fontsize=25, fontweight="bold", family=fams["mono"])

    y = 52
    if stats["best_lines"]:
        ax.text(7, y, "BEST", color=SIGMA_CYAN, fontsize=10.5, family=fams["mono"]); y -= 4.5
        for line, r in stats["best_lines"]:
            ax.text(7, y, line, color=SIGMA_PAPER, fontsize=12.5, family=fams["disp"])
            ax.text(101, y, r, color=SIGMA_GREEN, fontsize=12.5, family=fams["mono"], ha="right")
            y -= 4.6
        y -= 2.5
    if stats["worst_lines"]:
        ax.text(7, y, "WORST", color=SIGMA_AMBER, fontsize=10.5, family=fams["mono"]); y -= 4.5
        for line, r in stats["worst_lines"]:
            ax.text(7, y, line, color=SIGMA_PAPER, fontsize=12.5, family=fams["disp"])
            ax.text(101, y, r, color=SIGMA_RED, fontsize=12.5, family=fams["mono"], ha="right")
            y -= 4.6
    ax.plot([7, 101], [max(y, 15.5), max(y, 15.5)], color=SIGMA_SLATE, linewidth=1.2)
    ax.text(7, 11.5, "Every setup was posted before it played out.", color=SIGMA_ASH,
            fontsize=12, family=fams["disp"])
    ax.text(7, 7.8, "Full log open in #results-board.", color=SIGMA_ASH, fontsize=12, family=fams["disp"])
    ax.text(7, 3.4, "SETUPS, NOT SIGNALS", color=SIGMA_CYAN, fontsize=10.5, family=fams["mono"])

    buf = io.BytesIO()
    fig.savefig(buf, dpi=100, facecolor=SIGMA_BG)
    plt.close(fig)
    buf.seek(0)
    return buf

def make_monthly_recap_image(stats: dict) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    fams = _sigma_fonts()
    DISP, MONO = fams["disp"], fams["mono"]
    LBL = "#A9B7C6"
    fig = plt.figure(figsize=(16, 9), facecolor=SIGMA_BG)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 160); ax.set_ylim(0, 90); ax.axis("off")

    def box(x, y, w, h, fc=SIGMA_CARD, ec=SIGMA_SLATE):
        ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.4",
                     facecolor=fc, edgecolor=ec, linewidth=1.3))

    box(8, 80, 6.2, 6.2, fc=SIGMA_CYAN, ec=SIGMA_CYAN)
    ax.plot([12.7, 9.3, 11.1, 9.3, 12.7], [84.9, 84.9, 83.1, 81.3, 81.3],
            color=SIGMA_BG, lw=2.5, solid_capstyle="butt")
    ax.text(16.5, 83.1, "SIGMA TRADING", color=SIGMA_PAPER, fontsize=15, family=DISP, va="center")
    ax.text(152, 84.6, stats["month"].upper(), color=SIGMA_CYAN, fontsize=12, family=MONO, ha="right", va="center")
    ax.text(152, 81.6, "MONTHLY RECAP \u00b7 THE DESK", color=LBL, fontsize=9, family=MONO, ha="right", va="center")

    nrows = len(stats.get("rows", []))
    ax.text(8, 70, "THE DESK", color=SIGMA_PAPER, fontsize=30, family=DISP)
    ncol = SIGMA_GREEN if stats["total_r"] >= 0 else SIGMA_RED
    ax.text(7.6, 54, f"{stats['total_r']:+.2f}R", color=ncol, fontsize=46, family=MONO)
    ax.text(8, 48.5, f"NET \u00b7 SUM OF ALL CLOSES \u00b7 {nrows} ANALYSTS", color=LBL, fontsize=8.6, family=MONO)
    rows_l = [("SETUPS CLOSED", str(stats["n"])), ("WIN RATE, DECIDED", f"{stats['wr']:.0f}%"),
              ("BE / INVALIDATED", f"{stats['be']} / {stats['inv']}")]
    yy = 40
    for k, v in rows_l:
        ax.text(8, yy, k, color=LBL, fontsize=8.8, family=MONO)
        ax.text(56, yy, v, color=SIGMA_PAPER, fontsize=11.5, family=MONO, ha="right")
        yy -= 5.4

    # right: BY ANALYST table
    tx = 68
    ax.text(tx, 70, "BY ANALYST", color=SIGMA_CYAN, fontsize=9.5, family=MONO)
    ax.text(152, 70, "every row from the public board", color=LBL, fontsize=8, family=MONO, ha="right")
    cols = [("ANALYST", tx, "left"), ("SETUPS", tx + 42, "center"), ("W", tx + 54, "center"),
            ("L", tx + 62, "center"), ("WR", tx + 72, "center"), ("NET", 152, "right")]
    hy = 64
    for lab, x, ha in cols:
        ax.text(x, hy, lab, color=SIGMA_CYAN, fontsize=7.8, family=MONO, ha=ha)
    ax.plot([tx, 152], [hy - 2, hy - 2], color=SIGMA_SLATE, lw=1.1)
    ry = hy - 8.5
    for r in stats["rows"][:5]:
        ax.text(tx, ry, r["name"], color=SIGMA_PAPER, fontsize=13, family=DISP)
        ax.text(tx + 42, ry, str(r["n"]), color=SIGMA_PAPER, fontsize=11, family=MONO, ha="center")
        ax.text(tx + 54, ry, str(r["w"]), color=SIGMA_GREEN, fontsize=11, family=MONO, ha="center")
        ax.text(tx + 62, ry, str(r["l"]), color=SIGMA_RED, fontsize=11, family=MONO, ha="center")
        ax.text(tx + 72, ry, f"{r['wr']:.0f}%", color=SIGMA_PAPER, fontsize=11, family=MONO, ha="center")
        rcol = SIGMA_GREEN if r["r"] >= 0 else SIGMA_RED
        ax.text(152, ry, f"{r['r']:+.2f}R", color=rcol, fontsize=11.5, family=MONO, ha="right")
        ax.plot([tx, 152], [ry - 2.6, ry - 2.6], color=SIGMA_SLATE, lw=0.8, alpha=0.5)
        ry -= 8.2
    ax.text(tx, ry - 1, "losses on the board too \u2014 that's the point.",
            color=LBL, fontsize=8.6, family=fams.get("txt", MONO))

    ax.plot([8, 152], [30, 30], color=SIGMA_SLATE, lw=1.2)
    cells = [
        ("ANALYSTS", str(nrows), SIGMA_PAPER),
        ("SETUPS", str(stats["n"]), SIGMA_PAPER),
        ("WIN RATE", f"{stats['wr']:.0f}%", SIGMA_PAPER),
        ("NET", f"{stats['total_r']:+.2f}R", ncol),
        ("EVERY RESULT", "logged", SIGMA_CYAN),
    ]
    for i, (k, v, col) in enumerate(cells):
        x = 8 + i * 29.6
        box(x, 12, 26.6, 12.5)
        ax.text(x + 2.4, 20.6, k, color=LBL, fontsize=7.4, family=MONO)
        ax.text(x + 2.4, 15.6, v, color=col, fontsize=10.4, family=MONO)

    ax.text(80, 7, "every setup logged by a bot at post time \u00b7 the full log is public \u00b7 CSV export in results-board",
            color=LBL, fontsize=8.2, family=MONO, ha="center")
    ax.text(80, 3.2, "SETUPS, NOT SIGNALS", color=SIGMA_CYAN, fontsize=8.2, family=MONO, ha="center")
    buf = io.BytesIO()
    fig.savefig(buf, dpi=110, facecolor=SIGMA_BG)
    plt.close(fig)
    buf.seek(0)
    return buf

ANALYST_JOURNAL_CHANNEL_IDS: dict[int, int] = {}   # analyst_id -> channel_id; empty = post to RECAP_CHANNEL_ID

def _analyst_month_stats(analyst_id: int, start, end):
    entries = []
    for k, mid, t in _res_all_closed():
        if t.get("analyst_id") != analyst_id:
            continue
        try:
            d = datetime.fromisoformat(t["closed_at"])
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if start <= d.astimezone(IST) < end:
                entries.append((k, mid, t))
        except Exception:
            continue
    if not entries:
        return None
    tot = _res_totals(entries)
    longs  = [(k, m, t) for k, m, t in entries if t.get("direction") == "LONG"]
    shorts = [(k, m, t) for k, m, t in entries if t.get("direction") == "SHORT"]
    def _wr(sub):
        w = sum(1 for _, _, t in sub if t.get("result") == "WIN")
        l = sum(1 for _, _, t in sub if t.get("result") == "LOSS")
        return (w / (w + l) * 100) if (w + l) else 0
    win_rs  = [r for r in (_rec_r(k, t) for k, _, t in entries if t.get("result") == "WIN") if r is not None]
    loss_rs = [r for r in (_rec_r(k, t) for k, _, t in entries if t.get("result") == "LOSS") if r is not None]
    best = None
    for k, _, t in entries:
        r = _rec_r(k, t)
        if r is not None and (best is None or r > best[0]):
            best = (r, t.get("pair", "?"), (t.get("direction") or ("spot" if k == "spot" else "")))
    pair_counts = {}
    for _, _, t in entries:
        p = (t.get("pair") or "?").upper()
        pair_counts[p] = pair_counts.get(p, 0) + 1
    top_pair, top_n = max(pair_counts.items(), key=lambda x: x[1]) if pair_counts else ("\u2014", 0)
    log = []
    for k, _, t in sorted(entries, key=lambda x: x[2].get("closed_at") or ""):
        res = {"WIN": "W", "LOSS": "L", "BE": "BE", "INVALID": "INV"}.get(t.get("result"), "?")
        rv = _rec_r(k, t)
        if rv is not None:
            rtxt = f"{rv:+.2f}"; rv = float(rv)
        elif k == "spot" and t.get("result_pct"):
            rtxt = str(t["result_pct"])
        else:
            rtxt = "\u2014"
        try:
            dtxt = datetime.fromisoformat(t["closed_at"]).astimezone(IST).strftime("%d %b")
        except Exception:
            dtxt = "\u2014"
        log.append({"pair": t.get("pair", "?"), "side": (t.get("direction") or "").title() or "Spot",
                    "res": res, "r": rtxt, "rv": rv, "date": dtxt})
    an = next((t.get("analyst_name") for _, _, t in entries if t.get("analyst_name")), "Analyst")
    return {"analyst": an.upper(), "n": tot["n"], "w": tot["wins"], "l": tot["losses"],
            "be": tot["be"], "inv": tot["inv"], "wr": tot["wr"], "net": tot["total_r"],
            "longs": len(longs), "shorts": len(shorts),
            "wr_long": _wr(longs), "wr_short": _wr(shorts),
            "avg_w": (sum(win_rs) / len(win_rs)) if win_rs else 0.0,
            "avg_l": (sum(loss_rs) / len(loss_rs)) if loss_rs else 0.0,
            "hl1_value": (f"{best[0]:+.2f}R" if best else "\u2014"),
            "hl1_sub": (f"{best[1]} {best[2].lower()}" if best else "\u2014"),
            "hl2_value": top_pair.split("/")[0], "hl2_sub": f"{top_n} of {tot['n']} setups",
            "log": log}

def make_analyst_month_image(s: dict) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    fams = _sigma_fonts()
    DISP, MONO = fams["disp"], fams["mono"]
    TXTF = fams.get("txt", MONO)
    LBL = "#A9B7C6"
    fig = plt.figure(figsize=(16, 9), facecolor=SIGMA_BG)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 160); ax.set_ylim(0, 90); ax.axis("off")

    def box(x, y, w, h, fc=SIGMA_CARD, ec=SIGMA_SLATE):
        ax.add_patch(mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.4",
                     facecolor=fc, edgecolor=ec, linewidth=1.3))

    box(8, 80, 6.2, 6.2, fc=SIGMA_CYAN, ec=SIGMA_CYAN)
    ax.plot([12.7, 9.3, 11.1, 9.3, 12.7], [84.9, 84.9, 83.1, 81.3, 81.3],
            color=SIGMA_BG, lw=2.5, solid_capstyle="butt")
    ax.text(16.5, 83.1, "SIGMA TRADING", color=SIGMA_PAPER, fontsize=15, family=DISP, va="center")
    ax.text(152, 84.6, s["month"].upper(), color=SIGMA_CYAN, fontsize=12, family=MONO, ha="right", va="center")
    ax.text(152, 81.6, "MONTHLY JOURNAL", color=LBL, fontsize=9, family=MONO, ha="right", va="center")

    ax.text(8, 70, s["analyst"], color=SIGMA_PAPER, fontsize=30, family=DISP)
    ncol = SIGMA_GREEN if s["net"] >= 0 else SIGMA_RED
    ax.text(7.6, 54, f"{s['net']:+.2f}R", color=ncol, fontsize=46, family=MONO)
    ax.text(8, 48.5, "NET \u00b7 SUM OF ALL CLOSES", color=LBL, fontsize=8.6, family=MONO)
    rows = [("SETUPS CLOSED", str(s["n"])), ("WIN RATE, DECIDED", f"{s['wr']:.0f}%"),
            ("W / L / BE / INV", f"{s['w']} / {s['l']} / {s['be']} / {s['inv']}")]
    yy = 40
    for k, v in rows:
        ax.text(8, yy, k, color=LBL, fontsize=8.8, family=MONO)
        ax.text(56, yy, v, color=SIGMA_PAPER, fontsize=11.5, family=MONO, ha="right")
        yy -= 5.4

    ax.text(68, 70, "THE TAPE", color=SIGMA_CYAN, fontsize=9.5, family=MONO)
    ax.text(152, 70, "one bar per close \u00b7 height = R", color=LBL, fontsize=8, family=MONO, ha="right")
    base_y = 55
    ax.plot([68, 152], [base_y, base_y], color=SIGMA_SLATE, lw=1.2)
    tape = s.get("log", [])[:60]
    rs = [r.get("rv") for r in tape]
    mx = max((abs(v) for v in rs if v), default=1) or 1
    n = max(len(tape), 1)
    step = 84 / n
    bw = min(2.4, step * 0.62)
    for i, r in enumerate(tape):
        x = 68 + i * step + (step - bw) / 2
        v = r.get("rv")
        if v is None:
            ax.add_patch(mpatches.Rectangle((x, base_y - 0.35), bw, 0.7, facecolor=SIGMA_SLATE, edgecolor="none"))
            continue
        h = max(0.5, abs(v) / mx * 11)
        col = SIGMA_GREEN if v > 0 else (SIGMA_RED if v < 0 else SIGMA_SLATE)
        y = base_y if v >= 0 else base_y - h
        ax.add_patch(mpatches.Rectangle((x, y), bw, h, facecolor=col, edgecolor="none"))
    ax.text(68, 38.5, "wins, losses, breakevens, invalidations \u2014 all of it, in order",
            color=LBL, fontsize=8.6, family=TXTF)

    ax.plot([8, 152], [30, 30], color=SIGMA_SLATE, lw=1.2)
    aw, al = s["avg_w"], abs(s["avg_l"]) or 0.0001
    payoff = aw / al if al else 0
    best_pair = (s.get("hl1_sub") or "").split(" ")[0] or "\u2014"
    cells = [
        ("LONGS", f"{s['longs']} \u00b7 {s['wr_long']:.0f}% WR", SIGMA_PAPER),
        ("SHORTS", f"{s['shorts']} \u00b7 {s['wr_short']:.0f}% WR", SIGMA_PAPER),
        ("AVG WIN / LOSS", f"{s['avg_w']:+.2f} / {s['avg_l']:+.2f}R", SIGMA_PAPER),
        ("PAYOFF", f"{payoff:.1f} : 1", SIGMA_CYAN),
        ("BEST CLOSE", f"{s['hl1_value']} \u00b7 {best_pair}", SIGMA_GREEN),
    ]
    for i, (k, v, col) in enumerate(cells):
        x = 8 + i * 29.6
        box(x, 12, 26.6, 12.5)
        ax.text(x + 2.4, 20.6, k, color=LBL, fontsize=7.4, family=MONO)
        ax.text(x + 2.4, 15.6, v, color=col, fontsize=9.6, family=MONO)

    ax.text(80, 7, "every setup logged by a bot at post time \u00b7 the full log is public \u00b7 CSV export in results-board",
            color=LBL, fontsize=8.2, family=MONO, ha="center")
    ax.text(80, 3.2, "SETUPS, NOT SIGNALS", color=SIGMA_CYAN, fontsize=8.2, family=MONO, ha="center")
    buf = io.BytesIO()
    fig.savefig(buf, dpi=110, facecolor=SIGMA_BG)
    plt.close(fig)
    buf.seek(0)
    return buf

async def post_analyst_journals(start=None, end=None, label=None) -> int:
    if start is None:
        start, end, _slug, label = _last_complete_month_range()
    posted = 0
    for aid in (RESULTS_ANALYST_IDS or set()):
        stats = _analyst_month_stats(aid, start, end)
        if not stats:
            continue
        stats["month"] = label or start.strftime("%B %Y")
        ch = bot.get_channel(ANALYST_JOURNAL_CHANNEL_IDS.get(aid) or RECAP_CHANNEL_ID or RESULTS_CHANNEL_ID)
        if ch is None:
            continue
        try:
            buf = await asyncio.to_thread(make_analyst_month_image, stats)
            await ch.send(content=f"**{stats['analyst'].title()} \u2014 {stats['month']}** \u00b7 "
                                  f"{stats['n']} setups, {stats['net']:+.2f}R net.",
                          file=discord.File(buf, filename=f"journal_{stats['analyst'].lower()}.png"))
            posted += 1
            await asyncio.sleep(1.5)
        except Exception as e:
            print(f"[journal] error for {aid}: {e}", flush=True)
    return posted

# (moved to /admin - registered in sigma.commands_admin)
@app_commands.describe(days="Use last N days instead of last complete month")
async def journal_month_cmd(interaction: discord.Interaction, days: int = 0):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if days and days > 0:
        end = datetime.now(IST)
        start = end - timedelta(days=min(days, 365))
        n = await post_analyst_journals(start, end, f"Last {min(days,365)} days")
    else:
        n = await post_analyst_journals()
    await interaction.followup.send(f"Posted {n} journal card(s)." if n else
                                    "No closed setups for any analyst in that range.", ephemeral=True)

async def post_monthly_recap(start=None, end=None, label=None) -> bool:
    if start is None:
        start, end, _slug, label = _last_complete_month_range()
    stats = _sigma_range_stats(start, end)
    if stats["n"] == 0:
        print("[recap] monthly skipped - no closed setups in range", flush=True)
        return False
    stats["month"] = label or start.strftime("%B %Y")
    ch = bot.get_channel(RECAP_CHANNEL_ID or RESULTS_CHANNEL_ID)
    if ch is None:
        return False
    try:
        buf = await asyncio.to_thread(make_monthly_recap_image, stats)
        content = f"**{stats['month']} - Monthly Recap** \u00b7 {stats['n']} setups, {stats['total_r']:+.2f}R net."
        if RESULTS_CHANNEL_ID:
            content += f" Full log in <#{RESULTS_CHANNEL_ID}>."
        await ch.send(content=content, file=discord.File(buf, filename="sigma_monthly_recap.png"))
        return True
    except Exception as e:
        print(f"[recap] monthly error: {e}", flush=True)
        return False

@tasks.loop(time=RECAP_UTC)
async def sigma_monthly_loop():
    # runs daily at 10:00 IST; posts only on the 1st, for the month just ended
    if datetime.now(IST).day != 1:
        return
    await post_monthly_recap()
    await post_analyst_journals()

@sigma_monthly_loop.before_loop
async def _before_sigma_monthly():
    await bot.wait_until_ready()

async def post_weekly_recap() -> bool:
    stats = _sigma_week_stats(7)
    if stats["n"] == 0:
        print("[recap] skipped - no closed trades this week", flush=True)
        return False
    ch = bot.get_channel(RECAP_CHANNEL_ID or RESULTS_CHANNEL_ID)
    if ch is None:
        return False
    try:
        buf = await asyncio.to_thread(make_recap_image, stats)
    except Exception as e:
        print(f"[recap] render error: {e}", flush=True)
        return False
    content = f"**Weekly Recap** - {stats['n']} setups, {stats['total_r']:+.2f}R net."
    if RESULTS_CHANNEL_ID:
        content += f" Full log in <#{RESULTS_CHANNEL_ID}>."
    try:
        await ch.send(content=content, file=discord.File(buf, filename="sigma_weekly_recap.png"))
        return True
    except Exception as e:
        print(f"[recap] post error: {e}", flush=True)
        return False

@tasks.loop(time=RECAP_UTC)
async def sigma_recap_loop():
    if datetime.now(timezone.utc).weekday() != RECAP_DAY:
        return
    await post_weekly_recap()

@sigma_recap_loop.before_loop
async def _before_sigma_recap():
    await bot.wait_until_ready()

# (moved to /admin - registered in sigma.commands_admin)
async def recap_now_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    ok = await post_weekly_recap()
    await interaction.followup.send("Recap posted." if ok else
                                    "Recap failed or no closed trades this week - check logs.",
                                    ephemeral=True)

# (moved to /admin - registered in sigma.commands_admin)
async def results_debug_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    state = load_results()
    posted = set(state.get("posted", []))
    all_closed = _res_all_closed()
    pending = [(k, m, t) for k, m, t in all_closed if f"{k}:{m}" not in posted]

    # closed trades that the allowlist is filtering out
    filtered = []
    for mid, t in load_trades().items():
        if t.get("closed") and RESULTS_ANALYST_IDS and t.get("analyst_id") not in RESULTS_ANALYST_IDS:
            filtered.append(f"{t.get('pair')} ({t.get('analyst_name')})")

    ch = bot.get_channel(RESULTS_CHANNEL_ID)
    perms = "channel not found"
    if ch is not None:
        p = ch.permissions_for(interaction.guild.me)
        perms = (f"view={p.view_channel} send={p.send_messages} "
                 f"embed={p.embed_links} manage={p.manage_messages}")
    rch = bot.get_channel(RECAP_CHANNEL_ID) if RECAP_CHANNEL_ID else None
    rperms = "not set"
    if rch is not None:
        rp = rch.permissions_for(interaction.guild.me)
        rperms = f"send={rp.send_messages} attach={rp.attach_files}"

    lines = [
        f"**Watcher running:** {'yes' if results_watch_loop.is_running() else '**NO - this is the problem**'}",
        f"**Recap loop running:** {'yes' if sigma_recap_loop.is_running() else 'no'}",
        f"**Closed trades visible to board:** {len(all_closed)}",
        f"**Already posted:** {len(posted)}",
        f"**Pending (should post within {RESULTS_POLL_MIN} min):** {len(pending)}",
    ]
    if pending:
        lines.append("  " + ", ".join(f"{t.get('pair')}" for _, _, t in pending[:8]))
    if filtered:
        lines.append(f"**Filtered out by analyst allowlist:** {len(filtered)}")
        lines.append("  " + ", ".join(filtered[:8]))
    lines.append(f"**results-board perms:** {perms}")
    lines.append(f"**recap channel perms:** {rperms}")
    lines.append(f"**This week's closed trades (recap needs >0):** {_sigma_week_stats(7)['n']}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)

# (moved to /admin - registered in sigma.commands_admin)
@app_commands.describe(days="How many days back to include (default 30)")
async def recap_month_cmd(interaction: discord.Interaction, days: int = 30):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    days = max(1, min(days, 365))
    end = datetime.now(IST)
    start = end - timedelta(days=days)
    label = _last_complete_month_range()[3] if days == 30 else f"Last {days} days"
    ok = await post_monthly_recap(start, end, label if days != 30 else None)
    await interaction.followup.send("Recap posted." if ok else
                                    f"No closed setups in the last {days} days.", ephemeral=True)

_sigma_view_registered = False

@bot.listen("on_ready")
async def _sigma_results_on_ready():
    global _sigma_view_registered
    if not _sigma_view_registered:
        bot.add_view(ResultsBoardView())
        _sigma_view_registered = True
    if RESULTS_CHANNEL_ID and not results_watch_loop.is_running():
        results_watch_loop.start()
    if not sigma_recap_loop.is_running():
        sigma_recap_loop.start()
    if not sigma_monthly_loop.is_running():
        sigma_monthly_loop.start()
    if not price_watch_loop.is_running():
        price_watch_loop.start()
        print("[watch] auto price tracker armed", flush=True)
    try:
        await refresh_results_summary()   # re-attach buttons + fresh numbers on every boot
    except Exception as e:
        print(f"[results] startup summary refresh error: {e}", flush=True)
    print("[results] board watcher armed", flush=True)

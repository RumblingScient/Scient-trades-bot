"""sigma.cards - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import EDIT_WINDOW_MIN, GUILD_ID, IST, NAVY, TRADE_UPDATES_CHANNEL_ID
from sigma.core import bot
from sigma.calculations import (_tp_presets, _tp_taken, any_entry_filled, display_rr, entry_num, fills_pct, fmt_frameworks,
                                fmt_risk, fnum, initial_sl_num, signed_r, sl_num, spot_pct_text, spot_result_r, spot_signed_r,
                                spot_status_line, spot_zone_projection, stop_locked_r, stop_trailed, tf,
                                banked_r, first_num, progress_bar, risk_per_unit, spot_inv_num, spot_num)


def stop_state(t: dict) -> str:
    """Short phrase for a moved stop: 'at entry - risk-free' / 'trailed, +0.5R locked' / ''."""
    if t.get("closed") or not stop_trailed(t):
        return ""
    if t.get("be"):
        return "at entry - risk-free"
    locked = stop_locked_r(t)
    if locked is None:
        return "trailed"
    if locked > 0.05:
        return f"trailed, {locked:+.1f}R locked"
    if locked < -0.05:
        return f"trailed, risk {locked:+.1f}R"
    return "at entry - risk-free"


_EMO_CACHE: dict = {}

def emo(name: str, fallback: str = "") -> str:
    """Server custom emote by name, cached; falls back to unicode if missing."""
    if name in _EMO_CACHE:
        return _EMO_CACHE[name]
    val = fallback
    try:
        g = bot.get_guild(GUILD_ID) if GUILD_ID else None
        if g:
            e = discord.utils.get(g.emojis, name=name)
            if e:
                val = str(e)
    except Exception:
        pass
    _EMO_CACHE[name] = val
    return val

def unified_tp_text(t: dict, spot: bool = False, mark_next: bool = True) -> str:
    """ONE list: taken TPs (ticked, auto-numbered) then the presets still pending; the next pending one is flagged.
    Row shape: `✅ TP1  104,000  +1.3R  40% closed` / `○ TP2  108,000  +2.7R  30% ← next`."""
    taken = _tp_taken(t, spot)
    presets = _tp_presets(t, spot)
    tick = emo("tracked", "\u2705")
    ring = "\u25cb"
    rows = []
    n = 0
    pxf = (lambda v: f"{v:g}") if spot else fnum
    for f in taken:
        n += 1
        px = f["price"]
        if spot:
            extra = spot_pct_text(t, px).strip()
            rr = spot_signed_r(t, px)
            if rr is not None:
                extra += f" {rr:+.1f}R"
        else:
            r = signed_r(t, px)
            extra = f"{r:+.1f}R" if r is not None else ""
        rows.append(f"{tick} TP{n} \u00a0{pxf(px)} \u00a0{extra} \u00a0{f['pct']:g}% {'sold' if spot else 'closed'}")
    first_pending = True
    for i, k, px, planned in presets:
        if t.get(f"{k}_hit"):
            continue
        n += 1
        if spot:
            extra = spot_pct_text(t, px).strip()
        else:
            r = signed_r(t, px)
            extra = f"{r:+.1f}R" if r is not None else ""
        ptxt = f" \u00a0{planned:g}%" if planned is not None else ""
        nxt = " \u2190 next" if (mark_next and first_pending and taken and not t.get("closed")) else ""
        rows.append(f"{ring} TP{n} \u00a0{pxf(px)} \u00a0{extra}{ptxt}{nxt}")
        first_pending = False
    return "\n".join(rows)


def _closed_pct(t: dict, spot: bool) -> float:
    if spot:
        return sum(x["pct"] for x in (t.get("sells") or []) if str(x.get("label") or "") != "close")
    return fills_pct(t)


def card_color(t: dict, spot: bool = False) -> discord.Color:
    """Border by state: pending grey, live = analyst colour, profit taken green, closed = result colour."""
    if t.get("closed"):
        return {"WIN": discord.Color.from_str("#3ba55d"), "LOSS": discord.Color.from_str("#ed4245"),
                "BE": discord.Color.from_str("#747f8d"), "INVALID": discord.Color.from_str("#4f545c")}.get(t.get("result"), discord.Color.from_str("#747f8d"))
    taken = _tp_taken(t, spot)
    if taken:
        return discord.Color.from_str("#3ba55d")
    live = (t.get("zone_filled") or (t.get("buys") or []) or spot_num(t.get("avg_entry"))) if spot else any_entry_filled(t)
    if not live:
        return discord.Color.from_str("#4f545c")
    try:
        return discord.Color.from_str(t.get("analyst_color") or "#1C4E80")
    except Exception:
        return NAVY


def entry_display(t: dict, marks: bool = True) -> str:
    e1 = str(t.get("entry"))
    e2 = t.get("entry2")
    closed = t.get("closed")
    if not e2:
        s = e1
        if marks and not closed:
            s += " (filled)" if t.get("entry1_filled") else " (pending)"
        return s
    split = t.get("entry_split")
    split_txt = f" [{split}]" if split else ""
    if marks and not closed:
        m1 = f" {emo('tracked', chr(0x2713))}" if t.get("entry1_filled") else ""
        m2 = f" {emo('tracked', chr(0x2713))}" if t.get("entry2_filled") else ""
        return f"{e1}{m1} / {e2}{m2} (DCA{split_txt})"
    return f"{e1} / {e2} (DCA{split_txt})"

def full_status(t: dict) -> str:
    """One phrase for the title: 'TP1 hit · 40% closed · stop at entry' / 'waiting for fill' / 'closed +1.63R'."""
    if t.get("closed"):
        r = t.get("result_r")
        rtxt = f" {r:+.2f}R" if isinstance(r, (int, float)) else ""
        return {"WIN": f"closed{rtxt}", "LOSS": f"closed{rtxt}", "BE": "closed breakeven", "INVALID": "invalidated"}.get(t.get("result"), "closed")
    closed_pct = fills_pct(t)
    parts = []
    hit = next((n for n in (4, 3, 2, 1) if t.get(f"tp{n}_hit")), 0)
    if hit:
        parts.append(f"TP{hit} hit")
    elif t.get("entry2") and t.get("entry1_filled") and not t.get("entry2_filled"):
        parts.append("entry 1 filled · DCA pending")
    elif any_entry_filled(t):
        parts.append("live")
    else:
        return "waiting for fill"
    if closed_pct > 0:
        parts.append(f"{closed_pct:g}% closed")
    st = stop_state(t)
    if st:
        parts.append("stop " + st.split(",")[0] if st.startswith("trailed") else "stop " + st.replace(" - risk-free", ""))
        if st.startswith("trailed") and "locked" in st:
            parts[-1] = "stop " + st.replace("trailed, ", "").replace(" locked", " locked")
    return " \u00b7 ".join(parts)

def short_status(t: dict) -> str:
    base = "Pending"
    for n in (4, 3, 2, 1):
        if t.get(f"tp{n}_hit"):
            base = f"TP{n}"
            break
    else:
        if any_entry_filled(t):
            base = "Active"
    if t.get("be"):
        return base + " · BE"
    if stop_trailed(t) and not t.get("closed"):
        return base + " · trailed"
    return base

def jump_url(t: dict) -> str:
    return f"https://discord.com/channels/{GUILD_ID}/{t['channel_id']}/{t['message_id']}"

def within_edit_window(t: dict) -> bool:
    try:
        created = datetime.fromisoformat(t["created_at"])
    except Exception:
        return False
    return (datetime.now(timezone.utc) - created).total_seconds() <= EDIT_WINDOW_MIN * 60

def footer_with_edit(t: dict, base: str) -> str:
    if t.get("edited_at"):
        try:
            _ed = datetime.fromisoformat(t["edited_at"]).astimezone(IST)
            return base + f" \u00b7 edited {_ed.strftime('%d/%m %I:%M %p')}"
        except Exception:
            return base + " \u00b7 edited"
    if t.get("edited"):
        return base + " \u00b7 edited"
    return base

def _chips(items) -> str:
    return " ".join(f"`{x}`" for x in items if x)


def targets_inline(t: dict, spot: bool = False) -> str:
    """One line: ✅ 104,000 +1.3R (40%) · ○ 108,000 +2.7R (30%) · ○ 112,000 +4.0R (30%)"""
    taken = _tp_taken(t, spot)
    presets = _tp_presets(t, spot)
    tick = emo("tracked", "\u2705")
    pxf = (lambda v: f"{v:g}") if spot else fnum
    parts = []
    for f in taken:
        px = f["price"]
        if spot:
            extra = spot_pct_text(t, px).strip()
            rr = spot_signed_r(t, px)
            extra = (f"{rr:+.1f}R" if rr is not None else extra)
        else:
            r = signed_r(t, px)
            extra = f"{r:+.1f}R" if r is not None else ""
        parts.append(f"{tick} {pxf(px)} {extra} ({f['pct']:g}%)".replace("  ", " "))
    for _i, k, px, planned in presets:
        if t.get(f"{k}_hit"):
            continue
        if spot:
            extra = spot_pct_text(t, px).strip()
        else:
            r = signed_r(t, px)
            extra = f"{r:+.1f}R" if r is not None else ""
        ptxt = f" ({planned:g}%)" if planned is not None else ""
        parts.append(f"\u25cb {pxf(px)} {extra}{ptxt}".replace("  ", " "))
    return " \u00b7 ".join(parts)


def next_target(t: dict, spot: bool = False):
    """(label, price, extra) of the first pending preset, or None."""
    for i, k, px, planned in _tp_presets(t, spot):
        if t.get(f"{k}_hit"):
            continue
        if spot:
            extra = spot_pct_text(t, px).strip()
        else:
            r = signed_r(t, px)
            extra = f"{r:+.1f}R" if r is not None else ""
        bits = [f"TP{i + 1}", extra] + ([f"{planned:g}%"] if planned is not None else [])
        return (" \u00b7 ".join(b for b in bits if b), px)
    return None


def build_embed(t: dict, image_url: str = None) -> discord.Embed:
    """Dashboard card: chips + progress on top, Entry | Stop | Next, targets on one line, setup."""
    is_long = t["direction"] == "LONG"
    closed = t.get("closed")
    result = t.get("result")
    arrow = "\u25b2 LONG" if is_long else "\u25bc SHORT"
    tftxt = tf(t)
    prefix = {"WIN": "[WIN] ", "LOSS": "[LOSS] ", "BE": "[BE] ", "INVALID": "[INV] "}.get(result, "") if closed else ""
    title = f"{prefix}{arrow} {t['pair'].upper()}" + (f" \u00b7 {tftxt}" if tftxt else "")
    embed = discord.Embed(title=title[:256], color=card_color(t))

    rr = display_rr(t)
    chips = []
    if closed:
        r = t.get("result_r")
        chips.append(f"result {r:+.2f}R" if isinstance(r, (int, float)) else {"INVALID": "invalidated"}.get(result, "closed"))
    chips += [f"R:R {rr}" if rr else None, f"risk {fmt_risk(t.get('risk')) or '1%'}"]
    if not closed:
        br = banked_r(t)
        if br is not None and abs(br) > 0.005:
            chips.append(f"{br:+.1f}R banked")
        st = stop_state(t)
        if st:
            chips.append("stop " + (st.split(" - ")[0] if st.startswith("at entry") else st.replace("trailed, ", "")))
        if not any_entry_filled(t):
            chips.append("waiting for fill" if t.get("entry_type") != "MARKET" else "market")
        elif t.get("entry2") and not t.get("entry2_filled"):
            chips.append("entry 1 filled \u00b7 DCA pending")
    desc = _chips(chips)
    closed_pct = fills_pct(t)
    n_tp = len(_tp_presets(t))
    hit = next((n for n in (4, 3, 2, 1) if t.get(f"tp{n}_hit")), 0)
    if closed or closed_pct > 0:
        bar = progress_bar(100 if closed else closed_pct)
        tail = "position closed" if closed else f"{closed_pct:g}% closed"
        if hit and n_tp:
            tail += f" \u00b7 TP{hit} of {n_tp}"
        desc += f"\n{bar} {tail}"
    elif any_entry_filled(t) and n_tp:
        desc += f"\n{progress_bar(0)} live \u00b7 0 of {n_tp} targets"
    embed.description = desc

    # Entry | Stop | Next
    type_label = "market" if t.get("entry_type") == "MARKET" else "limit"
    e1 = first_num(t.get("entry"))
    e2 = first_num(t.get("entry2")) if t.get("entry2") else None
    if e2 is not None:
        m1 = " \u2713" if t.get("entry1_filled") and not closed else ""
        m2 = " \u2713" if t.get("entry2_filled") and not closed else ""
        entry_val = f"{fnum(e1)}{m1} / {fnum(e2)}{m2}"
        split = t.get("entry_split")
        sub = f"DCA{(' ' + split) if split else ''}"
        if t.get("entry1_filled") and t.get("entry2_filled"):
            avg_e = entry_num(t)
            if avg_e:
                sub += f" \u00b7 avg {fnum(avg_e)}"
        entry_val += f"\n{sub}"
    else:
        entry_val = fnum(e1) if e1 is not None else str(t.get("entry"))
        entry_val += f"\n{type_label} \u00b7 {'filled' if t.get('entry1_filled') else 'pending'}" if not closed else f"\n{type_label}"
        if t.get("entry2_cancelled"):
            entry_val += f" \u00b7 E2 {t['entry2_cancelled']} cancelled"
    embed.add_field(name="Entry", value=entry_val, inline=True)

    sl_shown = fnum(sl_num(t)) if sl_num(t) is not None else str(t.get("sl"))
    sl_val = f"{(t.get('sl_condition') + ' ') if t.get('sl_condition') else ''}{sl_shown}"
    if t.get("sl_hit"):
        sl_val += " (hit)"
    if stop_trailed(t):
        st = stop_state(t)
        sub = ("at entry" if (st.startswith("at entry") or t.get("be")) else "trailed") if not closed else "final"
        sl_val += f"\n{sub} \u00b7 posted {fnum(initial_sl_num(t))}"
    else:
        rpu = risk_per_unit(t)
        sl_val += f"\n1R = {fnum(rpu)}" if rpu else "\nposted"
    embed.add_field(name="Stop", value=sl_val, inline=True)

    if closed:
        ax = t.get("avg_exit")
        nxt_name, nxt_val = "Exit", (f"{fnum(ax)}\navg exit" if ax is not None else "-")
    else:
        nt = next_target(t)
        nxt_name = "Next"
        nxt_val = f"{fnum(nt[1])}\n{nt[0]}" if nt else "-\nall targets hit"
    embed.add_field(name=nxt_name, value=nxt_val, inline=True)

    tl = targets_inline(t)
    if tl:
        embed.add_field(name="Targets", value=tl[:1020], inline=False)

    fw = fmt_frameworks(t)
    if t.get("setup_detail"):
        fw = f"{fw} \u2014 {t['setup_detail']}" if fw and fw != "-" else t["setup_detail"]
    if fw and fw != "-":
        embed.add_field(name="Setup", value=fw[:1020], inline=False)
    if closed and t.get("close_note"):
        embed.add_field(name="Note", value=t["close_note"][:1020], inline=False)

    embed.set_author(name=t["analyst_name"], icon_url=t.get("analyst_avatar") or None)
    embed.set_footer(text=footer_with_edit(t, "Journal entry, not financial advice \u00b7 risk % = portfolio risked"))
    if image_url:
        embed.set_image(url=image_url)
    return embed


def build_spot_embed(p: dict, image_url: str = None) -> discord.Embed:
    closed = p.get("closed")
    result = p.get("result")
    prefix = {"WIN": "[WIN] ", "LOSS": "[LOSS] ", "BE": "[BE] ", "INVALID": "[INV] "}.get(result, "") if closed else ""
    title = f"{prefix}\u25c6 SPOT {p['pair'].upper()}"
    embed = discord.Embed(title=title[:256], color=card_color(p, spot=True))

    chips = []
    if closed:
        _rr = spot_result_r(p)
        chips.append(f"result {p['result_pct']}" if p.get("result_pct") else {"INVALID": "invalidated"}.get(result, "closed"))
        if _rr is not None:
            chips.append(f"{_rr:+.2f}R")
    else:
        chips.append((p.get("status") or "accumulating").lower())
    if p.get("allocation"):
        chips.append(f"alloc {fmt_risk(p['allocation'])}")
    if p.get("horizon"):
        chips.append(f"horizon {p['horizon']}")
    desc = _chips(chips)
    sold = _closed_pct(p, True)
    n_t = len(_tp_presets(p, spot=True))
    top = max((i for i, k in enumerate(("t1_hit", "t2_hit", "t3_hit"), 1) if p.get(k)), default=0)
    if closed or sold > 0:
        bar = progress_bar(100 if closed else sold)
        tail = "bag sold" if closed else f"{sold:g}% sold"
        if top and n_t:
            tail += f" \u00b7 target {top} of {n_t}"
        desc += f"\n{bar} {tail}"
    elif (p.get("zone_filled") or (p.get("buys") or [])) and n_t:
        desc += f"\n{progress_bar(0)} holding \u00b7 0 of {n_t} targets"
    embed.description = desc

    zone_val = str(p.get("dca_zone") or "-")
    if p.get("avg_entry"):
        zone_val += f"\navg {p['avg_entry']}"
    elif not closed and not p.get("zone_filled"):
        proj = spot_zone_projection(p)
        zone_val += f"\nif it fills \u2248 {proj:g}" if proj else "\nbidding"
    elif not closed:
        zone_val += "\nfilled"
    embed.add_field(name="Zone", value=zone_val, inline=True)
    embed.add_field(name="Invalidation", value=str(p.get("invalidation") or "-"), inline=True)
    if closed:
        embed.add_field(name="Exit", value=(f"{p['avg_exit']}\navg exit" if p.get("avg_exit") else "-"), inline=True)
    else:
        nt = next_target(p, spot=True)
        embed.add_field(name="Next", value=(f"{nt[1]:g}\n{nt[0].replace('TP', 'T')}" if nt else "-\nall targets hit"), inline=True)
    tl = targets_inline(p, spot=True)
    if tl:
        embed.add_field(name="Targets", value=tl[:1020], inline=False)
    if closed and p.get("close_note"):
        embed.add_field(name="Note", value=p["close_note"][:1020], inline=False)

    embed.set_author(name=p["analyst_name"], icon_url=p.get("analyst_avatar") or None)
    embed.set_footer(text=footer_with_edit(p, "Spot journal entry, not financial advice"))
    if image_url:
        embed.set_image(url=image_url)
    return embed


def _ac_label(t: dict, spot: bool = False) -> str:
    """Grouped autocomplete label: analyst · side · pair - status."""
    who = t.get("analyst_name", "?")
    if spot:
        return f"{who} · \U0001F48E SPOT · {t['pair'].upper()} - {spot_status_line(t)}"
    side = "\u25b2 LONG" if str(t.get("direction", "")).upper() == "LONG" else "\u25bc SHORT"
    return f"{who} · {side} · {t['pair'].upper()} {tf(t)} - {short_status(t)}"

def _ac_sortkey(t: dict, spot: bool = False, uid: int = 0):
    # invoker's own trades first, then other analysts alphabetically;
    # within each analyst: longs, then shorts, spot always last
    own_rank = 0 if t.get("analyst_id") == uid else 1
    side_rank = 2 if spot else (0 if str(t.get("direction", "")).upper() == "LONG" else 1)
    return (own_rank, t.get("analyst_name", "z").lower(), side_rank, t.get("pair", ""))

async def refresh_and_edit(t: dict, spot_mode: bool = False):
    channel = bot.get_channel(t["channel_id"])
    msg = await channel.fetch_message(t["message_id"])
    builder = build_spot_embed if spot_mode else build_embed
    embed = builder(t)
    if msg.attachments:
        # reference the existing attachment (attachment://) instead of its CDN url -
        # the CDN url makes Discord render the chart twice (attachment + embed image)
        embed.set_image(url=f"attachment://{msg.attachments[0].filename}")
        await msg.edit(embed=embed, attachments=list(msg.attachments))
    else:
        await msg.edit(embed=embed)
    return msg

async def thread_note(t: dict, text: str):
    if t.get("thread_id"):
        try:
            th = bot.get_channel(t["thread_id"]) or await bot.fetch_channel(t["thread_id"])
            await th.send(text)
        except Exception:
            pass

async def post_update_feed(t: dict, title: str, color: discord.Color, line: str, footer: str = "Sigma Trading - Trade Updates"):
    if not TRADE_UPDATES_CHANNEL_ID:
        return
    ch = bot.get_channel(TRADE_UPDATES_CHANNEL_ID)
    if ch is None:
        return
    e = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    pair_line = f"**{t['pair'].upper()}"
    if t.get("kind") == "spot":
        pair_line += " SPOT"
    else:
        pair_line += f" {t['direction']}" + (f" - {tf(t)}" if tf(t) else "")
    e.description = f"{pair_line}**\n{line}\n[View original call]({jump_url(t)})"
    e.set_author(name=t["analyst_name"], icon_url=t.get("analyst_avatar") or None)
    e.set_footer(text=footer)
    await ch.send(embed=e)

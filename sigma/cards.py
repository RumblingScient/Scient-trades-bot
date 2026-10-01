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
from sigma.calculations import _tp_presets, _tp_taken, any_entry_filled, display_rr, entry_num, fills_pct, fmt_frameworks, fmt_risk, fnum, signed_r, spot_pct_text, spot_result_r, spot_signed_r, spot_status_line, spot_zone_projection, tf


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

def unified_tp_text(t: dict, spot: bool = False) -> str:
    """ONE list: taken TPs (auto-numbered, ticked) then the presets still pending (plan)."""
    taken = _tp_taken(t, spot)
    presets = _tp_presets(t, spot)
    tick = emo("tracked", chr(0x2705))
    rows = []
    n = 0
    for f in taken:
        n += 1
        px = f["price"]
        extra = ""
        if spot:
            extra = spot_pct_text(t, px)
            rr = spot_signed_r(t, px)
            if rr is not None:
                extra += f" {rr:+.1f}R"
        else:
            r = signed_r(t, px)
            extra = f" ({r:+.1f}R)" if r is not None else ""
        rows.append(f"TP{n} \u00b7 {f['pct']:g}% @ {(f'{px:g}' if spot else fnum(px))}{extra} {tick}")
    for i, k, px, planned in presets:
        if t.get(f"{k}_hit"):
            continue
        n += 1
        extra = spot_pct_text(t, px) if spot else (f" ({signed_r(t, px):+.1f}R)" if signed_r(t, px) is not None else "")
        ptxt = f" [{planned:g}%]" if planned is not None else ""
        rows.append(f"TP{n} \u00b7 {(f'{px:g}' if spot else fnum(px))}{extra}{ptxt}")
    if not rows:
        return ""
    done = sum(x["pct"] for x in taken)
    if t.get("closed") or not taken:
        return "\n".join(rows)
    left = max(0.0, 100 - (fills_pct(t) if not spot else done))
    return "\n".join(rows) + f"\n{done:g}% {'sold' if spot else 'closed'} \u00b7 {left:g}% {'still held' if spot else 'running'}"

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
    if t.get("closed"):
        r = t.get("result_r")
        rtxt = f" ({r:+.2f}R)" if isinstance(r, (int, float)) else ""
        return {
            "WIN": f"CLOSED - WIN{rtxt}",
            "LOSS": f"CLOSED - LOSS{rtxt}",
            "BE": f"CLOSED - BREAKEVEN{rtxt}",
            "INVALID": "INVALIDATED",
        }.get(t.get("result"), "CLOSED")
    closed_pct = fills_pct(t)
    pct_txt = f" - {closed_pct:g}% closed" if closed_pct > 0 else ""
    if t.get("tp4_hit"):
        return f"TP4 HIT{pct_txt}"
    if t.get("tp3_hit"):
        return f"TP3 HIT{pct_txt}"
    if t.get("tp2_hit"):
        return f"TP2 HIT{pct_txt}"
    if t.get("tp1_hit"):
        return f"TP1 HIT{pct_txt}"
    if t.get("be"):
        return "SL AT ENTRY - RISK-FREE" + pct_txt
    if t.get("entry2") and t.get("entry1_filled") and not t.get("entry2_filled"):
        return "ACTIVE - Entry 1 filled, DCA pending"
    if any_entry_filled(t):
        return "ACTIVE" + pct_txt
    return "PENDING - waiting for fill"

def short_status(t: dict) -> str:
    if t.get("tp4_hit"):
        return "TP4"
    if t.get("tp3_hit"):
        return "TP3"
    if t.get("tp2_hit"):
        return "TP2"
    if t.get("tp1_hit"):
        return "TP1"
    if t.get("be"):
        return "BE"
    if any_entry_filled(t):
        return "Active"
    return "Pending"

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

def build_embed(t: dict, image_url: str = None) -> discord.Embed:
    is_long = t["direction"] == "LONG"
    closed = t.get("closed")
    result = t.get("result")
    try:
        color = discord.Color.from_str(t.get("analyst_color") or "#1C4E80")
    except Exception:
        color = NAVY
    arrow = "\u25b2 LONG" if is_long else "\u25bc SHORT"
    prefix = ""
    if closed:
        prefix = {"WIN": "[WIN] ", "LOSS": "[LOSS] ", "BE": "[BE] ", "INVALID": "[INV] "}.get(result, "")
    elif not any_entry_filled(t):
        prefix = "[PENDING] "
    tftxt = tf(t)
    title = f"{prefix}{arrow} | {t['pair'].upper()}"
    if tftxt:
        title += f" | {tftxt}"
    embed = discord.Embed(title=title, color=color)

    sl_mark = f" {emo('invalid', '')}(hit)" if t.get("sl_hit") else ""
    type_label = "Market" if t.get("entry_type") == "MARKET" else "Limit"
    sl_val = f"{(t.get('sl_condition') + ' ') if t.get('sl_condition') else ''}{t['sl']}{sl_mark}"

    # Row 1: three inline columns - Entry | Stop Loss | Risk / R:R
    entry_val = entry_display(t) or "-"
    if t.get("entry2") and t.get("entry1_filled") and t.get("entry2_filled") and not closed:
        avg_e = entry_num(t)
        if avg_e:
            entry_val += f"\nAvg {fnum(avg_e)}"
    embed.add_field(name=f"Entry ({type_label})", value=entry_val, inline=True)
    embed.add_field(name="Stop Loss", value=sl_val, inline=True)
    rr = display_rr(t)
    risk_rr = fmt_risk(t.get("risk")) or "-"
    if rr:
        risk_rr += f"  ·  R:R {rr}"
    embed.add_field(name="Risk", value=risk_rr, inline=True)

    # Take profits - one list: taken (ticked) then pending presets
    _tpt = unified_tp_text(t)
    if _tpt:
        embed.add_field(name="Take Profits", value=_tpt[:1020], inline=False)

    # Setup (full width)
    fw = fmt_frameworks(t)
    if t.get("setup_detail"):
        fw = f"{fw} - {t['setup_detail']}"
    if fw and fw != "-":
        embed.add_field(name="Setup", value=fw[:1020], inline=False)

    # Status (full width)
    embed.add_field(name="Status", value=full_status(t), inline=False)

    if closed and t.get("avg_exit") is not None:
        embed.add_field(name="Avg Exit", value=fnum(t["avg_exit"]), inline=True)
    if closed and t.get("close_note"):
        embed.add_field(name="Note", value=t["close_note"][:1020], inline=False)

    embed.set_author(name=t["analyst_name"], icon_url=t.get("analyst_avatar") or None)
    embed.set_footer(text=footer_with_edit(t, "Journal entry, not financial advice · Risk % = portfolio risked · Never risk more than you can afford to lose"))
    if image_url:
        embed.set_image(url=image_url)
    return embed

def build_spot_embed(p: dict, image_url: str = None) -> discord.Embed:
    closed = p.get("closed")
    result = p.get("result")
    try:
        color = discord.Color.from_str(p.get("analyst_color") or "#1C4E80")
    except Exception:
        color = NAVY
    prefix = ""
    if closed:
        prefix = {"WIN": "[WIN] ", "LOSS": "[LOSS] ", "BE": "[BE] ", "INVALID": "[INV] "}.get(result, "")
    title = f"{prefix}\u25c6 SPOT | {p['pair'].upper()}"
    embed = discord.Embed(title=title, color=color)

    if closed:
        embed.add_field(name="Avg Entry", value=str(p.get("avg_entry") or "-"), inline=True)
        if p.get("avg_exit"):
            embed.add_field(name="Avg Exit", value=str(p["avg_exit"]), inline=True)
        _rr = spot_result_r(p)
        if p.get("result_pct") or _rr is not None:
            embed.add_field(name="Result", value=" \u00b7 ".join(x for x in (str(p.get("result_pct") or ""), f"{_rr:+.2f}R" if _rr is not None else "") if x), inline=True)
    else:
        zone_mark = " (filled)" if p.get("zone_filled") else ""
        embed.add_field(name="DCA Zone", value=f"{p['dca_zone']}{zone_mark}", inline=True)
        if p.get("allocation"):
            embed.add_field(name="Allocation", value=fmt_risk(p["allocation"]) or "-", inline=True)
        if p.get("avg_entry"):
            embed.add_field(name="Avg Entry", value=str(p["avg_entry"]), inline=True)
        if p.get("horizon"):
            embed.add_field(name="Horizon", value=str(p["horizon"]), inline=True)
        if not p.get("zone_filled"):
            proj = spot_zone_projection(p)
            if proj:
                embed.add_field(name="If Zone Fills", value=f"avg entry \u2248 {proj:g}", inline=True)
        _spt = unified_tp_text(p, spot=True)
        if _spt:
            embed.add_field(name="Take Profits", value=_spt[:1020], inline=False)
        if p.get("invalidation"):
            embed.add_field(name="Invalidation", value=str(p["invalidation"]), inline=False)
    embed.add_field(name="Status", value=spot_status_line(p), inline=False)
    if closed and p.get("close_note"):
        embed.add_field(name="Note", value=p["close_note"][:1020], inline=False)

    embed.set_author(name=p["analyst_name"], icon_url=p.get("analyst_avatar") or None)
    embed.set_footer(text=footer_with_edit(p, "Spot journal entry, not financial advice · Never risk more than you can afford to lose"))
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

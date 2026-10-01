"""sigma.services.trades - every state change on a futures setup or spot play.

Pure functions: they take the record dict, mutate it, and return an Outcome describing what to
show. They never touch Discord. Slash commands (/setup ...), the old alias commands, card
buttons (Phase 2) and the tracker all go through here, so there is exactly one code path per
action. Input problems raise UserError with a plain sentence.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sigma.config import BLUE, DGREY, GREEN, GREY, RED, SPOT_STATUSES
from sigma.errors import UserError
from sigma.calculations import (
    _is_tp_fill, _sync_tp_flags, entry_num, fills_pct, finalize_close, first_num, fnum,
    parse_num, parse_sl, parse_spot_split, sl_num, spot_num, spot_ref_entry, spot_signed_r,
    spot_weighted_entry,
)

BE_R_BAND = 0.05      # |R| at or under this = breakeven
BE_PCT_BAND = 0.5     # spot with no invalidation: |%| at or under this = breakeven

FUT_TP_KEYS = ("tp1", "tp2", "tp3", "tp4")
SPOT_TP_KEYS = ("t1", "t2", "t3")

# (value, label) - the single event list for /setup update, both markets
EVENTS = [
    ("EF1", "Entry filled - futures: entry 1 · spot: buy at price (tp_pct = % of bag), no price = zone filled"),
    ("EF2", "Entry 2 filled (DCA) - futures only"),
    ("TPN", "Preset TP reached - next planned one, or all up to price"),
    ("PTP", "Take profit at a price - any level, auto-numbered (needs price + tp_pct)"),
    ("BE",  "Stop to entry - risk-free (futures only)"),
    ("SLU", "Stop updated - new level or condition (spot: invalidation)"),
    ("SL",  "Stop hit - closes the trade (futures only)"),
    ("CI",  "Invalidated - never triggered / thesis gone"),
    ("UL",  "Undo last take profit"),
    ("RTP", "Rebuild TP list from recorded fills (repair)"),
]


@dataclass
class Outcome:
    desc: str                       # short line for the thread note + ephemeral reply
    title: str = ""                 # update-feed title
    color: object = GREY
    line: str = ""                  # update-feed body
    closed: bool = False
    result_txt: str = ""            # " (+2.10R)" style suffix, already formatted
    extra: str = ""                 # appended to the ephemeral reply only
    changes: list = field(default_factory=list)
    silent: bool = False            # no update-feed post (repairs, tracking toggles)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _grade_r(r):
    return "WIN" if r > BE_R_BAND else "LOSS" if r < -BE_R_BAND else "BE"


def _grade_pct(pc):
    return "WIN" if pc > BE_PCT_BAND else "LOSS" if pc < -BE_PCT_BAND else "BE"


# ----------------------------------------------------------------------------- new records

def _tp_plan(tp_split, n_tps):
    if not tp_split:
        return None
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(tp_split))]
    if not nums or len(nums) > n_tps:
        raise UserError(f"tp_split has {len(nums)} number(s) but only {n_tps} TP level(s) are set - it can cover fewer TPs, not more.")
    total = sum(nums)
    if total <= 0 or total > 100.5:
        raise UserError("tp_split must add up to 100 or less (the rest rides).")
    return [round(x, 1) for x in nums]


def _entry_split_disp(entry_split):
    nums = re.findall(r"\d+(?:\.\d+)?", str(entry_split))
    if len(nums) >= 2:
        a, b = float(nums[0]), float(nums[1])
        if a + b > 0:
            a_pct = round(a / (a + b) * 100)
            return f"{a_pct}% / {100 - a_pct}%"
    raise UserError("Couldn't read the entry split - use a format like 20/80.")


def build_futures(*, analyst: dict, pair: str, direction: str, entry: str, stop: str,
                  tp1=None, tp2=None, tp3=None, tp4=None, tp_split=None, entry_type=None,
                  entry2=None, entry_split=None, risk="1", timeframe=None, frameworks=(),
                  context=None, notes=None) -> dict:
    """Validated futures record (no message ids yet). entry_type defaults: DCA if entry2, else Limit."""
    s_num, s_cond = parse_sl(stop)
    if s_num is None:
        raise UserError("Couldn't find a price in the stop - e.g. `63000` or `4h close below 63000`.")
    e_num = first_num(entry)
    if e_num is None:
        raise UserError("Entry must contain a price.")
    if direction == "LONG" and float(s_num) >= e_num:
        raise UserError(f"Stop must be below entry for a long. You entered {fnum(float(s_num))} vs entry {fnum(e_num)}.")
    if direction == "SHORT" and float(s_num) <= e_num:
        raise UserError(f"Stop must be above entry for a short. You entered {fnum(float(s_num))} vs entry {fnum(e_num)}.")
    etype = (entry_type or ("DCA" if entry2 else "LIMIT")).upper()
    if etype == "DCA" and not entry2:
        raise UserError("DCA needs **entry2** (the second entry price).")
    if etype != "DCA":
        entry2 = None
        entry_split = None
    split_disp = _entry_split_disp(entry_split) if (etype == "DCA" and entry_split) else None
    tps = [tp1, tp2, tp3, tp4]
    tp_plan = _tp_plan(tp_split, sum(1 for x in tps if x))
    is_market = etype == "MARKET"
    frameworks = [f for f in frameworks if f]
    return {
        "sl_condition": s_cond,
        "analyst_id": analyst["id"], "analyst_name": analyst["name"],
        "analyst_avatar": analyst.get("avatar"), "analyst_key": analyst.get("key"),
        "analyst_color": analyst.get("color"),
        "pair": pair, "direction": direction, "timeframe": timeframe,
        "framework": frameworks[0] if frameworks else None, "frameworks": frameworks, "setup_detail": context,
        "entry": entry, "entry2": entry2, "entry_split": split_disp, "tp_split": tp_plan, "sl": s_num,
        "entry_type": "MARKET" if is_market else "LIMIT",
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp4": tp4, "risk": risk or "1", "notes": notes,
        "created_at": _now(),
        "entry1_filled": bool(is_market), "entry2_filled": False,
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False, "tp4_hit": False, "sl_hit": False, "be": False,
        "fills": [], "avg_exit": None,
        "closed": False, "result": None, "result_r": None, "close_note": None,
        "edited": False, "edited_at": None,
    }


def build_spot(*, analyst: dict, pair: str, zone: str, tp1: str, tp2=None, tp3=None, tp_split=None,
               stop=None, avg_entry=None, play_type=None, allocation=None, horizon=None, notes=None) -> dict:
    """Validated spot record. play_type defaults: Scaling if avg_entry given, else Fresh."""
    pt = (play_type or ("SCALING" if avg_entry else "FRESH")).upper()
    n_t = sum(1 for x in (tp1, tp2, tp3) if x)
    split_list, split_err = parse_spot_split(tp_split, n_t)
    if split_err:
        raise UserError(split_err)
    if pt in ("SCALING", "FILLED") and not avg_entry:
        raise UserError(f"avg_entry is required for a {pt.title()} play - that's the number members anchor to.")
    if spot_num(tp1) is None:
        raise UserError("tp1 must be a price.")
    return {
        "kind": "spot",
        "analyst_id": analyst["id"], "analyst_name": analyst["name"],
        "analyst_avatar": analyst.get("avatar"), "analyst_key": analyst.get("key"),
        "analyst_color": analyst.get("color"),
        "pair": pair, "dca_zone": zone, "allocation": allocation,
        "avg_entry": (avg_entry if pt != "FRESH" else None), "avg_exit": None,
        "t1": tp1, "t2": tp2, "t3": tp3, "tp_split": split_list,
        "t1_hit": False, "t2_hit": False, "t3_hit": False,
        "invalidation": stop, "thesis": notes, "horizon": horizon,
        "buys": ([{"price": spot_num(avg_entry), "pct": None}] if pt != "FRESH" and spot_num(avg_entry) else []),
        "status": ("HOLDING" if pt == "FILLED" else "ACCUMULATING"),
        "zone_filled": (pt == "FILLED"),
        "created_at": _now(),
        "closed": False, "result": None, "result_pct": None, "close_note": None,
        "edited": False, "edited_at": None,
    }


# ----------------------------------------------------------------------------- events

def _pct_or_plan(t, key, pct, spot):
    """tp_pct given -> use it; else the planned % for this preset; else error."""
    if pct is None:
        plan = t.get("tp_split") or []
        idx = int(key[-1]) - 1
        if idx < len(plan):
            pct = float(plan[idx])
    if pct is None:
        raise UserError("**tp_pct is required** - how much was closed at this level? (e.g. 25)")
    if pct <= 0 or pct > 100:
        raise UserError("tp_pct must be between 0 and 100.")
    done = sum(s["pct"] for s in (t.get("sells") or [])) if spot else fills_pct(t)
    if done + pct > 100.01:
        raise UserError(f"That would close {done + pct:g}% in total - only {100 - done:g}% is left.")
    return pct


def apply_event(kind: str, t: dict, event: str, price=None, pct=None, note=None) -> Outcome:
    """Apply one lifecycle event. kind = 'fut' | 'spot'. price/pct are raw strings or numbers."""
    spot = kind == "spot"
    ev = event
    px = spot_num(price) if spot else parse_num(price)
    pct = parse_num(pct)
    t.setdefault("fills" if not spot else "sells", [])
    keys = SPOT_TP_KEYS if spot else FUT_TP_KEYS
    is_long = True if spot else (t.get("direction") == "LONG")
    numf = spot_num if spot else first_num
    fmt = (lambda v: f"{v:g}") if spot else fnum

    if ev == "RTP":
        before = {k: bool(t.get(f"{k}_hit")) for k in keys}
        _sync_tp_flags(t, spot=spot)
        fixed = [k.upper() for k in before if before[k] != bool(t.get(f"{k}_hit"))]
        return Outcome(desc="TP list rebuilt from recorded fills" + (f" - {', '.join(fixed)} adjusted" if fixed else " - no change needed"),
                       silent=True)

    if ev == "UL":
        if spot:
            rows = t.get("sells") or []
            if not rows:
                raise UserError("Nothing to undo - no take profits recorded.")
            gone = rows.pop()
            t["sells"] = rows
        else:
            rows = t.get("fills") or []
            if not rows or not _is_tp_fill(rows[-1]):
                raise UserError("Nothing to undo - the last recorded fill isn't a take profit.")
            gone = rows.pop()
            t["fills"] = rows
        _sync_tp_flags(t, spot=spot)
        return Outcome(desc=f"undid last take profit ({gone.get('pct', 0):g}% @ {fmt(gone.get('price', 0))})",
                       title="Take profit undone", color=GREY, line="Last take profit removed - recorded in error.")

    if ev == "TPN":
        pend = [(k, numf(t.get(k))) for k in keys if t.get(k) and numf(t.get(k)) is not None and not t.get(f"{k}_hit")]
        if not pend:
            raise UserError("No planned targets left on this card - use **Take profit at a price**.")
        if px is not None:
            reached = [k for k, tpx in pend if (px >= tpx if is_long else px <= tpx)]
            if not reached:
                raise UserError(f"Price {fmt(px)} hasn't reached the next planned target ({fmt(pend[0][1])}) - use **Take profit at a price** for a discretionary exit.")
            ev = reached[-1].upper()
        else:
            ev = pend[0][0].upper()

    if ev in [k.upper() for k in keys]:
        k = ev.lower()
        tp_px = numf(t.get(k))
        if tp_px is None:
            raise UserError(f"{ev} has no price set on the card.")
        pct = _pct_or_plan(t, k, pct, spot)
        fill_price = px if px is not None else tp_px
        for prev in keys[:keys.index(k)]:
            t[f"{prev}_hit"] = True
        t[f"{k}_hit"] = True
        label = f"TP{keys.index(k) + 1}"
        if spot:
            t.setdefault("sells", []).append({"pct": round(pct, 1), "price": fill_price, "label": label})
        else:
            t["entry1_filled"] = True
            t.setdefault("fills", []).append({"price": fill_price, "pct": pct, "label": label})
        _sync_tp_flags(t, spot=spot)
        out = Outcome(desc=f"{label} reached @ {fmt(fill_price)} ({pct:g}%)", title=f"{label} reached", color=GREEN)
        return _after_tp(kind, t, out, note)

    if ev == "PTP":
        if px is None:
            raise UserError("**price is required** - where did you take profit?")
        if pct is None:
            raise UserError("**tp_pct is required** - how much was closed at this price? (e.g. 25)")
        if pct <= 0 or pct > 100:
            raise UserError("tp_pct must be between 0 and 100.")
        done = sum(s["pct"] for s in (t.get("sells") or [])) if spot else fills_pct(t)
        if done + pct > 100.01:
            raise UserError(f"That would close {done + pct:g}% in total - only {100 - done:g}% is left.")
        reached = []
        for k in keys:
            tpx = numf(t.get(k))
            if tpx is None or t.get(f"{k}_hit"):
                continue
            if (is_long and px >= tpx) or (not is_long and px <= tpx):
                t[f"{k}_hit"] = True
                reached.append(k.upper())
        n = (len([s for s in t.get("sells", []) if s.get("price") is not None]) if spot
             else sum(1 for f in (t.get("fills") or []) if _is_tp_fill(f))) + 1
        label = f"TP{n}"
        if spot:
            t.setdefault("sells", []).append({"pct": round(pct, 1), "price": px, "label": label})
        else:
            t["entry1_filled"] = True
            t.setdefault("fills", []).append({"price": px, "pct": pct, "label": label})
        _sync_tp_flags(t, spot=spot)
        out = Outcome(desc=f"{label} taken @ {fmt(px)} ({pct:g}%)" + (f" - planned {', '.join(reached)} reached" if reached else ""),
                      title=f"{label} taken", color=GREEN)
        return _after_tp(kind, t, out, note)

    if ev == "EF1":
        if spot:
            if px is None:
                t["zone_filled"] = True
                if t.get("status") == "ACCUMULATING":
                    t["status"] = "HOLDING"
                return Outcome(desc="zone filled - position built", title="Zone filled", color=BLUE, line="DCA zone fully filled.")
            if pct is not None and not (0 < pct <= 100):
                raise UserError("tp_pct here = % of the planned bag this buy was - between 0 and 100.")
            t.setdefault("buys", []).append({"price": px, "pct": pct})
            auto = spot_weighted_entry(t)
            if auto:
                t["avg_entry"] = f"{auto:g}"
            ptxt = f" ({pct:g}% of the bag)" if pct else ""
            return Outcome(desc=f"buy @ {px:g}{ptxt} -> avg {t.get('avg_entry')}", title="Buy filled", color=BLUE,
                           line=f"Bought @ {px:g}{ptxt}. Average entry now {t.get('avg_entry')}.")
        t["entry1_filled"] = True
        d = "Entry 1 filled" if t.get("entry2") else "Entry filled"
        return Outcome(desc=d, title=d, color=BLUE, line=d + ".")

    if ev == "EF2":
        if spot:
            raise UserError("Spot has no Entry 2 - log each buy with **Entry filled** + price.")
        if not t.get("entry2"):
            raise UserError("This trade has no DCA entry (entry2) - use Entry filled.")
        t["entry2_filled"] = True
        t["entry1_filled"] = True
        d = "DCA entry filled - full position live"
        return Outcome(desc=d, title="Entry 2 filled", color=BLUE, line=d + ".")

    if ev == "BE":
        if spot:
            raise UserError("Spot has no stop to move - update the invalidation with **Stop updated** if the thesis changed.")
        t["be"] = True
        d = "SL moved to entry - trade is risk-free"
        return Outcome(desc=d, title="Stop to entry", color=GREY, line=d + ".")

    if ev == "SLU":
        raw = price if price not in (None, "") else None
        if raw is None:
            raise UserError("**price is required** - the new level, e.g. `64000` or `4h close below 64000`.")
        s_num, s_cond = parse_sl(raw)
        if s_num is None:
            raise UserError("Couldn't find a price in that - e.g. `64000` or `4h close below 64000`.")
        if spot:
            t["invalidation"] = str(raw).strip()
            d = f"invalidation -> {t['invalidation']}"
            return Outcome(desc=d, title="Invalidation updated", color=GREY, line=d)
        t["sl"] = s_num
        t["sl_condition"] = s_cond
        t["be"] = False
        shown = (s_cond + " " if s_cond else "") + s_num
        d = "SL updated -> " + shown
        return Outcome(desc=d, title="Stop updated", color=GREY, line=d)

    if ev == "SL":
        if spot:
            raise UserError("Spot has no stop - close it with **/setup close** at the price you sold.")
        if t.get("sl_condition") and px is None and not t.get("be"):
            raise UserError(f"This trade has a **soft SL** ({t['sl_condition']} {t['sl']}). **price is required** - at what exact price did it close?")
        exit_px = px if px is not None else (entry_num(t) if t.get("be") else sl_num(t))
        if exit_px is None:
            raise UserError("Couldn't read the SL price - pass `price` with this update.")
        t["sl_hit"] = not t.get("be")
        return _close_futures(t, exit_px, note, desc="Stopped out - closed")

    if ev == "CI":
        if spot:
            had = bool(spot_num(t.get("avg_entry")) or t.get("zone_filled") or (t.get("sells") or []))
            if had:
                raise UserError("This play had fills - close it with **/setup close** at the price you cut, so the journal records it.")
            t["closed"] = True
            t["result"] = "INVALID"
            t["result_pct"] = None
            t["result_r"] = None
            t["closed_at"] = _now()
            t["close_note"] = note or "Zone never filled - play cancelled before entry"
            t["status"] = "INVALIDATED"
            return Outcome(desc="Invalidated", title="Spot Invalidated", color=DGREY, line="Thesis invalidated.", closed=True)
        t["closed"] = True
        t["result"] = "INVALID"
        t["closed_at"] = _now()
        if note:
            t["close_note"] = note
        return Outcome(desc="Invalidated", title="Invalidated", color=DGREY, line="Setup invalidated before trigger.", closed=True)

    raise UserError(f"Unknown event `{event}`.")


def _after_tp(kind, t, out: Outcome, note):
    """Shared tail for TP events: position-left line, and spot auto-close at 100% sold."""
    spot = kind == "spot"
    done = sum(s["pct"] for s in (t.get("sells") or [])) if spot else fills_pct(t)
    if spot and done >= 99.99 and not t.get("closed"):
        res = close(kind, t, None, note or "Bag fully sold - closed automatically")
        res.desc = out.desc + " - " + res.desc
        return res
    out.line = out.desc + (f"\n{done:g}% closed, {100 - done:g}% running" if 0 < done < 100 else "")
    return out


# ----------------------------------------------------------------------------- close

def _result_suffix(t):
    r = t.get("result_r")
    rtxt = f"{r:+.2f}R" if isinstance(r, (int, float)) else ""
    pc = t.get("result_pct")
    if pc and rtxt:
        return f" ({pc} · {rtxt})"
    if pc:
        return f" ({pc})"
    return f" ({rtxt})" if rtxt else ""


def _close_futures(t, exit_px, note, desc="Closed") -> Outcome:
    avg_exit, r = finalize_close(t, exit_px)
    if r is None:
        raise UserError("Couldn't calculate the result - check that entry and stop are numeric on this trade.")
    t["closed"] = True
    t["avg_exit"] = avg_exit
    t["result_r"] = round(r, 2)
    t["result"] = _grade_r(r)
    t["closed_at"] = _now()
    if note:
        t["close_note"] = note
    res = t["result"]
    rtxt = _result_suffix(t)
    title = f"Closed - {'Win' if res == 'WIN' else 'Loss' if res == 'LOSS' else 'Breakeven'}{rtxt}"
    color = GREEN if res == "WIN" else RED if res == "LOSS" else GREY
    return Outcome(desc=desc, title=title, color=color, line=f"{desc}{rtxt} | Avg exit: {fnum(avg_exit)}",
                   closed=True, result_txt=rtxt)


def close(kind: str, t: dict, price=None, note=None) -> Outcome:
    """Close at a price. The bot grades WIN / LOSS / BE itself. Invalidated is an update event, not a close."""
    if t.get("closed"):
        raise UserError("Already closed. Use **/setup reopen** first if the close was wrong.")
    if kind != "spot":
        px = parse_num(price)
        rem = 100 - fills_pct(t)
        if rem > 0.01 and px is None:
            raise UserError(f"**price is required** - {rem:g}% of the position is still open. At what price was it closed?")
        return _close_futures(t, px, note)
    # spot
    px = spot_num(price)
    had_position = bool(spot_num(t.get("avg_entry")) or t.get("zone_filled") or (t.get("sells") or []) or (t.get("buys") or []))
    if not had_position:
        raise UserError("This play never filled - mark it **Invalidated** with /setup update instead of closing it.")
    sells = list(t.get("sells") or [])
    rem = 100 - sum(s["pct"] for s in sells)
    if rem > 0.01:
        if px is None:
            raise UserError(f"**price is required** - {rem:g}% of the bag is still held. At what price did you sell it?")
        sells.append({"pct": round(rem, 1), "price": px, "label": "close"})
    tot = sum(s["pct"] for s in sells)
    avg_exit = sum(s["pct"] * s["price"] for s in sells) / tot if tot > 0 else px
    if avg_exit is None:
        raise UserError("Couldn't work out the exit - give `price`.")
    t["sells"] = sells
    ref = spot_ref_entry(t)
    pc = ((avg_exit - ref) / ref * 100) if (ref and ref > 0) else None
    r = spot_signed_r(t, avg_exit)
    t["closed"] = True
    t["avg_exit"] = f"{avg_exit:g}"
    t["result_pct"] = f"{pc:+.1f}%" if pc is not None else None
    t["result_r"] = round(r, 2) if r is not None else None
    if r is not None:
        t["result"] = _grade_r(r)
    elif pc is not None:
        t["result"] = _grade_pct(pc)
    else:
        raise UserError("Couldn't grade this play - it has no numeric average entry. Set one with /setup edit, then close.")
    t["closed_at"] = _now()
    if note:
        t["close_note"] = note
    _sync_tp_flags(t, spot=True)
    res = t["result"]
    rtxt = _result_suffix(t)
    title, color, line = {
        "WIN": (f"Spot Closed - Win{rtxt}", GREEN, "Play closed in profit."),
        "LOSS": (f"Spot Closed - Loss{rtxt}", RED, "Play closed at a loss."),
        "BE": ("Spot Closed - Breakeven", GREY, "Play closed flat."),
    }[res]
    return Outcome(desc=f"Closed - {res}", title=title, color=color, line=line, closed=True, result_txt=rtxt)


# ----------------------------------------------------------------------------- edit / fix

def edit(kind: str, t: dict, fields: dict) -> list:
    """Apply card-field corrections. fields = {name: value} with only the changed ones. Returns change labels."""
    spot = kind == "spot"
    ch = []
    v = fields
    if v.get("pair") is not None:
        t["pair"] = v["pair"]; ch.append("pair")
    if spot:
        if v.get("entry") is not None:
            t["dca_zone"] = v["entry"]; ch.append("zone")
        if v.get("stop") is not None:
            t["invalidation"] = v["stop"]; ch.append("invalidation")
        if v.get("risk") is not None:
            t["allocation"] = v["risk"]; ch.append("allocation")
        for k, key in (("tp1", "t1"), ("tp2", "t2"), ("tp3", "t3")):
            if v.get(k) is not None:
                t[key] = v[k]; ch.append(k.upper())
        if v.get("tp_split") is not None:
            raw = str(v["tp_split"]).strip()
            if not raw:
                t["tp_split"] = []; ch.append("TP split removed")
            else:
                n_t = sum(1 for k in SPOT_TP_KEYS if t.get(k))
                sl, se = parse_spot_split(raw, n_t)
                if se:
                    raise UserError(se)
                t["tp_split"] = sl; ch.append("TP split")
        if v.get("avg_entry") is not None:
            t["avg_entry"] = v["avg_entry"]; ch.append("avg entry (manual)")
        if v.get("status") is not None:
            if v["status"] not in SPOT_STATUSES:
                raise UserError("Unknown status.")
            t["status"] = v["status"]; ch.append("status")
        if v.get("notes") is not None:
            t["thesis"] = v["notes"]; ch.append("thesis")
    else:
        if v.get("direction") is not None:
            t["direction"] = v["direction"]; ch.append("direction")
        if v.get("entry") is not None:
            t["entry"] = v["entry"]; ch.append("entry")
        if v.get("entry2") is not None:
            t["entry2"] = v["entry2"]; ch.append("entry 2")
        if v.get("entry_split") is not None:
            raw = str(v["entry_split"]).strip()
            if not raw:
                t["entry_split"] = None; ch.append("split removed")
            else:
                t["entry_split"] = _entry_split_disp(raw); ch.append("entry split")
        if v.get("tp_split") is not None:
            raw = str(v["tp_split"]).strip()
            if not raw:
                t["tp_split"] = None; ch.append("TP split removed")
            else:
                n_tps = sum(1 for k in FUT_TP_KEYS if t.get(k))
                t["tp_split"] = _tp_plan(raw, n_tps); ch.append("TP split")
        if v.get("stop") is not None:
            e_num, e_cond = parse_sl(v["stop"])
            if not e_num:
                raise UserError("Couldn't find a price in the stop.")
            t["sl"] = e_num
            t["sl_condition"] = e_cond
            ch.append("stop")
        if v.get("risk") is not None:
            t["risk"] = v["risk"]; ch.append("risk")
        if v.get("entry_type") is not None:
            t["entry_type"] = v["entry_type"]
            t["entry1_filled"] = v["entry_type"] == "MARKET"
            ch.append("entry type")
        if v.get("framework") is not None or v.get("framework2") is not None:
            fws = [f for f in (v.get("framework"), v.get("framework2")) if f]
            t["frameworks"] = fws
            t["framework"] = fws[0] if fws else None
            ch.append("framework")
        for k in FUT_TP_KEYS:
            if v.get(k) is not None:
                t[k] = v[k]; ch.append(k.upper())
        if v.get("timeframe") is not None:
            t["timeframe"] = v["timeframe"]; ch.append("timeframe")
        if v.get("context") is not None:
            t["setup_detail"] = v["context"]; ch.append("context")
        if v.get("notes") is not None:
            t["notes"] = v["notes"]; ch.append("notes")
    if v.get("chart") is not None:
        ch.append("chart")
    if not ch:
        raise UserError("Nothing to change - fill at least one field.")
    # a stop/entry change must still make sense for the direction
    if not spot and t.get("sl") and first_num(t.get("entry")):
        s, e = float(t["sl"]), first_num(t["entry"])
        if t.get("direction") == "LONG" and s >= e:
            raise UserError(f"Stop must be below entry for a long. Stop {fnum(s)} vs entry {fnum(e)}.")
        if t.get("direction") == "SHORT" and s <= e:
            raise UserError(f"Stop must be above entry for a short. Stop {fnum(s)} vs entry {fnum(e)}.")
    t["edited"] = True
    t["edited_at"] = _now()
    return ch


def ledger_text(kind: str, t: dict) -> str:
    """Numbered list of recorded fills - what /setup fix refers to by item number."""
    spot = kind == "spot"
    fmt = (lambda v: f"{v:g}") if spot else fnum
    lines = []
    tps = (t.get("sells") if spot else [f for f in (t.get("fills") or []) if _is_tp_fill(f)]) or []
    if tps:
        lines.append("**Take profits:**")
        for i, s in enumerate(tps, 1):
            lab = f" ({str(s['label']).upper()})" if s.get("label") else ""
            lines.append(f"`{i}.` {s.get('pct', 0):g}% @ {fmt(s.get('price', 0))}{lab}")
    if spot:
        buys = t.get("buys") or []
        if buys:
            lines.append("**Buys:**")
            for i, b in enumerate(buys, 1):
                ptxt = f"{b['pct']:g}% " if b.get("pct") else ""
                lines.append(f"`{i}.` {ptxt}@ {b.get('price', 0):g}")
    return "\n".join(lines) if lines else "Nothing recorded on this trade yet."


def fix_fill(kind: str, t: dict, action: str, item: int = None, price=None, pct=None) -> str:
    """Remove or correct one recorded take profit (both markets) or buy (spot). Returns the change text."""
    spot = kind == "spot"
    if action == "list":
        return ledger_text(kind, t)
    is_tp = action.endswith("_tp")
    if not is_tp and not spot:
        raise UserError("Futures entries are flags, not a list - fix the entry price with **/setup edit**.")
    if spot:
        rows = (t.get("sells") if is_tp else t.get("buys")) or []
        store_key = "sells" if is_tp else "buys"
    else:
        rows = [f for f in (t.get("fills") or []) if _is_tp_fill(f)]
        store_key = "fills"
    what = "take profit" if is_tp else "buy"
    if not rows:
        raise UserError(f"No {what}s recorded on this trade.")
    if item is None or not (1 <= item <= len(rows)):
        raise UserError(f"Pick which one with `item` (1-{len(rows)}):\n{ledger_text(kind, t)}")
    row = rows[item - 1]
    fmt = (lambda v: f"{v:g}") if spot else fnum
    old_txt = f"{row.get('pct', 0):g}% @ {fmt(row.get('price', 0))}"
    if action.startswith("remove_"):
        if spot:
            rows.pop(item - 1)
            t[store_key] = rows
        else:
            t["fills"] = [f for f in (t.get("fills") or []) if f is not row]
        change = f"removed {what} #{item} ({old_txt})"
    else:
        npx = spot_num(price) if spot else parse_num(price)
        npc = parse_num(pct)
        if price is not None and npx is None:
            raise UserError("price must be a number.")
        if npx is None and npc is None:
            raise UserError("Give `price` and/or `pct` to fix this entry.")
        if npc is not None and not (0 < npc <= 100):
            raise UserError("pct must be between 0 and 100.")
        if npx is not None:
            row["price"] = npx
        if npc is not None:
            row["pct"] = npc
        if is_tp:
            others = sum(r.get("pct", 0) for i2, r in enumerate(rows) if i2 != item - 1)
            if others + row.get("pct", 0) > 100.01:
                raise UserError(f"That would make {others + row.get('pct', 0):g}% closed in total - over 100%. Adjust the pct.")
        change = f"fixed {what} #{item}: {old_txt} -> {row.get('pct', 0):g}% @ {fmt(row.get('price', 0))}"
    if is_tp:
        _sync_tp_flags(t, spot=spot)
    else:
        auto = spot_weighted_entry(t)
        if auto:
            t["avg_entry"] = f"{auto:g}"
    t["edited"] = True
    t["edited_at"] = _now()
    return change


# ----------------------------------------------------------------------------- tracking / reopen

def set_tracking(t: dict, on: bool) -> str:
    if on:
        t["watch_disabled"] = False
        t["watch_verified"] = False
        t["watch_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        return "ON - tracker re-armed, feed re-verified"
    t["watch_disabled"] = True
    return "OFF - manual updates only"


def reopen(kind: str, t: dict):
    """Reverse a close on the record. The caller deletes the results card and refreshes boards."""
    if not t.get("closed"):
        raise UserError("That trade isn't closed.")
    for f in ("result", "result_r", "result_pct", "avg_exit", "closed_at", "close_note"):
        t.pop(f, None)
    t["closed"] = False
    if kind == "spot":
        sells = t.get("sells") or []
        if sells and sells[-1].get("label") == "close":
            sells.pop()
            t["sells"] = sells
        if t.get("status") == "INVALIDATED":
            t["status"] = "HOLDING" if t.get("zone_filled") else "ACCUMULATING"
        _sync_tp_flags(t, spot=True)
    else:
        t["sl_hit"] = False
        t["watch_disabled"] = True   # safety: manual tracking after a reopen
        fills = t.get("fills") or []
        if fills and fills[-1].get("label") in ("SL", "STOP", "close"):
            fills.pop()
            t["fills"] = fills

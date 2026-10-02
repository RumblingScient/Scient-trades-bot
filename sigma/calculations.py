"""sigma.calculations - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.config import IST


def parse_sl(text):
    """Smart SL parser. '63000' -> ('63000', None). '4h close below 63000' -> ('63000', '4h close below').
    Takes the LAST standalone number as the level; the rest becomes the condition."""
    if not text:
        return None, None
    s = str(text).strip()
    matches = list(re.finditer(r"(?<![\w.])(\d[\d,]*\.?\d*)(?![\w])", s))
    if not matches:
        return None, None
    last = matches[-1]
    num = last.group(1).replace(",", "")
    cond = (s[:last.start()] + s[last.end():]).strip(" -:@")
    cond = re.sub(r"\s+", " ", cond).strip()[:60]
    return num, (cond or None)

def parse_num(val):
    if val is None:
        return None
    cleaned = "".join(c for c in str(val).replace(",", "") if c in "0123456789.")
    try:
        return float(cleaned)
    except ValueError:
        return None

def first_num(s):
    if s is None:
        return None
    s = re.sub(r"\([^)]*\)", "", str(s))
    nums = re.findall(r"\d+(?:\.\d+)?", s.replace(",", ""))
    return float(nums[0]) if nums else None

def entry_weights(t):
    """DCA weights from entry_split ('40% / 60%') - defaults to 50/50."""
    s = t.get("entry_split")
    if s:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(s))]
        if len(nums) >= 2 and nums[0] + nums[1] > 0:
            tot = nums[0] + nums[1]
            return nums[0] / tot, nums[1] / tot
    return 0.5, 0.5

def entry_num(t):
    e1 = first_num(t.get("entry"))
    e2 = first_num(t.get("entry2")) if t.get("entry2") else None
    if e2 is None:
        s = re.sub(r"\([^)]*\)", "", str(t.get("entry", "")))
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s.replace(",", ""))]
        if len(nums) >= 2:
            return (nums[0] + nums[1]) / 2
        return e1
    f1, f2 = t.get("entry1_filled"), t.get("entry2_filled")
    w1, w2 = entry_weights(t)
    if f1 and f2:
        return e1 * w1 + e2 * w2
    if f1:
        return e1
    if f2:
        return e2
    return e1 * w1 + e2 * w2

def sl_num(t):
    return first_num(t.get("sl"))

def risk_per_unit(t):
    e, s = entry_num(t), sl_num(t)
    if e is None or s is None or e == s:
        return None
    return abs(e - s)

def signed_r(t, price):
    e = entry_num(t)
    rpu = risk_per_unit(t)
    if e is None or rpu is None or price is None:
        return None
    diff = (price - e) if t.get("direction") == "LONG" else (e - price)
    return diff / rpu

def _tp_presets(t: dict, spot: bool = False):
    """[(idx, key, price, planned_pct|None)] from the card's preset targets."""
    keys = ("t1", "t2", "t3") if spot else ("tp1", "tp2", "tp3", "tp4")
    plan = t.get("tp_split") or []
    out = []
    for i, k in enumerate(keys):
        px = spot_num(t.get(k)) if spot else first_num(t.get(k))
        if px is None:
            continue
        out.append((i, k, px, (float(plan[i]) if i < len(plan) else None)))
    return out

def _tp_taken(t: dict, spot: bool = False):
    """Profit-taking fills in the order they happened: [{'price','pct'}]."""
    if spot:
        return [s for s in (t.get("sells") or []) if s.get("price") is not None and str(s.get("label") or "") != "close"]
    return [f for f in (t.get("fills") or []) if _is_tp_fill(f)]

def _sync_tp_flags(t: dict, spot: bool = False):
    """Preset N counts as done if (a) a taken TP already replaced its slot, or (b) any taken
    price reached it. Keeps the tracker / status logic from re-firing replaced presets."""
    taken = _tp_taken(t, spot)
    n = len(taken)
    is_long = True if spot else (t.get("direction") == "LONG")
    best = None
    if taken:
        best = max(x["price"] for x in taken) if is_long else min(x["price"] for x in taken)
    for i, k, px, _ in _tp_presets(t, spot):
        reached = best is not None and ((best >= px) if is_long else (best <= px))
        t[f"{k}_hit"] = bool(i < n or reached)

def _is_tp_fill(f: dict) -> bool:
    lab = str(f.get("label") or "")
    return lab.startswith("TP") or lab in ("Partial TP", "PTP")

def fills_pct(t) -> float:
    return sum(f.get("pct", 0) for f in t.get("fills", []))

def finalize_close(t, final_price=None):
    fills = list(t.get("fills", []))
    rem = 100 - sum(f.get("pct", 0) for f in fills)
    if final_price is not None and rem > 0.01:
        fills.append({"price": final_price, "pct": rem, "label": "close"})
    total = sum(f.get("pct", 0) for f in fills)
    if total <= 0:
        return None, None
    avg_exit = sum(f["price"] * f["pct"] for f in fills) / total
    return avg_exit, signed_r(t, avg_exit)

def fnum(x):
    if x is None:
        return "-"
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    return f"{x:.8f}".rstrip("0")

def tf(t: dict) -> str:
    v = t.get("timeframe")
    return v.upper() if v else ""

def fmt_risk(val):
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if "%" in s:
        return s
    try:
        float(s)
        return f"{s}%"
    except ValueError:
        return s

def fmt_frameworks(t: dict) -> str:
    fws = t.get("frameworks")
    if fws:
        return " + ".join(fws)
    return t.get("framework") or "-"

def fix_x_link(url: str) -> str:
    url = url.strip()
    return re.sub(r"https?://(www\.)?(twitter|x)\.com", "https://fxtwitter.com", url, flags=re.I)

def any_entry_filled(t: dict) -> bool:
    return bool(t.get("entry1_filled") or t.get("entry2_filled"))

def display_rr(t: dict):
    for key in ("tp4", "tp3", "tp2", "tp1"):
        if t.get(key):
            r = signed_r(t, first_num(t[key]))
            if r is not None:
                return f"{r:.1f}"
    return t.get("rr")

def spot_num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "").strip())
    except Exception:
        return None

def spot_ref_entry(p: dict):
    """Reference entry for % math: avg_entry if numeric, else DCA-zone midpoint."""
    v = spot_num(p.get("avg_entry"))
    if v:
        return v
    nums = [spot_num(x) for x in re.findall(r"\d[\d,]*\.?\d*", str(p.get("dca_zone", "")))]
    nums = [n for n in nums if n]
    if not nums:
        return None
    return sum(nums[:2]) / min(2, len(nums))

def spot_inv_num(p: dict):
    """Invalidation level for a spot play - last standalone number in the invalidation text."""
    num, _cond = parse_sl(p.get("invalidation"))
    return spot_num(num) if num else None

def spot_signed_r(p: dict, price) -> float | None:
    """R for a spot play: (price - entry) / (entry - invalidation). None if it can't be graded."""
    ref = spot_ref_entry(p)
    inv = spot_inv_num(p)
    px = spot_num(price)
    if not ref or not inv or px is None or ref <= inv:
        return None
    return (px - ref) / (ref - inv)

def spot_result_r(p: dict):
    """Stored result_r, else computed from avg_exit - so plays closed before R existed are graded too."""
    if isinstance(p.get("result_r"), (int, float)):
        return p["result_r"]
    if p.get("result") == "INVALID":
        return None
    r = spot_signed_r(p, p.get("avg_exit"))
    return round(r, 2) if r is not None else None

def _rec_r(kind: str, t: dict):
    """Graded R for any closed record - futures stored, spot stored-or-computed."""
    if kind == "spot":
        return spot_result_r(t)
    r = t.get("result_r")
    return r if isinstance(r, (int, float)) else None

def spot_pct_text(p: dict, target) -> str:
    ref = spot_ref_entry(p)
    t = spot_num(target)
    if ref and t and ref > 0:
        return f" ({(t - ref) / ref * 100:+.0f}%)"
    return ""

def parse_spot_split(raw: str, n_targets: int):
    """'30/30/40' -> [30.0, 30.0, 40.0]. Sum may be under 100 (moonbag). Returns (list, error)."""
    if not raw:
        return [], None
    try:
        parts = [float(x.strip().replace("%", "")) for x in str(raw).replace(",", "/").split("/") if x.strip()]
    except Exception:
        return None, "tp_split must look like 30/30/40."
    if not parts or any(x <= 0 for x in parts):
        return None, "tp_split values must be positive numbers."
    if len(parts) > n_targets:
        return None, f"tp_split has {len(parts)} parts but the play only has {n_targets} target(s)."
    if sum(parts) > 100.01:
        return None, f"tp_split adds up to {sum(parts):g}% - it can't be more than 100%."
    return parts, None

def spot_zone_projection(p: dict):
    """Projected avg entry if the DCA zone fully fills.
    Even DCA across the zone ~ zone midpoint; blended with logged buys when pcts are known."""
    import re as _re
    nums = _re.findall(r"[0-9]*\.?[0-9]+", str(p.get("dca_zone") or ""))
    if len(nums) < 2:
        return None
    try:
        a, b = float(nums[0]), float(nums[1])
    except Exception:
        return None
    mid = (a + b) / 2
    buys = p.get("buys") or []
    filled = sum(b_.get("pct") or 0 for b_ in buys)
    cur = spot_weighted_entry(p)
    if cur and 0 < filled < 100:
        return (cur * filled + mid * (100 - filled)) / 100
    return mid

def spot_weighted_entry(p: dict):
    buys = p.get("buys") or []
    if not buys:
        return None
    if all(b.get("pct") for b in buys):
        tot = sum(b["pct"] for b in buys)
        return sum(b["pct"] * b["price"] for b in buys) / tot if tot > 0 else None
    return sum(b["price"] for b in buys) / len(buys)

def spot_weighted_exit(p: dict):
    sells = p.get("sells") or []
    tot = sum(s["pct"] for s in sells)
    if tot <= 0:
        return None
    return sum(s["pct"] * s["price"] for s in sells) / tot

def spot_status_line(p: dict) -> str:
    if p.get("closed"):
        res = p.get("result_pct")
        rtxt = f" ({res})" if res else ""
        return {"WIN": f"CLOSED - WIN{rtxt}", "LOSS": f"CLOSED - LOSS{rtxt}", "BE": "CLOSED - BREAKEVEN", "INVALID": "INVALIDATED"}.get(p.get("result"), "CLOSED")
    s = p.get("status", "ACCUMULATING")
    top = max((i for i, k in enumerate(("t1_hit", "t2_hit", "t3_hit"), 1) if p.get(k)), default=0)
    sold = sum(x["pct"] for x in (p.get("sells") or []))
    parts = [s]
    if top:
        parts.append(f"Target {top} reached")
    if sold:
        parts.append(f"{sold:g}% sold")
    return " - ".join(parts)

def _stats_window(val: int):
    """(start_ist|None, end_ist|None, label). val: -1 last complete month, -2 this month so far,
    0 all time, n>0 rolling days. Default (None) = last complete month."""
    now = datetime.now(IST)
    if val == 0:
        return None, None, "All time"
    if val == -2:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, now, now.strftime("%B %Y") + " (so far)"
    if val == -1:
        start, end, _slug, label = _last_complete_month_range()
        return start, end, label
    start = now - timedelta(days=val)
    return start, now, f"Last {val} days"

def _in_window(item: dict, start, end) -> bool:
    if start is None:
        return True
    try:
        d = datetime.fromisoformat(item["closed_at"])
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        d = d.astimezone(IST)
        return start <= d < (end or datetime.now(IST) + timedelta(minutes=1))
    except Exception:
        return False

def _res_ts(iso) -> int:
    try:
        d = datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except Exception:
        return int(datetime.now(timezone.utc).timestamp())

def _last_complete_month_range():
    now = datetime.now(IST)
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_prev = first_this - timedelta(seconds=1)
    first_prev = last_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first_prev, first_this, first_prev.strftime("%b_%Y").lower(), first_prev.strftime("%B %Y")

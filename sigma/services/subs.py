"""sigma.services.subs - the one place a subscription record changes.

grant()  : new sub or extension (extends from the current expiry when still active)
revoke() : drop the record
expire() : what the daily loop applies
Pure functions on the subs dict. Discord side effects (roles, DMs, mod-log) live in sigma.members / sigma.payments.
"""
from datetime import datetime, timedelta, timezone

from sigma.errors import UserError
from sigma.services import plans as plansvc


def _now():
    return datetime.now(timezone.utc)


def seats_left(subs: dict, plan: str) -> int | None:
    cap = plansvc.all().get(plan, {}).get("seats")
    if cap is None:
        return None
    taken = sum(1 for s in subs.values() if s.get("plan") == plan)
    return max(0, cap - taken)


def parse_date(text):
    """'2026-11-15' / '15-11-2026' / '15 Nov 2026' / '15 Nov' -> aware datetime at 23:59 UTC. None for blank."""
    if text in (None, ""):
        return None
    s = str(text).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %b"):
        try:
            d = datetime.strptime(s, fmt)
            if fmt == "%d %b":
                d = d.replace(year=_now().year)
                if d.replace(tzinfo=timezone.utc) < _now():
                    d = d.replace(year=d.year + 1)
            return d.replace(hour=23, minute=59, tzinfo=timezone.utc)
        except ValueError:
            continue
    raise UserError("Date must look like `2026-11-15` or `15 Nov`.")


def grant(subs: dict, uid, name: str, plan: str, by: str, note: str = "", tx: str = None, usd: float = None,
          expires_at=None) -> dict:
    """Mutates subs; returns the new record. Raises UserError for an unknown plan or a sold-out one.
    expires_at: explicit expiry (datetime or date text) - used when importing members from the old bot."""
    p = plansvc.all().get(plan)
    if not p:
        raise UserError("Unknown plan.")
    uid = str(uid)
    existing = subs.get(uid)
    if p.get("seats") is not None and (not existing or existing.get("plan") != plan) and seats_left(subs, plan) <= 0:
        raise UserError(f"{p['label']} is sold out - all {p['seats']} seats are taken.")
    now = _now()
    base = now
    if existing and existing.get("expires"):
        try:
            cur = datetime.fromisoformat(existing["expires"])
            base = max(cur, now)
        except Exception:
            pass
    expires = base + timedelta(days=p["days"])
    if expires_at not in (None, ""):
        expires = expires_at if isinstance(expires_at, datetime) else parse_date(expires_at)
        if expires <= now:
            raise UserError("That expiry is already in the past.")
    rec = {
        "name": name,
        "plan": plan,
        "price": p["price"],
        "days": p["days"],
        "started": existing.get("started") if existing else now.isoformat(),
        "expires": expires.isoformat(),
        "reminded": [],
        "note": (note or "")[:120],
        "history": (existing.get("history", []) if existing else []) + [
            {"plan": plan, "price": p["price"], "usd": usd if usd is not None else p["price"],
             "at": now.isoformat(), "by": by, "tx": tx}
        ],
    }
    subs[uid] = rec
    return rec


def revoke(subs: dict, uid) -> dict:
    rec = subs.pop(str(uid), None)
    if rec is None:
        raise UserError("No subscription on record for that member.")
    return rec


def days_left(rec: dict, now=None) -> int | None:
    try:
        exp = datetime.fromisoformat(rec["expires"])
    except Exception:
        return None
    return (exp - (now or _now())).days


def due_reminder(rec: dict, thresholds, now=None):
    """Which reminder threshold (days) fires now, if any - largest unsent threshold that we're at or under."""
    dl = days_left(rec, now)
    if dl is None or dl < 0:
        return None
    sent = rec.get("reminded")
    sent = set(sent) if isinstance(sent, list) else set()   # old bool records -> treat as none sent
    for th in sorted(thresholds):
        if dl <= th and th not in sent:
            return th
    return None


def mark_reminded(rec: dict, th: int, thresholds):
    """Firing the N-day reminder also settles every looser threshold, so a late first reminder isn't followed by a redundant one."""
    sent = rec.get("reminded") if isinstance(rec.get("reminded"), list) else []
    rec["reminded"] = sorted(set(sent) | {t for t in thresholds if t >= th})


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def revenue(subs: dict, lapsed: list = None, now=None) -> dict:
    """USD recorded in grant history - active records + lapsed archive. Keys: all, this_month, last_month, by_plan, by_month (last 6)."""
    now = now or _now()
    this_m = _month_key(now)
    last_m = _month_key((now.replace(day=1) - timedelta(days=1)))
    out = {"all": 0.0, "this_month": 0.0, "last_month": 0.0, "by_plan": {}, "by_month": {}, "new_this_month": 0}
    hist = []
    for s in subs.values():
        hist += [(h, s) for h in s.get("history", [])]
    for s in (lapsed or []):
        hist += [(h, s) for h in s.get("history", [])]
    seen_new = set()
    for h, s in hist:
        usd = float(h.get("usd") if h.get("usd") is not None else h.get("price") or 0)
        try:
            mk = _month_key(datetime.fromisoformat(h["at"]))
        except Exception:
            mk = "?"
        out["all"] += usd
        out["by_plan"][h.get("plan")] = out["by_plan"].get(h.get("plan"), 0.0) + usd
        out["by_month"][mk] = out["by_month"].get(mk, 0.0) + usd
        if mk == this_m:
            out["this_month"] += usd
            if s.get("started", "")[:7] == this_m and id(s) not in seen_new:
                seen_new.add(id(s)); out["new_this_month"] += 1
        elif mk == last_m:
            out["last_month"] += usd
    out["by_month"] = dict(sorted(out["by_month"].items())[-6:])
    return out


def summary(subs: dict, now=None) -> dict:
    now = now or _now()
    out = {"active": 0, "expiring_7d": [], "by_plan": {}, "revenue_recorded": 0.0,
           "lifetime_seats_left": seats_left(subs, "lifetime") if "lifetime" in plansvc.all() else None}
    for uid, s in subs.items():
        dl = days_left(s, now)
        if dl is None:
            continue
        out["active"] += 1
        out["by_plan"][s.get("plan")] = out["by_plan"].get(s.get("plan"), 0) + 1
        if 0 <= dl <= 7:
            out["expiring_7d"].append((dl, s.get("name", uid)))
        out["revenue_recorded"] += sum(float(h.get("usd") or h.get("price") or 0) for h in s.get("history", []))
    out["expiring_7d"].sort()
    return out

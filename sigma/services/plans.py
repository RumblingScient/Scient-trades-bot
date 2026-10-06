"""sigma.services.plans - plans and promo codes, editable from Discord, stored in plans.json.

config.SUB_PLANS is only the seed: the first load writes it to plans.json and from then on the file
is the truth. Every reader (panel, quotes, grant, dashboard) calls get() / all() here.

plan   : {key, short, label, price, days, seats|None, order, enabled, discount_pct, discount_until}
promo  : {code: {pct|usd, uses_left|None, until|None, plans: [] (all) | [keys]}}
"""
from datetime import datetime, timezone

from sigma.config import SUB_PLANS as _SEED
from sigma.errors import UserError
from sigma.storage import load_plans as _load, save_plans as _save

LIFETIME_DAYS = 36500


def _now():
    return datetime.now(timezone.utc)


def _seed() -> dict:
    out = {}
    for i, (k, p) in enumerate(_SEED.items()):
        out[k] = {"key": k, "short": p.get("short", k), "label": p["label"], "price": float(p["price"]), "days": int(p["days"]),
                  "seats": p.get("seats"), "order": i, "enabled": True, "discount_pct": 0, "discount_until": None}
    return out


def _state() -> dict:
    st = _load()
    if not st.get("plans"):
        st["plans"] = _seed()
        st.setdefault("promos", {})
        _save(st)
    st.setdefault("promos", {})
    return st


def all(enabled_only: bool = False) -> dict:
    """Ordered dict of plans."""
    ps = _state()["plans"]
    items = sorted(ps.values(), key=lambda p: (p.get("order", 99), p["key"]))
    return {p["key"]: p for p in items if (p.get("enabled", True) or not enabled_only)}


def get(key: str) -> dict:
    p = _state()["plans"].get(key)
    if not p:
        raise UserError(f"No plan `{key}`. Run `/admin plans action:List`.")
    return p


def _parse_until(until):
    if until in (None, "", "none", "off"):
        return None
    s = str(until).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %b"):
        try:
            d = datetime.strptime(s, fmt)
            if fmt == "%d %b":
                d = d.replace(year=_now().year)
                if d.replace(tzinfo=timezone.utc) < _now():
                    d = d.replace(year=d.year + 1)
            return d.replace(hour=23, minute=59, tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    raise UserError("Date must look like `2026-10-15` or `15 Oct`.")


def discount_active(p: dict, now=None) -> bool:
    if not p.get("discount_pct"):
        return False
    u = p.get("discount_until")
    if not u:
        return True
    try:
        return datetime.fromisoformat(u) >= (now or _now())
    except Exception:
        return False


def effective_price(p: dict, promo: dict = None, now=None) -> tuple[float, list]:
    """(usd, [reasons]). Plan discount first, then promo on top."""
    price = float(p["price"])
    why = []
    if discount_active(p, now):
        price = round(price * (1 - float(p["discount_pct"]) / 100), 2)
        why.append(f"{p['discount_pct']:g}% off")
    if promo:
        if promo.get("pct"):
            price = round(price * (1 - float(promo["pct"]) / 100), 2)
            why.append(f"code {promo['code']} -{promo['pct']:g}%")
        elif promo.get("usd"):
            price = round(max(0.0, price - float(promo["usd"])), 2)
            why.append(f"code {promo['code']} -${promo['usd']:g}")
    return max(0.0, price), why


# ----------------------------------------------------------------------------- admin edits

def _slug(s: str) -> str:
    k = "".join(c if c.isalnum() else "_" for c in str(s).strip().lower()).strip("_")
    if not k:
        raise UserError("Plan key must have letters or numbers.")
    return k[:24]


def add(key: str, name: str, price, days, seats=None) -> dict:
    st = _state()
    key = _slug(key)
    if key in st["plans"]:
        raise UserError(f"Plan `{key}` already exists - use Edit.")
    try:
        price = float(str(price).replace("$", "").replace(",", ""))
        days = LIFETIME_DAYS if str(days).lower() in ("lifetime", "life", "forever") else int(days)
    except Exception:
        raise UserError("price must be a number and days a whole number (or `lifetime`).")
    if price <= 0 or days <= 0:
        raise UserError("price and days must be above 0.")
    seats = int(seats) if seats not in (None, "", 0, "0") else None
    short = str(name).strip()[:24]
    label = f"Sigma Pro - {short} (${price:,.0f})" if days < LIFETIME_DAYS else f"{short} (${price:,.0f})"
    st["plans"][key] = {"key": key, "short": short, "label": label, "price": price, "days": days, "seats": seats,
                        "order": max([p.get("order", 0) for p in st["plans"].values()] + [-1]) + 1,
                        "enabled": True, "discount_pct": 0, "discount_until": None}
    _save(st)
    return st["plans"][key]


def edit(key: str, name=None, price=None, days=None, seats=None, enabled=None, order=None) -> tuple[dict, list]:
    st = _state()
    p = st["plans"].get(key)
    if not p:
        raise UserError(f"No plan `{key}`.")
    ch = []
    if name not in (None, ""):
        p["short"] = str(name).strip()[:24]; ch.append("name")
    if price not in (None, ""):
        try:
            v = float(str(price).replace("$", "").replace(",", ""))
        except Exception:
            raise UserError("price must be a number.")
        if v <= 0:
            raise UserError("price must be above 0.")
        p["price"] = v; ch.append(f"price ${v:,.0f}")
    if days not in (None, ""):
        try:
            d = LIFETIME_DAYS if str(days).lower() in ("lifetime", "life", "forever") else int(days)
        except Exception:
            raise UserError("days must be a whole number or `lifetime`.")
        if d <= 0:
            raise UserError("days must be above 0.")
        p["days"] = d; ch.append(f"{'lifetime' if d >= LIFETIME_DAYS else str(d) + ' days'}")
    if seats not in (None, ""):
        try:
            s = int(seats)
        except Exception:
            raise UserError("seats must be a whole number (0 = unlimited).")
        p["seats"] = s if s > 0 else None; ch.append(f"seats {s if s > 0 else 'unlimited'}")
    if enabled is not None:
        p["enabled"] = bool(enabled); ch.append("enabled" if enabled else "disabled")
    if order not in (None, ""):
        p["order"] = int(order); ch.append(f"position {order}")
    if not ch:
        raise UserError("Nothing to change - fill at least one field.")
    p["label"] = (f"Sigma Pro - {p['short']} (${p['price']:,.0f})" if p["days"] < LIFETIME_DAYS else f"{p['short']} (${p['price']:,.0f})")
    _save(st)
    return p, ch


def set_discount(key: str, pct, until=None) -> dict:
    st = _state()
    p = st["plans"].get(key)
    if not p:
        raise UserError(f"No plan `{key}`.")
    try:
        pct = float(str(pct).replace("%", ""))
    except Exception:
        raise UserError("discount must be a number, e.g. 20 for 20% off. 0 removes it.")
    if not (0 <= pct < 100):
        raise UserError("discount must be between 0 and 99.")
    p["discount_pct"] = pct
    p["discount_until"] = _parse_until(until) if pct else None
    _save(st)
    return p


def remove(key: str) -> dict:
    st = _state()
    p = st["plans"].pop(key, None)
    if not p:
        raise UserError(f"No plan `{key}`.")
    if not st["plans"]:
        st["plans"][key] = p
        raise UserError("Can't remove the last plan - disable it instead.")
    _save(st)
    return p


# ----------------------------------------------------------------------------- promos

def promos() -> dict:
    return _state()["promos"]


def promo_add(code: str, pct=None, usd=None, uses=None, until=None, plan: str = None,
              owner_id: int = None, owner_name: str = None, share_pct=None) -> dict:
    """A promo code. With owner + share_pct it's also a referral code: every payment made with it
    records `share_pct` of the USD as owed to the owner (see referrals / payouts)."""
    st = _state()
    code = str(code).strip().upper().replace(" ", "")
    if not code or len(code) > 24:
        raise UserError("Code must be 1-24 characters.")
    if (pct not in (None, "")) and (usd not in (None, "")):
        raise UserError("Give `pct` (percent off) or `usd` (dollars off), not both.")
    if (pct in (None, "")) and (usd in (None, "")) and owner_id is None:
        raise UserError("A code needs a discount (`pct` or `usd`) or an owner (referral), or both.")
    rec = {"code": code, "pct": None, "usd": None, "uses_left": None, "until": None, "plans": [], "used": 0,
           "owner_id": owner_id, "owner_name": owner_name, "share_pct": None, "created": _now().isoformat()}
    if owner_id is not None:
        try:
            rec["share_pct"] = float(str(share_pct).replace("%", "")) if share_pct not in (None, "") else 0.0
            if not (0 <= rec["share_pct"] <= 100):
                raise ValueError
        except ValueError:
            raise UserError("share must be 0-100 (% of each payment that goes to the owner).")
    try:
        if pct not in (None, ""):
            rec["pct"] = float(str(pct).replace("%", ""))
            if not (0 < rec["pct"] < 100):
                raise ValueError
        elif usd not in (None, ""):
            rec["usd"] = float(str(usd).replace("$", ""))
            if rec["usd"] <= 0:
                raise ValueError
        if uses not in (None, "", 0, "0"):
            rec["uses_left"] = int(uses)
    except ValueError:
        raise UserError("pct must be 1-99, usd above 0, uses a whole number.")
    rec["until"] = _parse_until(until)
    if plan:
        if plan not in st["plans"]:
            raise UserError(f"No plan `{plan}`.")
        rec["plans"] = [plan]
    st["promos"][code] = rec
    _save(st)
    return rec


def promo_remove(code: str) -> dict:
    st = _state()
    rec = st["promos"].pop(str(code).strip().upper(), None)
    if not rec:
        raise UserError("No such code.")
    _save(st)
    return rec


def promo_check(code: str, plan_key: str, now=None) -> dict:
    """Valid promo for this plan right now, else UserError with the reason."""
    rec = _state()["promos"].get(str(code).strip().upper())
    if not rec:
        raise UserError("That code doesn't exist.")
    if rec.get("until"):
        try:
            if datetime.fromisoformat(rec["until"]) < (now or _now()):
                raise UserError("That code has expired.")
        except ValueError:
            pass
    if rec.get("uses_left") is not None and rec["uses_left"] <= 0:
        raise UserError("That code has been fully used.")
    if rec.get("plans") and plan_key not in rec["plans"]:
        raise UserError("That code doesn't apply to this plan.")
    return rec


def promo_consume(code: str):
    st = _state()
    rec = st["promos"].get(str(code).upper())
    if not rec:
        return
    rec["used"] = int(rec.get("used", 0)) + 1
    if rec.get("uses_left") is not None:
        rec["uses_left"] = max(0, rec["uses_left"] - 1)
    _save(st)


def describe(p: dict, seats_left=None, now=None) -> str:
    """One panel line: **Monthly** - ~~$100~~ $80 (20% off till 15 Oct) / 30 days · 3 seats left"""
    eff, why = effective_price(p, None, now)
    if why:
        till = ""
        if p.get("discount_until"):
            try:
                till = " till " + datetime.fromisoformat(p["discount_until"]).strftime("%d %b")
            except Exception:
                pass
        price_txt = f"~~${p['price']:,.0f}~~ **${eff:,.0f}** ({p['discount_pct']:g}% off{till})"
    else:
        price_txt = f"${p['price']:,.0f}"
    dur = "" if p["days"] >= LIFETIME_DAYS else f" / {p['days']} days"
    seat = ""
    if p.get("seats") is not None:
        seat = f" · {seats_left} of {p['seats']} seats left" if seats_left else " · sold out"
    return f"**{p['short']}** - {price_txt}{dur}{seat}"


# ----------------------------------------------------------------------------- referrals

def record_referral(state: dict, promo: dict, session: dict, tx: str):
    """Append an attribution row to payments.json['referrals'] (caller saves). No-op for codes without an owner."""
    if not promo or promo.get("owner_id") is None:
        return None
    usd = float(session.get("usd") or 0)
    row = {"code": promo["code"], "owner_id": int(promo["owner_id"]), "owner_name": promo.get("owner_name"),
           "user_id": session.get("user_id"), "user": session.get("user"), "plan": session.get("plan"),
           "usd": usd, "share_pct": float(promo.get("share_pct") or 0), "share_usd": round(usd * float(promo.get("share_pct") or 0) / 100, 2),
           "tx": tx, "at": _now().isoformat(), "settled": None}
    state.setdefault("referrals", []).append(row)
    return row


def referral_summary(state: dict, owner_id: int = None) -> dict:
    """{owner_id: {name, codes, members, usd, share_usd, owed, settled}} - optionally one owner."""
    out = {}
    for r in state.get("referrals") or []:
        if owner_id is not None and r["owner_id"] != owner_id:
            continue
        o = out.setdefault(r["owner_id"], {"name": r.get("owner_name"), "codes": set(), "members": set(), "usd": 0.0,
                                           "share_usd": 0.0, "owed": 0.0, "settled": 0.0, "rows": 0})
        o["codes"].add(r["code"]); o["members"].add(r["user_id"]); o["rows"] += 1
        o["usd"] += r["usd"]; o["share_usd"] += r["share_usd"]
        if r.get("settled"):
            o["settled"] += r["share_usd"]
        else:
            o["owed"] += r["share_usd"]
    for o in out.values():
        o["codes"] = sorted(o["codes"]); o["members"] = len(o["members"])
    return out


def settle(state: dict, owner_id: int, note: str = "") -> float:
    """Mark every unsettled row of one owner as paid out. Returns the USD settled. Caller saves."""
    total = 0.0
    stamp = _now().isoformat()
    for r in state.get("referrals") or []:
        if r["owner_id"] == owner_id and not r.get("settled"):
            r["settled"] = stamp
            r["settle_note"] = note
            total += r["share_usd"]
    if total <= 0:
        raise UserError("Nothing owed to that member.")
    return round(total, 2)

"""sigma.services.store - one API over the two JSON stores.

A trade id is "f:<message_id>" (futures) or "s:<message_id>" (spot). Bare ids are accepted for
backwards compatibility and resolved by looking in trades.json first, then spot.json.
"""
from sigma.storage import load_trades, save_trades, load_spot, save_spot
from sigma.errors import UserError

FUT, SPOT = "fut", "spot"


def split_id(trade_id: str):
    """'f:123' -> ('fut', '123'); 's:123' -> ('spot', '123'); '123' -> (None, '123')."""
    s = str(trade_id or "")
    if s.startswith("f:"):
        return FUT, s[2:]
    if s.startswith("s:"):
        return SPOT, s[2:]
    return None, s


def make_id(kind: str, key: str) -> str:
    return ("s:" if kind == SPOT else "f:") + str(key)


def load(kind: str) -> dict:
    return load_spot() if kind == SPOT else load_trades()


def save(kind: str, data: dict):
    (save_spot if kind == SPOT else save_trades)(data)


def find(trade_id: str):
    """Returns (kind, key, record, data). Raises UserError when the trade doesn't exist."""
    kind, key = split_id(trade_id)
    kinds = (kind,) if kind else (FUT, SPOT)
    for k in kinds:
        data = load(k)
        rec = data.get(key)
        if rec is not None:
            return k, key, rec, data
    raise UserError("Trade not found - pick it from the list, don't type the id.")


def is_spot(rec: dict) -> bool:
    return rec.get("kind") == "spot"


def open_records(analyst_id: int = None):
    """[(kind, key, record)] for every open trade, optionally only one analyst's."""
    out = []
    for kind in (FUT, SPOT):
        for key, rec in load(kind).items():
            if rec.get("closed"):
                continue
            if analyst_id is not None and rec.get("analyst_id") != analyst_id:
                continue
            out.append((kind, key, rec))
    return out


def closed_records(analyst_id: int = None):
    out = []
    for kind in (FUT, SPOT):
        for key, rec in load(kind).items():
            if not rec.get("closed"):
                continue
            if analyst_id is not None and rec.get("analyst_id") != analyst_id:
                continue
            out.append((kind, key, rec))
    return out

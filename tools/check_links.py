"""Diagnostic: does every trade record point at its own card?

Run on the VPS:  venv/bin/python tools/check_links.py
For every record in trades.json / spot.json it checks key == message_id, duplicate message ids, and then fetches the
card from Discord and compares the pair in the embed title with the pair in the record. Also reads the last 40
posts in the trade-updates channel, follows each "View original call" link and reports where it actually lands.
Read-only - nothing is edited.
"""
import asyncio, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from sigma.config import TRADE_UPDATES_CHANNEL_ID, BOT_TOKEN as DISCORD_TOKEN, JOURNAL_FILE as TRADES_FILE, SPOT_FILE  # noqa: E402

import aiohttp  # noqa: E402

API = "https://discord.com/api/v10"
HDR = {"Authorization": f"Bot {DISCORD_TOKEN}", "User-Agent": "SigmaLinkCheck/1.0"}


def _pair_from_title(title: str) -> str:
    # card titles look like "▲ LONG BTC · 4H" / "[WIN] ▼ SHORT ETH" / "● SPOT FET"
    m = re.search(r"(?:LONG|SHORT|SPOT)\s+([A-Z0-9]+)", title or "")
    return m.group(1) if m else ""


async def fetch(session, cid, mid):
    async with session.get(f"{API}/channels/{cid}/messages/{mid}", headers=HDR) as r:
        if r.status != 200:
            return None, r.status
        return await r.json(), 200


async def main():
    files = [(TRADES_FILE, "fut"), (SPOT_FILE, "spot")]
    recs = []
    for fn, kind in files:
        p = Path(fn)
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        for key, t in data.items():
            recs.append((kind, key, t))
    print(f"{len(recs)} records")
    bad_key = [(k, key, t.get("message_id")) for k, key, t in recs if str(t.get("message_id")) != str(key)]
    print(f"key != message_id: {len(bad_key)}")
    for row in bad_key[:20]:
        print("   ", row)
    seen = {}
    for k, key, t in recs:
        seen.setdefault(str(t.get("message_id")), []).append((k, key, t.get("pair")))
    dups = {m: v for m, v in seen.items() if len(v) > 1}
    print(f"message_id shared by several records: {len(dups)}")
    for m, v in list(dups.items())[:20]:
        print("   ", m, v)

    async with aiohttp.ClientSession() as s:
        print("\n-- card check (open + recently closed) --")
        mism = 0
        for k, key, t in recs:
            if t.get("closed") and not str(t.get("closed_at", "")).startswith("2026-11"):
                continue
            msg, st = await fetch(s, t.get("channel_id"), t.get("message_id"))
            if msg is None:
                print(f"  {t.get('pair')} {key}: card fetch {st}")
                continue
            title = (msg.get("embeds") or [{}])[0].get("title", "")
            cp = _pair_from_title(title)
            if cp and cp != str(t.get("pair", "")).upper():
                mism += 1
                print(f"  MISMATCH record {t.get('pair')} ({key}) -> card says {title!r}")
            await asyncio.sleep(0.3)
        print(f"card mismatches: {mism}")

        if TRADE_UPDATES_CHANNEL_ID:
            print("\n-- last 40 trade-updates posts --")
            async with s.get(f"{API}/channels/{TRADE_UPDATES_CHANNEL_ID}/messages?limit=40", headers=HDR) as r:
                feed = await r.json() if r.status == 200 else []
            for m in feed:
                e = (m.get("embeds") or [{}])[0]
                desc = e.get("description", "") or ""
                fp = re.search(r"\*\*([A-Z0-9]+)", desc)
                link = re.search(r"channels/(\d+)/(\d+)/(\d+)", desc)
                if not (fp and link):
                    continue
                card, st = await fetch(s, link.group(2), link.group(3))
                ctitle = (card.get("embeds") or [{}])[0].get("title", "") if card else f"HTTP {st}"
                ok = fp.group(1) == _pair_from_title(ctitle)
                print(f"  {'ok  ' if ok else 'BAD '} feed {fp.group(1):8} -> {ctitle!r}  ({m.get('timestamp','')[:16]})")
                await asyncio.sleep(0.3)


asyncio.run(main())

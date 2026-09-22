#!/usr/bin/env python3
"""Pre-deploy self-check for scient_trades_bot.py.
Run on the VPS from the bot folder BEFORE restarting the service:
    SCIENT_BOT_TOKEN=x python3 bot_selfcheck.py
Loads the whole module with bot.run stubbed, registers every command, and runs the money-math /
data-safety tests. Exit code 0 = safe to restart. Anything else = do NOT restart, read the output.
"""
import sys, json, importlib, os, tempfile
from pathlib import Path
os.environ.setdefault("SCIENT_BOT_TOKEN", "x")
import discord.ext.commands as commands
commands.Bot.run = lambda self, *a, **k: None
out = sys.__stdout__
ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    ok += bool(cond); fail += (not cond)
    out.write(f"  {'PASS' if cond else 'FAIL'}  {name} {detail}\n")

try:
    m = importlib.import_module("scient_trades_bot")
except Exception as e:
    out.write(f"FAIL  module did not load: {e}\n"); sys.exit(2)
cmds = m.bot.tree.get_commands()
out.write(f"module loaded - {len(cmds)} commands\n")
names = {c.name for c in cmds}
for must in ("health","update","spot_update","edit","reopen","track","trade","spot","stats","journal_month","recap_month","results_debug","results_sync"):
    check(f"command /{must}", must in names)
check("all 13 loops present", len(m._loops()) == 13)
# Discord hard limits - a single violation makes the WHOLE command sync fail on boot
def _walk_cmds(cmds):
    for c in cmds:
        yield c
        for sub in getattr(c, "commands", []) or []:
            yield sub
lim_bad = []
for c in _walk_cmds(cmds):
    if len(c.name) > 32: lim_bad.append(f"/{c.name} name>32")
    if len(c.description or "") > 100: lim_bad.append(f"/{c.name} description {len(c.description)}>100")
    for prm in getattr(c, "parameters", []) or []:
        if len(prm.description or "") > 100: lim_bad.append(f"/{c.name} param {prm.name} desc {len(prm.description)}>100")
        for ch in getattr(prm, "choices", []) or []:
            if len(ch.name) > 100: lim_bad.append(f"/{c.name} choice '{ch.name[:30]}...' >100")
        if len(getattr(prm, "choices", []) or []) > 25: lim_bad.append(f"/{c.name} param {prm.name} >25 choices")
    if len(getattr(c, "parameters", []) or []) > 25: lim_bad.append(f"/{c.name} >25 params")
check("Discord limits (names/descriptions/choices)", not lim_bad, ("; ".join(lim_bad[:5]) if lim_bad else ""))
check("command count <= 100", len(cmds) <= 100, f"({len(cmds)})")


t = {"direction":"LONG","entry":"100","sl":"95","fills":[{"price":110,"pct":50,"label":"TP1"}]}
ae, r = m.finalize_close(t, 120); check("R math long partial+close", abs(ae-115)<1e-9 and abs(r-3.0)<1e-9)
ae, r = m.finalize_close({"direction":"SHORT","entry":"100","sl":"105","fills":[]}, 90); check("R math short", abs(r-2.0)<1e-9)
p = {"t1":"2.05","t2":"5.4","t3":"8.4","tp_split":[],"avg_entry":"1.65",
     "sells":[{"pct":10,"price":2.26},{"pct":10,"price":3.14}],"t1_hit":True,"t2_hit":True,"t3_hit":True}
m._sync_tp_flags(p, spot=True); check("unified TP ledger", not p["t3_hit"] and p["t2_hit"])

tf = Path(tempfile.mkdtemp()) / "t.json"
m._save(tf, {"a":1}); m._save(tf, {"a":2}); tf.write_text("{corrupt")
check("corrupt file served from .bak", m._load(tf) == {"a":1})
refused = False
try: m._save(tf, {"a":3})
except RuntimeError: refused = True
check("save refused on corrupt file", refused)
m._CORRUPT.discard(str(tf))

# live data files on this box must parse
for nm, pth in (("trades", m.JOURNAL_FILE), ("spot", m.SPOT_FILE), ("results", m.RESULTS_FILE)):
    if pth.exists():
        try: json.loads(pth.read_text()); check(f"data file {nm} parses", True, f"({pth.stat().st_size//1024} KB)")
        except Exception as e: check(f"data file {nm} parses", False, str(e))
out.write(f"\n{ok} passed, {fail} failed -> {'SAFE TO RESTART' if fail == 0 else 'DO NOT RESTART'}\n")
sys.exit(0 if fail == 0 else 1)

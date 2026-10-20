"""sigma.desk - the analyst control panel behind the Manage button on every trade card.

Click Manage -> an ephemeral panel (only the clicker sees it) with the trade's live state and one
button per thing that can happen next. Numbers are asked for in a small popup, pre-filled where the
card already knows them. Every action runs through sigma.services.trades (the same code path as the
tracker) and sigma.actions.commit (card, board, update feed, thread, results), so nothing here
duplicates trade logic.

Futures: Entry filled · TP hit · Stop to entry · Trail stop · Stopped out · Close · Edit · More
Spot:    Buy · Zone filled · Sold · Close · Edit · More
More:    Invalidated, Undo last TP, Fix a fill, Tracking on/off, Rebuild TPs, Phase (spot), Reopen (closed)

Only the analyst who posted the trade (or an admin) gets past the Manage button.
"""
import discord

from sigma.logging_setup import print
from sigma.config import EDIT_WINDOW_MIN, SPOT_STATUSES
from sigma.core import bot
from sigma.errors import UserError
from sigma.members import is_analyst
from sigma.calculations import (_tp_presets, any_entry_filled, display_rr, entry_num, fills_pct, first_num, fnum,
                                initial_sl_num, parse_num, sl_num, spot_num, spot_zone_projection, stop_locked_r, stop_trailed)
from sigma.cards import entry_display, full_status, jump_url, spot_status_line, stop_state, unified_tp_text, within_edit_window
from sigma.tracker import STATE_LOCK
from sigma.services import store
from sigma.services import trades as svc
from sigma import actions

PREFIX = "desk:"
PANEL_TIMEOUT = 14 * 60          # ephemeral interaction tokens last 15 minutes


def _is_admin(interaction: discord.Interaction) -> bool:
    return bool(interaction.user.guild_permissions.administrator)


def _can_manage(interaction: discord.Interaction, rec: dict) -> bool:
    return _is_admin(interaction) or rec.get("analyst_id") == interaction.user.id


# ----------------------------------------------------------------------------- popup form

class Form(discord.ui.Modal):
    """Generic popup: fields = [(key, label, default, required, placeholder)], on_ok(interaction, values)."""
    def __init__(self, title: str, fields, on_ok):
        super().__init__(title=title[:45], timeout=600)
        self.on_ok = on_ok
        self.inputs = {}
        for spec in fields[:5]:
            key, label, default, required, ph = spec[:5]
            max_len = spec[5] if len(spec) > 5 else 200
            style = discord.TextStyle.paragraph if max_len > 200 else discord.TextStyle.short
            ti = discord.ui.TextInput(label=label[:45], default=(str(default)[:max_len] if default not in (None, "") else None),
                                      required=required, placeholder=(ph or None)[:100] if ph else None, max_length=max_len, style=style)
            self.add_item(ti)
            self.inputs[key] = ti

    async def on_submit(self, interaction: discord.Interaction):
        vals = {k: (ti.value or "").strip() for k, ti in self.inputs.items()}
        await self.on_ok(interaction, vals)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"[desk] form error: {error!r}", flush=True)
        try:
            await interaction.followup.send("Something broke on that form - try again, and ping ops if it repeats.", ephemeral=True)
        except Exception:
            pass


class ChartForm(discord.ui.Modal):
    """Popup with a file picker - replaces the chart image on the card."""
    def __init__(self, on_ok):
        super().__init__(title="Replace the chart", timeout=600)
        self.on_ok = on_ok
        self.upload = discord.ui.FileUpload(custom_id="chart", required=True, min_values=1, max_values=1)
        self.add_item(discord.ui.Label(text="New chart image", description="PNG / JPG - replaces the one on the card",
                                       component=self.upload))

    async def on_submit(self, interaction: discord.Interaction):
        files = list(self.upload.values or [])
        await self.on_ok(interaction, files[0] if files else None)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"[desk] chart form error: {error!r}", flush=True)
        try:
            await interaction.followup.send("Something broke on that upload - try again.", ephemeral=True)
        except Exception:
            pass


# ----------------------------------------------------------------------------- the panel

class Desk(discord.ui.View):
    def __init__(self, origin: discord.Interaction, kind: str, key: str):
        super().__init__(timeout=PANEL_TIMEOUT)
        self.origin = origin
        self.kind, self.key = kind, key
        self.spot = kind == store.SPOT
        self.last = ""
        self.pending = None          # (label, coroutine factory) - an instant action waiting for Confirm
        self.editing = False         # Edit pressed - section picker shown in row 3
        self.rebuild()

    # ---- state
    def rec(self) -> dict:
        _k, _key, rec, _data = store.find(store.make_id(self.kind, self.key))
        return rec

    def embed(self) -> discord.Embed:
        t = self.rec()
        pair = (t.get("pair") or "?").upper()
        if self.spot:
            head = f"◆ SPOT {pair}"
            lines = [f"**{spot_status_line(t)}**"]
            z = f"Zone {t.get('dca_zone')}" + (" (filled)" if t.get("zone_filled") else "")
            if t.get("avg_entry"):
                z += f" · avg entry {t['avg_entry']}"
            elif not t.get("zone_filled"):
                proj = spot_zone_projection(t)
                if proj:
                    z += f" · if zone fills ≈ {proj:g}"
            lines.append(z)
            if t.get("invalidation"):
                lines.append(f"Invalidation {t['invalidation']}")
            tpt = unified_tp_text(t, spot=True)
            if tpt:
                lines.append(tpt)
        else:
            side = "▲ LONG" if t.get("direction") == "LONG" else "▼ SHORT"
            head = f"{side} {pair}" + (f" {t['timeframe'].upper()}" if t.get("timeframe") else "")
            lines = [f"**{full_status(t)}**", f"Entry {entry_display(t)}"]
            sl = fnum(sl_num(t)) if sl_num(t) is not None else str(t.get("sl"))
            st = stop_state(t)
            sl_line = f"Stop {(t.get('sl_condition') + ' ') if t.get('sl_condition') else ''}{sl}"
            if st:
                sl_line += f" ({st}, initial {fnum(initial_sl_num(t))})"
            rr = display_rr(t)
            if rr:
                sl_line += f" · R:R {rr}"
            lines.append(sl_line)
            tpt = unified_tp_text(t)
            if tpt:
                lines.append(tpt)
            if t.get("watch_disabled"):
                lines.append("Auto-tracking: off")
        if t.get("closed"):
            lines.append(f"Closed{' - ' + t['close_note'] if t.get('close_note') else ''}")
        e = discord.Embed(title=head, description="\n".join(lines)[:4000], color=discord.Color.dark_grey())
        if self.last:
            e.add_field(name="Last", value=self.last[:1000], inline=False)
        e.set_footer(text="Analyst controls · only you can see this · closes after 14 min")
        e.url = None
        try:
            e.description += f"\n[Open card]({jump_url(t)})"
        except Exception:
            pass
        return e

    # ---- buttons
    def rebuild(self):
        self.clear_items()
        try:
            t = self.rec()
        except UserError:
            return
        if self.pending:
            label, _fn = self.pending
            self._btn(f"\u2714 Confirm: {label}"[:80], self.on_confirm, discord.ButtonStyle.success, row=0)
            self._btn("\u2716 Cancel", self.on_cancel, discord.ButtonStyle.secondary, row=0)
            return
        closed = bool(t.get("closed"))
        if closed:
            self.add_item(self._more([("Reopen - the close was wrong", "reopen")]))
            return
        if getattr(self, "editing", False):
            sel = discord.ui.Select(placeholder="Edit what\u2026", row=3, min_values=1, max_values=1,
                                    options=[discord.SelectOption(label=l, value=v) for l, v in (self.SPOT_SECTIONS if self.spot else self.FUT_SECTIONS)])
            sel.callback = self.on_edit_pick
            self.add_item(sel)
        if self.spot:
            self._btn("Buy", self.on_buy, discord.ButtonStyle.primary, row=0)
            self._btn("Zone filled", self.on_zone, discord.ButtonStyle.primary, row=0, disabled=bool(t.get("zone_filled")))
            self._btn("Sold", self.on_sold, discord.ButtonStyle.success, row=0)
            self._btn("Close", self.on_close, discord.ButtonStyle.danger, row=0)
            self._btn("Edit", self.on_edit, discord.ButtonStyle.secondary, row=1)
            self._btn("Replace chart", self.on_chart, discord.ButtonStyle.secondary, row=1)
            opts = [("Invalidation - new level", "inv"), ("Invalidated - never filled / thesis gone", "ci"),
                    ("Undo last sell", "ul"), ("Fix a sell or buy (list + correct)", "fix"),
                    ("Rebuild target list from sells (repair)", "rtp")]
            opts += [(f"Phase: {s.capitalize()}", f"st:{s}") for s in SPOT_STATUSES]
            self.add_item(self._more(opts))
            return
        filled = any_entry_filled(t)
        all_filled = t.get("entry1_filled") and (not t.get("entry2") or t.get("entry2_filled"))
        self._btn("Entry filled", self.on_entry, discord.ButtonStyle.primary, row=0, disabled=bool(all_filled))
        self._btn("TP hit", self.on_tp, discord.ButtonStyle.success, row=0, disabled=not filled)
        locked = stop_locked_r(t)
        past_entry = bool(t.get("be")) or (locked is not None and locked > 0.05)     # already at or beyond entry
        self._btn("Stop to entry", self.on_be, discord.ButtonStyle.secondary, row=0, disabled=(not filled or past_entry))
        self._btn("Trail stop", self.on_trail, discord.ButtonStyle.secondary, row=0)
        self._btn("Stopped out", self.on_stopped, discord.ButtonStyle.danger, row=0, disabled=not filled)
        self._btn("Close", self.on_close, discord.ButtonStyle.danger, row=1, disabled=not filled)
        self._btn("Edit", self.on_edit, discord.ButtonStyle.secondary, row=1)
        self._btn("Replace chart", self.on_chart, discord.ButtonStyle.secondary, row=1)
        track = "Tracking: turn ON (tracker re-armed)" if t.get("watch_disabled") else "Tracking: turn OFF (manual updates)"
        opts = [("Invalidated - never triggered", "ci"), ("Undo last take profit", "ul"),
                ("Fix a take profit (list + correct)", "fix"), (track, "track"),
                ("Rebuild TP list from fills (repair)", "rtp")]
        if filled and not [f for f in (t.get("fills") or []) if f.get("label", "").startswith("TP")]:
            opts.insert(0, ("Entry fill recorded in error - back to pending", "efx"))
        if t.get("entry2") and not t.get("entry2_filled"):
            opts.insert(0, ("Cancel entry 2 - DCA leg pulled, entry 1 only", "e2x"))
        if filled:
            opts = [o for o in opts if o[1] != "ci"]          # a filled setup is closed, not invalidated
        self.add_item(self._more(opts))

    def _btn(self, label, cb, style, row=0, disabled=False):
        b = discord.ui.Button(label=label, style=style, row=row, disabled=disabled)
        b.callback = cb
        self.add_item(b)

    def _more(self, opts):
        sel = discord.ui.Select(placeholder="More…", row=2, min_values=1, max_values=1,
                                options=[discord.SelectOption(label=l[:100], value=v) for l, v in opts])
        sel.callback = self.on_more
        return sel

    # ---- plumbing
    async def _refresh(self, interaction: discord.Interaction):
        self.rebuild()
        emb = self.embed()
        try:
            await interaction.edit_original_response(embed=emb, view=self)
        except Exception:
            try:
                await self.origin.edit_original_response(embed=emb, view=self)
            except Exception as ex:
                print(f"[desk] panel refresh failed: {ex}", flush=True)

    async def _apply(self, interaction: discord.Interaction, fn):
        """Defer, run fn(kind, key, rec, data) -> reply text under the state lock, re-render the panel."""
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            async with STATE_LOCK:
                kind, key, rec, data = store.find(store.make_id(self.kind, self.key))
                if not _can_manage(interaction, rec):
                    raise UserError("Only the analyst who posted this trade (or an admin) can change it.")
                if svc.migrate_legacy_be(rec):
                    data[key] = rec
                    store.save(kind, data)
                self.last = await fn(kind, key, rec, data)
        except UserError as e:
            self.last = f"⚠ {e.message}"
        except Exception as ex:
            print(f"[desk] action error: {ex!r}", flush=True)
            self.last = "⚠ Something broke - nothing was changed. Try again, and ping ops if it repeats."
        await self._refresh(interaction)

    async def _event(self, interaction, event, price=None, pct=None, note=None):
        async def run(kind, key, rec, data):
            if rec.get("closed"):
                raise UserError("That trade is closed - Reopen it first (More) if the close was wrong.")
            out = svc.apply_event(kind, rec, event, price=price, pct=pct, note=note)
            return await actions.commit(kind, key, rec, data, out, note)
        await self._apply(interaction, run)

    def _form(self, title, fields, on_ok):
        return Form(title, fields, on_ok)

    async def _ask(self, interaction, label: str, what: str, run):
        """Instant actions (no popup) park here first: the panel shows Confirm / Cancel, nothing changes until Confirm."""
        self.pending = (label, run)
        self.last = f"**{label}** - {what}\nPress Confirm to apply, Cancel to keep the trade as it is."
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self._refresh(interaction)

    async def on_confirm(self, interaction):
        if not self.pending:
            await self._refresh(interaction); return
        _label, run = self.pending
        self.pending = None
        await run(interaction)

    async def on_cancel(self, interaction):
        self.pending = None
        self.last = "Cancelled - nothing changed."
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self._refresh(interaction)

    # ---- futures actions
    async def on_entry(self, interaction):
        t = self.rec()
        ev = "EF2" if (t.get("entry2") and t.get("entry1_filled") and not t.get("entry2_filled")) else "EF1"
        label = "Entry 2 filled" if ev == "EF2" else "Entry filled"
        await self._ask(interaction, label, "marks the entry as filled and announces it in the update feed.",
                        lambda i: self._event(i, ev))

    async def on_tp(self, interaction):
        t = self.rec()
        pend = [(k, px, plan) for _i, k, px, plan in _tp_presets(t) if not t.get(f"{k}_hit")]
        px_def = f"{pend[0][1]:g}" if pend else ""
        pct_def = f"{pend[0][2]:g}" if pend and pend[0][2] is not None else ""
        hint = f"next planned: {px_def}" if pend else "no preset left - any price"

        async def ok(i, v):
            px = parse_num(v["price"])
            if px is None:
                self.last = "⚠ price must be a number."
                await i.response.defer(); await self._refresh(i); return
            is_long = t.get("direction") == "LONG"
            reached = bool(pend) and ((px >= pend[0][1]) if is_long else (px <= pend[0][1]))
            await self._event(i, "TPN" if reached else "PTP", price=v["price"], pct=(v["pct"] or None), note=(v["note"] or None))
        await interaction.response.send_modal(self._form("Take profit", [
            ("price", "Fill price", px_def, True, hint),
            ("pct", "% of the position closed here", pct_def, not pct_def, "e.g. 40 - blank = planned %"),
            ("note", "Note (optional)", "", False, "shown in the update feed"),
        ], ok))

    async def on_be(self, interaction):
        await self._ask(interaction, "Stop to entry", "moves the stop to the entry price - the trade becomes risk-free.",
                        lambda i: self._event(i, "BE"))

    async def on_trail(self, interaction):
        async def ok(i, v):
            await self._event(i, "SLU", price=v["level"], note=(v["note"] or None))
        await interaction.response.send_modal(self._form("Move the stop", [
            ("level", "New stop level", "", True, "e.g. 64000  or  4h close below 64000"),
            ("note", "Note (optional)", "", False, "why - shown in the update feed"),
        ], ok))

    async def on_stopped(self, interaction):
        t = self.rec()
        s = entry_num(t) if t.get("be") else sl_num(t)
        async def ok(i, v):
            await self._event(i, "SL", price=(v["price"] or None), note=(v["note"] or None))
        await interaction.response.send_modal(self._form("Stopped out", [
            ("price", "Exit price", (f"{s:g}" if s is not None and not t.get("sl_condition") else ""),
             bool(t.get("sl_condition")) and not t.get("be"), "blank = the stop level on the card"),
            ("note", "Note (optional)", "", False, ""),
        ], ok))

    async def on_close(self, interaction):
        t = self.rec()
        left = (100 - sum(s["pct"] for s in (t.get("sells") or []))) if self.spot else (100 - fills_pct(t))
        async def ok(i, v):
            async def run(kind, key, rec, data):
                out = svc.close(kind, rec, (v["price"] or None), (v["note"] or None))
                return await actions.commit(kind, key, rec, data, out, (v["note"] or None))
            await self._apply(i, run)
        await interaction.response.send_modal(self._form("Close the trade", [
            ("price", f"Exit price for the {left:g}% still open" if left > 0.01 else "Exit price (all already taken as TPs)",
             "", left > 0.01, "the bot grades Win / Loss / Breakeven"),
            ("note", "Closing note (optional)", "", False, "shown in the update feed and the thread"),
        ], ok))

    # ---- spot actions
    async def on_buy(self, interaction):
        async def ok(i, v):
            await self._event(i, "BUY", price=v["price"], pct=(v["pct"] or None), note=(v["note"] or None))
        await interaction.response.send_modal(self._form("Buy filled", [
            ("price", "Buy price", "", True, ""),
            ("pct", "% of the planned bag (optional)", "", False, "e.g. 30"),
            ("note", "Note (optional)", "", False, ""),
        ], ok))

    async def on_zone(self, interaction):
        t = self.rec()
        proj = spot_zone_projection(t)
        async def ok(i, v):
            await self._event(i, "ZONE", price=(v["avg"] or None), note=(v["note"] or None))
        await interaction.response.send_modal(self._form("Zone filled - now holding", [
            ("avg", "Your real average entry", (t.get("avg_entry") or ""), False,
             f"blank = zone midpoint {proj:g}" if proj else "blank = keep current"),
            ("note", "Note (optional)", "", False, ""),
        ], ok))

    async def on_sold(self, interaction):
        t = self.rec()
        pend = [(k, px, plan) for _i, k, px, plan in _tp_presets(t, spot=True) if not t.get(f"{k}_hit")]
        px_def = f"{pend[0][1]:g}" if pend else ""
        pct_def = f"{pend[0][2]:g}" if pend and pend[0][2] is not None else ""
        async def ok(i, v):
            px = spot_num(v["price"])
            if px is None:
                self.last = "⚠ price must be a number."
                await i.response.defer(); await self._refresh(i); return
            reached = bool(pend) and px >= pend[0][1]
            await self._event(i, "TPN" if reached else "PTP", price=v["price"], pct=(v["pct"] or None), note=(v["note"] or None))
        await interaction.response.send_modal(self._form("Sold", [
            ("price", "Sell price", px_def, True, f"next target: {px_def}" if pend else "any price"),
            ("pct", "% of the bag sold here", pct_def, not pct_def, "e.g. 30 - blank = planned %"),
            ("note", "Note (optional)", "", False, ""),
        ], ok))

    # ---- chart (both)
    async def on_chart(self, interaction):
        await interaction.response.send_modal(ChartForm(self._replace_chart))

    async def _replace_chart(self, interaction, attachment):
        """Swap the card's image for the uploaded one. No edit window - a wrong chart can be fixed any time."""
        async def run(kind, key, rec, data):
            if attachment is None:
                raise UserError("No image received - pick a PNG or JPG.")
            ctype = (getattr(attachment, "content_type", "") or "").lower()
            if ctype and not ctype.startswith("image/"):
                raise UserError("That isn't an image - upload a PNG or JPG.")
            return await actions.replace_chart(kind, key, rec, data, attachment)
        await self._apply(interaction, run)

    # ---- edit (both) - sectioned: every field the post command takes has an edit popup here
    # (modals hold 5 inputs, so Edit first asks which section)
    FUT_SECTIONS = [("Levels - entry, entry 2, split, stop", "levels"), ("Targets - prices, TP split", "targets"),
                    ("Risk, timeframe, framework, context", "meta"), ("Pair, direction, entry type", "identity"),
                    ("Reasoning - thread note", "notes")]
    SPOT_SECTIONS = [("Zone, invalidation, average entry", "levels"), ("Targets - prices, TP split", "targets"),
                     ("Plan - allocation, horizon, phase", "meta"), ("Pair", "identity"), ("Thesis - thread note", "notes")]

    async def on_edit(self, interaction):
        """Show the section picker (row 3); the pick opens the right popup."""
        self.editing = not getattr(self, "editing", False)
        self.last = "Pick what to edit below." if self.editing else ""
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self._refresh(interaction)

    def _edit_fields(self, t: dict, section: str):
        """(fields for Form, mapper(values) -> svc.edit dict, window_exempt)"""
        spot = self.spot
        if section == "levels":
            if spot:
                fields = [("entry", "Buy zone", t.get("dca_zone") or "", False, "e.g. 65 - 52"),
                          ("stop", "Invalidation", t.get("invalidation") or "", False, "e.g. weekly close below 48"),
                          ("avg_entry", "Average entry (blank = clear it)", t.get("avg_entry") or "", False, ""),
                          ("zone_filled", "Zone filled? (yes / no)", "yes" if t.get("zone_filled") else "no", False, "")]
                def mp(v, b):
                    f = {}
                    if v["entry"] != b["entry"]: f["entry"] = v["entry"]
                    if v["stop"] != b["stop"]: f["stop"] = v["stop"]
                    if v["avg_entry"] != b["avg_entry"]: f["avg_entry"] = v["avg_entry"]
                    zf = (v.get("zone_filled") or "").strip().lower()
                    if zf and zf != b["zone_filled"]:
                        if zf not in ("yes", "no", "y", "n"):
                            raise UserError("Zone filled must be yes or no.")
                        f["zone_filled"] = zf in ("yes", "y")
                    return f
                return fields, mp, False
            fields = [("entry", "Entry (DCA: entry 1)", str(t.get("entry") or ""), False, ""),
                      ("entry2", "Entry 2 (DCA leg; clear it = remove the leg)", str(t.get("entry2") or ""), False, "blank = no DCA leg"),
                      ("entry_split", "DCA split e.g. 20/80 (blank = 50/50)", str(t.get("entry_split") or "").replace("%", "").replace(" ", ""), False, ""),
                      ("stop", "Posted stop (R is measured from it)", ((t.get("sl_condition") + " ") if t.get("sl_condition") else "") + str(t.get("initial_sl") or t.get("sl") or ""), False, "")]
            def mp(v, b):
                f = {}
                if v["entry"] != b["entry"]: f["entry"] = v["entry"]
                if v["entry2"] != b["entry2"]:
                    f["entry2"] = v["entry2"]                 # blank = remove the leg (svc.edit refuses if it already filled)
                if v["entry_split"] != b["entry_split"]: f["entry_split"] = v["entry_split"] or " "
                if v["stop"] != b["stop"]: f["stop"] = v["stop"]
                return f
            return fields, mp, False
        if section == "targets":
            keys = ("t1", "t2", "t3", "t4") if spot else ("tp1", "tp2", "tp3", "tp4")
            mapk = ("tp1", "tp2", "tp3", "tp4")
            tgt = " / ".join(str(t.get(k)) for k in keys if t.get(k))
            split = t.get("tp_split") or []
            split_txt = "/".join(f"{float(x):g}" for x in split) if split else ""
            fields = [("targets", f"Targets (up to {len(keys)})", tgt, False, "e.g. 104000 / 108000 / 112000"),
                      ("tp_split", "Planned % per target (blank = none)", split_txt, False, "e.g. 40/30/30")]
            def mp(v, b):
                f = {}
                norm = lambda x: [p for p in actions.parse_targets(x, len(keys))] if x.strip() else []
                if norm(v["targets"]) != norm(b["targets"]):
                    tl = actions.parse_targets(v["targets"], len(keys))
                    for n, mk in enumerate(mapk):
                        f[mk] = tl[n] if n < len(tl) else ""
                if v["tp_split"] != b["tp_split"]:
                    f["tp_split"] = v["tp_split"] or " "
                return f
            return fields, mp, False
        if section == "meta":
            if spot:
                fields = [("risk", "Allocation %", t.get("allocation") or "", False, ""),
                          ("horizon", "Horizon", t.get("horizon") or "", False, "e.g. 3-6 months"),
                          ("status", "Phase", (t.get("status") or "").lower(), False, "watching / accumulating / holding / trimmed / distributing")]
                def mp(v, b):
                    f = {}
                    if v["risk"] != b["risk"]: f["risk"] = v["risk"]
                    if v["horizon"] != b["horizon"]: f["horizon"] = v["horizon"]
                    if v["status"] != b["status"]:
                        st = v["status"].strip().upper()
                        if st not in SPOT_STATUSES:
                            raise UserError("Phase must be one of: " + ", ".join(s.lower() for s in SPOT_STATUSES))
                        f["status"] = st
                    return f
                return fields, mp, True
            fw = " + ".join(t.get("frameworks") or ([t["framework"]] if t.get("framework") else []))
            fields = [("risk", "Risk %", str(t.get("risk") or "1"), False, ""),
                      ("timeframe", "Timeframe", t.get("timeframe") or "", False, "e.g. 4H"),
                      ("framework", "Framework (two: A + B)", fw, False, ""),
                      ("context", "Context line", t.get("setup_detail") or "", False, "")]
            def mp(v, b):
                from sigma.commands_setup import _framework_value, _frameworks_list
                f = {}
                if v["risk"] != b["risk"]: f["risk"] = v["risk"] or "1"
                if v["timeframe"] != b["timeframe"]: f["timeframe"] = v["timeframe"]
                if v["framework"] != b["framework"]:
                    fws = _frameworks_list(_framework_value(v["framework"]) or "")
                    f["framework"] = fws[0] if fws else ""
                    f["framework2"] = fws[1] if len(fws) > 1 else None
                if v["context"] != b["context"]: f["context"] = v["context"]
                return f
            return fields, mp, False
        if section == "identity":
            fields = [("pair", "Pair", t.get("pair") or "", False, "")]
            if not spot:
                fields += [("direction", "Direction (long / short)", (t.get("direction") or "").lower(), False, ""),
                           ("entry_type", "Entry type (limit / market)", (t.get("entry_type") or "limit").lower(), False, "")]
            def mp(v, b):
                f = {}
                if v["pair"].strip().upper() != b["pair"].upper(): f["pair"] = v["pair"].strip().upper()
                if not spot:
                    v = dict(v, direction=v["direction"].strip().lower(), entry_type=v["entry_type"].strip().lower())
                    if v["direction"] != b["direction"]:
                        d = v["direction"].strip().upper()
                        if d not in ("LONG", "SHORT"):
                            raise UserError("Direction must be long or short.")
                        f["direction"] = d
                    if v["entry_type"] != b["entry_type"]:
                        et = v["entry_type"].strip().upper()
                        if et not in ("LIMIT", "MARKET"):
                            raise UserError("Entry type must be limit or market (DCA = give entry 2 in Levels).")
                        f["entry_type"] = et
                return f
            return fields, mp, False
        if section == "notes":
            cur = (t.get("thesis") if spot else t.get("notes")) or ""
            fields = [("notes", "Thesis" if spot else "Reasoning", cur, False, "posted in the thread, not on the card", 1900)]
            def mp(v, b):
                return {"notes": v["notes"]} if v["notes"] != b["notes"] else {}
            return fields, mp, True
        raise UserError("Unknown section.")

    async def on_edit_pick(self, interaction):
        section = (interaction.data.get("values") or [""])[0]
        self.editing = False
        t = self.rec()
        try:
            fields, mapper, exempt = self._edit_fields(t, section)
        except UserError as e:
            self.last = f"\u26a0 {e.message}"
            await interaction.response.defer(); await self._refresh(interaction); return
        if not exempt and not _is_admin(interaction) and not within_edit_window(t):
            self.last = f"\u26a0 Edit window ({EDIT_WINDOW_MIN // 60}h) has passed for card fields - ask an admin, or log what happened with the buttons."
            await interaction.response.defer(); await self._refresh(interaction); return
        before = {f[0]: f[2] for f in fields}
        title = next(l for l, v in (self.SPOT_SECTIONS if self.spot else self.FUT_SECTIONS) if v == section)

        async def ok(i, v):
            async def run(kind, key, rec, data):
                f = mapper(v, before)
                if not f:
                    raise UserError("Nothing changed.")
                notes = f.get("notes")
                changes = svc.edit(kind, rec, f)
                return await actions.commit_edit(kind, key, rec, data, changes, notes=notes)
            await self._apply(i, run)
        await interaction.response.send_modal(self._form(f"Edit - {title.split(' - ')[0]}", fields, ok))

    # ---- more
    async def on_more(self, interaction):
        val = interaction.data["values"][0]
        if val == "reopen":
            async def run(kind, key, rec, data):
                return await actions.reopen_trade(kind, key, rec, data)
            await self._ask(interaction, "Reopen", "reverses the close, deletes the results card and rebuilds the summary.",
                            lambda i: self._apply(i, run)); return
        if val in ("ci", "ul", "rtp", "e2x", "efx"):
            what = {"ci": "closes the trade as never triggered / thesis gone - it goes to results as Invalidated.",
                    "ul": "removes the last recorded take profit.",
                    "rtp": "re-derives the TP ticks from the recorded fills (repair only).",
                    "e2x": "removes the unfilled DCA leg - R, sizing and the card run from entry 1 only. The stop stays; trail it after if needed.",
                    "efx": "clears the entry-filled flag(s) - the setup goes back to pending and the tracker watches the entry again."}[val]
            label = {"ci": "Invalidated", "ul": "Undo last take profit", "rtp": "Rebuild TP list", "e2x": "Cancel entry 2", "efx": "Clear entry fill"}[val]
            await self._ask(interaction, label, what, lambda i, ev=val.upper(): self._event(i, ev)); return
        if val.startswith("st:"):
            async def run(kind, key, rec, data):
                out = svc.set_status(kind, rec, val[3:])
                return await actions.commit(kind, key, rec, data, out)
            await self._ask(interaction, f"Phase: {val[3:].capitalize()}", "changes the phase shown on the card and posts it to the feed.",
                            lambda i: self._apply(i, run)); return
        if val == "track":
            async def run(kind, key, rec, data):
                txt = svc.set_tracking(rec, bool(rec.get("watch_disabled")))
                data[key] = rec
                store.save(kind, data)
                return f"Auto-tracking {txt}"
            t = self.rec()
            await self._ask(interaction, "Tracking " + ("ON" if t.get("watch_disabled") else "OFF"),
                            "re-arms the price tracker for this setup." if t.get("watch_disabled") else "stops the tracker - fills and stops by hand only.",
                            lambda i: self._apply(i, run)); return
        if val == "inv":
            async def ok(i, v):
                await self._event(i, "SLU", price=v["level"], note=(v["note"] or None))
            await interaction.response.send_modal(self._form("Invalidation", [
                ("level", "New invalidation level", "", True, "e.g. weekly close below 48"),
                ("note", "Note (optional)", "", False, ""),
            ], ok)); return
        if val == "fix":
            t = self.rec()
            ledger = svc.ledger_text(self.kind, t)
            async def ok(i, v):
                async def run(kind, key, rec, data):
                    item = int(parse_num(v["item"]) or 0)
                    what = "buy" if (self.spot and v["what"].lower().startswith("b")) else "tp"
                    action = ("remove_" if not (v["price"] or v["pct"]) else "fix_") + what
                    change = svc.fix_fill(kind, rec, action, item, (v["price"] or None), (v["pct"] or None))
                    return await actions.commit_fix(kind, key, rec, data, change)
                await self._apply(i, run)
            self.last = ledger
            try:
                if interaction.message is not None:
                    await interaction.message.edit(embed=self.embed(), view=self)
                else:
                    await self.origin.edit_original_response(embed=self.embed(), view=self)
            except Exception:
                pass
            fields = [("item", "Item number (from the list on the panel)", "", True, "1 = oldest"),
                      ("price", "Corrected price (blank = remove)", "", False, ""),
                      ("pct", "Corrected % (blank = remove)", "", False, "")]
            if self.spot:
                fields.append(("what", "Fixing a sell or a buy?", "sell", True, "sell / buy"))
            else:
                fields.append(("what", "Type", "tp", False, "tp"))
            await interaction.response.send_modal(self._form("Fix a recorded fill", fields, ok)); return
        self.last = "⚠ Unknown action."
        await interaction.response.defer(); await self._refresh(interaction)

    async def on_timeout(self):
        try:
            await self.origin.edit_original_response(content="Panel closed (14 min). Press Manage on the card again.", embed=None, view=None)
        except Exception:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        print(f"[desk] view error: {error!r}", flush=True)
        self.last = "⚠ Something broke on that click - nothing was changed."
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            await self._refresh(interaction)
        except Exception:
            pass


# ----------------------------------------------------------------------------- router

async def open_panel(interaction: discord.Interaction, kind: str, key: str):
    try:
        _k, _key, rec, _data = store.find(store.make_id(kind, key))
    except UserError:
        await interaction.response.send_message("This trade isn't in the log anymore.", ephemeral=True)
        return
    if not is_analyst(interaction):
        await interaction.response.send_message("Analyst controls - members use the position-size buttons.", ephemeral=True)
        return
    if not _can_manage(interaction, rec):
        await interaction.response.send_message("Only the analyst who posted this trade (or an admin) can manage it.", ephemeral=True)
        return
    view = Desk(interaction, kind, key)
    await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)


@bot.listen("on_interaction")
async def _desk_router(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    cid = (interaction.data or {}).get("custom_id", "")
    if not cid.startswith(PREFIX):
        return
    try:
        _p, kind, key = cid.split(":", 2)
        await open_panel(interaction, kind, key)
    except Exception as ex:
        print(f"[desk] open error: {ex!r}", flush=True)
        try:
            await interaction.response.send_message("Something broke - try again.", ephemeral=True)
        except Exception:
            pass

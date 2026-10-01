"""sigma.commands_setup - the /setup group: everything an analyst does to a trade.

    /setup futures | spot        post a new setup / play
    /setup update                one event list for both markets
    /setup close                 price only - the bot grades WIN / LOSS / BE
    /setup edit                  card-field typos (window) + spot avg entry / status
    /setup fix                   remove or correct a recorded take profit / buy, any time
    /setup track                 auto-tracking on / off (futures)
    /setup reopen                undo a wrong close
    /setup xpost                 share an X post

Every handler: gate -> defer -> store.find -> owner check -> service call -> save -> refresh
card + board -> update feed + thread note -> ephemeral reply. Input problems (UserError) come
back as one plain line. The old top-level names are registered as aliases in sigma.aliases and
run these very same callbacks.
"""
import re
import discord
from discord import app_commands

from sigma.logging_setup import print
from sigma.config import (ANALYST_ROLE_NAME, BLUE, EDIT_WINDOW_MIN, FRAMEWORKS, PING_ROLE_ID,
                          RESULTS_CHANNEL_ID, SPOT_CHANNEL_ID, SPOT_STATUSES, TRADES_CHANNEL_ID,
                          X_FEED_CHANNEL_ID, X_PING_ROLE_ID)
from sigma.core import bot
from sigma.errors import UserError
from sigma.storage import load_results, save_results
from sigma.calculations import fix_x_link
from sigma.members import analyst_color_hex, is_analyst, resolve_analyst
from sigma.cards import (_ac_label, _ac_sortkey, build_embed, build_spot_embed, jump_url,
                         post_update_feed, refresh_and_edit, thread_note, within_edit_window)
from sigma.sizer import TradeSizerView
from sigma.boards import refresh_board
from sigma.tracker import _with_state_lock
from sigma.results import refresh_results_summary
from sigma.services import store
from sigma.services import trades as svc

SPOT_FOOTER = "Sigma Trading - Spot Plays"
FUT_FOOTER = "Sigma Trading - Trade Updates"

setup = app_commands.Group(name="setup", description="Analyst tools - post, update, close and fix trades", guild_only=True)


# ----------------------------------------------------------------------------- helpers

def _moved_note(interaction: discord.Interaction) -> str:
    """Old command name used -> tell them where it lives now (aliases carry extras['moved'])."""
    cmd = interaction.command
    moved = (getattr(cmd, "extras", None) or {}).get("moved")
    return f"Moved to `{moved}` - use that next time.\n" if moved else ""


async def _reply(interaction: discord.Interaction, text: str):
    await interaction.followup.send(_moved_note(interaction) + text, ephemeral=True)


async def _gate(interaction: discord.Interaction, what="do this") -> bool:
    if not is_analyst(interaction):
        await interaction.response.send_message(f"Only members with the **{ANALYST_ROLE_NAME}** role can {what}.", ephemeral=True)
        return False
    await interaction.response.defer(ephemeral=True)
    return True


def _is_admin(interaction: discord.Interaction) -> bool:
    return bool(interaction.user.guild_permissions.administrator)


def _own(interaction: discord.Interaction, rec: dict):
    if not (_is_admin(interaction) or rec.get("analyst_id") == interaction.user.id):
        raise UserError("Only the analyst who posted this trade (or an admin) can change it.")


def _analyst_ctx(user) -> dict:
    akey, acfg = resolve_analyst(user)
    return {"id": user.id, "name": user.display_name, "avatar": user.display_avatar.url,
            "key": akey, "color": analyst_color_hex(user), "cfg": acfg}


async def _post_card(interaction, rec: dict, channel_id: int, spot: bool, chart, thread_label: str, body_label: str, body: str):
    """Send the card, open its thread, return the message. Shared by futures + spot."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        raise UserError(f"{'Spot' if spot else 'Trades'} channel not found - check the channel id in config.")
    embed = (build_spot_embed if spot else build_embed)(rec)
    files = []
    if chart:
        f = await chart.to_file()
        files.append(f)
        embed.set_image(url=f"attachment://{chart.filename}")
    ctx = _analyst_ctx(interaction.user)
    mention_ids = []
    if PING_ROLE_ID:
        mention_ids.append(PING_ROLE_ID)
    if ctx["cfg"] and ctx["cfg"].get("ping_role_id"):
        mention_ids.append(ctx["cfg"]["ping_role_id"])
    content = None
    allowed = discord.AllowedMentions.none()
    if mention_ids:
        content = " ".join(f"<@&{r}>" for r in mention_ids) + (" New spot play" if spot else " New setup")
        allowed = discord.AllowedMentions(roles=True)
    msg = await channel.send(content=content, embed=embed, files=files, allowed_mentions=allowed)
    rec["message_id"] = msg.id
    rec["channel_id"] = channel.id
    try:
        thread = await msg.create_thread(name=thread_label)
        rec["thread_id"] = thread.id
        if body:
            try:
                await thread.send(f"**{body_label}:** {body[:1900]}")
            except Exception:
                pass
    except discord.HTTPException:
        rec["thread_id"] = None
    return msg, channel


async def _finish(interaction, kind, key, rec, data, out: svc.Outcome, note=None):
    """Persist + refresh + announce one applied event/close."""
    spot = kind == store.SPOT
    data[key] = rec
    store.save(kind, data)
    try:
        await refresh_and_edit(rec, spot_mode=spot)
    except Exception as ex:
        print(f"[setup] card refresh failed for {rec.get('pair')}: {ex}", flush=True)
    await refresh_board()
    if not out.silent:
        line = out.line or out.desc
        if note:
            line += f"\n> {note}"
        await post_update_feed(rec, out.title or out.desc, out.color, line, footer=(SPOT_FOOTER if spot else FUT_FOOTER))
    await thread_note(rec, f"**{out.desc}{out.result_txt}**" + (f" - {note}" if note else ""))
    if out.closed:
        try:
            await refresh_results_summary()
        except Exception as ex:
            print(f"[setup] results refresh failed: {ex}", flush=True)
    await _reply(interaction, f"{'Closed' if out.closed else 'Updated'}: {out.desc}{out.result_txt}" + (f"\n{out.extra}" if out.extra else ""))


async def _run(interaction, coro):
    """Run a handler body; UserError -> one plain ephemeral line."""
    try:
        await coro
    except UserError as e:
        await _reply(interaction, e.message)


# ----------------------------------------------------------------------------- autocomplete

async def open_any_ac(interaction: discord.Interaction, current: str):
    """Own open trades (admins: everyone's), futures then spot, e.g. 'Scient · ▲ LONG · BTC 4H - ...'."""
    uid = None if _is_admin(interaction) else interaction.user.id
    rows = []
    for kind, key, rec in store.open_records(uid):
        spot = kind == store.SPOT
        label = _ac_label(rec, spot=spot)
        if current.lower() in label.lower():
            rows.append((_ac_sortkey(rec, spot=spot, uid=interaction.user.id), label, store.make_id(kind, key)))
    rows.sort(key=lambda r: r[0])
    return [app_commands.Choice(name=lbl[:100], value=val) for _, lbl, val in rows[:25]]


async def open_futures_ac(interaction: discord.Interaction, current: str):
    uid = None if _is_admin(interaction) else interaction.user.id
    rows = []
    for kind, key, rec in store.open_records(uid):
        if kind == store.SPOT:
            continue
        label = _ac_label(rec)
        if current.lower() in label.lower():
            rows.append((_ac_sortkey(rec, uid=interaction.user.id), label, store.make_id(kind, key)))
    rows.sort(key=lambda r: r[0])
    return [app_commands.Choice(name=lbl[:100], value=val) for _, lbl, val in rows[:25]]


async def closed_own_ac(interaction: discord.Interaction, current: str):
    uid = None if _is_admin(interaction) else interaction.user.id
    rows = []
    for kind, key, rec in store.closed_records(uid):
        spot = kind == store.SPOT
        lbl = f"[{rec.get('result', '?')}] {rec.get('pair', '?').upper()} {'SPOT' if spot else rec.get('direction', '')} - closed {str(rec.get('closed_at', ''))[:10]}"
        rows.append((rec.get("closed_at") or "", lbl, store.make_id(kind, key)))
    rows.sort(reverse=True)
    cur = current.lower()
    return [app_commands.Choice(name=lbl[:100], value=v) for _, lbl, v in rows if cur in lbl.lower()][:25]


# ----------------------------------------------------------------------------- new trades

_FW = [app_commands.Choice(name=f, value=f) for f in FRAMEWORKS]


@setup.command(name="futures", description="Post a futures setup")
@app_commands.describe(
    pair="Pair, e.g. BTC or BTC/USDT",
    direction="Long or Short",
    entry="Entry price. With entry2 this is Entry 1",
    stop="Stop level - a number (63000) or a condition (4h close below 63000)",
    tp1="Take-profit 1 price. R:R is calculated for you",
    tp2="Take-profit 2 price",
    tp3="Take-profit 3 price",
    tp4="Take-profit 4 price",
    tp_split="Planned % to close at each TP, e.g. 25/50/25 - blank = you give tp_pct on each update",
    entry_type="Blank = Limit. Market = filled now. Giving entry2 makes it DCA automatically",
    entry2="Entry 2 price - makes the setup DCA",
    entry_split="DCA size split, e.g. 20/80 - blank = 50/50",
    risk="Account risk %, just the number - blank = 1",
    timeframe="Setup timeframe, e.g. 4H",
    framework="Setup framework",
    framework2="Second framework, if the setup uses two",
    context="One line of specifics, e.g. sweep + reclaim of range low - shown on the card",
    chart="Chart image",
    notes="Reasoning - posted in the trade thread, not on the card",
)
@app_commands.choices(
    direction=[app_commands.Choice(name="Long", value="LONG"), app_commands.Choice(name="Short", value="SHORT")],
    entry_type=[
        app_commands.Choice(name="Limit - one order, fills when price gets there (default)", value="LIMIT"),
        app_commands.Choice(name="Market - filled now", value="MARKET"),
    ],
    framework=_FW, framework2=_FW,
)
async def setup_futures(interaction: discord.Interaction, pair: str, direction: app_commands.Choice[str], entry: str, stop: str,
                        tp1: str = None, tp2: str = None, tp3: str = None, tp4: str = None, tp_split: str = None,
                        entry_type: app_commands.Choice[str] = None, entry2: str = None, entry_split: str = None,
                        risk: str = None, timeframe: str = None, framework: app_commands.Choice[str] = None,
                        framework2: app_commands.Choice[str] = None, context: str = None,
                        chart: discord.Attachment = None, notes: str = None):
    if not await _gate(interaction, "post setups"):
        return

    async def body():
        rec = svc.build_futures(
            analyst=_analyst_ctx(interaction.user), pair=pair, direction=direction.value, entry=entry, stop=stop,
            tp1=tp1, tp2=tp2, tp3=tp3, tp4=tp4, tp_split=tp_split,
            entry_type=(entry_type.value if entry_type else None), entry2=entry2, entry_split=entry_split,
            risk=risk or "1", timeframe=timeframe,
            frameworks=[f.value for f in (framework, framework2) if f], context=context, notes=notes)
        msg, channel = await _post_card(interaction, rec, TRADES_CHANNEL_ID, False, chart,
                                        f"{pair.upper()} {direction.value} - {interaction.user.display_name}", "Reasoning", notes)
        data = store.load(store.FUT)
        data[str(msg.id)] = rec
        store.save(store.FUT, data)
        try:
            await msg.edit(view=TradeSizerView(msg.id))
        except Exception as ex:
            print(f"[sizer] attach failed: {ex}", flush=True)
        await refresh_board()
        await _reply(interaction, f"Setup posted in {channel.mention} ({msg.jump_url})")
    await _run(interaction, body())


@setup.command(name="spot", description="Post a spot play - DCA zone, take-profits, invalidation")
@app_commands.describe(
    pair="Coin, e.g. SOL or AAVE - no /USDT needed",
    zone="Buy zone, top to bottom, e.g. 65 - 52",
    tp1="Take-profit 1 price",
    tp2="Take-profit 2 price",
    tp3="Take-profit 3 price",
    tp_split="Planned % to sell at each TP, e.g. 30/30/40 - under 100 leaves a moonbag",
    stop="Invalidation - level that kills the thesis, e.g. Weekly close below 48. Spot R is measured from it",
    avg_entry="Average entry so far - give it if you're already partly or fully in",
    play_type="Blank = Fresh (or Scaling when avg_entry is given). Filled = position built, now holding",
    allocation="Suggested portfolio allocation %, just the number",
    horizon="Expected hold, e.g. 3-6 months",
    chart="Chart image",
    notes="Thesis - posted in the play thread",
)
@app_commands.choices(play_type=[
    app_commands.Choice(name="Fresh - zone posted, buying starts now", value="FRESH"),
    app_commands.Choice(name="Scaling - partially filled, still bidding the zone", value="SCALING"),
    app_commands.Choice(name="Filled - position built, now holding", value="FILLED"),
])
async def setup_spot(interaction: discord.Interaction, pair: str, zone: str, tp1: str, tp2: str = None, tp3: str = None,
                     tp_split: str = None, stop: str = None, avg_entry: str = None, play_type: app_commands.Choice[str] = None,
                     allocation: str = None, horizon: str = None, chart: discord.Attachment = None, notes: str = None):
    if not await _gate(interaction, "post plays"):
        return

    async def body():
        rec = svc.build_spot(analyst=_analyst_ctx(interaction.user), pair=pair, zone=zone, tp1=tp1, tp2=tp2, tp3=tp3,
                             tp_split=tp_split, stop=stop, avg_entry=avg_entry,
                             play_type=(play_type.value if play_type else None), allocation=allocation,
                             horizon=horizon, notes=notes)
        msg, channel = await _post_card(interaction, rec, SPOT_CHANNEL_ID, True, chart,
                                        f"{pair.upper()} SPOT - {interaction.user.display_name}", "Thesis", notes)
        data = store.load(store.SPOT)
        data[str(msg.id)] = rec
        store.save(store.SPOT, data)
        await refresh_board()
        await _reply(interaction, f"Spot play posted in {channel.mention} ({msg.jump_url})")
    await _run(interaction, body())


# ----------------------------------------------------------------------------- update / close

@setup.command(name="update", description="Log what happened on a live trade - fills, take profits, stop moves, invalidation")
@app_commands.describe(
    trade="Pick the trade",
    event="What happened",
    price="The price it happened at - TPs, stop moves, spot buys. Blank = the preset level",
    tp_pct="% of the position closed at this TP - blank = planned % from the card. Spot buy: % of the bag",
    note="Note - shown in the update and the thread",
)
@app_commands.choices(event=[app_commands.Choice(name=n, value=v) for v, n in svc.EVENTS])
@app_commands.autocomplete(trade=open_any_ac)
@_with_state_lock
async def setup_update(interaction: discord.Interaction, trade: str, event: app_commands.Choice[str],
                       price: str = None, tp_pct: str = None, note: str = None):
    if not await _gate(interaction):
        return

    async def body():
        kind, key, rec, data = store.find(trade)
        _own(interaction, rec)
        if rec.get("closed"):
            raise UserError("That trade is closed. **/setup reopen** it first if the close was wrong.")
        out = svc.apply_event(kind, rec, event.value, price=price, pct=tp_pct, note=note)
        await _finish(interaction, kind, key, rec, data, out, note)
    await _run(interaction, body())


@setup.command(name="close", description="Close a trade at a price - the bot works out Win / Loss / Breakeven")
@app_commands.describe(
    trade="Pick the trade",
    price="Exit price for whatever is still open. Blank only if every % was already taken as TPs",
    note="Closing note - shown in the update and the thread",
)
@app_commands.autocomplete(trade=open_any_ac)
@_with_state_lock
async def setup_close(interaction: discord.Interaction, trade: str, price: str = None, note: str = None):
    if not await _gate(interaction):
        return

    async def body():
        kind, key, rec, data = store.find(trade)
        _own(interaction, rec)
        out = svc.close(kind, rec, price, note)
        await _finish(interaction, kind, key, rec, data, out, note)
    await _run(interaction, body())


# ----------------------------------------------------------------------------- edit / fix

@setup.command(name="edit", description="Fix a typo on a posted trade - fill only what changes")
@app_commands.describe(
    trade="Pick the trade",
    pair="Pair",
    direction="Long / Short - futures",
    entry_type="Market / Limit - futures",
    entry="Entry price (futures) or buy zone (spot)",
    entry2="Entry 2 price - futures DCA",
    entry_split="DCA size split e.g. 20/80 - a space clears it",
    stop="Stop (futures) or invalidation (spot)",
    risk="Risk % (futures) or allocation % (spot)",
    tp1="Take-profit 1",
    tp2="Take-profit 2",
    tp3="Take-profit 3",
    tp4="Take-profit 4 - futures",
    tp_split="Planned % per TP e.g. 25/50/25 - a space clears it",
    timeframe="Timeframe - futures",
    framework="Framework - futures",
    framework2="Second framework - futures",
    context="Setup context line - futures",
    avg_entry="Spot: set the average entry by hand (no time limit)",
    status="Spot: phase, only if the auto status is wrong (no time limit)",
    chart="Replacement chart image",
    notes="Reasoning / thesis - posted in the thread",
)
@app_commands.choices(
    direction=[app_commands.Choice(name="Long", value="LONG"), app_commands.Choice(name="Short", value="SHORT")],
    entry_type=[app_commands.Choice(name="Market - filled now", value="MARKET"), app_commands.Choice(name="Limit", value="LIMIT")],
    framework=_FW, framework2=_FW,
    status=[app_commands.Choice(name=s.capitalize(), value=s) for s in SPOT_STATUSES],
)
@app_commands.autocomplete(trade=open_any_ac)
@_with_state_lock
async def setup_edit(interaction: discord.Interaction, trade: str, pair: str = None, direction: app_commands.Choice[str] = None,
                     entry_type: app_commands.Choice[str] = None, entry: str = None, entry2: str = None, entry_split: str = None,
                     stop: str = None, risk: str = None, tp1: str = None, tp2: str = None, tp3: str = None, tp4: str = None,
                     tp_split: str = None, timeframe: str = None, framework: app_commands.Choice[str] = None,
                     framework2: app_commands.Choice[str] = None, context: str = None, avg_entry: str = None,
                     status: app_commands.Choice[str] = None, chart: discord.Attachment = None, notes: str = None):
    if not await _gate(interaction):
        return

    async def body():
        kind, key, rec, data = store.find(trade)
        _own(interaction, rec)
        spot = kind == store.SPOT
        fields = {"pair": pair, "direction": direction.value if direction else None,
                  "entry_type": entry_type.value if entry_type else None, "entry": entry, "entry2": entry2,
                  "entry_split": entry_split, "stop": stop, "risk": risk, "tp1": tp1, "tp2": tp2, "tp3": tp3, "tp4": tp4,
                  "tp_split": tp_split, "timeframe": timeframe, "framework": framework.value if framework else None,
                  "framework2": framework2.value if framework2 else None, "context": context,
                  "avg_entry": avg_entry, "status": status.value if status else None, "chart": chart, "notes": notes}
        state_only = {k for k, v in fields.items() if v is not None} <= {"avg_entry", "status", "notes"}
        if not _is_admin(interaction) and not state_only and not within_edit_window(rec):
            raise UserError(f"Edit window ({EDIT_WINDOW_MIN // 60}h) has passed. Use **/setup update** for what happened since, "
                            f"**/setup fix** for a wrong fill, or ask an admin.")
        changes = svc.edit(kind, rec, fields)
        data[key] = rec
        store.save(kind, data)
        channel = bot.get_channel(rec["channel_id"])
        try:
            msg = await channel.fetch_message(rec["message_id"])
        except Exception:
            raise UserError("Original message not found - it may have been deleted.")
        builder = build_spot_embed if spot else build_embed
        if chart is not None:
            f = await chart.to_file()
            embed = builder(rec)
            embed.set_image(url=f"attachment://{chart.filename}")
            await msg.edit(embed=embed, attachments=[f])
        elif msg.attachments:
            att = msg.attachments[0]
            embed = builder(rec)
            embed.set_image(url=f"attachment://{att.filename}")
            await msg.edit(embed=embed, attachments=list(msg.attachments))
        else:
            await msg.edit(embed=builder(rec))
        await refresh_board()
        changed_txt = ", ".join(changes)
        await thread_note(rec, f"**Edited** - corrected: {changed_txt}")
        if notes is not None:
            await thread_note(rec, f"**{'Thesis' if spot else 'Reasoning'} (updated):** {notes[:1900]}")
        await _reply(interaction, f"Updated ({changed_txt}). {jump_url(rec)}")
    await _run(interaction, body())


@setup.command(name="fix", description="Remove or correct a wrongly recorded take profit or buy - any time")
@app_commands.describe(
    trade="Pick the trade",
    action="Run 'List' first to see the item numbers",
    item="Which one - the number from the list (1 = oldest)",
    price="Corrected price (with Fix)",
    pct="Corrected % (with Fix)",
    note="Correction note - posted in the thread",
)
@app_commands.choices(action=[
    app_commands.Choice(name="List - show the recorded fills with their numbers", value="list"),
    app_commands.Choice(name="Remove a take profit", value="remove_tp"),
    app_commands.Choice(name="Fix a take profit (price / pct)", value="fix_tp"),
    app_commands.Choice(name="Remove a buy (spot)", value="remove_buy"),
    app_commands.Choice(name="Fix a buy (price / pct, spot)", value="fix_buy"),
])
@app_commands.autocomplete(trade=open_any_ac)
@_with_state_lock
async def setup_fix(interaction: discord.Interaction, trade: str, action: app_commands.Choice[str], item: int = None,
                    price: str = None, pct: str = None, note: str = None):
    if not await _gate(interaction):
        return

    async def body():
        kind, key, rec, data = store.find(trade)
        _own(interaction, rec)
        if action.value == "list":
            await _reply(interaction, f"**{rec['pair'].upper()}** - recorded fills:\n{svc.ledger_text(kind, rec)}\n"
                                      f"*Re-run `/setup fix` with the action + `item` number to remove or fix one.*")
            return
        change = svc.fix_fill(kind, rec, action.value, item, price, pct)
        data[key] = rec
        store.save(kind, data)
        await refresh_and_edit(rec, spot_mode=(kind == store.SPOT))
        await refresh_board()
        await thread_note(rec, f"**Correction** - {change}" + (f" - {note}" if note else ""))
        await _reply(interaction, f"✅ {change}. Card and journal updated.")
    await _run(interaction, body())


# ----------------------------------------------------------------------------- track / reopen / xpost

@setup.command(name="track", description="Auto price tracking on or off for one futures setup")
@app_commands.describe(trade="Pick the open setup", mode="Off = you update it by hand. On = tracker fills entries, TPs and the stop from live price")
@app_commands.choices(mode=[app_commands.Choice(name="Off - manual updates only", value="off"),
                            app_commands.Choice(name="On - tracker re-armed, feed re-verified", value="on")])
@app_commands.autocomplete(trade=open_futures_ac)
@_with_state_lock
async def setup_track(interaction: discord.Interaction, trade: str, mode: app_commands.Choice[str]):
    if not await _gate(interaction):
        return

    async def body():
        kind, key, rec, data = store.find(trade)
        if kind == store.SPOT:
            raise UserError("Spot plays aren't auto-tracked - log buys and TPs with /setup update.")
        _own(interaction, rec)
        txt = svc.set_tracking(rec, mode.value == "on")
        data[key] = rec
        store.save(kind, data)
        await _reply(interaction, f"Auto-tracking **{txt}** for {rec.get('pair', '?').upper()}.")
    await _run(interaction, body())


@setup.command(name="reopen", description="Undo a wrong close - the trade goes live again and its results card is removed")
@app_commands.describe(trade="Pick the closed trade")
@app_commands.autocomplete(trade=closed_own_ac)
@_with_state_lock
async def setup_reopen(interaction: discord.Interaction, trade: str):
    if not await _gate(interaction):
        return

    async def body():
        kind, key, rec, data = store.find(trade)
        _own(interaction, rec)
        spot = kind == store.SPOT
        svc.reopen(kind, rec)
        data[key] = rec
        store.save(kind, data)
        state = load_results()
        removed = False
        for rk in ("fut", "spot"):
            rkey = f"{rk}:{key}"
            if rkey in set(state.get("posted", [])):
                state["posted"] = [x for x in state.get("posted", []) if x != rkey]
                msg_id = (state.get("posted_msgs") or {}).pop(rkey, None)
                if msg_id and RESULTS_CHANNEL_ID:
                    ch = bot.get_channel(RESULTS_CHANNEL_ID)
                    if ch:
                        try:
                            m = await ch.fetch_message(msg_id)
                            await m.delete()
                        except Exception:
                            pass
                removed = True
        save_results(state)
        try:
            await refresh_and_edit(rec, spot_mode=spot)
        except Exception:
            pass
        await refresh_board()
        await refresh_results_summary(repost=True)
        await post_update_feed(rec, "Setup reopened", BLUE,
                               "Previous close was recorded in error and has been reversed. "
                               + ("The play is live again." if spot else "The setup is live again - auto-tracking is off for it (manual updates)."),
                               footer=(SPOT_FOOTER if spot else FUT_FOOTER))
        await _reply(interaction, f"Reopened {rec.get('pair', '?').upper()}." + (" Wrong results card deleted, summary rebuilt." if removed else ""))
    await _run(interaction, body())


@setup.command(name="xpost", description="Share an X post into the X feed channel")
@app_commands.describe(link="X/Twitter post URL", comment="Optional intro text")
async def setup_xpost(interaction: discord.Interaction, link: str, comment: str = None):
    if not await _gate(interaction, "share X posts"):
        return

    async def body():
        if not X_FEED_CHANNEL_ID:
            raise UserError("X feed channel not set.")
        channel = bot.get_channel(X_FEED_CHANNEL_ID)
        if channel is None:
            raise UserError("X feed channel not found.")
        if not re.match(r"https?://(www\.|mobile\.|m\.)?(twitter|x|fxtwitter|vxtwitter|nitter)\.com/", link.strip(), flags=re.I):
            raise UserError("That doesn't look like an X/Twitter post link.")
        fixed = fix_x_link(link)
        parts = []
        allowed = discord.AllowedMentions.none()
        if X_PING_ROLE_ID:
            parts.append(f"<@&{X_PING_ROLE_ID}>")
            allowed = discord.AllowedMentions(roles=True)
        if comment:
            parts.append(comment)
        parts.append(fixed)
        msg = await channel.send(content="\n".join(parts), allowed_mentions=allowed)
        await _reply(interaction, f"Posted to {channel.mention} ({msg.jump_url})")
    await _run(interaction, body())


bot.tree.add_command(setup)

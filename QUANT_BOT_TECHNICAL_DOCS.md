# Quant — Scient Lounge Discord Bot
## Technical Documentation

**Version:** v3.0 (August 2026) · **File:** `scient_trades_bot.py` (~2,750 lines, single file) · **Commands:** 37

---

## 1. Overview

Quant is the trade journaling, market tooling, and news infrastructure bot for the Scient Lounge Discord server. It handles four jobs:

1. **Trade journaling** — analysts post futures setups and spot plays through slash commands; the bot renders branded embed cards, tracks every fill, calculates realized R automatically, and maintains live pinned position boards.
2. **Quant Terminal** — member-facing market tools: charts, heatmaps, auto levels, funding, open interest, calculators, and more (Binance/CoinGecko public data, no API keys).
3. **News wire** — a persistent TreeNews websocket feed filtered to high-impact headlines, posted to a dedicated channel with urgent-only role pings.
4. **Community** — analyst follow/ping roles, X post auto-feed, TA quiz engine, help panels.

Design principles: **math over judgement** (Win/Loss decided by calculated R, never manually), **transparency by default** (edit timestamps, mandatory size percentages), **zero paid APIs** for market data.

---

## 2. Architecture & Stack

| Layer | Detail |
|---|---|
| Language | Python 3.12 |
| Framework | `discord.py` (app commands / slash command tree) |
| HTTP/WS | `aiohttp` (REST calls + TreeNews websocket) |
| Charts | `mplfinance` + `matplotlib` + `pandas` (rendered off the event loop via `asyncio.to_thread`) |
| Storage | Flat JSON files next to the script (no database) |
| Host | Hostinger KVM 1 VPS, Ubuntu 24.04 — `root@200.97.168.62` |
| Process manager | systemd — service `scient-bot`, auto-start on boot, venv Python at `/root/scient_trades_bot/venv/bin/python` |
| Source control | GitHub — `github.com/RumblingScient/Scient-trades-bot`, branch `main` |

### External APIs (all free, no keys)

| API | Used for |
|---|---|
| `api.binance.com` (spot) | /price, /chart klines, /gainers, /losers, /convert, /vol, /levels, /heatmap, /compare |
| `fapi.binance.com` (futures) | /funding, /oi |
| `api.alternative.me` | /fear (Fear & Greed) |
| `api.coingecko.com` | /dominance |
| `wss://news.treeofalpha.com/ws` | News wire (websocket, push-based — no polling, no credits) |
| `api.twitterapis.com` | X auto-feed (API key in `.env`, polled every 30 min) |

### Deploy pipeline

```
Edit file locally / via Claude
  → paste into GitHub web editor (pencil icon → Cmd+A → paste → Commit changes)
  → SSH: ssh root@200.97.168.62
  → cd ~/scient_trades_bot && git pull && systemctl restart scient-bot
  → verify: journalctl -u scient-bot -n 5 --no-pager   ("Logged in as Quant - commands synced")
  → Discord: Cmd+R to refresh the command list
```

One-time dependency installs go into the **venv**, not system Python:

```
/root/scient_trades_bot/venv/bin/pip install <package>
```

---

## 3. Configuration Reference

All configuration is constants at the top of the file. `.env` (same directory) holds secrets and is loaded manually at startup.

### Secrets (`.env`)
| Key | Purpose |
|---|---|
| `SCIENT_BOT_TOKEN` | Discord bot token |
| `TWITTERAPIS_KEY` | TwitterAPIs.com key for the X auto-feed |

### Discord IDs
| Constant | Value | Purpose |
|---|---|---|
| `GUILD_ID` | 1213101801675554846 | Server (commands synced per-guild) |
| `TRADES_CHANNEL_ID` | 1525147189360332840 | #future-trades — futures cards |
| `SPOT_CHANNEL_ID` | 1533876035462889482 | #spot-trades — spot cards |
| `TRADE_UPDATES_CHANNEL_ID` | 1525863205174378617 | #trade-updates — event feed |
| `OPEN_BOARD_CHANNEL_ID` | 1525863082256109690 | #open-positions — both pinned boards |
| `X_FEED_CHANNEL_ID` | 1525862152076923020 | #x-updates — X auto-feed |
| `NEWS_CHANNEL_ID` | 1535048677406539797 | #news-wire — TreeNews feed |
| `QUANT_CHANNEL_ID` | 0 | Reserved; 0 = commands work in every channel |
| `PING_ROLE_ID` | 1525861312729452704 | "All Trades" ping role |
| `X_PING_ROLE_ID` | 1525861448088031462 | X updates ping role |
| `NEWS_PING_ROLE_ID` | 1535053641378037760 | Breaking News role — pinged on **urgent** news only |

### Behavior constants
| Constant | Value | Meaning |
|---|---|---|
| `EDIT_WINDOW_MIN` | 60 | Minutes after posting during which `/edit` works (admins bypass) |
| `EMA_PERIODS` | [20, 50, 100, 200] | EMAs drawn on `/chart` |
| `EMA_COLORS` | orange/amber/blue/navy | Brand-palette EMA line colors |
| `X_AUTO_USERNAME` | Crypto_Scient | Account polled for the X feed |
| `X_POLL_MINUTES` | 30 | X feed poll interval |
| `NEWS_COINS` | BTC/ETH/SOL (+full names) | Coin tags that always pass the news filter |
| `NEWS_KEYWORDS` | ~30 terms | Keyword filter: SEC, ETF, Fed, FOMC, CPI, hack, exploit, listing, delisting, bankruptcy, liquidat-, halt, lawsuit, Binance, Coinbase, Tether, BlackRock… |
| `ANALYSTS` dict | scient / owais / 94 | Per-analyst color, ping role ID, Discord user IDs |
| `FRAMEWORKS` list | 11 entries | Setup framework dropdown (FRVP/POC, AMD, Wyckoff, RSI Div, BOS/MSS, Fib Pocket, Range, Three Drives, Deviation Reclaim, EMA Cross, Other) |

### Analyst colors
| Analyst | Hex |
|---|---|
| Scient | `#1C4E80` (navy — brand primary) |
| Owais | `#7C3AED` (purple) |
| 94 | `#2E7D32` (green) |

Unknown analysts get a deterministic color hashed from their user ID.

---

## 4. Data Storage

Flat JSON files in the bot directory. Written atomically on every change via `_save()`. **These files are the entire database — back them up before any reset.**

| File | Contents |
|---|---|
| `trades.json` | All futures trades, keyed by Discord message ID |
| `spot_plays.json` | All spot plays, keyed by message ID |
| `board.json` / `spot_board.json` | Pinned board message IDs (so boards edit in place) |
| `x_posted.json` | Seen tweet IDs (dedup, last 500) |
| `.env` | Secrets (never in git) |

### Trade record schema (futures, key fields)

```json
{
  "analyst_id": 249880856993202187,
  "analyst_key": "scient", "analyst_color": "#1C4E80",
  "pair": "SOL/USDT", "direction": "LONG", "timeframe": "4H",
  "entry": "74.9", "entry2": "73",            // entry2 only for DCA setups
  "sl": "66", "risk": "1.5",
  "entry_type": "LIMIT",                        // or MARKET
  "tp1": "78.1", "tp2": "82.6", "tp3": "88.5",
  "entry1_filled": true, "entry2_filled": false,
  "tp1_hit": true, "tp2_hit": false, "tp3_hit": false,
  "sl_hit": false, "be": false,
  "fills": [ {"price": 78.1, "pct": 25, "label": "TP1"} ],
  "avg_exit": null,
  "closed": false, "result": null, "result_r": null,
  "edited": false, "edited_at": null,
  "message_id": ..., "channel_id": ..., "thread_id": ...
}
```

**Backups:** `backup_trades.sh` + `~/scient_backups/` exist on the VPS. Manual backup before any destructive operation:

```
cp ~/scient_trades_bot/trades.json ~/bot_backups/trades_$(date +%Y%m%d).json
```

**Full data reset** (fresh records): `echo '{}' > trades.json` (and/or `spot_plays.json`) → restart → `/board` + `/spot_board` to rebuild pinned boards. Old cards in Discord remain as history but become orphans (no further /update possible on them).

---

## 5. The Auto-R Engine (core logic)

The bot computes realized R from actual fills — no manual R entry, no manual Win/Loss.

### Definitions
- **Entry price** — single entry as given; for a DCA trade, the average of filled legs: both filled → `(e1+e2)/2`, only one filled → that leg. Legacy range entries ("74.9 - 73") use the midpoint.
- **Risk per unit (1R)** = `|entry − SL|`
- **Signed R of a price** = `(price − entry) / 1R` for longs, `(entry − price) / 1R` for shorts.

### Fill tracking
Every TP/Partial event **requires `size_pct`** (rejected otherwise). Each fill is stored as `{price, pct, label}`. Total fills can never exceed 100% (validated).

### Close calculation
On close (via `/update` → Closed, or SL Hit):

```
remaining % = 100 − Σ fill percentages
avg_exit    = Σ (price × pct) / Σ pct        (remaining % filled at the close/SL price)
realized R  = signed_r(avg_exit)
```

**Result is decided by the math:** R > +0.05 → WIN · R < −0.05 → LOSS · otherwise → BE.

SL Hit uses the SL price for the remaining position — unless breakeven was set (`be: true`), in which case the entry price is used.

**Worked example** — entry 100, SL 95 (1R = 5): TP1 105 @ 25%, TP2 111 @ 40%, close 120 @ 35% → avg exit = 112.65 → **+2.53R WIN**.

### Card display
Planned R is auto-shown per TP: `TP: 105 (1.0R) / 111 (2.2R) / 120 (4.0R)`. The R:R column shows the furthest TP's R. Closed cards show `CLOSED - WIN (+2.53R)` + `Avg Exit`.

---

## 6. Trade Lifecycle

```
/trade (Market | Limit single | Limit DCA)
   └─ card posted in #future-trades + thread created (+ Reasoning in thread)
      + ping: All-Trades role + analyst's own follower role
      + open-positions board refreshed

/update  — events:
   Entry 1 Filled · DCA Entry Filled · TP1/2/3 Hit (size_pct REQUIRED)
   Partial TP (size_pct + price REQUIRED; auto-slots into next unhit TP)
   SL Moved to Entry (Risk-Free) · SL Hit (closes) · Closed (price of remainder REQUIRED)
   Invalidated
   └─ every event: card updated, board refreshed, #trade-updates embed, thread note

/edit — any field or chart, within 60 min of posting (admins bypass)
   └─ card footer stamped "· edited DD/MM HH:MM AM/PM" (IST) + thread note
```

**Spot plays** mirror this with `/spot` → `/spot_update` (avg entry, phase ACCUMULATING/HOLDING/DISTRIBUTING, targets, zone filled) → `/spot_close` (result in %, e.g. +190%). Spot has its own gold pinned board and separate `/spot_stats` — spot % results never mix with futures R stats.

**Boards:** two pinned embeds in #open-positions (navy = futures, gold = spot), grouped by analyst, edited in place on every change. `/board` and `/spot_board` (admin) force-rebuild if the pinned message is lost.

---

## 7. Command Reference (37)

### Analyst commands (require the **Analyst** role; admins always pass)
| Command | Purpose |
|---|---|
| `/trade` | Post futures setup — pair, direction, entry_type (Market/Limit/Limit DCA), entry, [entry2], SL, risk + optional chart, TPs, frameworks (2 max), timeframe, setup detail, notes→thread |
| `/update` | Trade events incl. closing — see lifecycle above |
| `/spot` `/spot_update` `/spot_close` | Spot play lifecycle |
| `/edit` | Fix mistakes within the 60-min window (works on futures + spot) |
| `/xpost` | Share an X link to #x-updates (auto-converts to fxtwitter, pings X role) |

### Member commands — market tools (public replies)
| Command | Data source | Notes |
|---|---|---|
| `/price coin` | Binance spot | Price, 24h %, high/low; color-coded |
| `/chart coin timeframe` | Binance klines | Candlestick PNG, 4 EMAs, volume, right-side scale, last-price tag; 15m/1H/4H/1D/1W; 220 candles |
| `/funding coin` | Binance perps | Rate per 8h + annualized, crowded side, next funding countdown |
| `/oi coin` | Binance perps | OI in coins + USD, 24h change |
| `/gainers` `/losers` | Binance spot | Top 5, USDT pairs, min $10M volume, leveraged tokens excluded |
| `/heatmap` | Binance spot | Top-20 grid PNG, color intensity ∝ 24h move |
| `/levels coin [tf]` | Binance klines | Swing-pivot (window 5) support/resistance, clustered at 0.6% tolerance, ⭐ = touches, % distance |
| `/vol coin` | Binance klines | ATR(14, 4H) as % + 10-day avg daily range + 🔥/🌡️/🧊 rating |
| `/dominance` | CoinGecko | BTC/ETH/others %, total mcap + 24h change |
| `/compare coin1 coin2` | Binance klines | 30-day normalized performance overlay PNG |
| `/convert amount coin [to_coin]` | Binance | Coin→USD or USD→coin quantity |
| `/fear` | alternative.me | Fear & Greed with bar + change vs yesterday |

### Member commands — private (ephemeral replies)
| Command | Purpose |
|---|---|
| `/pnl` | Position size from account, risk %, entry, SL; optional leverage → margin + over-account ⚠️ |
| `/liq` | Liquidation estimate (generic 0.5% maintenance margin + disclaimer) |
| `/open` | Both live boards |
| `/recent [analyst]` | Last 7 futures + 5 spot closed, with results |
| `/stats [analyst]` | Futures scorecard: totals, win rate, TP1 rate, **Total R**, Avg R, "Graded on" count, best/worst |
| `/spot_stats [analyst]` | Spot scorecard |
| `/help` | Full command guide (same embed as the pinned panel) |

### Fun & learning
| Command | Purpose |
|---|---|
| `/quiz` | 74-question bank (basics → psychology → Scient frameworks). Public question, **private** results, "Next question ▶" streak sessions with score, no repeats within a session, 1h button timeout with graceful grey-out |
| `/coinflip` | Coin flip + a rotating risk-management lesson |

### Alerts
| Command | Purpose |
|---|---|
| `/follow` `/unfollow` | Per-analyst ping role self-assign |
| Follow panel buttons | Per-analyst + Follow All + X Updates + 🚨 Breaking News (persistent view, survives restarts) |

### Admin commands
| Command | Purpose |
|---|---|
| `/setup_follow_panel` | Post the follow-role button panel |
| `/setup_help_panel` | Post + pin the public command guide (shares one embed builder with `/help` — they can never drift) |
| `/board` `/spot_board` | Force-rebuild pinned boards |
| `/news_status` | News wire health: running/last message/posted count |
| `/xtest` | Test the TwitterAPIs connection |

---

## 8. News Wire (TreeNews)

- **Transport:** persistent websocket to `wss://news.treeofalpha.com/ws` (free tier — all core sources, small delay acceptable for swing trading). Push-based: zero polling cost, cannot burn API credits.
- **Filter (`_news_relevant`)** — a headline posts only if: any tagged symbol ∈ {BTC, ETH, SOL} **or** text contains a high-impact keyword (see config table). Everything else is silently dropped.
- **Urgent detection (`_news_urgent`)** — hack/exploit/breach/stolen/bankrupt/halt/delist → red embed, 🚨 prefix, **and the only case that pings** `NEWS_PING_ROLE_ID`. Normal news posts navy 📰 with no ping.
- **Formatting** — headline (≤250 chars), body (≤400), coin tags as inline code, source in footer, link on the title.
- **Dedup** — MD5 of first 200 chars, rolling window of 300.
- **Resilience** — reconnect loop with exponential backoff 5s → 300s cap; heartbeat 30s; task starts in `on_ready` and survives Discord reconnects. VPS reboot → systemd restarts bot → wire reconnects automatically.
- **Monitoring** — `/news_status` (admin, ephemeral) + `[news]` prefixed journal logs.

Channel: **#news-wire** (read-only, PRO LOUNGE, default-muted recommended). Role: **News Ping** — advertised on the follow panel as "🚨 Breaking News".

---

## 9. X Auto-Feed

Polls TwitterAPIs advanced search every 30 min for `from:Crypto_Scient -filter:replies -filter:retweets`. New tweets post to #x-updates as fxtwitter links with the X-Updates role ping. First run seeds seen-IDs without posting (no flood). Dedup persists in `x_posted.json`. Silently skips when the API key is missing or credits are exhausted — never crashes the bot.

> Note: the standalone confluence tracker (multi-analyst monitoring) was **removed** in Aug 2026 after burning API credits at ~30× expected rate with unreliable signal quality. Its service and files were deleted from the VPS.

---

## 10. Permissions Model

| Level | Check | Applies to |
|---|---|---|
| Admin | `guild_permissions.administrator` | setup/board/status commands; bypasses edit window and ownership checks |
| Analyst | role name `Analyst` (or admin) | trade/spot/edit/update/xpost |
| Member | none | all market tools, calculators, stats, quiz, follow |

Ownership: analysts can only `/edit` and `/update` **their own** trades (autocomplete only offers their own open trades); admins see everyone's.

Bot needs: Send Messages, Embed Links, Attach Files, Manage Messages (pinning), Manage Roles (follow roles — bot's role must sit above the ping roles), Create Public Threads.

---

## 11. Operations Runbook

### Service management
```
systemctl status scient-bot          # health
systemctl restart scient-bot         # after every git pull
journalctl -u scient-bot -n 20 --no-pager    # recent logs
journalctl -u scient-bot -f          # live tail
```

### Log markers
| Marker | Meaning |
|---|---|
| `Logged in as Quant - commands synced` | Healthy start |
| `[news] connected to TreeNews` | News wire live |
| `[news] disconnected - retrying in Ns` | Auto-reconnect in progress (normal occasionally) |
| `[x_poll] posted N new tweet(s)` | X feed working |
| `Privileged message content intent is missing` | **Harmless** — bot is slash-command only |

### Common issues
| Symptom | Fix |
|---|---|
| SSH hangs at "Connecting… port 22" | Server-side is usually fine — check Hostinger panel firewall, or reboot VPS from hPanel; browser terminal (hPanel) works as fallback |
| `No module named 'X'` in a command | Package went into system Python — install with `/root/scient_trades_bot/venv/bin/pip install X` |
| New/changed commands not visible | Wait ~1 min after restart, then Cmd+R in Discord |
| Old quiz/panel buttons error after deploy | Expected — in-memory views die on restart. Follow panel is persistent; quizzes are not (run a new `/quiz`) |
| Duplicate command name on start | Bot exits at startup — check the file wasn't pasted twice into GitHub |
| Board unpinned/deleted | `/board` or `/spot_board` rebuilds it |

### Adding a new analyst
1. Give them the **Analyst** role in Discord.
2. Create their personal ping role; copy its ID.
3. Add an entry to the `ANALYSTS` dict: key (lowercase), `color` hex, `ping_role_id`, `user_ids: [their Discord ID]`.
4. Deploy. 5. Re-run `/setup_follow_panel` so their follow button appears (delete the old panel message).

---

## 12. Known Limitations

- **Single-guild, single-file, JSON storage** — fine at current scale; a database migration is only worth it if trade volume grows 10×.
- **Slash-command field visibility** — Discord shows all optional fields regardless of the chosen event; conditional forms aren't possible without a modal/button flow (evaluated and rejected as slower UX). Mitigated with event-specific field descriptions + server-side validation.
- **Non-persistent quiz views** — quiz buttons die on bot restart (deploys) and after the 1h timeout (buttons grey out gracefully).
- **Legacy trades** — records created before fills-tracking have no `fills` data; the "Graded on" stat field makes this visible instead of hiding it.
- **Liquidation calculator is an estimate** — generic 0.5% maintenance margin; exact levels vary by exchange/tier/margin mode (disclaimed in the reply).
- **TreeNews free tier** — headlines may arrive with a small delay vs paid subscribers; irrelevant for swing-trading use.

---

## 13. Version History (high level)

| Version | Date | Highlights |
|---|---|---|
| v1.x | pre-Aug 2026 | Trade cards, boards, follow panel, X feed, stats |
| v2.0 | Aug 3–4 | Compact cards, auto-R engine with weighted fills, mandatory size_pct, /close merged into /update, DCA entries, /edit 1h window, spot plays system, member commands (/open /recent /price /pnl) |
| v2.1 | Aug 5 | /trade field reorder, event-specific field descriptions, "SL Moved to Entry (Risk-Free)" rename, Total R in /stats, data reset, confluence tracker removed |
| v3.0 | Aug 7 | Quant Terminal (16 new commands incl. /chart /heatmap /levels /vol /oi /compare), quiz academy (74 Q, streaks), TradingView-grade chart engine, TreeNews news wire + Breaking News role, /setup_help_panel |

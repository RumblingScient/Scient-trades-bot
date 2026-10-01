"""sigma.commands_terminal - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from sigma.logging_setup import print
from sigma.config import EMA_PERIODS, GOLD, GREEN, GREY, IST, NAVY, RED, SG_AMBER, SG_ASH, SG_CARD, SG_CYAN, SG_CYAND, SG_GRA, SG_LONG, SG_OBS, SG_PAPER, SG_SHORT, SG_SLATE
from sigma.core import bot
from sigma.ops import _note_error
from sigma.http import http
from sigma.market_data import _get_json, fetch_agg_rows, is_tradfi, md_coinbase_price, md_funding, md_klines, md_oi, md_price, md_ticker24, md_tradfi
from sigma.charts_theme import CHART_INTERVALS, make_chart_image, sigma_logo_ax, sigma_style_ax
from sigma.calculations import fnum, parse_num
from sigma.telegram import build_daily_brief


@bot.tree.command(name="price", description="Live price for any coin")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL, ETH")
async def price(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    if symbol.endswith("USDT"):
        pair = symbol
    else:
        pair = f"{symbol}USDT"
    if is_tradfi(symbol):
        tf = await md_tradfi(symbol)
        if not tf:
            await interaction.followup.send(f"Couldn't fetch **{symbol}** right now.")
            return
        arrow = "\U0001F7E2" if tf["chg"] >= 0 else "\U0001F534"
        color = GREEN if tf["chg"] >= 0 else RED
        embed = discord.Embed(title=f"{arrow} {symbol}", color=color, timestamp=datetime.now(timezone.utc))
        embed.description = f"**Price:** {tf['price']:,.2f}\n**24h:** {tf['chg']:+.2f}%"
        embed.set_footer(text="Sigma Trading - traditional markets")
        await interaction.followup.send(embed=embed)
        return
    data = await md_ticker24(pair)
    if not data:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit - check the symbol (e.g. BTC, SOL, ETH).")
        return
    last = data["lastPrice"]
    chg = data["priceChangePercent"]
    high = data["highPrice"]
    low = data["lowPrice"]
    arrow = "\U0001F7E2" if chg >= 0 else "\U0001F534"
    color = GREEN if chg >= 0 else RED
    embed = discord.Embed(title=f"{arrow} {symbol}/USDT", color=color, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**Price:** ${fnum(last)}\n"
        f"**24h:** {chg:+.2f}%\n"
        f"**24h High:** ${fnum(high)} | **24h Low:** ${fnum(low)}"
    )
    embed.set_footer(text="Sigma Trading - Binance spot")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="pnl", description="Position size calculator - know your size before you enter")
@app_commands.describe(
    account="Account size in $ (e.g. 5000)",
    risk="Risk per trade in % (e.g. 1)",
    entry="Entry price",
    stop_loss="Stop loss price",
    leverage="Leverage (optional, shows margin needed)",
)
async def pnl(interaction: discord.Interaction, account: str, risk: str, entry: str, stop_loss: str, leverage: str = None):
    await interaction.response.defer(ephemeral=True)
    acc = parse_num(account)
    rk = parse_num(risk)
    en = parse_num(entry)
    sl = parse_num(stop_loss)
    lev = parse_num(leverage) if leverage else None
    if not all([acc, rk, en, sl]) or acc <= 0 or rk <= 0 or en <= 0 or sl <= 0:
        await interaction.followup.send("Check your inputs - account, risk, entry, and SL must all be positive numbers.", ephemeral=True)
        return
    if en == sl:
        await interaction.followup.send("Entry and SL can't be the same price.", ephemeral=True)
        return
    if lev is not None and lev <= 0:
        await interaction.followup.send("Leverage must be a positive number.", ephemeral=True)
        return
    risk_amount = acc * rk / 100
    sl_dist_pct = abs(en - sl) / en * 100
    position_value = risk_amount / (sl_dist_pct / 100)
    units = position_value / en
    direction = "LONG" if sl < en else "SHORT"
    lines = [
        f"**Direction:** {direction} (based on SL vs entry)",
        f"**Risk:** ${fnum(risk_amount)} ({rk:g}% of ${fnum(acc)})",
        f"**SL distance:** {sl_dist_pct:.2f}%",
        f"**Position size:** {fnum(units)} units (${fnum(position_value)} notional)",
    ]
    if lev:
        margin = position_value / lev
        if margin > acc:
            lines.append(f"**Margin @ {lev:g}x:** ${fnum(margin)} \u26A0\uFE0F exceeds your account size")
        else:
            lines.append(f"**Margin @ {lev:g}x:** ${fnum(margin)} ({margin / acc * 100:.1f}% of account)")
    embed = discord.Embed(title="Position Size Calculator", color=NAVY)
    embed.description = "\n".join(lines)
    embed.set_footer(text="Sigma Trading - risk first, always")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="chart", description="Quick price chart with EMAs (Binance data)")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL", timeframe="Chart timeframe")
@app_commands.choices(timeframe=[app_commands.Choice(name=k, value=k) for k in CHART_INTERVALS])
async def chart_cmd(interaction: discord.Interaction, coin: str, timeframe: app_commands.Choice[str]):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    interval = CHART_INTERVALS[timeframe.value]
    klines = await md_klines(pair, interval, 220)
    if not klines:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit - check the symbol.")
        return
    if not klines or len(klines) < 20:
        await interaction.followup.send(f"Not enough data for **{symbol}** on {timeframe.value}.")
        return
    try:
        buf = await asyncio.to_thread(make_chart_image, f"{symbol}/USDT", timeframe.value, klines)
    except Exception as e:
        await interaction.followup.send(f"Chart rendering failed: {e}")
        return
    f = discord.File(buf, filename=f"{symbol}_{timeframe.value}.png")
    ema_txt = " / ".join(str(p) for p in EMA_PERIODS)
    await interaction.followup.send(content=f"**{symbol}/USDT - {timeframe.value}** | EMAs: {ema_txt}", file=f)

@bot.tree.command(name="liq", description="Liquidation price calculator")
@app_commands.describe(entry="Entry price", leverage="Leverage, e.g. 10", direction="Long or Short")
@app_commands.choices(direction=[app_commands.Choice(name="Long", value="LONG"), app_commands.Choice(name="Short", value="SHORT")])
async def liq(interaction: discord.Interaction, entry: str, leverage: str, direction: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    en = parse_num(entry)
    lev = parse_num(leverage)
    if not en or not lev or en <= 0 or lev <= 1:
        await interaction.followup.send("Check inputs - entry must be positive and leverage above 1.", ephemeral=True)
        return
    mmr = 0.005
    if direction.value == "LONG":
        liq_price = en * (1 - 1 / lev + mmr)
        dist = (en - liq_price) / en * 100
    else:
        liq_price = en * (1 + 1 / lev - mmr)
        dist = (liq_price - en) / en * 100
    embed = discord.Embed(title="Liquidation Calculator", color=NAVY)
    embed.description = (
        f"**Direction:** {direction.value} @ {fnum(en)} | **Leverage:** {lev:g}x\n"
        f"**Est. liquidation:** {fnum(liq_price)}\n"
        f"**Distance:** {dist:.2f}% against you\n\n"
        f"*Estimate with 0.5% maintenance margin - exact level varies by exchange, position size, and margin mode. Always check your exchange.*"
    )
    embed.set_footer(text="Sigma Trading - risk first, always")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="funding", description="Current funding rate (Binance perps)")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL")
async def funding(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    fd = await md_funding(pair)
    if not fd:
        await interaction.followup.send(f"No perp market found for **{symbol}** on Binance or Bybit.")
        return
    rate = fd["rate"]
    mark = fd["mark"]
    nxt = fd["next"]
    lean = "Longs paying shorts (crowded long)" if rate > 0 else "Shorts paying longs (crowded short)" if rate < 0 else "Neutral"
    color = RED if abs(rate) > 0.05 else GREEN if abs(rate) < 0.01 else GOLD
    embed = discord.Embed(title=f"Funding - {symbol} Perp", color=color, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**Rate:** {rate:+.4f}% per 8h ({rate * 3 * 365:+.1f}% annualized)\n"
        f"**Lean:** {lean}\n"
        f"**Mark:** ${fnum(mark)} | **Next funding:** <t:{nxt}:R>"
    )
    embed.set_footer(text="Sigma Trading - Binance perps")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="fear", description="Crypto Fear & Greed index")
async def fear(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as session:
            async with session.get("https://api.alternative.me/fng/?limit=2", timeout=15) as resp:
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Fear & Greed data unavailable right now.")
        return
    try:
        today = data["data"][0]
        val = int(today["value"])
        label = today["value_classification"]
        prev = int(data["data"][1]["value"]) if len(data["data"]) > 1 else None
    except Exception:
        await interaction.followup.send("Couldn't parse Fear & Greed data.")
        return
    if val <= 25:
        color, emoji = RED, "\U0001F628"
    elif val <= 45:
        color, emoji = GOLD, "\U0001F61F"
    elif val <= 55:
        color, emoji = GREY, "\U0001F610"
    elif val <= 75:
        color, emoji = GREEN, "\U0001F642"
    else:
        color, emoji = GREEN, "\U0001F911"
    bar_filled = round(val / 10)
    bar = "\u2588" * bar_filled + "\u2591" * (10 - bar_filled)
    chg = f" ({val - prev:+d} vs yesterday)" if prev is not None else ""
    embed = discord.Embed(title=f"{emoji} Fear & Greed: {val} - {label}", color=color, timestamp=datetime.now(timezone.utc))
    embed.description = f"`{bar}` {val}/100{chg}\n\n*Extreme fear = others panicking. Extreme greed = time to be careful.*"
    embed.set_footer(text="Sigma Trading - alternative.me")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="oi", description="Open interest for a perp market")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL")
async def oi(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    od = await md_oi(pair)
    fd = await md_funding(pair)
    if not od:
        await interaction.followup.send(f"No perp market found for **{symbol}** on Binance or Bybit.")
        return
    oi_now = od["oi"]
    mark = fd["mark"] if fd else 0
    oi_usd = oi_now * mark if mark else None
    chg_txt = ""
    color = NAVY
    if od.get("oi_then"):
        try:
            oi_then = od["oi_then"]
            chg = (oi_now - oi_then) / oi_then * 100
            arrow = "\U0001F4C8" if chg >= 0 else "\U0001F4C9"
            chg_txt = f"\n**24h change:** {arrow} {chg:+.2f}%"
            color = GREEN if chg > 2 else RED if chg < -2 else NAVY
        except Exception:
            pass
    usd_txt = f" (${oi_usd / 1e9:.2f}B)" if oi_usd and oi_usd >= 1e9 else (f" (${oi_usd / 1e6:.1f}M)" if oi_usd else "")
    embed = discord.Embed(title=f"Open Interest - {symbol} Perp", color=color, timestamp=datetime.now(timezone.utc))
    embed.description = f"**OI:** {fnum(oi_now)} {symbol}{usd_txt}{chg_txt}"
    embed.set_footer(text="Sigma Trading - Binance perps")
    await interaction.followup.send(embed=embed)

async def _movers(interaction: discord.Interaction, top: bool):
    await interaction.response.defer()
    try:
        async with http() as session:
            async with session.get("https://api.binance.com/api/v3/ticker/24hr", timeout=20) as resp:
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Market data unavailable right now.")
        return
    rows = []
    for d in data:
        s = d.get("symbol", "")
        if not s.endswith("USDT") or any(x in s for x in ("UP", "DOWN", "BULL", "BEAR")):
            continue
        try:
            qv = float(d["quoteVolume"])
            chg = float(d["priceChangePercent"])
            last = float(d["lastPrice"])
        except Exception:
            continue
        if qv < 10_000_000:
            continue
        rows.append((s[:-4], chg, last, qv))
    rows.sort(key=lambda r: r[1], reverse=top)
    rows = rows[:5]
    if not rows:
        await interaction.followup.send("No data right now.")
        return
    title = "\U0001F4C8 Top Gainers (24h)" if top else "\U0001F4C9 Top Losers (24h)"
    color = GREEN if top else RED
    lines = []
    for i, (sym, chg, last, qv) in enumerate(rows, 1):
        vol = f"${qv / 1e9:.1f}B" if qv >= 1e9 else f"${qv / 1e6:.0f}M"
        lines.append(f"**{i}. {sym}** {chg:+.2f}% - ${fnum(last)} - vol {vol}")
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines) + "\n\n*USDT pairs, min $10M volume*"
    embed.set_footer(text="Sigma Trading - Binance spot")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="gainers", description="Top 5 gainers of the day")
async def gainers(interaction: discord.Interaction):
    await _movers(interaction, top=True)

@bot.tree.command(name="losers", description="Top 5 losers of the day")
async def losers(interaction: discord.Interaction):
    await _movers(interaction, top=False)

@bot.tree.command(name="dominance", description="BTC dominance + total market cap")
async def dominance(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as session:
            async with session.get("https://api.coingecko.com/api/v3/global", timeout=15) as resp:
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Market data unavailable right now.")
        return
    try:
        d = data["data"]
        btc_d = d["market_cap_percentage"]["btc"]
        eth_d = d["market_cap_percentage"].get("eth", 0)
        mcap = d["total_market_cap"]["usd"]
        mcap_chg = d.get("market_cap_change_percentage_24h_usd", 0)
    except Exception:
        await interaction.followup.send("Couldn't parse dominance data.")
        return
    others = 100 - btc_d - eth_d
    arrow = "\U0001F4C8" if mcap_chg >= 0 else "\U0001F4C9"
    embed = discord.Embed(title="Market Dominance", color=GOLD, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**BTC:** {btc_d:.1f}% | **ETH:** {eth_d:.1f}% | **Others:** {others:.1f}%\n"
        f"**Total market cap:** ${mcap / 1e12:.2f}T {arrow} {mcap_chg:+.2f}% (24h)\n\n"
        f"*BTC dominance rising = money rotating to BTC. Falling = alts catching bids.*"
    )
    embed.set_footer(text="Sigma Trading - CoinGecko")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="vol", description="Volatility snapshot - how much does this coin move?")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL")
async def vol(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    klines = await md_klines(pair, "4h", 60)
    if not klines:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit.")
        return
    if len(klines) < 20:
        await interaction.followup.send(f"Not enough data for **{symbol}**.")
        return
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    last = closes[-1]
    trs = []
    for i in range(1, len(klines)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr14 = sum(trs[-14:]) / 14
    atr_pct = atr14 / last * 100
    day_ranges = []
    for i in range(0, len(klines) - 6, 6):
        chunk_h = max(highs[i:i + 6])
        chunk_l = min(lows[i:i + 6])
        day_ranges.append((chunk_h - chunk_l) / chunk_l * 100)
    avg_day = sum(day_ranges) / len(day_ranges) if day_ranges else 0
    rating = "\U0001F525 High" if atr_pct > 2.5 else "\U0001F321\uFE0F Moderate" if atr_pct > 1.2 else "\U0001F9CA Low"
    embed = discord.Embed(title=f"Volatility - {symbol}", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**ATR (14, 4H):** {fnum(atr14)} ({atr_pct:.2f}% of price)\n"
        f"**Avg daily range (10d):** {avg_day:.2f}%\n"
        f"**Volatility:** {rating}\n\n"
        f"*Rule of thumb: your SL should live outside the noise - tighter than ATR usually means getting wicked out.*"
    )
    embed.set_footer(text="Sigma Trading - Binance")
    await interaction.followup.send(embed=embed)

def make_cvd_image(symbol: str, tf_label: str, dates: list, closes: list, cvd_spot: list, cvd_perp: list) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1.4], "hspace": 0.06},
                                   facecolor=SG_OBS)
    for ax in (ax1, ax2):
        ax.set_facecolor(SG_OBS)
        ax.grid(color=SG_SLATE, linewidth=0.5)
        for sp in ax.spines.values():
            sp.set_color(SG_SLATE)
        ax.tick_params(colors=SG_ASH, labelsize=8)
        ax.yaxis.tick_right()
    ax1.plot(dates, closes, color=SG_PAPER, linewidth=1.4)
    ax1.set_title(f"{symbol}  {tf_label}  |  Price vs CVD", color=SG_PAPER, fontsize=12, loc="left", pad=10)
    ax2.plot(dates, cvd_spot, color="#E8590C", linewidth=1.6, label="Spot CVD")
    ax2.plot(dates, cvd_perp, color=SG_CYAN, linewidth=1.6, label="Perp CVD")
    ax2.axhline(0, color=SG_ASH, linewidth=0.6, linestyle="--", alpha=0.5)
    leg = ax2.legend(facecolor=SG_OBS, edgecolor=SG_SLATE, labelcolor=SG_PAPER, fontsize=9, loc="upper left")
    def _fmt_usd(x, _):
        ax_abs = abs(x)
        if ax_abs >= 1e9: return f"{x/1e9:.1f}B"
        if ax_abs >= 1e6: return f"{x/1e6:.0f}M"
        if ax_abs >= 1e3: return f"{x/1e3:.0f}K"
        return f"{x:.0f}"
    import matplotlib.ticker as mticker
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_usd))
    try:
        sigma_logo_ax(ax1)
    except Exception:
        pass
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

async def _fetch_cvd_klines(url: str, pair: str, interval: str, limit: int):
    async with http() as s:
        return await _get_json(s, url, {"symbol": pair, "interval": interval, "limit": limit}, 20)

def _cvd_series(klines: list):
    """Cumulative volume delta in USD from taker-buy quote volume (idx 10) vs total quote volume (idx 7)."""
    out = []
    run = 0.0
    for k in klines:
        try:
            qv = float(k[7])
            tq = float(k[10])
        except Exception:
            out.append(run)
            continue
        run += (2 * tq - qv)  # taker buys minus taker sells, in quote (USD) terms
        out.append(run)
    return out

CVD_INTERVALS = {"15m": "15m", "1H": "1h", "4H": "4h"}

@bot.tree.command(name="cvd", description="Spot vs Perp CVD with OI and funding - who is driving the move?")
@app_commands.describe(coin="Coin symbol, e.g. BTC", timeframe="Candle timeframe")
@app_commands.choices(timeframe=[app_commands.Choice(name=k, value=k) for k in CVD_INTERVALS])
async def cvd_cmd(interaction: discord.Interaction, coin: str, timeframe: app_commands.Choice[str] = None):
    await interaction.response.defer()
    tfv = timeframe.value if timeframe else "1H"
    interval = CVD_INTERVALS[tfv]
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    spot = await _fetch_cvd_klines("https://api.binance.com/api/v3/klines", pair, interval, 200)
    perp = await _fetch_cvd_klines("https://fapi.binance.com/fapi/v1/klines", pair, interval, 200)
    if not spot or not isinstance(spot, list) or len(spot) < 30:
        await interaction.followup.send(f"CVD needs Binance taker data and **{symbol}** isn't on Binance spot - try a major pair.")
        return
    if not perp or not isinstance(perp, list):
        perp = []
    n = min(len(spot), len(perp)) if perp else len(spot)
    spot = spot[-n:]
    perp = perp[-n:] if perp else []
    cvd_spot = _cvd_series(spot)
    cvd_perp = _cvd_series(perp) if perp else [0.0] * n
    from datetime import datetime as _dt
    dates = [_dt.fromtimestamp(int(k[0]) / 1000) for k in spot]
    closes = [float(k[4]) for k in spot]
    try:
        buf = await asyncio.to_thread(make_cvd_image, f"{symbol}/USDT", tfv, dates, closes, cvd_spot, cvd_perp)
    except Exception as e:
        await interaction.followup.send(f"CVD rendering failed: {e}")
        return
    # context numbers
    fd = await md_funding(pair)
    od = await md_oi(pair)
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    cb_px = await md_coinbase_price(base)
    premium_pct = None
    if cb_px and closes[-1] > 0:
        premium_pct = (cb_px - closes[-1]) / closes[-1] * 100
    def _musd(x):
        return f"{'+' if x >= 0 else '-'}${abs(x)/1e6:.1f}M" if abs(x) < 1e9 else f"{'+' if x >= 0 else '-'}${abs(x)/1e9:.2f}B"
    d_spot = cvd_spot[-1] - cvd_spot[0]
    d_perp = (cvd_perp[-1] - cvd_perp[0]) if perp else 0.0
    px_chg = (closes[-1] - closes[0]) / closes[0] * 100
    # simple auto-read
    if px_chg > 0.5 and d_spot > 0 and d_spot >= d_perp:
        read = "Spot-led move - real buyers, healthier structure."
    elif px_chg > 0.5 and d_perp > 0 and d_perp > d_spot:
        read = "Perp-led move - leverage driving, watch funding for crowding."
    elif px_chg > 0.5 and d_spot < 0 and d_perp < 0:
        read = "Price up while CVD bleeds - short covering or thin absorption. Fragile."
    elif px_chg < -0.5 and (d_spot > 0 or d_perp > 0):
        read = "Price down into positive delta - buyers absorbing, possible accumulation."
    else:
        read = "No strong divergence between price and flows right now."
    lines = [f"**{symbol} ({n} x {tfv}):** price {px_chg:+.2f}% | Spot CVD {_musd(d_spot)} | Perp CVD {_musd(d_perp)}"]
    ctx = []
    if od and od.get("oi_then"):
        oi_chg = (od["oi"] - od["oi_then"]) / od["oi_then"] * 100
        ctx.append(f"OI 24h {oi_chg:+.2f}%")
    if fd:
        ctx.append(f"funding {fd['rate']:+.4f}%")
    if premium_pct is not None:
        ctx.append(f"Coinbase premium {premium_pct:+.3f}%")
    if ctx:
        lines.append("**Context:** " + " | ".join(ctx))
    if premium_pct is not None:
        if premium_pct >= 0.05:
            read += " Coinbase premium positive - US bid active."
        elif premium_pct <= -0.05:
            read += " Coinbase premium negative - US money absent or selling."
    lines.append(f"**Read:** {read}")
    f = discord.File(buf, filename=f"{symbol}_cvd_{tfv}.png")
    await interaction.followup.send(content="\n".join(lines), file=f)

def make_rvol_image(symbol: str, months: list, rvs: list, current_rv: float) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5.5), facecolor=SG_OBS)
    ax.set_facecolor(SG_OBS)
    ax.grid(color=SG_SLATE, linewidth=0.5, axis="y")
    for sp in ax.spines.values():
        sp.set_color(SG_SLATE)
    ax.tick_params(colors=SG_ASH, labelsize=8)
    colors = ["#E8590C" if i == len(rvs) - 1 else SG_CYAND for i in range(len(rvs))]
    ax.bar(range(len(rvs)), rvs, color=colors, width=0.8)
    step = max(1, len(months) // 12)
    ax.set_xticks(range(0, len(months), step))
    ax.set_xticklabels([months[i] for i in range(0, len(months), step)], rotation=45, ha="right")
    ax.set_title(f"{symbol}  |  30d Realized Volatility by Month (annualized)",
                 color=SG_PAPER, fontsize=12, loc="left", pad=10)
    ax.yaxis.set_major_formatter(lambda x, _: f"{x:.0f}%")
    plt.tight_layout()
    try:
        sigma_logo_ax(ax)
    except Exception:
        pass
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

_ALTSEASON_CACHE = {"ts": 0, "data": None}

ALTSEASON_STABLES = {"USDT","USDC","DAI","FDUSD","TUSD","USDE","PYUSD","USDS","WBTC","WETH","STETH","WSTETH","WEETH","CBBTC","LEO","OKB"}

def make_altseason_image(months: list, vals: list, current: float,
                         read_line: str = "", trigger_line: str = "") -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (axm, axg) = plt.subplots(2, 1, figsize=(12, 6.5), facecolor=SG_OBS,
                                   gridspec_kw={"height_ratios": [2, 1], "hspace": 0.4})
    sigma_style_ax(axm)
    colors = [SG_LONG if v >= 75 else (SG_SHORT if v <= 25 else SG_CYAN) for v in vals]
    axm.bar(range(len(vals)), vals, color=colors, width=0.62)
    axm.axhline(75, color=SG_LONG, lw=1, ls="--", alpha=0.7)
    axm.axhline(25, color=SG_SHORT, lw=1, ls="--", alpha=0.7)
    axm.text(len(vals) - 0.4, 77, "ALTSEASON", color=SG_LONG, fontsize=8, ha="right")
    axm.text(len(vals) - 0.4, 19, "BTC SEASON", color=SG_SHORT, fontsize=8, ha="right")
    axm.set_xticks(range(len(months)))
    axm.set_xticklabels(months, fontsize=8)
    axm.set_ylim(0, 100)
    axm.set_title("ALTSEASON INDEX  ·  % of top 50 outperforming BTC (90d)",
                  color=SG_PAPER, fontsize=14, loc="left", pad=12, fontweight="bold")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    axm.text(0.868, 1.04, stamp, transform=axm.transAxes, color=SG_ASH, fontsize=9.5,
             ha="right", family="monospace")
    sigma_logo_ax(axm)
    axg.set_facecolor(SG_OBS); axg.axis("off"); axg.set_xlim(0, 100); axg.set_ylim(0, 1)
    axg.barh(0.5, 100, height=0.34, color=SG_CARD, edgecolor=SG_SLATE)
    axg.barh(0.5, 25, height=0.34, color=SG_SHORT, alpha=0.25)
    axg.barh(0.5, 50, left=25, height=0.34, color=SG_ASH, alpha=0.12)
    axg.barh(0.5, 25, left=75, height=0.34, color=SG_LONG, alpha=0.25)
    axg.plot([current], [0.5], marker="v", color=SG_PAPER, markersize=13)
    axg.text(current, 0.94, f"NOW  {current:.0f}", color=SG_PAPER, fontsize=12, ha="center",
             fontweight="bold", family="monospace")
    axg.text(1.5, 0.06, "BTC dominance rules", color=SG_ASH, fontsize=8.5)
    axg.text(98.5, 0.06, "Full rotation", color=SG_ASH, fontsize=8.5, ha="right")
    axg.text(25, 1.28, "<25 BTC season", color=SG_SHORT, fontsize=8, ha="center")
    axg.text(75, 1.28, ">75 ALTSEASON confirmed", color=SG_LONG, fontsize=8, ha="center")
    if trigger_line:
        axg.text(50, -0.42, trigger_line, color=SG_CYAN, fontsize=10, ha="center", family="monospace")
    if read_line:
        axg.text(50, -0.82, read_line, color=SG_PAPER, fontsize=10.5, ha="center")
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="altseason", description="Altseason index - % of top 50 beating BTC over 90 days")
async def altseason_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    import time as _time
    now = _time.time()
    if _ALTSEASON_CACHE["data"] and now - _ALTSEASON_CACHE["ts"] < 6 * 3600:
        months, vals, current, n_out, n_tot = _ALTSEASON_CACHE["data"]
    else:
        # top coins by mcap from CoinGecko (free), then history from Binance
        try:
            async with http() as s:
                cg = await _get_json(s, "https://api.coingecko.com/api/v3/coins/markets",
                                     {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 80, "page": 1}, 20)
        except Exception:
            cg = None
        if not cg or not isinstance(cg, list):
            await interaction.followup.send("Couldn't fetch the top-50 list right now (CoinGecko rate limit) - try again in a minute.")
            return
        syms = []
        for c in cg:
            sym = str(c.get("symbol", "")).upper()
            if sym in ("BTC",) or sym in ALTSEASON_STABLES:
                continue
            syms.append(sym)
            if len(syms) >= 50:
                break
        async def _kl(sym):
            async with http() as s2:
                return sym, await _get_json(s2, "https://api.binance.com/api/v3/klines",
                                            {"symbol": f"{sym}USDT", "interval": "1d", "limit": 400}, 20)
        results = await asyncio.gather(*[_kl(x) for x in syms], return_exceptions=True)
        async with http() as s3:
            btc = await _get_json(s3, "https://api.binance.com/api/v3/klines",
                                  {"symbol": "BTCUSDT", "interval": "1d", "limit": 400}, 20)
        if not btc or not isinstance(btc, list):
            await interaction.followup.send("BTC history unavailable right now.")
            return
        btc_close = {int(k[0]): float(k[4]) for k in btc}
        btc_keys = sorted(btc_close.keys())
        coins = {}
        for r in results:
            if isinstance(r, Exception) or not r or not isinstance(r[1], list) or len(r[1]) < 120:
                continue
            coins[r[0]] = {int(k[0]): float(k[4]) for k in r[1]}
        if len(coins) < 20:
            await interaction.followup.send("Not enough listed history to compute the index right now.")
            return
        # monthly index over last ~12 month-ends + current
        def index_at(ts_idx):
            ts = btc_keys[ts_idx]
            ts_90 = btc_keys[max(0, ts_idx - 90)]
            if ts_90 not in btc_close:
                return None
            btc_r = btc_close[ts] / btc_close[ts_90] - 1
            out = tot = 0
            for cmap in coins.values():
                if ts in cmap and ts_90 in cmap:
                    tot += 1
                    if cmap[ts] / cmap[ts_90] - 1 > btc_r:
                        out += 1
            if tot < 15:
                return None
            return out / tot * 100, out, tot
        months, vals = [], []
        step = 30
        for idx in range(len(btc_keys) - 1 - step * 11, len(btc_keys) - 1, step):
            if idx < 90:
                continue
            r = index_at(idx)
            if r:
                months.append(datetime.fromtimestamp(btc_keys[idx] / 1000, tz=timezone.utc).strftime("%d %b"))
                vals.append(r[0])
        cur = index_at(len(btc_keys) - 1)
        if not cur:
            await interaction.followup.send("Index computation failed - try again shortly.")
            return
        current, n_out, n_tot = cur
        months.append("Now"); vals.append(current)
        _ALTSEASON_CACHE["data"] = (months, vals, current, n_out, n_tot)
        _ALTSEASON_CACHE["ts"] = now
    need = max(0, 75 - current)
    coins_needed = max(0, int((75 * n_tot / 100) + 0.999) - n_out)
    if current < 25:
        read = "BTC season - alts bleed vs BTC. Rotation here is fighting the tape."
        trigger = f"Altseason trigger: 75  ·  {need:.0f} points away ({coins_needed} more coins must flip vs BTC)"
    elif current < 50:
        read = "BTC-led market. Only the strongest alts keep up - be selective, majors only."
        trigger = f"Altseason trigger: 75  ·  {need:.0f} points away ({coins_needed} more coins must flip vs BTC)"
    elif current < 75:
        read = "Broadening - rotation building. Majors lead first, mid-caps follow."
        trigger = f"Altseason trigger: 75  ·  only {need:.0f} points away ({coins_needed} coins) - watch BTC.D for the break"
    else:
        read = "ALTSEASON CONFIRMED - historically lasts weeks, not months. Sell INTO strength."
        trigger = "Trigger crossed  ·  falls back below 75 = rotation ending"
    try:
        buf = await asyncio.to_thread(make_altseason_image, months, vals, current, read, trigger)
    except Exception as e:
        await interaction.followup.send(f"Chart render failed: {e}")
        return
    f = discord.File(buf, filename="altseason.png")
    await interaction.followup.send(
        content=(f"**Altseason Index: {current:.0f}** - {n_out}/{n_tot} of the top 50 beat BTC over 90d.\n"
                 f"**Read:** {read}\n**Trigger:** {trigger}\n"
                 f"*History: 2021 altseason ran ~10 weeks above 75. Confirmation beats anticipation - "
                 f"entering at 75 still caught most of it.*"),
        file=f)

@bot.tree.command(name="unlocks", description="Token unlocks in the next 14 days - the supply calendar")
async def unlocks_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as s:
            d = await _get_json(s, "https://api.llama.fi/emissions", None, 25)
    except Exception:
        d = None
    if not d or not isinstance(d, list):
        await interaction.followup.send("Unlock data unavailable right now (DefiLlama).")
        return
    now_ts = datetime.now(timezone.utc).timestamp()
    horizon = now_ts + 14 * 86400
    rows = []
    for proto in d:
        try:
            events = proto.get("upcomingEvent") or []
            if isinstance(events, dict):
                events = [events]
            mcap = proto.get("mcap") or 0
            sym = (proto.get("token") or proto.get("name") or "?").upper()
            for ev in events:
                ts = ev.get("timestamp")
                if not ts or not (now_ts <= float(ts) <= horizon):
                    continue
                usd = 0
                try:
                    tokens = sum(float(x) for x in (ev.get("noOfTokens") or []) if x)
                    price = float(proto.get("price") or 0)
                    usd = tokens * price
                except Exception:
                    pass
                if usd < 1_000_000:
                    continue
                pct = (usd / mcap * 100) if mcap else None
                rows.append((float(ts), sym, usd, pct))
        except Exception:
            continue
    if not rows:
        await interaction.followup.send("No significant unlocks (> $1M) found in the next 14 days - or the data source changed shape. Calendar looks clear.")
        return
    rows.sort(key=lambda r: r[0])
    lines = []
    for ts, sym, usd, pct in rows[:15]:
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%a %d %b")
        if pct is None:
            sev, note = "\U0001F7E1", ""
        elif pct < 1:
            sev, note = "\U0001F7E2", "minor - usually absorbed"
        elif pct < 3:
            sev, note = "\U0001F7E1", "meaningful - weakness tends to front-run these"
        else:
            sev, note = "\U0001F534", "heavy supply - longs into this fight the calendar"
        ptxt = f" ({pct:.1f}% of mcap)" if pct is not None else ""
        lines.append(f"{sev} **{sym}** - ${usd/1e6:.0f}M{ptxt} - {day}" + (f"\n    *{note}*" if note else ""))
    embed = discord.Embed(title="\U0001F513 Token Unlocks - next 14 days", color=NAVY,
                          timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Sigma Trading - DefiLlama data · unlock selling front-runs the date")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="fees", description="BTC + ETH network fees - the retail thermometer")
async def fees_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    btc_fee = eth_gwei = None
    try:
        async with http() as s:
            mf = await _get_json(s, "https://mempool.space/api/v1/fees/recommended", None, 15)
            if mf:
                btc_fee = float(mf.get("fastestFee") or 0)
            async with s.post("https://cloudflare-eth.com",
                              json={"jsonrpc": "2.0", "method": "eth_gasPrice", "params": [], "id": 1},
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    j = await r.json()
                    eth_gwei = int(j["result"], 16) / 1e9
    except Exception:
        pass
    if btc_fee is None and eth_gwei is None:
        await interaction.followup.send("Fee data unavailable right now.")
        return
    def btc_read(f):
        if f < 20: return "calm"
        if f < 60: return "active"
        if f < 100: return "busy"
        return "CONGESTED"
    def eth_read(g):
        if g < 15: return "calm"
        if g < 35: return "active"
        if g < 60: return "busy"
        return "CONGESTED"
    lines = []
    if btc_fee is not None:
        lines.append(f"**BTC:** {btc_fee:.0f} sat/vB ({btc_read(btc_fee)})")
    if eth_gwei is not None:
        lines.append(f"**ETH:** {eth_gwei:.1f} gwei ({eth_read(eth_gwei)})")
    hot = (btc_fee or 0) >= 100 or (eth_gwei or 0) >= 60
    quiet = (btc_fee or 999) < 20 and (eth_gwei or 999) < 15
    if hot:
        read = "\U0001F525 Chains congested - retail FOMO is live. Historically distribution weather, not accumulation."
    elif quiet:
        read = "Chains quiet - retail absent. Tops don't form in silence."
    else:
        read = "Activity building - normal bull rotation, no euphoria signal from fees yet."
    embed = discord.Embed(title="\u26FD Network Fees - Retail Thermometer", color=NAVY,
                          timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines) + f"\n**Read:** {read}"
    embed.set_footer(text="Sigma Trading - fee spikes mark euphoria, not entries")
    await interaction.followup.send(embed=embed)

async def _btc_daily_full():
    """BTC daily closes since 2017 listing - paginated (Binance 1000/call)."""
    out = []
    start = 1502928000000  # 2017-08-17
    async with http() as s:
        for _ in range(6):
            d = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                {"symbol": "BTCUSDT", "interval": "1d", "startTime": start, "limit": 1000}, 20)
            if not d or not isinstance(d, list):
                break
            out += d
            if len(d) < 1000:
                break
            start = int(d[-1][0]) + 86400000
    return out

ETF_JSON_URLS = [
    "https://www.tftc.io/bitcoin-etf-flows/data.json",
    "https://tftc.io/bitcoin-etf-flows/data.json",
    "https://www.tftc.io/api/bitcoin-etf-flows.json",
]

@bot.tree.command(name="etf", description="US spot Bitcoin ETF flows - the institutional bid, daily")
async def etf_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    data = None
    headers = {"User-Agent": "Mozilla/5.0 (SigmaTerminal; +discord bot; data credit: TFTC CC BY 4.0)"}
    async with aiohttp.ClientSession(headers=headers) as s:
        for url in ETF_JSON_URLS:
            try:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        if data:
                            break
            except Exception:
                continue
    def _num(x):
        if x is None:
            return None
        try:
            s2 = str(x).replace(",", "").replace("$", "").strip()
            if s2.startswith("(") and s2.endswith(")"):
                s2 = "-" + s2[1:-1]
            return float(s2)
        except Exception:
            return None
    rows = []
    if data:
        try:
            if isinstance(data, dict):
                arr = None
                for key in ("data", "flows", "daily", "days", "series", "rows", "history"):
                    if isinstance(data.get(key), list):
                        arr = data[key]
                        break
                if arr is None:
                    arr = next((v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)), [])
            else:
                arr = data
            for it in arr:
                if not isinstance(it, dict):
                    continue
                low = {str(k).lower(): v for k, v in it.items()}
                dt = low.get("date") or low.get("day") or low.get("d") or low.get("timestamp")
                v = None
                for key in ("total", "net", "flow", "net_flow", "total_flow", "totalusd", "value"):
                    if low.get(key) is not None:
                        v = _num(low[key])
                        break
                if v is None:
                    # sum per-fund numeric fields as a last resort
                    fund_vals = [_num(x) for k2, x in low.items()
                                 if k2 not in ("date", "day", "d", "timestamp") and _num(x) is not None]
                    v = sum(fund_vals) if fund_vals else None
                if dt is None or v is None:
                    continue
                dts = str(dt)[:10]
                rows.append((dts, v))
        except Exception as e:
            print(f"[etf] parse error: {e}", flush=True)
            rows = []
    if not rows:
        await interaction.followup.send(
            "ETF flow feed unreachable right now - the open dataset URL may have moved. "
            "Ping Scient with the current TFTC JSON link and it'll be rewired in minutes."
        )
        return
    rows = rows[-60:]
    last_date, last_v = rows[-1]
    streak = 0
    for _, v in reversed(rows):
        if (v > 0) == (last_v > 0) and v != 0:
            streak += 1
        else:
            break
    avg30 = sum(v for _, v in rows[-30:]) / min(30, len(rows))
    cum = sum(v for _, v in rows)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5.8), facecolor=SG_OBS)
    sigma_style_ax(ax)
    ax.bar(range(len(rows)), [v for _, v in rows],
           color=[SG_LONG if v >= 0 else SG_SHORT for _, v in rows], width=0.7)
    ax.axhline(0, color=SG_ASH, lw=0.7)
    step = max(1, len(rows) // 8)
    ax.set_xticks(range(0, len(rows), step))
    ax.set_xticklabels([rows[i][0][5:] for i in range(0, len(rows), step)], fontsize=8)
    ax.yaxis.set_major_formatter(lambda x, _: f"${x:,.0f}M")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.set_title("US SPOT BITCOIN ETF - DAILY NET FLOWS", color=SG_PAPER, fontsize=14,
                 loc="left", pad=12, fontweight="bold")
    ax.text(0.865, 1.03, stamp, transform=ax.transAxes, color=SG_ASH, fontsize=10, ha="right", family="monospace")
    sigma_logo_ax(ax)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    direction = "inflow" if last_v >= 0 else "OUTFLOW"
    if last_v >= 0 and streak >= 3:
        read = f"{streak}-day inflow streak - institutions bidding. Flows lead price in the ETF era."
    elif last_v < 0 and streak >= 3:
        read = f"{streak}-day outflow streak - institutional distribution. Respect it until it flips."
    else:
        read = "Mixed flows - no institutional conviction either way this week."
    f = discord.File(buf, filename="btc_etf_flows.png")
    await interaction.followup.send(
        content=(f"**{last_date}: {last_v:+,.0f}M ({direction})** | 30d avg {avg30:+,.0f}M | 60d cum {cum:+,.0f}M\n"
                 f"**Read:** {read}\n*Data: TFTC open dataset (CC BY 4.0)*"),
        file=f)

@bot.tree.command(name="options", description="Deribit options: DVOL, put/call, max pain")
async def options_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    dvol = pc_ratio = max_pain = nearest_exp = None
    dvol_pct = 50
    try:
        async with http() as s:
            end = int(datetime.now(timezone.utc).timestamp() * 1000)
            vd = await _get_json(s, "https://www.deribit.com/api/v2/public/get_volatility_index_data",
                                 {"currency": "BTC", "start_timestamp": end - 400 * 86400_000,
                                  "end_timestamp": end, "resolution": "86400"}, 20)
            series = ((vd or {}).get("result") or {}).get("data") or []
            if series:
                dvol_series = [row[4] if len(row) > 4 else row[1] for row in series]
                dvol = dvol_series[-1]
                below = sum(1 for x in dvol_series if x <= dvol)
                dvol_pct = below / len(dvol_series) * 100
            bs = await _get_json(s, "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                                 {"currency": "BTC", "kind": "option"}, 25)
            insts = (bs or {}).get("result") or []
            call_oi = put_oi = 0.0
            by_exp = {}
            for it in insts:
                name = it.get("instrument_name", "")
                oi = float(it.get("open_interest") or 0)
                parts = name.split("-")
                if len(parts) != 4:
                    continue
                _, exp, strike, typ = parts
                if typ == "C":
                    call_oi += oi
                else:
                    put_oi += oi
                by_exp.setdefault(exp, []).append((float(strike), oi, typ))
            if call_oi + put_oi > 0:
                pc_ratio = put_oi / call_oi if call_oi else None
            if by_exp:
                def _expts(e):
                    try:
                        return datetime.strptime(e, "%d%b%y").timestamp()
                    except Exception:
                        return 9e18
                nearest_exp = min(by_exp.keys(), key=_expts)
                rows2 = by_exp[nearest_exp]
                strikes = sorted({r[0] for r in rows2})
                best, best_pay = None, None
                for sK in strikes:
                    pay = 0.0
                    for k2, oi, typ in rows2:
                        if typ == "C" and sK > k2:
                            pay += (sK - k2) * oi
                        elif typ == "P" and sK < k2:
                            pay += (k2 - sK) * oi
                    if best_pay is None or pay < best_pay:
                        best, best_pay = sK, pay
                max_pain = best
    except Exception as e:
        print(f"[options] {e}", flush=True)
    if dvol is None and pc_ratio is None:
        await interaction.followup.send("Deribit data unavailable right now.")
        return
    lines = []
    if dvol is not None:
        vol_read = "options CHEAP - big moves underpriced" if dvol_pct <= 25 else (
            "options EXPENSIVE - market paying up for protection/upside" if dvol_pct >= 75 else "mid-range")
        lines.append(f"**DVOL:** {dvol:.1f} ({dvol_pct:.0f}th pctile, 1yr) - {vol_read}")
    if pc_ratio is not None:
        pc_read = "calls crowded - upside consensus" if pc_ratio < 0.5 else (
            "puts heavy - fear premium in" if pc_ratio > 0.9 else "balanced")
        lines.append(f"**Put/Call OI:** {pc_ratio:.2f} - {pc_read}")
    if max_pain is not None:
        lines.append(f"**Max Pain ({nearest_exp}):** ${max_pain:,.0f} - price often gravitates here into expiry")
    embed = discord.Embed(title="\U0001F3B0 BTC Options - Deribit", color=NAVY,
                          timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Sigma Trading - Deribit public data")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="hash", description="Hash Ribbons - miner capitulation and the legendary buy signal")
async def hash_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as s:
            hr = await _get_json(s, "https://api.blockchain.info/charts/hash-rate",
                                 {"timespan": "18months", "format": "json"}, 25)
    except Exception:
        hr = None
    vals = [(v["x"], float(v["y"])) for v in (hr or {}).get("values", []) if v.get("y")]
    if len(vals) < 90:
        await interaction.followup.send("Hashrate data unavailable right now.")
        return
    hs = [v for _, v in vals]
    sma30 = [sum(hs[max(0, i - 29):i + 1]) / len(hs[max(0, i - 29):i + 1]) for i in range(len(hs))]
    sma60 = [sum(hs[max(0, i - 59):i + 1]) / len(hs[max(0, i - 59):i + 1]) for i in range(len(hs))]
    cap_now = sma30[-1] < sma60[-1]
    crossed_up = sma30[-1] >= sma60[-1] and sma30[-8] < sma60[-8]
    # BTC closes aligned to the hashrate timestamps (blockchain.info x = unix seconds)
    prices = None
    try:
        async with http() as s2:
            kl = await _get_json(s2, "https://api.binance.com/api/v3/klines",
                                 {"symbol": "BTCUSDT", "interval": "1d", "limit": 1000}, 20)
        if kl and isinstance(kl, list):
            close_by_day = {int(k[0] // 86400000): float(k[4]) for k in kl}
            prices = []
            last_c = None
            for ts, _v in vals:
                c = close_by_day.get(int(ts // 86400))
                if c is not None:
                    last_c = c
                prices.append(last_c)
            if prices and prices[0] is None:
                first = next((p for p in prices if p is not None), None)
                prices = [p if p is not None else first for p in prices]
            if any(p is None for p in prices):
                prices = None
    except Exception:
        prices = None
    # state duration + last recovery cross
    days_in_state = 1
    for i in range(len(hs) - 2, -1, -1):
        if (sma30[i] < sma60[i]) == cap_now:
            days_in_state += 1
        else:
            break
    last_cross_idx = None
    for i in range(len(hs) - 1, 0, -1):
        if sma30[i] >= sma60[i] and sma30[i - 1] < sma60[i - 1]:
            last_cross_idx = i
            break
    cross_note = ""
    if last_cross_idx is not None:
        days_ago = len(hs) - 1 - last_cross_idx
        cross_dt = datetime.fromtimestamp(vals[last_cross_idx][0], tz=timezone.utc).strftime("%d %b %y")
        if prices and prices[last_cross_idx]:
            chg = (prices[-1] / prices[last_cross_idx] - 1) * 100
            cross_note = f"Last recovery cross: {cross_dt} ({days_ago}d ago) - BTC {chg:+.0f}% since."
        else:
            cross_note = f"Last recovery cross: {cross_dt} ({days_ago}d ago)."
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5.8), facecolor=SG_OBS)
    sigma_style_ax(ax)
    x = list(range(len(hs)))
    ax.plot(x, hs, color=SG_ASH, lw=0.7, alpha=0.45, label="Hashrate")
    ax.plot(x, sma30, color=SG_CYAN, lw=1.6, label="30D SMA")
    ax.plot(x, sma60, color=SG_AMBER, lw=1.6, label="60D SMA")
    for i in x:
        if sma30[i] < sma60[i]:
            ax.axvspan(i - 0.5, i + 0.5, color=SG_SHORT, alpha=0.05)
    if prices and len(prices) == len(hs):
        ax2 = ax.twinx()
        ax2.plot(x, prices, color=SG_PAPER, lw=1.2)
        ax2.set_yscale("log")
        ax2.set_yticks([])
        for sp in ax2.spines.values():
            sp.set_color(SG_SLATE)
        ax2.text(x[-1], prices[-1], f"  ${prices[-1]:,.0f}", color=SG_PAPER, fontsize=9,
                 va="center", family="monospace")
        ax.plot([], [], color=SG_PAPER, lw=1.2, label="BTC price")
    ax.legend(facecolor=SG_GRA, edgecolor=SG_SLATE, labelcolor=SG_PAPER, fontsize=9, loc="upper left")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.set_title("HASH RIBBONS  ·  miner capitulation tracker", color=SG_PAPER, fontsize=14,
                 loc="left", pad=12, fontweight="bold")
    ax.text(0.865, 1.03, stamp, transform=ax.transAxes, color=SG_ASH, fontsize=10, ha="right", family="monospace")
    ax.set_yticks([])
    sigma_logo_ax(ax)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    if crossed_up:
        read = "\U0001F7E2 RIBBON RECOVERY - the historical buy signal just fired. One of BTC's best-performing signals ever."
    elif cap_now:
        read = (f"\U0001F534 Miner capitulation in progress, {days_in_state}d and counting (30D below 60D). "
                f"The signal is NOT the capitulation - it's the recovery cross that ends it. Watch for 30D reclaiming 60D.")
    else:
        read = (f"Ribbons healthy {days_in_state}d running - miners expanding, network secure. "
                f"No capitulation, no signal. Miner stress usually appears AFTER major price damage, not before.")
    body = f"**Hash Ribbons:** {'capitulation' if cap_now else 'healthy'} ({days_in_state}d).\n**Read:** {read}"
    if cross_note:
        body += f"\n{cross_note}"
    f = discord.File(buf, filename="hash_ribbons.png")
    await interaction.followup.send(content=body, file=f)

def _pearson(a: list, b: list):
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a) ** 0.5
    vb = sum((x - mb) ** 2 for x in b) ** 0.5
    if va == 0 or vb == 0:
        return None
    return cov / (va * vb)

@bot.tree.command(name="premium", description="Coinbase premium history + Kimchi premium - US vs Korean bid")
async def premium_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    headers = {"User-Agent": "Mozilla/5.0 (SigmaTerminal)"}
    bn_by_day, cb_by_day = {}, {}
    kimchi = None
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            bn = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                 {"symbol": "BTCUSDT", "interval": "1d", "limit": 95}, 20)
            for k in bn or []:
                bn_by_day[int(k[0] // 86400000)] = float(k[4])
            async with s.get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                             params={"granularity": 86400},
                             timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status == 200:
                    for c in await r.json(content_type=None):
                        cb_by_day[int(c[0] // 86400)] = float(c[4])
            up = await _get_json(s, "https://api.upbit.com/v1/candles/days",
                                 {"market": "KRW-BTC", "count": 1}, 15)
            fx = await _get_json(s, "https://open.er-api.com/v6/latest/USD", None, 15)
            krw = float((fx or {}).get("rates", {}).get("KRW") or 0)
            if up and isinstance(up, list) and krw > 0 and bn_by_day:
                bn_now = bn_by_day[max(bn_by_day)]
                kimchi = (float(up[0]["trade_price"]) / krw - bn_now) / bn_now * 100
    except Exception as e:
        print(f"[premium] {e}", flush=True)
    days = sorted(set(bn_by_day) & set(cb_by_day))[-90:]
    if len(days) < 20:
        await interaction.followup.send("Premium data unavailable right now (Coinbase/Binance).")
        return
    prem = [(cb_by_day[d] - bn_by_day[d]) / bn_by_day[d] * 100 for d in days]
    ma7 = [sum(prem[max(0, i - 6):i + 1]) / len(prem[max(0, i - 6):i + 1]) for i in range(len(prem))]
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5.6), facecolor=SG_OBS)
    sigma_style_ax(ax)
    x = list(range(len(prem)))
    ax.fill_between(x, prem, 0, where=[p >= 0 for p in prem], color=SG_LONG, alpha=0.25, interpolate=True)
    ax.fill_between(x, prem, 0, where=[p < 0 for p in prem], color=SG_SHORT, alpha=0.25, interpolate=True)
    ax.plot(x, prem, color=SG_PAPER, lw=1.0, alpha=0.7)
    ax.plot(x, ma7, color=SG_CYAN, lw=1.7, label="7D MA")
    ax.axhline(0, color=SG_ASH, lw=0.8)
    ax.legend(facecolor=SG_GRA, edgecolor=SG_SLATE, labelcolor=SG_PAPER, fontsize=9, loc="upper left")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:+.2f}%")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.set_title("COINBASE PREMIUM  ·  90 days", color=SG_PAPER, fontsize=14, loc="left", pad=12, fontweight="bold")
    ax.text(0.865, 1.03, stamp, transform=ax.transAxes, color=SG_ASH, fontsize=10, ha="right", family="monospace")
    read_img = "US bid present" if ma7[-1] > 0.01 else ("US selling into overseas bid" if ma7[-1] < -0.01 else "neutral")
    ax.text(0.5, -0.14, f"7D avg {ma7[-1]:+.3f}% - {read_img}", transform=ax.transAxes,
            color=SG_PAPER, fontsize=10.5, ha="center")
    sigma_logo_ax(ax)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    cur = prem[-1]
    if ma7[-1] > 0.02:
        read = "Sustained positive premium - US institutions paying up. This has accompanied every ETF-era leg up."
    elif ma7[-1] < -0.02:
        read = "Sustained discount - US selling into the overseas bid. Rallies without US support tend to fade."
    else:
        read = "Flat premium - no regional conviction either way."
    lines = [f"**Coinbase premium:** {cur:+.3f}% today, 7D avg {ma7[-1]:+.3f}%."]
    if kimchi is not None:
        if kimchi > 5:
            k_read = "Korean retail euphoric - historically a late-cycle marker (2021 top printed double digits)."
        elif kimchi > 2:
            k_read = "Korean retail bidding - risk appetite building."
        elif kimchi < 0:
            k_read = "Korean discount - retail apathy, historically closer to bottoms than tops."
        else:
            k_read = "neutral."
        lines.append(f"**Kimchi premium:** {kimchi:+.2f}% - {k_read}")
    lines.append(f"**Read:** {read}")
    f = discord.File(buf, filename="btc_premium.png")
    await interaction.followup.send(content="\n".join(lines), file=f)

@bot.tree.command(name="basis", description="Quarterly futures basis - the leverage/carry regime")
async def basis_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = []
    try:
        async with http() as s:
            info = await _get_json(s, "https://fapi.binance.com/fapi/v1/exchangeInfo", None, 25)
            now_ms = datetime.now(timezone.utc).timestamp() * 1000
            targets = []
            for sym in (info or {}).get("symbols", []):
                if sym.get("contractType") in ("CURRENT_QUARTER", "NEXT_QUARTER") and \
                   sym.get("baseAsset") in ("BTC", "ETH") and sym.get("quoteAsset") == "USDT" and \
                   sym.get("status") == "TRADING":
                    targets.append((sym["symbol"], sym["baseAsset"], sym.get("contractType"),
                                    float(sym.get("deliveryDate") or 0)))
            spot = {}
            for base in ("BTC", "ETH"):
                sp = await _get_json(s, "https://api.binance.com/api/v3/ticker/price",
                                     {"symbol": f"{base}USDT"}, 15)
                spot[base] = float((sp or {}).get("price") or 0)
            for symbol, base, ctype, ddate in targets:
                if not spot.get(base) or ddate <= now_ms:
                    continue
                px = await _get_json(s, "https://fapi.binance.com/fapi/v1/ticker/price",
                                     {"symbol": symbol}, 15)
                fut = float((px or {}).get("price") or 0)
                if fut <= 0:
                    continue
                days = (ddate - now_ms) / 86400000
                ann = (fut / spot[base] - 1) * 365 / max(days, 1) * 100
                rows.append((base, ctype.replace("_", " ").title(), symbol, days, ann))
    except Exception as e:
        print(f"[basis] {e}", flush=True)
    if not rows:
        await interaction.followup.send("Quarterly futures data unavailable right now.")
        return
    rows.sort(key=lambda r: (r[0], r[3]))
    lines = []
    worst = max(r[4] for r in rows)
    for base, ctype, symbol, days, ann in rows:
        lines.append(f"**{base}** {ctype} ({days:.0f}d): **{ann:+.1f}%** annualized")
    if worst > 15:
        read = "Basis in euphoria territory (>15% ann) - leverage paying heavily for exposure. 2021-top vibes; this is where longs get expensive and squeezes get violent."
    elif worst > 8:
        read = "Healthy bull carry (8-15%) - demand for leverage present but not desperate."
    elif worst > 2:
        read = "Muted basis - spot-driven market, little speculative froth. Room to run."
    else:
        read = "Flat/negative basis - capitulation conditions. Historically closer to bottoms than tops."
    embed = discord.Embed(title="\U0001F4C8 Futures Basis - Carry Regime", color=NAVY,
                          timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines) + f"\n\n**Read:** {read}"
    embed.set_footer(text="Sigma Trading - Binance quarterly futures vs spot")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="sessions", description="Asia vs Europe vs US - who is buying, who is selling (30d)")
async def sessions_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as s:
            kl = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                 {"symbol": "BTCUSDT", "interval": "1h", "limit": 744}, 25)
    except Exception:
        kl = None
    if not kl or not isinstance(kl, list) or len(kl) < 200:
        await interaction.followup.send("Hourly data unavailable right now.")
        return
    SESSIONS = [("Asia", 0, 8), ("Europe", 8, 14), ("US", 14, 22)]
    day_sess = {}
    for k in kl:
        dt = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
        for name, h1, h2 in SESSIONS:
            if h1 <= dt.hour < h2:
                key = (dt.date(), name)
                o, c = float(k[1]), float(k[4])
                if key not in day_sess:
                    day_sess[key] = [o, c]
                else:
                    day_sess[key][1] = c
                break
    stats = {}
    for (day, name), (o, c) in day_sess.items():
        if o > 0:
            stats.setdefault(name, []).append((c / o - 1) * 100)
    if not stats:
        await interaction.followup.send("Couldn't compute session returns.")
        return
    names = [n for n, _, _ in SESSIONS]
    cums, avgs, wins = {}, {}, {}
    for n in names:
        rets = stats.get(n, [])
        cum = 1.0
        for r in rets:
            cum *= (1 + r / 100)
        cums[n] = (cum - 1) * 100
        avgs[n] = sum(rets) / len(rets) if rets else 0
        wins[n] = sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else 0
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 5.4), facecolor=SG_OBS)
    sigma_style_ax(ax)
    vals = [cums[n] for n in names]
    ax.bar(names, vals, color=[SG_LONG if v >= 0 else SG_SHORT for v in vals], width=0.5)
    ax.axhline(0, color=SG_ASH, lw=0.8)
    for i, n in enumerate(names):
        ax.text(i, vals[i] + (0.15 if vals[i] >= 0 else -0.3), f"{vals[i]:+.1f}%",
                color=SG_PAPER, fontsize=11, ha="center", fontweight="bold", family="monospace")
        ax.text(i, min(0, min(vals)) - 1.1, f"avg {avgs[n]:+.2f}%/d · {wins[n]:.0f}% green",
                color=SG_ASH, fontsize=8.5, ha="center", family="monospace")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.set_title("SESSION RETURNS  ·  BTC cumulative, last 30 days (UTC)", color=SG_PAPER,
                 fontsize=14, loc="left", pad=12, fontweight="bold")
    ax.text(0.865, 1.03, stamp, transform=ax.transAxes, color=SG_ASH, fontsize=10, ha="right", family="monospace")
    sigma_logo_ax(ax)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    best = max(names, key=lambda n: cums[n])
    worst = min(names, key=lambda n: cums[n])
    if cums["US"] > 0 and cums["Asia"] > 0:
        read = "Both US and Asia net buyers - broad demand, healthiest kind of tape."
    elif cums["US"] < 0 < cums["Asia"]:
        read = "Asia buying what the US sells - absorption pattern. Watch which side exhausts first."
    elif cums["Asia"] < 0 < cums["US"]:
        read = "US bid carrying the tape while Asia distributes - ETF-era signature, sustainable while flows stay positive."
    else:
        read = "All sessions weak - no regional bid. Defensive tape."
    f = discord.File(buf, filename="btc_sessions.png")
    await interaction.followup.send(
        content=(f"**Strongest: {best} ({cums[best]:+.1f}%)** · Weakest: {worst} ({cums[worst]:+.1f}%) over 30d.\n"
                 f"**Read:** {read}\n*Sessions in UTC: Asia 00-08 · Europe 08-14 · US 14-22.*"),
        file=f)

CORR_TRADFI = {"SPX": "^GSPC", "DXY": "DX-Y.NYB", "GOLD": "GC=F"}

@bot.tree.command(name="corr", description="30d correlation matrix - BTC/ETH/SOL vs SPX, DXY, GOLD")
async def corr_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    headers = {"User-Agent": "Mozilla/5.0 (SigmaTerminal)"}
    closes = {}
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            for base in ("BTC", "ETH", "SOL"):
                kl = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                     {"symbol": f"{base}USDT", "interval": "1d", "limit": 95}, 20)
                if kl and isinstance(kl, list):
                    closes[base] = {int(k[0] // 86400000): float(k[4]) for k in kl}
            for name, ysym in CORR_TRADFI.items():
                d = await _get_json(s, f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}",
                                    {"interval": "1d", "range": "3mo"}, 20)
                try:
                    res = d["chart"]["result"][0]
                    ts = res["timestamp"]
                    cl = res["indicators"]["quote"][0]["close"]
                    closes[name] = {int(t // 86400): float(c) for t, c in zip(ts, cl) if c}
                except Exception:
                    continue
    except Exception as e:
        print(f"[corr] {e}", flush=True)
    assets = [a for a in ("BTC", "ETH", "SOL", "SPX", "DXY", "GOLD") if a in closes and len(closes[a]) > 25]
    if len(assets) < 3:
        await interaction.followup.send("Not enough data for the matrix right now.")
        return
    common = None
    for a in assets:
        common = set(closes[a]) if common is None else common & set(closes[a])
    days = sorted(common)[-45:]
    if len(days) < 25:
        await interaction.followup.send("Not enough overlapping trading days right now.")
        return
    rets = {}
    for a in assets:
        series = [closes[a][d] for d in days]
        rets[a] = [(series[i] / series[i - 1] - 1) for i in range(1, len(series))][-30:]
    n = len(assets)
    M = [[(_pearson(rets[assets[i]], rets[assets[j]]) if i != j else 1.0) for j in range(n)] for i in range(n)]
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9.5, 8), facecolor=SG_OBS)
    ax.set_facecolor(SG_OBS)
    for i in range(n):
        for j in range(n):
            v = M[i][j]
            if v is None:
                colr, a2 = SG_CARD, 0.6
                txt = "-"
            else:
                colr = SG_CYAN if v >= 0 else SG_SHORT
                a2 = 0.15 + 0.75 * min(abs(v), 1)
                txt = f"{v:+.2f}"
            ax.add_patch(plt.Rectangle((j, n - 1 - i), 0.95, 0.95, color=colr, alpha=a2))
            ax.text(j + 0.475, n - 1 - i + 0.42, txt, color=SG_PAPER, fontsize=11,
                    ha="center", family="monospace", fontweight="bold")
    for i, a in enumerate(assets):
        ax.text(i + 0.475, n + 0.12, a, color=SG_PAPER, fontsize=11, ha="center", fontweight="bold")
        ax.text(-0.15, n - 1 - i + 0.42, a, color=SG_PAPER, fontsize=11, ha="right", fontweight="bold")
    ax.set_xlim(-1.2, n + 0.2); ax.set_ylim(-0.9, n + 0.7); ax.axis("off")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.text(0, n + 0.55, "CORRELATION MATRIX  ·  30d daily returns", color=SG_PAPER, fontsize=14, fontweight="bold")
    ax.text(n + 0.1, -0.75, stamp, color=SG_ASH, fontsize=9.5, ha="right", family="monospace")
    sigma_logo_ax(ax, pos=(1.0, 1.06), zoom=0.07)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    lines = []
    def _get(a, b):
        if a in assets and b in assets:
            return M[assets.index(a)][assets.index(b)]
        return None
    bs = _get("BTC", "SPX")
    if bs is not None:
        if bs > 0.5:
            lines.append(f"**BTC-SPX {bs:+.2f}** - macro-coupled. BTC trades as a risk asset right now; watch equities and FOMC.")
        elif bs < 0.2:
            lines.append(f"**BTC-SPX {bs:+.2f}** - decoupled. Crypto-native flows driving price, macro noise matters less.")
        else:
            lines.append(f"**BTC-SPX {bs:+.2f}** - loosely coupled.")
    bd = _get("BTC", "DXY")
    if bd is not None:
        lines.append(f"**BTC-DXY {bd:+.2f}** - " + ("normal inverse relationship intact." if bd < -0.2 else "inverse link weak right now - dollar isn't the driver."))
    be = _get("BTC", "ETH")
    if be is not None and be < 0.7:
        lines.append(f"**BTC-ETH {be:+.2f}** - unusually low; rotation/divergence phase inside crypto.")
    f = discord.File(buf, filename="corr_matrix.png")
    await interaction.followup.send(content="\n".join(lines) if lines else "Correlation matrix:", file=f)

RAINBOW_BANDS = [
    (1.35, "#EA3943", "MAX BUBBLE - sell, seriously"),
    (1.00, "#F06A3C", "FOMO intensifies - distribute"),
    (0.65, "#F5A11B", "Is this a bubble? - trim"),
    (0.30, "#F5C211", "HODL - trend mature"),
    (-0.05, "#9BC53D", "Still cheap - hold"),
    (-0.40, "#16C784", "Accumulate"),
    (-0.75, "#22D3C5", "BUY - fire sale"),
]

def make_rainbow_image(dates_n: int, prices: list, fit: list, resid_std: float, cur_z: float, band_label: str) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import math as _m
    fig, ax = plt.subplots(figsize=(12.5, 7), facecolor=SG_OBS)
    sigma_style_ax(ax)
    x = list(range(dates_n))
    prev_k = 1.9
    for k, color, label in RAINBOW_BANDS:
        upper = [f * (10 ** (prev_k * resid_std)) for f in fit]
        lower = [f * (10 ** (k * resid_std)) for f in fit]
        ax.fill_between(x, lower, upper, color=color, alpha=0.32, linewidth=0)
        ax.text(dates_n * 1.005, lower[-1] * 1.15, label.split(" - ")[0], color=color, fontsize=7.5, va="center")
        prev_k = k
    ax.plot(x, prices, color=SG_PAPER, lw=1.2)
    ax.set_yscale("log")
    ax.set_xlim(0, dates_n * 1.16)
    ax.set_title("BTC RAINBOW  ·  log regression bands", color=SG_PAPER, fontsize=15, loc="left", pad=12, fontweight="bold")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.text(0.985, 1.03, f"${prices[-1]:,.0f}  ·  {stamp}", transform=ax.transAxes, color=SG_ASH,
            fontsize=10, ha="right", family="monospace")
    ax.annotate("YOU ARE HERE", xy=(dates_n - 1, prices[-1]), xytext=(dates_n * 0.72, prices[-1] * 2.6),
                color=SG_PAPER, fontsize=10, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=SG_PAPER, lw=1.2))
    ax.text(dates_n * 0.5, min(prices) * 0.75, f"Current band: {band_label}", color=SG_PAPER,
            fontsize=11, ha="center", fontweight="bold")
    sigma_logo_ax(ax, pos=(0.10, 0.97))
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="rainbow", description="The Bitcoin Rainbow - where are we in the cycle bands?")
async def rainbow_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    import math as _m
    kl = await _btc_daily_full()
    if len(kl) < 800:
        await interaction.followup.send("Not enough BTC history right now - try again.")
        return
    closes = [float(k[4]) for k in kl]
    t0 = 1231006505000  # genesis 2009-01-03
    days = [(int(k[0]) - t0) / 86400000 for k in kl]
    xs = [_m.log10(d) for d in days]
    ys = [_m.log10(c) for c in closes]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / sum((xs[i] - mx) ** 2 for i in range(n))
    inter = my - slope * mx
    fit = [10 ** (slope * xs[i] + inter) for i in range(n)]
    resid = [ys[i] - (slope * xs[i] + inter) for i in range(n)]
    rs = (sum(r * r for r in resid) / (n - 1)) ** 0.5
    cur_z = resid[-1] / rs
    band_label = RAINBOW_BANDS[-1][2]
    for k, color, label in RAINBOW_BANDS:
        if cur_z >= k:
            band_label = label
            break
    try:
        buf = await asyncio.to_thread(make_rainbow_image, n, closes, fit, rs, cur_z, band_label.split(" - ")[0])
    except Exception as e:
        await interaction.followup.send(f"Render failed: {e}")
        return
    reads = {
        "MAX BUBBLE - sell, seriously": "Every prior cycle top printed inside this band. History says distribute, not accumulate.",
        "FOMO intensifies - distribute": "Late-cycle heat. Selling into strength here has beaten holding through it every cycle.",
        "Is this a bubble? - trim": "Markup phase - trend strong, but this is where trimming plans get written.",
        "HODL - trend mature": "Mid-cycle. Trend intact, chasing gets punished - let entries come to you.",
        "Still cheap - hold": "Below trend. Historically a hold zone, not a sell zone.",
        "Accumulate": "Discount territory - past cycles rewarded steady bids here.",
        "BUY - fire sale": "Every past visit to this band was a generational bottom window.",
    }
    await interaction.followup.send(
        content=(f"**Rainbow band: {band_label.split(' - ')[0]}** (z {cur_z:+.2f})\n"
                 f"**Read:** {reads.get(band_label, '')}\n"
                 f"*Bands fitted on 2017+ Binance data - positions drift as the regression refits. Context, not a trigger.*"),
        file=discord.File(buf, filename="btc_rainbow.png"))

def make_monthly_image(years: list, grid: dict, avg_row: list) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = len(years)
    fig, ax = plt.subplots(figsize=(12.5, 1.2 + 0.62 * (rows + 1)), facecolor=SG_OBS)
    ax.set_facecolor(SG_OBS); ax.axis("off")
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    ax.set_xlim(0, 13); ax.set_ylim(-1.6, rows + 1)
    for j, mname in enumerate(months):
        ax.text(j + 1.5, rows + 0.55, mname, color=SG_ASH, fontsize=9, ha="center", family="monospace")
    for i, yr in enumerate(years):
        y = rows - 1 - i
        ax.text(0.75, y + 0.28, str(yr), color=SG_PAPER, fontsize=9.5, ha="right", family="monospace", fontweight="bold")
        for j in range(12):
            v = grid.get((yr, j + 1))
            if v is None:
                continue
            mag = min(1.0, abs(v) / 40)
            col = SG_LONG if v >= 0 else SG_SHORT
            ax.add_patch(plt.Rectangle((j + 1.02, y + 0.03), 0.96, 0.62, color=col, alpha=0.18 + 0.62 * mag))
            ax.text(j + 1.5, y + 0.28, f"{v:+.0f}", color=SG_PAPER, fontsize=8.6, ha="center", family="monospace")
    ax.text(0.75, -0.62, "AVG", color=SG_CYAN, fontsize=9.5, ha="right", family="monospace", fontweight="bold")
    for j, a in enumerate(avg_row):
        if a is None:
            continue
        ax.text(j + 1.5, -0.62, f"{a:+.0f}", color=SG_CYAN, fontsize=8.8, ha="center", family="monospace")
    ax.set_title("BTC MONTHLY RETURNS (%)", color=SG_PAPER, fontsize=15, loc="left", pad=14, fontweight="bold")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.text(0.99, 1.02, stamp, transform=ax.transAxes, color=SG_ASH, fontsize=9.5, ha="right", family="monospace")
    sigma_logo_ax(ax, zoom=0.06, pos=(0.995, 1.10))
    buf = io.BytesIO()
    fig.savefig(buf, dpi=130, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="monthly", description="BTC monthly returns heatmap - which months pay?")
async def monthly_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as s:
            kl = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                 {"symbol": "BTCUSDT", "interval": "1M", "limit": 120}, 20)
    except Exception:
        kl = None
    if not kl or not isinstance(kl, list) or len(kl) < 24:
        await interaction.followup.send("Monthly data unavailable right now.")
        return
    grid = {}
    for k in kl:
        d = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
        o, c = float(k[1]), float(k[4])
        if o > 0:
            grid[(d.year, d.month)] = (c - o) / o * 100
    years = sorted({y for (y, _) in grid}, reverse=True)
    avg_row = []
    for m in range(1, 13):
        vals = [grid[(y, m)] for y in years if (y, m) in grid]
        # exclude the current (incomplete) month from the average
        nowd = datetime.now(timezone.utc)
        vals = [grid[(y, m)] for y in years if (y, m) in grid and not (y == nowd.year and m == nowd.month)]
        avg_row.append(sum(vals) / len(vals) if vals else None)
    try:
        buf = await asyncio.to_thread(make_monthly_image, years, grid, avg_row)
    except Exception as e:
        await interaction.followup.send(f"Render failed: {e}")
        return
    nowd = datetime.now(timezone.utc)
    this_avg = avg_row[nowd.month - 1]
    nxt = avg_row[nowd.month % 12]
    mn = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    lines = [f"**{mn[nowd.month-1]} historical avg:** {this_avg:+.1f}%" if this_avg is not None else "",
             f"**{mn[nowd.month%12]} historical avg:** {nxt:+.1f}%" if nxt is not None else "",
             "*Seasonality is a tendency, not a law - size accordingly.*"]
    await interaction.followup.send(content="\n".join(l for l in lines if l),
                                    file=discord.File(buf, filename="btc_monthly.png"))

def make_bmsb_image(prices: list, sma20: list, ema21: list, status: str, scolor: str) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 6), facecolor=SG_OBS)
    sigma_style_ax(ax)
    x = list(range(len(prices)))
    lo = [min(a, b) for a, b in zip(sma20, ema21)]
    hi = [max(a, b) for a, b in zip(sma20, ema21)]
    ax.fill_between(x, lo, hi, color=SG_CYAN, alpha=0.25)
    ax.plot(x, sma20, color=SG_CYAN, lw=1.2, label="20W SMA")
    ax.plot(x, ema21, color=SG_AMBER, lw=1.2, label="21W EMA")
    ax.plot(x, prices, color=SG_PAPER, lw=1.4)
    ax.set_yscale("log")
    ax.legend(facecolor=SG_GRA, edgecolor=SG_SLATE, labelcolor=SG_PAPER, fontsize=9, loc="upper left")
    ax.set_title("BTC  ·  BULL MARKET SUPPORT BAND (weekly)", color=SG_PAPER, fontsize=14, loc="left", pad=12, fontweight="bold")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.text(0.985, 1.03, f"${prices[-1]:,.0f}  ·  {stamp}", transform=ax.transAxes, color=SG_ASH,
            fontsize=10, ha="right", family="monospace")
    ax.text(0.5, -0.13, status, transform=ax.transAxes, color=scolor, fontsize=11.5, ha="center", fontweight="bold")
    sigma_logo_ax(ax)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="bmsb", description="Bull Market Support Band - is the bull structure intact?")
async def bmsb_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as s:
            kl = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                 {"symbol": "BTCUSDT", "interval": "1w", "limit": 200}, 20)
    except Exception:
        kl = None
    if not kl or not isinstance(kl, list) or len(kl) < 40:
        await interaction.followup.send("Weekly data unavailable right now.")
        return
    closes = [float(k[4]) for k in kl]
    sma20, ema21 = [], []
    ema = closes[0]
    alpha = 2 / 22
    for i in range(len(closes)):
        w = closes[max(0, i - 19):i + 1]
        sma20.append(sum(w) / len(w))
        ema = closes[i] * alpha + ema * (1 - alpha)
        ema21.append(ema)
    price = closes[-1]
    lo, hi = min(sma20[-1], ema21[-1]), max(sma20[-1], ema21[-1])
    if price > hi:
        pct = (price / hi - 1) * 100
        status, sc = f"ABOVE the band (+{pct:.0f}%) - bull structure intact", SG_LONG
        read = ("Bull intact. Corrections INTO the band have been buy zones every cycle - "
                f"the band sits {(1 - hi / price) * 100:.0f}% below, that's your line in the sand.")
    elif price >= lo:
        status, sc = "INSIDE the band - the retest is live", SG_AMBER
        read = "This is the decision zone. Weekly close above = bull resumes. Weekly close below = defense mode, cut leverage."
    else:
        pct = (1 - price / lo) * 100
        status, sc = f"BELOW the band (-{pct:.0f}%) - bull structure broken", SG_SHORT
        read = "Every extended stay below the band has been bear territory. Rallies into the band from below get sold - respect it."
    try:
        buf = await asyncio.to_thread(make_bmsb_image, closes, sma20, ema21, status, sc)
    except Exception as e:
        await interaction.followup.send(f"Render failed: {e}")
        return
    await interaction.followup.send(content=f"**BMSB:** {status}\n**Read:** {read}",
                                    file=discord.File(buf, filename="btc_bmsb.png"))

def make_roi_image(cur: list, prev: list, prev_label: str, day_now: int) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 6.2), facecolor=SG_OBS)
    sigma_style_ax(ax)
    ax.plot(range(len(prev)), prev, color=SG_ASH, lw=1.2, label=prev_label, alpha=0.85)
    ax.plot(range(len(cur)), cur, color=SG_CYAN, lw=1.7, label="This cycle (2024 halving)")
    ax.axvline(day_now, color=SG_PAPER, lw=0.8, ls="--", alpha=0.6)
    ax.annotate("WE ARE HERE", xy=(day_now, cur[-1]), xytext=(day_now * 0.62, cur[-1] * 1.9),
                color=SG_PAPER, fontsize=10, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=SG_PAPER, lw=1.1))
    ax.set_yscale("log")
    ax.set_xlabel("Days since halving", color=SG_ASH, fontsize=9)
    ax.set_title("BTC CYCLE ROI  ·  price multiple from halving day", color=SG_PAPER,
                 fontsize=14, loc="left", pad=12, fontweight="bold")
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    ax.text(0.985, 1.03, stamp, transform=ax.transAxes, color=SG_ASH, fontsize=9.5, ha="right", family="monospace")
    ax.legend(facecolor=SG_GRA, edgecolor=SG_SLATE, labelcolor=SG_PAPER, fontsize=9, loc="lower right")
    sigma_logo_ax(ax)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="roi", description="This cycle vs last - price multiple since each halving")
async def roi_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    kl = await _btc_daily_full()
    if len(kl) < 800:
        await interaction.followup.send("Not enough history right now.")
        return
    H_2020 = 1589155200000  # 2020-05-11
    H_2024 = 1713484800000  # 2024-04-19
    closes = {int(k[0]): float(k[4]) for k in kl}
    keys = sorted(closes)
    def cycle_series(h_ts, max_days):
        base = None
        out = []
        for ts in keys:
            d = (ts - h_ts) / 86400000
            if d < 0 or d > max_days:
                continue
            if base is None:
                base = closes[ts]
            out.append(closes[ts] / base)
        return out
    prev = cycle_series(H_2020, 1460)
    cur = cycle_series(H_2024, 1460)
    if len(cur) < 30 or len(prev) < 200:
        await interaction.followup.send("Cycle data incomplete right now.")
        return
    day_now = len(cur) - 1
    prev_at_now = prev[day_now] if day_now < len(prev) else prev[-1]
    prev_peak = max(prev)
    prev_peak_day = prev.index(prev_peak)
    try:
        buf = await asyncio.to_thread(make_roi_image, cur, prev, "2020 cycle", day_now)
    except Exception as e:
        await interaction.followup.send(f"Render failed: {e}")
        return
    ahead = cur[-1] / prev_at_now - 1
    comp = "ahead of" if ahead > 0 else "behind"
    if day_now < prev_peak_day:
        timing = f"2020's cycle peaked on day {prev_peak_day} ({prev_peak:.1f}x) - {prev_peak_day - day_now} days from where we are now."
    else:
        timing = f"We are PAST the day the 2020 cycle peaked (day {prev_peak_day}) - late-cycle by last cycle's clock."
    await interaction.followup.send(
        content=(f"**Day {day_now} since halving:** {cur[-1]:.2f}x vs 2020 cycle's {prev_at_now:.2f}x at the same point "
                 f"({abs(ahead)*100:.0f}% {comp}).\n**Timing:** {timing}\n"
                 f"*Cycles rhyme, they don't repeat - diminishing returns each cycle are the norm.*"),
        file=discord.File(buf, filename="btc_cycle_roi.png"))

def make_cycle_image(prices: list, ma111: list, ma350x2: list, score: float, metrics_line: str,
                     price_now: float = None, heat_word: str = "", read_line: str = "") -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n = len(prices)
    fig = plt.figure(figsize=(12, 7), facecolor=SG_OBS)
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.28)
    ax = fig.add_subplot(gs[0]); sigma_style_ax(ax)
    x = list(range(n))
    ax.plot(x, prices, color=SG_PAPER, lw=1.2, label="BTC")
    ax.plot(x, ma111, color=SG_CYAN, lw=1.5, label="111DMA")
    ax.plot(x, ma350x2, color=SG_AMBER, lw=1.5, label="2x350DMA")
    ax.set_yscale("log")
    ax.legend(facecolor=SG_GRA, edgecolor=SG_SLATE, labelcolor=SG_PAPER, fontsize=9, loc="upper left")
    ax.set_title("BTC  ·  CYCLE HEAT", color=SG_PAPER, fontsize=15, loc="left", pad=12, fontweight="bold")
    if price_now:
        stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
        ax.text(0.865, 1.03, f"${price_now:,.0f}   ·   {stamp}", transform=ax.transAxes,
                color=SG_ASH, fontsize=10, ha="right", family="monospace")
    sigma_logo_ax(ax)
    ax2 = fig.add_subplot(gs[1]); ax2.set_facecolor(SG_OBS)
    ax2.set_xlim(0, 10); ax2.set_ylim(0, 1); ax2.axis("off")
    ax2.barh(0.55, 10, height=0.3, color=SG_CARD, edgecolor=SG_SLATE)
    steps = 200
    for i2 in range(steps):
        g = i2 / steps
        c = SG_CYAN if g < 0.5 else (SG_AMBER if g < 0.8 else SG_SHORT)
        left = i2 * 10 / steps
        ax2.barh(0.55, 10 / steps, left=left, height=0.3, color=c,
                 alpha=0.9 if left <= score else 0.22)
    ax2.plot([score], [0.55], marker="v", color=SG_PAPER, markersize=12)
    ax2.text(0, 0.13, "COOL", color=SG_CYAN, fontsize=9)
    ax2.text(9.15, 0.13, "EUPHORIC", color=SG_SHORT, fontsize=9)
    hw = f"  ·  {heat_word.upper()}" if heat_word else ""
    ax2.text(5, 0.97, f"CYCLE HEAT  {score:g} / 10{hw}", color=SG_PAPER, fontsize=13, ha="center", fontweight="bold")
    ax2.text(5, -0.14, metrics_line, color=SG_ASH, fontsize=9, ha="center", family="monospace")
    if read_line:
        ax2.text(5, -0.45, read_line, color=SG_PAPER, fontsize=10.5, ha="center")
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="cycle", description="BTC cycle heat - Pi Cycle, Mayer, 200W MA, one score")
async def cycle_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    import math
    try:
        async with http() as s:
            daily = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                    {"symbol": "BTCUSDT", "interval": "1d", "limit": 1000}, 20)
            weekly = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                     {"symbol": "BTCUSDT", "interval": "1w", "limit": 250}, 20)
    except Exception:
        daily = weekly = None
    if not daily or not isinstance(daily, list) or len(daily) < 400:
        await interaction.followup.send("Couldn't fetch enough BTC history right now.")
        return
    closes = [float(k[4]) for k in daily]
    price = closes[-1]
    def sma(arr, w, idx=None):
        idx = len(arr) if idx is None else idx
        if idx < w:
            return None
        return sum(arr[idx - w:idx]) / w
    ma111_series, ma350x2_series = [], []
    for i2 in range(len(closes)):
        m1 = sma(closes, 111, i2 + 1)
        m3 = sma(closes, 350, i2 + 1)
        ma111_series.append(m1 if m1 else float("nan"))
        ma350x2_series.append(2 * m3 if m3 else float("nan"))
    ma111 = ma111_series[-1]
    ma350x2 = ma350x2_series[-1]
    mayer = price / sma(closes, 200) if sma(closes, 200) else None
    w_closes = [float(k[4]) for k in weekly] if weekly and isinstance(weekly, list) else []
    ma200w = sma(w_closes, 200) if len(w_closes) >= 200 else (sma(w_closes, len(w_closes)) if w_closes else None)
    dist_200w = (price / ma200w - 1) * 100 if ma200w else None
    pi_gap = (ma350x2 - ma111) / ma350x2 * 100 if ma350x2 and ma111 else None
    # RV percentile (30d ann. vs 1yr)
    rets = [math.log(closes[i2] / closes[i2 - 1]) for i2 in range(1, len(closes)) if closes[i2 - 1] > 0]
    def _std(xs):
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    rv_series = [_std(rets[i2 - 30:i2]) * math.sqrt(365) * 100 for i2 in range(max(30, len(rets) - 365), len(rets) + 1)]
    rv_now = rv_series[-1] if rv_series else 0
    rv_pct = sum(1 for x in rv_series if x <= rv_now) / len(rv_series) * 100 if rv_series else 50
    # Puell Multiple (miner revenue / 365d avg) - blockchain.info free
    puell = None
    try:
        async with http() as s:
            bj = await _get_json(s, "https://api.blockchain.info/charts/miners-revenue",
                                 {"timespan": "2years", "format": "json"}, 20)
        vals = [p["y"] for p in bj.get("values", []) if p.get("y")]
        if len(vals) >= 365:
            puell = vals[-1] / (sum(vals[-365:]) / 365)
    except Exception:
        pass
    # BMSB position (weekly band)
    bmsb_pos = None
    if len(w_closes) >= 21:
        w20 = sum(w_closes[-20:]) / 20
        e = w_closes[0]
        for c in w_closes:
            e = c * (2 / 22) + e * (1 - 2 / 22)
        band_hi = max(w20, e)
        bmsb_pos = price / band_hi - 1
    # score 0-10 (5 factors)
    s_mayer = 0 if not mayer else min(10, max(0, (mayer - 0.8) / (2.8 - 0.8) * 10))
    s_pi = 0 if pi_gap is None else min(10, max(0, (35 - pi_gap) / 35 * 10))
    s_200w = 0 if dist_200w is None else min(10, max(0, dist_200w / 350 * 10))
    s_puell = 0 if puell is None else min(10, max(0, (puell - 0.5) / (4.0 - 0.5) * 10))
    s_bmsb = 0 if bmsb_pos is None else min(10, max(0, bmsb_pos / 1.0 * 10))
    weights = [(0.30, s_mayer), (0.25, s_pi), (0.18, s_200w)]
    weights.append((0.17, s_puell) if puell is not None else (0.17, s_mayer))
    weights.append((0.10, s_bmsb) if bmsb_pos is not None else (0.10, s_200w))
    score = round(sum(w * v for w, v in weights), 1)
    # narration
    if mayer is None:
        mayer_read = ""
    elif mayer < 0.8:
        mayer_read = "Mayer in deep-value zone - historically generational bids."
    elif mayer < 1.5:
        mayer_read = "Mayer healthy - trend has room to run."
    elif mayer < 2.4:
        mayer_read = "Mayer heated - late-cycle behavior starts here, trail stops."
    else:
        mayer_read = "Mayer above 2.4 - every past reading here resolved in a major correction. Size down."
    if pi_gap is None:
        pi_read = ""
    elif pi_gap > 25:
        pi_read = "Pi Cycle crossover distant - no top signal."
    elif pi_gap > 10:
        pi_read = "Pi Cycle gap closing - weeks of vertical price could trigger it."
    else:
        pi_read = "PI CYCLE NEAR CROSSOVER - every past cross marked the cycle top within days."
    metrics_line = (f"Mayer {mayer:.2f}   ·   200W {dist_200w:+.0f}%   ·   Pi gap {pi_gap:.0f}%"
                    + (f"   ·   Puell {puell:.2f}" if puell is not None else "")
                    + (f"   ·   BMSB {bmsb_pos*100:+.0f}%" if bmsb_pos is not None else ""))
    heat_word = "Cool" if score < 3.5 else ("Elevated" if score < 7 else "Euphoric")
    if score < 3.5:
        chart_read = "Early/mid cycle - no top signals. Dips are for buying, not fearing."
    elif score < 7:
        chart_read = "Heating up - trend intact but late-cycle behavior begins. Trail stops."
    else:
        chart_read = "Euphoric zone - historically the exit window, not the entry."
    try:
        buf = await asyncio.to_thread(make_cycle_image, closes[-900:],
                                      ma111_series[-900:], ma350x2_series[-900:], score, metrics_line,
                                      price, heat_word, chart_read)
    except Exception as e:
        await interaction.followup.send(f"Chart render failed: {e}")
        return
    puell_read = ""
    if puell is not None:
        if puell >= 4:
            puell_read = "Puell above 4 - miner revenue euphoric, historically top territory."
        elif puell <= 0.5:
            puell_read = "Puell below 0.5 - miner capitulation zone, historically bottoms."
    lines = [f"**Cycle Heat {score:g}/10 - {heat_word}.**",
             f"{mayer_read}",
             f"{pi_read}",
             f"{puell_read}",
             f"**What flips this:** Mayer >2.4 or Pi gap <10% pushes heat toward 9/10 - historically the exit window, not the entry."]
    f = discord.File(buf, filename="btc_cycle.png")
    await interaction.followup.send(content="\n".join(l for l in lines if l), file=f)

def make_ratio_image(sym: str, ratios: list, ma50: list, chg90: float, verdict: str, vcolor: str) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5.6), facecolor=SG_OBS)
    sigma_style_ax(ax)
    x = list(range(len(ratios)))
    ax.plot(x, ratios, color=SG_PAPER, lw=1.3)
    ax.plot(x, ma50, color=SG_CYAN, lw=1.5, label="50DMA")
    ax.fill_between(x, ratios, min(ratios), color=SG_CYAN, alpha=0.05)
    ax.set_title(f"{sym} / BTC  ·  1D", color=SG_PAPER, fontsize=15, loc="left", pad=12, fontweight="bold")
    ax.text(0.99, 1.02, f"90d vs BTC: {chg90:+.1f}%  ·  {verdict}", transform=ax.transAxes,
            color=vcolor, fontsize=10, ha="right", family="monospace")
    ax.legend(facecolor=SG_GRA, edgecolor=SG_SLATE, labelcolor=SG_PAPER, fontsize=9, loc="upper left")
    sigma_logo_ax(ax)
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="ratio", description="Coin vs BTC - real strength or just USD beta?")
@app_commands.describe(coin="Coin symbol, e.g. SOL")
async def ratio_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    base = re.sub(r"[^A-Za-z0-9]", "", coin).upper().replace("USDT", "")
    ratios = None
    async with http() as s:
        d = await _get_json(s, "https://api.binance.com/api/v3/klines",
                            {"symbol": f"{base}BTC", "interval": "1d", "limit": 365}, 20)
        if d and isinstance(d, list) and len(d) > 60:
            ratios = [float(k[4]) for k in d]
        else:
            du = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                 {"symbol": f"{base}USDT", "interval": "1d", "limit": 365}, 20)
            db = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                 {"symbol": "BTCUSDT", "interval": "1d", "limit": 365}, 20)
            if du and db and isinstance(du, list) and isinstance(db, list) and len(du) > 60:
                n = min(len(du), len(db))
                ratios = [float(du[i2][4]) / float(db[i2][4]) for i2 in range(-n, 0)]
    if not ratios:
        await interaction.followup.send(f"No BTC-pair data for **{base}**.")
        return
    ma50 = []
    for i2 in range(len(ratios)):
        w = ratios[max(0, i2 - 49):i2 + 1]
        ma50.append(sum(w) / len(w))
    chg90 = (ratios[-1] / ratios[-91] - 1) * 100 if len(ratios) > 91 else (ratios[-1] / ratios[0] - 1) * 100
    above = ratios[-1] > ma50[-1]
    rising = ma50[-1] > ma50[-15] if len(ma50) > 15 else True
    if above and rising:
        verdict, vc = "genuine outperformance", SG_LONG
        read = "Strength in BTC terms, not just USD beta. Rotation favors it."
    elif above and not rising:
        verdict, vc = "stalling above the DMA", SG_AMBER
        read = "Outperformance losing momentum - a DMA loss turns this to dead money vs BTC."
    elif not above and not rising:
        verdict, vc = "underperforming BTC", SG_SHORT
        read = "USD gains here are beta, not alpha - dead money vs just holding BTC."
    else:
        verdict, vc = "early reversal attempt", SG_CYAN
        read = "Turning up from below - a clean DMA reclaim would confirm the rotation."
    try:
        buf = await asyncio.to_thread(make_ratio_image, base, ratios, ma50, chg90, verdict, vc)
    except Exception as e:
        await interaction.followup.send(f"Chart render failed: {e}")
        return
    f = discord.File(buf, filename=f"{base}_btc_ratio.png")
    await interaction.followup.send(content=f"**{base}/BTC:** {chg90:+.1f}% (90d) - {verdict}.\n**Read:** {read}", file=f)

@bot.tree.command(name="rvol", description="Realized volatility regime - is the market compressed or wild?")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def rvol_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    import math
    klines = None
    try:
        async with http() as s:
            klines = await _get_json(s, "https://api.binance.com/api/v3/klines",
                                     {"symbol": pair, "interval": "1d", "limit": 1000}, 20)
    except Exception:
        pass
    if not klines or not isinstance(klines, list) or len(klines) < 60:
        await interaction.followup.send(f"Not enough history for **{symbol}** (needs Binance daily data).")
        return
    closes = [float(k[4]) for k in klines]
    stamps = [int(k[0]) // 1000 for k in klines]
    rets = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            rets.append(math.log(closes[i] / closes[i-1]))
        else:
            rets.append(0.0)
    def _std(xs):
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    # rolling 30d RV annualized, then bucket by month (avg)
    monthly = {}
    for i in range(30, len(rets) + 1):
        rv = _std(rets[i-30:i]) * math.sqrt(365) * 100
        mkey = datetime.fromtimestamp(stamps[i], tz=timezone.utc).strftime("%Y-%m")
        monthly.setdefault(mkey, []).append(rv)
    months = sorted(monthly.keys())
    rvs = [sum(monthly[m]) / len(monthly[m]) for m in months]
    current_rv = _std(rets[-30:]) * math.sqrt(365) * 100
    # 1y percentile of current vs daily rolling values
    recent_series = []
    for i in range(max(30, len(rets) - 365), len(rets) + 1):
        recent_series.append(_std(rets[i-30:i]) * math.sqrt(365) * 100)
    below = sum(1 for x in recent_series if x <= current_rv)
    pctile = below / len(recent_series) * 100 if recent_series else 50
    if pctile <= 20:
        read = "Volatility compressed to the low end of its range - expansion usually follows. Watch for the break."
    elif pctile >= 80:
        read = "Elevated volatility - wide stops or smaller size. Mean reversion likely eventually."
    else:
        read = "Mid-range volatility - no regime extreme."
    labels = [m[2:] for m in months]  # YY-MM
    try:
        buf = await asyncio.to_thread(make_rvol_image, f"{symbol}/USDT", labels, rvs, current_rv)
    except Exception as e:
        await interaction.followup.send(f"Chart render failed: {e}")
        return
    content = (f"**{symbol} 30d Realized Vol:** {current_rv:.1f}% - **{pctile:.0f}th percentile** (1yr)\n"
               f"**Read:** {read}")
    f = discord.File(buf, filename=f"{symbol}_rvol.png")
    await interaction.followup.send(content=content, file=f)

@bot.tree.command(name="snapshot", description="Full market check for a coin in one command")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def snapshot_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    t24, fd, od = await asyncio.gather(md_ticker24(pair), md_funding(pair), md_oi(pair))
    if not t24:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit.")
        return
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    cb = await md_coinbase_price(base)
    # quick 24h CVD from 1h klines (Binance only)
    cvd_line = None
    try:
        async with http() as s:
            kl_s = await _get_json(s, "https://api.binance.com/api/v3/klines", {"symbol": pair, "interval": "1h", "limit": 24}, 15)
            kl_p = await _get_json(s, "https://fapi.binance.com/fapi/v1/klines", {"symbol": pair, "interval": "1h", "limit": 24}, 15)
        def _delta(kl):
            tot = 0.0
            for k in kl:
                tot += 2 * float(k[10]) - float(k[7])
            return tot
        if kl_s and isinstance(kl_s, list):
            ds = _delta(kl_s)
            dp = _delta(kl_p) if kl_p and isinstance(kl_p, list) else None
            def _m(x):
                return f"{'+' if x >= 0 else '-'}${abs(x)/1e6:.0f}M"
            cvd_line = f"**CVD (24h):** Spot {_m(ds)}" + (f" | Perp {_m(dp)}" if dp is not None else "")
    except Exception:
        pass
    # fear & greed
    fg_line = None
    try:
        async with http() as s:
            fg = await _get_json(s, "https://api.alternative.me/fng/", None, 10)
        v = fg["data"][0]
        fg_line = f"**Fear & Greed:** {v['value']} ({v['value_classification']})"
    except Exception:
        pass
    arrow = "\U0001F7E2" if t24["priceChangePercent"] >= 0 else "\U0001F534"
    lines = [f"**Price:** ${fnum(t24['lastPrice'])} {arrow} {t24['priceChangePercent']:+.2f}% (24h)"]
    ctx = []
    if fd:
        lean = "longs paying" if fd["rate"] > 0 else "shorts paying"
        ctx.append(f"**Funding:** {fd['rate']:+.4f}% - {lean}")
    if od and od.get("oi_then"):
        ctx.append(f"**OI 24h:** {(od['oi'] - od['oi_then']) / od['oi_then'] * 100:+.2f}%")
    if ctx:
        lines.append(" | ".join(ctx))
    if cvd_line:
        lines.append(cvd_line)
    if cb and t24["lastPrice"] > 0:
        prem = (cb - t24["lastPrice"]) / t24["lastPrice"] * 100
        lines.append(f"**Coinbase premium:** {prem:+.3f}%")
    if fg_line:
        lines.append(fg_line)
    embed = discord.Embed(title=f"\U0001F4F8 {symbol} Snapshot", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Sigma Trading - morning check, one command")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="stables", description="Stablecoin supply - is dry powder flowing in or out?")
async def stables_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as s:
            d = await _get_json(s, "https://stablecoins.llama.fi/stablecoincharts/all", None, 20)
    except Exception:
        d = None
    if not d or not isinstance(d, list) or len(d) < 31:
        await interaction.followup.send("Stablecoin data unavailable right now.")
        return
    def _tot(row):
        try:
            return float(row["totalCirculating"]["peggedUSD"])
        except Exception:
            return None
    now = _tot(d[-1])
    d7 = _tot(d[-8])
    d30 = _tot(d[-31])
    if not now:
        await interaction.followup.send("Couldn't parse stablecoin data.")
        return
    chg7 = (now - d7) / d7 * 100 if d7 else 0
    chg30 = (now - d30) / d30 * 100 if d30 else 0
    if chg7 >= 0.75:
        read = "Supply expanding - fresh dry powder entering. Liquidity tailwind."
    elif chg7 <= -0.75:
        read = "Supply contracting - capital leaving the system. Liquidity headwind."
    else:
        read = "Supply flat - no strong liquidity signal either way."
    arrow7 = "\U0001F7E2" if chg7 >= 0 else "\U0001F534"
    arrow30 = "\U0001F7E2" if chg30 >= 0 else "\U0001F534"
    embed = discord.Embed(title="\U0001F4B5 Stablecoin Supply", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = (
        f"**Total supply:** ${now/1e9:.1f}B\n"
        f"**7d:** {arrow7} {chg7:+.2f}% | **30d:** {arrow30} {chg30:+.2f}%\n"
        f"**Read:** {read}"
    )
    embed.set_footer(text="Sigma Trading - DefiLlama data · stablecoin supply leads price")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="agg", description="Aggregated OI + funding across Binance, Bybit and OKX")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def agg_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    rows = await fetch_agg_rows(base)
    if not rows:
        await interaction.followup.send(f"No perp data found for **{base}** on any tracked exchange.")
        return
    total_oi = sum(r[1] for r in rows)
    lines = []
    for name, oi_usd, fr in rows:
        share = oi_usd / total_oi * 100 if total_oi else 0
        lines.append(f"**{name}:** ${oi_usd/1e9:.2f}B OI ({share:.0f}%) | funding {fr:+.4f}%")
    avg_fr = sum(r[2] * r[1] for r in rows) / total_oi if total_oi else 0
    lines.append("")
    lines.append(f"**Total OI:** ${total_oi/1e9:.2f}B | **OI-weighted funding:** {avg_fr:+.4f}%")
    if avg_fr >= 0.03:
        lines.append("**Read:** Longs paying heavily across venues - crowded.")
    elif avg_fr <= -0.01:
        lines.append("**Read:** Shorts paying across venues - squeeze fuel.")
    else:
        lines.append("**Read:** Funding balanced across venues.")
    embed = discord.Embed(title=f"\U0001F310 Aggregated Derivatives - {base}", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Sigma Trading - Binance + Bybit + OKX")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="whale", description="Large individual trades in the last hour - what are whales doing?")
@app_commands.describe(coin="Coin symbol, e.g. BTC", min_usd="Minimum trade size in USD (default 500000)")
async def whale_cmd(interaction: discord.Interaction, coin: str, min_usd: int = 500000):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    min_usd = max(50_000, min(min_usd, 10_000_000))
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = end - 3600_000
    trades = []
    try:
        async with http() as s:
            cur = start
            for _ in range(6):  # aggTrades pages, max ~6k trades scanned
                d = await _get_json(s, "https://api.binance.com/api/v3/aggTrades",
                                    {"symbol": pair, "startTime": cur, "endTime": end, "limit": 1000}, 15)
                if not d or not isinstance(d, list):
                    break
                trades += d
                if len(d) < 1000:
                    break
                cur = int(d[-1]["T"]) + 1
    except Exception:
        pass
    if not trades:
        await interaction.followup.send(f"No trade data for **{symbol}** on Binance (whale scan is Binance-only).")
        return
    buys = []
    sells = []
    for t in trades:
        try:
            usd = float(t["p"]) * float(t["q"])
        except Exception:
            continue
        if usd < min_usd:
            continue
        # m = True means buyer is maker -> aggressive SELL
        (sells if t.get("m") else buys).append((usd, float(t["p"])))
    buy_usd = sum(u for u, _ in buys)
    sell_usd = sum(u for u, _ in sells)
    top = sorted(buys + [(-u, p) for u, p in sells], key=lambda x: -abs(x[0]))[:8]
    lines = [f"**Last 1h, trades >= ${min_usd/1e3:.0f}K:** {len(buys)} buys (${buy_usd/1e6:.1f}M) vs {len(sells)} sells (${sell_usd/1e6:.1f}M)"]
    if buy_usd + sell_usd > 0:
        lean = buy_usd / (buy_usd + sell_usd) * 100
        lines.append(f"**Whale lean:** {lean:.0f}% buy-side")
    for u, p in top:
        side = "\U0001F7E2 BUY " if u > 0 else "\U0001F534 SELL"
        lines.append(f"{side} ${abs(u)/1e6:.2f}M @ ${fnum(p)}")
    if len(buys) + len(sells) == 0:
        lines.append(f"*No single trades above ${min_usd/1e3:.0f}K this hour - quiet whales.*")
    embed = discord.Embed(title=f"\U0001F40B Whale Watch - {symbol}", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Sigma Trading - Binance spot aggTrades")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="lsr", description="Long/Short ratio of top traders (Binance futures)")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def lsr_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    try:
        async with http() as s:
            acct = await _get_json(s, "https://fapi.binance.com/futures/data/topLongShortAccountRatio",
                                   {"symbol": pair, "period": "1h", "limit": 25}, 15)
            pos = await _get_json(s, "https://fapi.binance.com/futures/data/topLongShortPositionRatio",
                                  {"symbol": pair, "period": "1h", "limit": 25}, 15)
            glob = await _get_json(s, "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                                   {"symbol": pair, "period": "1h", "limit": 2}, 15)
    except Exception:
        acct = pos = glob = None
    if not acct or not isinstance(acct, list):
        await interaction.followup.send(f"No LSR data for **{symbol}** (Binance futures only).")
        return
    def _ratio(d):
        try:
            return float(d[-1]["longShortRatio"])
        except Exception:
            return None
    r_acct = _ratio(acct)
    r_pos = _ratio(pos) if pos and isinstance(pos, list) else None
    r_glob = _ratio(glob) if glob and isinstance(glob, list) else None
    chg = ""
    try:
        prev = float(acct[0]["longShortRatio"])
        if prev:
            chg = f" ({(r_acct - prev) / prev * 100:+.1f}% vs 24h ago)"
    except Exception:
        pass
    lines = []
    if r_acct is not None:
        pct_long = r_acct / (1 + r_acct) * 100
        lines.append(f"**Top traders (accounts):** {r_acct:.2f} - {pct_long:.0f}% long{chg}")
    if r_pos is not None:
        lines.append(f"**Top traders (positions):** {r_pos:.2f}")
    if r_glob is not None:
        lines.append(f"**All accounts:** {r_glob:.2f}")
    read = ""
    if r_acct is not None:
        if r_acct >= 2.5:
            read = "Heavily long-crowded - fuel for downside wicks."
        elif r_acct <= 0.7:
            read = "Short-crowded - squeeze fuel above."
        else:
            read = "Positioning balanced."
    if read:
        lines.append(f"**Read:** {read}")
    embed = discord.Embed(title=f"\u2696\uFE0F Long/Short Ratio - {symbol}", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Sigma Trading - Binance futures, 1h data")
    await interaction.followup.send(embed=embed)

def make_liqzones_image(symbol: str, price: float, zones: list) -> io.BytesIO:
    """Clean two-sided liquidation chart: long-liq clusters below price (red),
    short-liq clusters above price (green). Bar length = estimated intensity."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    longs = sorted([z for z in zones if z["side"] == "long"], key=lambda z: z["price"])
    shorts = sorted([z for z in zones if z["side"] == "short"], key=lambda z: z["price"])

    fig, ax = plt.subplots(figsize=(11, 7.5), facecolor=SG_OBS)
    ax.set_facecolor(SG_OBS)

    bar_h = price * 0.006
    for z in longs:
        ax.barh(z["price"], -z["intensity"], height=bar_h, color=SG_SHORT, alpha=0.92,
                edgecolor="#ff6b6f", linewidth=0.5)
        dist = (z["price"] - price) / price * 100
        ax.text(-z["intensity"] - 0.03, z["price"], f"{z['lev']}x  {dist:+.1f}%",
                color="#ff8f92", fontsize=8.5, va="center", ha="right")
    for z in shorts:
        ax.barh(z["price"], z["intensity"], height=bar_h, color=SG_LONG, alpha=0.92,
                edgecolor="#4fd18b", linewidth=0.5)
        dist = (z["price"] - price) / price * 100
        ax.text(z["intensity"] + 0.03, z["price"], f"{z['lev']}x  {dist:+.1f}%",
                color="#5fe0a0", fontsize=8.5, va="center", ha="left")

    ax.axhline(price, color=SG_PAPER, linewidth=1.4)
    ax.text(0, price, f"  ${fnum(price)}  ", color=SG_OBS, fontsize=9.5, fontweight="bold",
            va="center", ha="center", bbox=dict(boxstyle="round,pad=0.3", fc=SG_PAPER, ec="none"))

    ax.set_xlim(-1.35, 1.35)
    ax.axvline(0, color=SG_SLATE, linewidth=0.8)
    ax.text(-0.7, ax.get_ylim()[1], "LONG liquidations \u25BC", color=SG_SHORT,
            fontsize=10, ha="center", va="bottom", fontweight="bold")
    ax.text(0.7, ax.get_ylim()[1], "\u25B2 SHORT liquidations", color=SG_LONG,
            fontsize=10, ha="center", va="bottom", fontweight="bold")

    ax.set_title(f"{symbol}   Estimated Liquidation Zones", color=SG_PAPER, fontsize=13, loc="left", pad=24)
    ax.set_xticks([])
    ax.tick_params(colors=SG_ASH, labelsize=8)
    ax.yaxis.set_major_formatter(lambda x, _: f"${fnum(x)}")
    ax.grid(color=SG_SLATE, linewidth=0.5, axis="y")
    for sp in ax.spines.values():
        sp.set_color(SG_SLATE)
    plt.tight_layout()
    try:
        sigma_logo_ax(ax)
    except Exception:
        pass
    buf = io.BytesIO()
    fig.savefig(buf, dpi=130, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="liqzones", description="Estimated liquidation heatmap - where leverage gets flushed")
@app_commands.describe(coin="Coin symbol, e.g. BTC")
async def liqzones_cmd(interaction: discord.Interaction, coin: str):
    await interaction.response.defer()
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    pair = f"{base}USDT"
    price = await md_price(pair)
    od = await md_oi(pair)
    if price is None or price <= 0:
        await interaction.followup.send(f"Couldn't price **{base}** - liqzones needs a Binance/Bybit perp.")
        return
    # long/short skew from LSR (fallback 1.0 balanced)
    ls_ratio = 1.0
    try:
        async with http() as s:
            acct = await _get_json(s, "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                                   {"symbol": pair, "period": "1h", "limit": 1}, 15)
        if acct and isinstance(acct, list):
            ls_ratio = float(acct[0]["longShortRatio"])
    except Exception:
        pass
    long_share = ls_ratio / (1 + ls_ratio)
    short_share = 1 - long_share
    oi_usd = (od["oi"] * price) if od else None
    # maintenance margin approx per tier (Binance-like): higher lev -> tighter
    LEV_TIERS = [(5, 0.004), (10, 0.005), (20, 0.008), (25, 0.01), (40, 0.015),
                 (50, 0.02), (75, 0.025), (100, 0.005), (125, 0.004)]
    # assumed position-count weighting: mid leverage most populated, extremes lighter
    TIER_WEIGHT = {5: 0.10, 10: 0.16, 20: 0.15, 25: 0.15, 40: 0.10,
                   50: 0.13, 75: 0.08, 100: 0.09, 125: 0.04}
    zones = []
    for lev, mmr in LEV_TIERS:
        long_liq = price * (1 - 1/lev + mmr)
        short_liq = price * (1 + 1/lev - mmr)
        w = TIER_WEIGHT[lev]
        zones.append({"price": long_liq, "side": "long", "lev": lev, "raw": w * long_share})
        zones.append({"price": short_liq, "side": "short", "lev": lev, "raw": w * short_share})
    # drop zones hugging the price (within 1.5%) - they clutter the center and aren't actionable
    zones = [z for z in zones if abs(z["price"] - price) / price >= 0.015]
    if not zones:
        await interaction.followup.send("Not enough separation to map liquidation zones right now.")
        return
    max_raw = max(z["raw"] for z in zones)
    for z in zones:
        z["intensity"] = max(0.15, z["raw"] / max_raw)
    try:
        buf = await asyncio.to_thread(make_liqzones_image, f"{base}/USDT", price, zones)
    except Exception as e:
        await interaction.followup.send(f"Heatmap render failed: {e}")
        return
    top_long = sorted([z for z in zones if z["side"] == "long"], key=lambda z: -z["intensity"])[:2]
    top_short = sorted([z for z in zones if z["side"] == "short"], key=lambda z: -z["intensity"])[:2]
    lines = [f"**Price:** ${fnum(price)}" + (f" | **OI:** ${oi_usd/1e9:.2f}B" if oi_usd else "")]
    lines.append(f"**Positioning:** {long_share*100:.0f}% long / {short_share*100:.0f}% short")
    lines.append("**\U0001F53B Long liquidations below** (downside magnets):")
    for z in top_long:
        dist = (z["price"] - price) / price * 100
        lines.append(f"   ${fnum(z['price'])} ({dist:+.1f}%) - {z['lev']}x")
    lines.append("**\U0001F53A Short liquidations above** (upside magnets):")
    for z in top_short:
        dist = (z["price"] - price) / price * 100
        lines.append(f"   ${fnum(z['price'])} ({dist:+.1f}%) - {z['lev']}x")
    lines.append("*Estimated from OI + positioning, not exchange-confirmed. Best on BTC/ETH.*")
    f = discord.File(buf, filename=f"{base}_liqzones.png")
    await interaction.followup.send(content="\n".join(lines), file=f)

@bot.tree.command(name="levels", description="Auto-detected support & resistance levels")
@app_commands.describe(coin="Coin symbol, e.g. BTC, SOL", timeframe="Timeframe for structure")
@app_commands.choices(timeframe=[app_commands.Choice(name=k, value=k) for k in ("1H", "4H", "1D")])
async def levels(interaction: discord.Interaction, coin: str, timeframe: app_commands.Choice[str] = None):
    await interaction.response.defer()
    tfv = timeframe.value if timeframe else "4H"
    interval = {"1H": "1h", "4H": "4h", "1D": "1d"}[tfv]
    symbol = re.sub(r"[^A-Za-z0-9]", "", coin).upper()
    pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
    klines = await md_klines(pair, interval, 300)
    if not klines:
        await interaction.followup.send(f"Couldn't find **{symbol}** on Binance or Bybit.")
        return
    if len(klines) < 50:
        await interaction.followup.send(f"Not enough data for **{symbol}** on {tfv}.")
        return
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    last = float(klines[-1][4])
    piv = 5
    raw = []
    for i in range(piv, len(klines) - piv):
        if highs[i] == max(highs[i - piv:i + piv + 1]):
            raw.append(highs[i])
        if lows[i] == min(lows[i - piv:i + piv + 1]):
            raw.append(lows[i])
    raw.sort()
    clusters = []
    tol = last * 0.006
    for lv in raw:
        if clusters and lv - clusters[-1][-1] <= tol:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])
    scored = [(sum(c) / len(c), len(c)) for c in clusters]
    res = sorted([s for s in scored if s[0] > last], key=lambda x: x[0])[:4]
    sup = sorted([s for s in scored if s[0] <= last], key=lambda x: -x[0])[:4]
    def fmt_lv(s):
        price, touches = s
        strength = "\u2B50" * min(touches, 3)
        dist = abs(price - last) / last * 100
        return f"`{fnum(price)}` {strength} ({dist:.1f}% away)"
    lines = [f"**Current price:** {fnum(last)}\n"]
    if res:
        lines.append("**Resistance above:**")
        lines += [fmt_lv(s) for s in res]
    if sup:
        lines.append("\n**Support below:**")
        lines += [fmt_lv(s) for s in sup]
    lines.append("\n*\u2B50 = number of touches (max 3 shown). Auto-detected from swing pivots - always confirm with your own chart.*")
    embed = discord.Embed(title=f"Key Levels - {symbol} ({tfv})", color=NAVY, timestamp=datetime.now(timezone.utc))
    embed.description = "\n".join(lines)
    embed.set_footer(text="Sigma Trading - swing pivot clusters")
    await interaction.followup.send(embed=embed)

def make_heatmap_image(rows: list) -> io.BytesIO:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    cols = 5
    nrows = (len(rows) + cols - 1) // cols
    fig, ax = plt.subplots(figsize=(11, nrows * 1.5), facecolor=SG_OBS)
    ax.set_facecolor(SG_OBS)
    ax.set_xlim(0, cols)
    ax.set_ylim(0, nrows)
    ax.axis("off")
    for idx, (sym, chg, vol_usd) in enumerate(rows):
        r_i = nrows - 1 - idx // cols
        c_i = idx % cols
        mag = min(abs(chg) / 8, 1.0)
        if chg >= 0:
            color = (0.05, 0.35 + 0.35 * mag, 0.25 + 0.2 * mag)
        else:
            color = (0.45 + 0.35 * mag, 0.13, 0.2)
        rect = mpatches.FancyBboxPatch((c_i + 0.03, r_i + 0.04), 0.94, 0.92, boxstyle="round,pad=0.01,rounding_size=0.03", facecolor=color, edgecolor=SG_OBS, linewidth=2)
        ax.add_patch(rect)
        ax.text(c_i + 0.5, r_i + 0.62, sym, ha="center", va="center", color=SG_PAPER, fontsize=13, fontweight="bold")
        ax.text(c_i + 0.5, r_i + 0.33, f"{chg:+.2f}%", ha="center", va="center", color=SG_PAPER, fontsize=11)
    fig.suptitle("24h Market Heatmap - top volume", color=SG_PAPER, fontsize=13, y=0.995)
    plt.tight_layout()
    try:
        sigma_logo_ax(ax)
    except Exception:
        pass
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

@bot.tree.command(name="heatmap", description="24h market heatmap - top 20 coins by volume")
async def heatmap(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with http() as session:
            async with session.get("https://api.binance.com/api/v3/ticker/24hr", timeout=20) as resp:
                data = await resp.json()
    except Exception:
        await interaction.followup.send("Market data unavailable right now.")
        return
    rows = []
    for d in data:
        s = d.get("symbol", "")
        if not s.endswith("USDT") or any(x in s for x in ("UP", "DOWN", "BULL", "BEAR", "USDC", "FDUSD", "TUSD", "DAI", "EUR")):
            continue
        try:
            rows.append((s[:-4], float(d["priceChangePercent"]), float(d["quoteVolume"])))
        except Exception:
            continue
    rows.sort(key=lambda r: r[2], reverse=True)
    rows = rows[:20]
    if not rows:
        await interaction.followup.send("No data right now.")
        return
    try:
        buf = await asyncio.to_thread(make_heatmap_image, rows)
    except Exception as e:
        await interaction.followup.send(f"Heatmap rendering failed: {e}")
        return
    f = discord.File(buf, filename="heatmap.png")
    green = sum(1 for r in rows if r[1] >= 0)
    await interaction.followup.send(content=f"**Market Heatmap** - {green}/20 green (24h)", file=f)

# ═══════════════ SIGMA TERMINAL - BULL MARKET SCANNERS ═══════════════
_SCAN_CACHE: dict = {"ts": 0, "rows": None}

_STABLE_BASES = {"USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "EUR"}

async def _scan_universe(n: int = 40):
    """Top-n Binance USDT perps by 24h quote volume, each with 31 daily candles. Cached 5 min."""
    now = _time.time()
    if _SCAN_CACHE["rows"] is not None and now - _SCAN_CACHE["ts"] < 300:
        return _SCAN_CACHE["rows"]
    async with http() as s:
        tick = await _get_json(s, "https://fapi.binance.com/fapi/v1/ticker/24hr", None, 15)
        if not tick:
            return None
        syms = [x for x in tick if x.get("symbol", "").endswith("USDT")
                and x["symbol"][:-4] not in _STABLE_BASES and not x["symbol"][:-4].endswith("DOWN")
                and not x["symbol"][:-4].endswith("UP")]
        syms.sort(key=lambda x: float(x.get("quoteVolume", 0) or 0), reverse=True)
        syms = syms[:n]
        sem = asyncio.Semaphore(8)

        async def one(sym):
            async with sem:
                k = await _get_json(s, "https://fapi.binance.com/fapi/v1/klines",
                                    {"symbol": sym, "interval": "1d", "limit": 31}, 15)
                if not k or len(k) < 8:
                    return None
                closes = [float(c[4]) for c in k]
                highs = [float(c[2]) for c in k]
                vols = [float(c[7]) for c in k]      # quote volume
                return {"symbol": sym, "base": sym[:-4], "closes": closes, "highs": highs, "vols": vols}
        rows = [r for r in await asyncio.gather(*(one(x["symbol"]) for x in syms)) if r]
        if "BTCUSDT" not in {r["symbol"] for r in rows}:
            b = await one("BTCUSDT")
            if b:
                rows.append(b)
    _SCAN_CACHE.update(ts=now, rows=rows)
    return rows

def _chg(closes, days):
    if len(closes) <= days:
        return None
    return (closes[-1] / closes[-1 - days] - 1) * 100

@bot.tree.command(name="rs", description="Relative strength scanner - which coins are beating BTC (7d / 30d), where rotation is")
async def rs_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = await _scan_universe(40)
    if not rows:
        await interaction.followup.send("Binance feed didn't answer - try again in a minute.")
        return
    btc = next((r for r in rows if r["symbol"] == "BTCUSDT"), None)
    b7, b30 = (_chg(btc["closes"], 7), _chg(btc["closes"], 30)) if btc else (0, 0)
    scored = []
    for r in rows:
        if r["symbol"] == "BTCUSDT":
            continue
        c7, c30 = _chg(r["closes"], 7), _chg(r["closes"], 30)
        if c7 is None or c30 is None:
            continue
        scored.append((r["base"], c7, c30, c7 - (b7 or 0), c30 - (b30 or 0)))
    scored.sort(key=lambda x: x[3], reverse=True)
    e = discord.Embed(title="Relative strength vs BTC - top 40 by volume", color=NAVY)
    e.description = f"BTC itself: **{b7:+.1f}%** 7d \u00b7 **{b30:+.1f}%** 30d. RS = coin minus BTC, in points."
    lead = "\n".join(f"`{b:<7}` 7d {c7:+6.1f}%  30d {c30:+6.1f}%  \u2192 RS7 **{r7:+.1f}**" for b, c7, c30, r7, r30 in scored[:10])
    lag = "\n".join(f"`{b:<7}` 7d {c7:+6.1f}%  30d {c30:+6.1f}%  \u2192 RS7 **{r7:+.1f}**" for b, c7, c30, r7, r30 in scored[-5:][::-1])
    e.add_field(name="Leaders (money is rotating here)", value=lead or "-", inline=False)
    e.add_field(name="Laggards (dead money or catch-up candidates)", value=lag or "-", inline=False)
    e.set_footer(text="Sigma Terminal \u00b7 Binance perps \u00b7 strength is context, not a signal")
    await interaction.followup.send(embed=e)

@bot.tree.command(name="breakouts", description="Momentum scanner - coins at 30d highs on expanding volume")
async def breakouts_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = await _scan_universe(40)
    if not rows:
        await interaction.followup.send("Binance feed didn't answer - try again in a minute.")
        return
    hits = []
    for r in rows:
        hi30 = max(r["highs"][:-1]) if len(r["highs"]) > 1 else None
        if not hi30:
            continue
        close = r["closes"][-1]
        dist = (close / hi30 - 1) * 100
        avgv = sum(r["vols"][:-1]) / max(1, len(r["vols"]) - 1)
        vmult = r["vols"][-1] / avgv if avgv else 0
        if dist >= -1.5:
            hits.append((r["base"], dist, vmult, _chg(r["closes"], 7) or 0))
    hits.sort(key=lambda x: (x[1] >= 0, x[2]), reverse=True)
    e = discord.Embed(title="Breakout scanner - within 1.5% of a 30-day high", color=NAVY)
    if not hits:
        e.description = "Nothing at a 30d high right now. Quiet tape, or the market is pulling back - both are information."
    else:
        e.description = "\n".join(
            f"`{b:<7}` {'**NEW HIGH**' if d >= 0 else f'{d:+.1f}% from high':<16} vol \u00d7{v:.1f}  7d {c7:+.1f}%"
            for b, d, v, c7 in hits[:14])
        e.add_field(name="Read it right", value="Volume \u00d72+ with a new high is expansion. A high on \u00d70.6 volume is a test, not a breakout. Levels first, then structure - the scanner just tells you where to look.", inline=False)
    e.set_footer(text="Sigma Terminal \u00b7 Binance perps, top 40 by volume \u00b7 educational")
    await interaction.followup.send(embed=e)

_CROWD_CACHE: dict = {"ts": 0, "embed": None}

CROWD_LONG_FLOOR = 0.03     # % per 8h  (~33% APR) - below this, longs are not "crowded" whatever the rank

CROWD_SHORT_FLOOR = -0.02   # % per 8h

CROWD_Z = 1.5               # current must be >= mean + 1.5 sigma of the coin's own 30d funding history

def _pct_rank(hist, x):
    if not hist:
        return None
    return sum(1 for h in hist if h <= x) / len(hist) * 100

@bot.tree.command(name="crowded", description="Funding extremes vs each coin's own history - crowded longs (squeeze risk) and crowded shorts")
async def crowded_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    now = _time.time()
    if _CROWD_CACHE["embed"] is not None and now - _CROWD_CACHE["ts"] < 300:
        await interaction.followup.send(embed=_CROWD_CACHE["embed"])
        return
    async with http() as s:
        prem = await _get_json(s, "https://fapi.binance.com/fapi/v1/premiumIndex", None, 15)
        if not prem:
            await interaction.followup.send("Binance feed didn't answer - try again in a minute.")
            return
        cur = {}
        for x in prem:
            sym = x.get("symbol", "")
            if not sym.endswith("USDT") or sym[:-4] in _STABLE_BASES:
                continue
            try:
                cur[sym] = float(x.get("lastFundingRate") or 0) * 100
            except Exception:
                pass
        btc_now = cur.get("BTCUSDT")
        # pre-filter: only coins already past the absolute floor get a history pull (keeps it to ~50 calls)
        cands = [s_ for s_, v in cur.items() if v >= CROWD_LONG_FLOOR or v <= CROWD_SHORT_FLOOR]
        cands.sort(key=lambda s_: abs(cur[s_]), reverse=True)
        cands = cands[:60]
        sem = asyncio.Semaphore(8)

        async def hist(sym):
            async with sem:
                h = await _get_json(s, "https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": sym, "limit": 90}, 15)
                if not h or len(h) < 24:
                    return sym, None
                return sym, [float(r["fundingRate"]) * 100 for r in h]
        hists = dict(await asyncio.gather(*(hist(c) for c in cands)))

    longs, shorts = [], []
    for sym in cands:
        h = hists.get(sym)
        if not h:
            continue
        x = cur[sym]
        mean = sum(h) / len(h)
        var = sum((v - mean) ** 2 for v in h) / len(h)
        sd = var ** 0.5 or 1e-9
        z = (x - mean) / sd
        pct = _pct_rank(h, x)
        row = (sym[:-4], x, mean, z, pct)
        if x >= CROWD_LONG_FLOOR and z >= CROWD_Z:
            longs.append(row)
        elif x <= CROWD_SHORT_FLOOR and z <= -CROWD_Z:
            shorts.append(row)
    longs.sort(key=lambda r: r[3], reverse=True)
    shorts.sort(key=lambda r: r[3])

    def fmt(rows):
        return "\n".join(
            f"`{b:<7}` now **{x:+.4f}%**  \u00b7 30d avg {m:+.4f}%  \u00b7 z {z:+.1f}  \u00b7 {p:.0f}th pct  (\u2248 {x * 3 * 365:+.0f}% APR)"
            for b, x, m, z, p in rows[:8])

    e = discord.Embed(title="Crowded trades - funding vs each coin's own 30-day history", color=NAVY)
    e.description = (f"BTC funding now: **{btc_now:+.4f}%** per 8h.\n" if btc_now is not None else "") + \
        f"A coin only counts as crowded when funding is past an absolute floor (longs \u2265 {CROWD_LONG_FLOOR}% / shorts \u2264 {CROWD_SHORT_FLOOR}% per 8h) **and** at least {CROWD_Z}\u03c3 above its own 30-day average. Rank alone means nothing."
    e.add_field(name=f"Crowded longs - {len(longs)} coin(s) \u00b7 squeeze risk",
                value=(fmt(longs) if longs else "None right now. Longs aren't overpaying anywhere - that's a calm tape, not a bullish one."), inline=False)
    e.add_field(name=f"Crowded shorts - {len(shorts)} coin(s) \u00b7 short-squeeze fuel",
                value=(fmt(shorts) if shorts else "None right now."), inline=False)
    e.add_field(name="Read it right", value=(
        "z is how unusual today's funding is *for that coin* - a meme coin at +0.05% may be normal, BTC at +0.05% is not. "
        "Crowded long + price at resistance is where longs get flushed. Crowded short + price at support is where squeezes start. "
        "Funding alone is never the trade."), inline=False)
    e.set_footer(text=f"Sigma Terminal \u00b7 Binance perps \u00b7 {len(cands)} coins screened \u00b7 5-min cache \u00b7 educational")
    _CROWD_CACHE.update(ts=now, embed=e)
    await interaction.followup.send(embed=e)

@bot.tree.command(name="exitwatch", description="Bull-market heat gauge - structure, leverage, sentiment in one score. When to de-risk")
async def exitwatch_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    def sma(v, n):
        return sum(v[-n:]) / n if len(v) >= n else None
    async with http() as s:
        daily = await _get_json(s, "https://api.binance.com/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1d", "limit": 720}, 20)
        weekly = await _get_json(s, "https://api.binance.com/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1w", "limit": 210}, 20)
        fund = await _get_json(s, "https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": "BTCUSDT", "limit": 21}, 15)
        ethbtc = await _get_json(s, "https://api.binance.com/api/v3/klines", {"symbol": "ETHBTC", "interval": "1d", "limit": 31}, 15)
        fng = await _get_json(s, "https://api.alternative.me/fng/", {"limit": 1}, 15)
    if not daily or not weekly:
        await interaction.followup.send("Binance feed didn't answer - try again in a minute.")
        return
    closes = [float(c[4]) for c in daily]
    wcl = [float(c[4]) for c in weekly]
    price = closes[-1]
    parts = []      # (label, value text, score 0-2)
    mayer = price / sma(closes, 200) if sma(closes, 200) else None
    if mayer:
        parts.append(("Mayer multiple (price / 200d)", f"{mayer:.2f}", 2 if mayer >= 2.4 else 1 if mayer >= 1.8 else 0))
    ma111, ma350x2 = sma(closes, 111), (sma(closes, 350) * 2 if sma(closes, 350) else None)
    if ma111 and ma350x2:
        gap = (ma350x2 - ma111) / ma350x2 * 100
        parts.append(("Pi Cycle gap (111d vs 2\u00d7350d)", f"{gap:.1f}% apart" if gap > 0 else "CROSSED", 2 if gap <= 5 else 1 if gap <= 15 else 0))
    ma200w = sma(wcl, 200) or (sma(wcl, len(wcl)) if wcl else None)
    if ma200w:
        dev = (price / ma200w - 1) * 100
        parts.append(("Above 200-week MA", f"{dev:+.0f}%", 2 if dev >= 250 else 1 if dev >= 150 else 0))
    if fund:
        try:
            avg = sum(float(x["fundingRate"]) for x in fund) / len(fund) * 100
            parts.append(("BTC funding, 7d avg (per 8h)", f"{avg:+.4f}%", 2 if avg >= 0.05 else 1 if avg >= 0.03 else 0))
        except Exception:
            pass
    if ethbtc and len(ethbtc) > 30:
        ec = [float(c[4]) for c in ethbtc]
        eb = (ec[-1] / ec[-31] - 1) * 100
        parts.append(("ETH/BTC 30d (late-cycle rotation)", f"{eb:+.1f}%", 2 if eb >= 30 else 1 if eb >= 15 else 0))
    if fng and fng.get("data"):
        try:
            v = int(fng["data"][0]["value"])
            parts.append(("Fear & Greed", f"{v} ({fng['data'][0].get('value_classification', '')})", 2 if v >= 85 else 1 if v >= 75 else 0))
        except Exception:
            pass
    score = sum(p[2] for p in parts)
    mx = 2 * len(parts) or 1
    pct = score / mx * 100
    label, col = ("COOL", GREEN) if pct < 25 else ("WARMING", NAVY) if pct < 50 else ("HOT", discord.Color.orange()) if pct < 75 else ("EUPHORIC", RED)
    e = discord.Embed(title=f"Exit watch - heat {score}/{mx} \u00b7 {label}", color=col)
    e.description = ("\n".join(f"{'\U0001F534' if sc == 2 else '\U0001F7E0' if sc == 1 else '\u26aa'} **{lab}** \u2014 {val}" for lab, val, sc in parts))
    e.add_field(name="How to use this", value=(
        "This is a heat gauge, not a sell signal. COOL and WARMING is where trends live. HOT means size down and take profits on the plan, not on emotion. "
        "EUPHORIC has historically been where tops form over weeks, not days - the plan is to be scaling out, not calling the top. "
        "Every component is public data; check it yourself."), inline=False)
    e.set_footer(text="Sigma Terminal \u00b7 Binance + alternative.me \u00b7 educational, not financial advice")
    await interaction.followup.send(embed=e)

@bot.tree.command(name="brief", description="Today's market brief - BTC, funding, sentiment, calendar, in one read")
async def brief_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        text = await build_daily_brief()
    except Exception as ex:
        _note_error("cmd:/brief", ex)
        text = None
    if not text:
        await interaction.followup.send("Brief couldn't be built right now - a data source is down. Try `/snapshot BTC` meanwhile.")
        return
    e = discord.Embed(title=f"Market brief \u00b7 {datetime.now(IST).strftime('%d %b, %H:%M')} IST", description=text[:4000], color=NAVY)
    e.set_footer(text="Sigma Terminal \u00b7 educational, not financial advice")
    await interaction.followup.send(embed=e)

_TERMINAL_SOURCES = [
    ("Binance spot", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"),
    ("Binance futures", "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"),
    ("Bybit", "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"),
    ("OKX", "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP"),
    ("CoinGecko", "https://api.coingecko.com/api/v3/ping"),
    ("Fear & Greed", "https://api.alternative.me/fng/?limit=1"),
    ("Deribit", "https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd"),
    ("Coinbase", "https://api.exchange.coinbase.com/products/BTC-USD/ticker"),
    ("blockchain.info", "https://api.blockchain.info/charts/hash-rate?timespan=7days&format=json"),
    ("Yahoo (SPX/DXY)", "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1d&interval=1d"),
    ("DefiLlama stables", "https://stablecoins.llama.fi/stablecoins?includePrices=false"),
    ("mempool.space", "https://mempool.space/api/v1/fees/recommended"),
    ("er-api (FX)", "https://open.er-api.com/v6/latest/USD"),
]

# (moved to /admin - registered in sigma.commands_admin)
async def terminal_check_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    lines = []
    bad = 0
    async with aiohttp.ClientSession(headers={"User-Agent": "SigmaBot/1.0"}) as s:
        async def probe(name, url):
            t0 = _time.time()
            try:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    ms = int((_time.time() - t0) * 1000)
                    return name, r.status, ms
            except Exception as ex:
                return name, f"ERR {type(ex).__name__}", int((_time.time() - t0) * 1000)
        res = await asyncio.gather(*(probe(n, u) for n, u in _TERMINAL_SOURCES))
    for name, st, ms in res:
        ok = st == 200
        bad += (not ok)
        lines.append(f"{'\u2705' if ok else '\u274c'} `{name:<18}` {st}  {ms} ms")
    e = discord.Embed(title=f"Terminal data sources - {len(res) - bad}/{len(res)} healthy", color=(GREEN if not bad else RED))
    e.description = "\n".join(lines)
    if bad:
        e.add_field(name="What breaks", value="A red source means the commands that read it will fail or show stale data until it's back. Binance down = tracker, scanners, most terminal commands. The others each cover one or two commands.", inline=False)
    await interaction.followup.send(embed=e, ephemeral=True)

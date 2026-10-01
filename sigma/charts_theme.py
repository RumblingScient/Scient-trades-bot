"""sigma.charts_theme - auto-extracted from scient_trades_bot.py (behaviour unchanged)."""
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
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent   # repo root: .env, logs/, *.json live here
from sigma.config import EMA_COLORS, EMA_PERIODS, SG_ASH, SG_LONG, SG_OBS, SG_PAPER, SG_SHORT, SG_SLATE


_SIGMA_LOGO_IMG = None

def _sigma_logo():
    global _SIGMA_LOGO_IMG
    if _SIGMA_LOGO_IMG is None:
        try:
            import matplotlib.image as _mpimg
            p = _ROOT.joinpath("brand") / "sigma_badge.png"
            _SIGMA_LOGO_IMG = _mpimg.imread(str(p)) if p.exists() else False
        except Exception:
            _SIGMA_LOGO_IMG = False
    return _SIGMA_LOGO_IMG

def sigma_style_ax(ax):
    ax.set_facecolor(SG_OBS)
    ax.grid(color=SG_SLATE, linewidth=0.5, alpha=0.35)
    for sp in ax.spines.values():
        sp.set_color(SG_SLATE)
    ax.tick_params(colors=SG_ASH, labelsize=8)

def sigma_logo_ax(ax, zoom=0.085, pos=(0.985, 0.97)):
    img = _sigma_logo()
    if img is False or img is None:
        return
    try:
        from matplotlib.offsetbox import OffsetImage, AnnotationBbox
        ab = AnnotationBbox(OffsetImage(img, zoom=zoom), pos, xycoords="axes fraction",
                            frameon=False, box_alignment=(1, 1))
        ax.add_artist(ab)
    except Exception:
        pass

CHART_INTERVALS = {"15m": "15m", "1H": "1h", "4H": "4h", "1D": "1d", "1W": "1w"}

def make_chart_image(symbol: str, interval: str, klines: list) -> io.BytesIO:
    import pandas as pd
    import mplfinance as mpf
    df = pd.DataFrame(klines, columns=["t", "o", "h", "l", "c", "v", "ct", "qv", "n", "tb", "tq", "ig"])
    df["Date"] = pd.to_datetime(df["t"], unit="ms")
    df = df.set_index("Date")
    for col, name in (("o", "Open"), ("h", "High"), ("l", "Low"), ("c", "Close"), ("v", "Volume")):
        df[name] = df[col].astype(float)
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    addplots = []
    for period, color in zip(EMA_PERIODS, EMA_COLORS):
        if len(df) >= period:
            ema = df["Close"].ewm(span=period, adjust=False).mean()
            addplots.append(mpf.make_addplot(ema, color=color, width=1.3))
    mc = mpf.make_marketcolors(up=SG_LONG, down=SG_SHORT, edge="inherit", wick="inherit", volume={"up": SG_LONG + "55", "down": SG_SHORT + "55"})
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc, facecolor=SG_OBS, edgecolor=SG_SLATE, figcolor=SG_OBS, gridcolor=SG_SLATE, gridstyle="-", rc={"axes.labelcolor": SG_ASH, "xtick.color": SG_ASH, "ytick.color": SG_ASH, "font.size": 9})
    last = df["Close"].iloc[-1]
    price_txt = f"{last:,.2f}" if last >= 1000 else f"{last:,.4f}".rstrip("0").rstrip(".")
    fig, axes = mpf.plot(
        df, type="candle", style=style, volume=True,
        addplot=addplots if addplots else None,
        panel_ratios=(5, 1), figsize=(13, 7.5),
        scale_width_adjustment=dict(candle=1.5, volume=0.9),
        tight_layout=True, returnfig=True,
        hlines=dict(hlines=[float(last)], colors=[SG_ASH], linestyle="--", linewidths=0.8, alpha=0.6),
        ylabel="", ylabel_lower="",
    )
    for ax in axes:
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
    x0, x1 = axes[0].get_xlim()
    axes[0].set_xlim(x0, x1 + (x1 - x0) * 0.06)
    axes[0].set_title(f"{symbol}  {interval}  |  {price_txt}", color=SG_PAPER, fontsize=13, loc="left", pad=12)
    up = df["Close"].iloc[-1] >= df["Open"].iloc[-1]
    tag_color = SG_LONG if up else SG_SHORT
    axes[0].annotate(
        price_txt, xy=(1.0, float(last)), xycoords=("axes fraction", "data"),
        xytext=(4, 0), textcoords="offset points", ha="left", va="center",
        color=SG_OBS, fontsize=9, fontweight="bold", clip_on=False,
        annotation_clip=False, zorder=10,
        bbox=dict(boxstyle="round,pad=0.25", facecolor=tag_color, edgecolor="none"),
    )
    try:
        sigma_logo_ax(axes[0], zoom=0.075, pos=(0.985, 0.955))
    except Exception:
        pass
    buf = io.BytesIO()
    fig.savefig(buf, dpi=120, facecolor=SG_OBS, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    buf.seek(0)
    return buf

def _sigma_fonts():
    try:
        from matplotlib import font_manager
        fdir = _ROOT.joinpath("fonts")
        fams = {"disp": "DejaVu Sans", "mono": "DejaVu Sans Mono"}
        if fdir.exists():
            found = set()
            for f in fdir.glob("*.ttf"):
                try:
                    font_manager.fontManager.addfont(str(f))
                    for fe in font_manager.fontManager.ttflist:
                        if str(f) == fe.fname:
                            found.add(fe.name)
                except Exception:
                    continue
            for name in found:
                low = name.lower()
                if "grotesk" in low or "sigmadisplay" in low:
                    fams["disp"] = name
                if "jetbrains" in low or "sigmamono" in low:
                    fams["mono"] = name
                if "inter" in low or "sigmatext" in low:
                    fams["txt"] = name
        return fams
    except Exception:
        return {"disp": "DejaVu Sans", "mono": "DejaVu Sans Mono"}

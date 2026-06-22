"""Signal Validator — shows every raw indicator value, threshold, and score contribution
for any ticker so you can cross-check against TradingView / Yahoo Finance."""
from __future__ import annotations

import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="Signal Validator — R&S", page_icon="🔬", layout="wide")

st.markdown("""
<style>
  .stApp, [data-testid="stAppViewContainer"] { background-color: #07090f !important; }
  [data-testid="stHeader"] { background: #07090f !important; }
  [data-testid="stSidebar"] { background: #080e1a !important; border-right: 1px solid #1a3350; }
  .block-container { padding-top: 1.5rem; }
  body, p, span, div, label, li, td, th { color: #ccd6f6; }
  h1 { color: #00e5ff !important; font-family: monospace !important; letter-spacing: 2px; }
  h2, h3 { color: #00e5ff !important; font-family: monospace !important; }
  hr { border-color: #1a3350 !important; }
  code { background: #0c1420 !important; color: #00ffa3 !important; border: 1px solid #1a3350; border-radius: 4px; padding: 1px 6px; }
  [data-testid="stCaptionContainer"] { color: #4a6a8a !important; font-family: monospace !important; }
  [data-testid="stExpander"] { border: 1px solid #1a3350 !important; border-radius: 8px !important; background: #0c1420 !important; }
  [data-testid="stExpander"] summary { color: #ccd6f6 !important; font-family: monospace !important; }
  [data-testid="stDataFrameResizable"] { border: 1px solid #1a3350 !important; border-radius: 8px !important; }
</style>
""", unsafe_allow_html=True)

st.title("SIGNAL VALIDATOR")
st.markdown('<p style="font-family:monospace;font-size:11px;color:#4a6a8a;margin-top:-12px">'
            'RAW INDICATOR VALUES · THRESHOLDS · SCORE BREAKDOWN</p>', unsafe_allow_html=True)

# ── Guard: need screener data ─────────────────────────────────────────────────
if "raw_data" not in st.session_state:
    st.info("Run the screener first (click ▶ Run Screener on the main app), then come back here.")
    st.stop()

from screener import TIMEFRAMES, bollinger
from screener.indicators import rsi as _rsi, macd as _macd, stochastic as _stoch, atr as _atr, obv as _obv

data      = st.session_state["raw_data"]
close_all = data["close"]
high_all  = data["high"]
low_all   = data["low"]
vol_all   = data["volume"]

# ── Selectors ─────────────────────────────────────────────────────────────────
c1, c2 = st.columns([1, 2])
tf_key = c1.selectbox("Timeframe", ["30d", "180d", "1y"],
                      format_func=lambda k: TIMEFRAMES[k]["label"])
cfg    = TIMEFRAMES[tf_key]

valid_tickers = sorted([
    t for t in close_all.columns
    if t not in ("SPY", "^VIX") and len(close_all[t].dropna()) >= cfg["min_data_days"]
])
ticker = c2.selectbox("Ticker", valid_tickers)

st.divider()

if not ticker:
    st.stop()

close  = close_all[ticker].dropna()
high   = high_all[ticker].dropna()
low    = low_all[ticker].dropna()
volume = vol_all[ticker].dropna()
price  = float(close.iloc[-1])

# ── 1. Raw OHLCV ─────────────────────────────────────────────────────────────
with st.expander("📊 Raw price data (last 10 bars)", expanded=False):
    ohlcv = pd.DataFrame({
        "Close":  close,
        "High":   high,
        "Low":    low,
        "Volume": volume,
    }).tail(10).sort_index(ascending=False)
    st.dataframe(ohlcv.style.format({
        "Close": "${:.4f}", "High": "${:.4f}", "Low": "${:.4f}",
        "Volume": "{:,.0f}",
    }), use_container_width=True)

# ── 2. Compute all indicators ─────────────────────────────────────────────────
# RSI
rsi_s       = _rsi(close, 14)
rsi_val     = float(rsi_s.iloc[-1])

# MACD
macd_line, sig_line = _macd(close)
hist        = macd_line - sig_line
hist_val    = float(hist.iloc[-1])
hist_prev   = float(hist.iloc[-2]) if len(hist) > 1 else 0.0
macd_val    = float(macd_line.iloc[-1])
sig_val     = float(sig_line.iloc[-1])
# Find days since last bullish cross
cross_days  = None
for i in range(1, min(20, len(hist))):
    if hist.iloc[-i] > 0 and hist.iloc[-i - 1] <= 0:
        cross_days = i
        break

# Stochastic
stoch_k, stoch_d = _stoch(high, low, close, 14)
k_val  = float(stoch_k.iloc[-1])
d_val  = float(stoch_d.iloc[-1])
k_prev = float(stoch_k.iloc[-2]) if len(stoch_k) > 1 else k_val

# SMAs
sma_f_s = close.rolling(cfg["sma_fast"]).mean()
sma_s_s = close.rolling(cfg["sma_slow"]).mean()
sma_f   = float(sma_f_s.iloc[-1])
sma_s   = float(sma_s_s.iloc[-1])

# Bollinger
bb_up_s, bb_mid_s, bb_lo_s = bollinger(close, cfg["bb_period"])
bb_up  = float(bb_up_s.iloc[-1])
bb_mid = float(bb_mid_s.iloc[-1])
bb_lo  = float(bb_lo_s.iloc[-1])
bb_pct = (price - bb_lo) / (bb_up - bb_lo) * 100 if bb_up != bb_lo else 50.0

# Volume
vol_rec = float(volume.iloc[-cfg["vol_recent"]:].mean())
vol_avg = float(volume.rolling(cfg["vol_avg"]).mean().iloc[-1])
vol_ratio = vol_rec / vol_avg if vol_avg > 0 else 1.0

# OBV
obv_s   = _obv(close, volume)
obv_now = float(obv_s.iloc[-1])
obv_old = float(obv_s.iloc[-cfg["obv_window"]]) if len(obv_s) > cfg["obv_window"] else obv_now
obv_price_old = float(close.iloc[-cfg["obv_window"]]) if len(close) > cfg["obv_window"] else price
obv_rising    = obv_now > obv_old
obv_diverge   = obv_rising and price <= obv_price_old  # price flat/down, OBV rising

# ATR
atr_s   = _atr(high, low, close, 14)
atr_val = float(atr_s.iloc[-1])
atr_mom = atr_val / price * 100  # as % of price

# RS vs SPY
spy_close  = close_all["SPY"].dropna()
rs_n       = cfg["rs_days"]
price_ret  = float(close.iloc[-1] / close.iloc[-rs_n] - 1) if len(close) >= rs_n + 1 else 0.0
spy_ret    = float(spy_close.iloc[-1] / spy_close.iloc[-rs_n] - 1) if len(spy_close) >= rs_n + 1 else 0.0
rs_diff    = price_ret - spy_ret

# VIX & Breadth
vix_key     = f"vix_{tf_key}"
breadth_key = f"breadth_{tf_key}"
vix_val     = st.session_state.get(vix_key, 20.0)
breadth_pct = st.session_state.get(breadth_key, 50.0)

# Golden cross (SMA50 vs SMA200)
sma50_s  = close.rolling(50).mean()
sma200_s = close.rolling(200).mean()
golden   = float(sma50_s.iloc[-1]) > float(sma200_s.iloc[-1]) if len(close) >= 200 else False
gc_lbk   = cfg.get("golden_cross_lookback")
golden_recent = False
if gc_lbk and len(sma50_s) > gc_lbk:
    for i in range(1, gc_lbk + 1):
        if sma50_s.iloc[-i] > sma200_s.iloc[-i] and sma50_s.iloc[-i - 1] <= sma200_s.iloc[-i - 1]:
            golden_recent = True
            break

# ── 3. Signal table ───────────────────────────────────────────────────────────
def _check(condition: bool, pts: int) -> tuple[str, int]:
    return ("✅", pts) if condition else ("—", 0)

rsi_lo, rsi_hi = cfg["rsi_recover"]
rows = []

# RSI recovery
fired_rsi = rsi_lo <= rsi_val <= rsi_hi
icon, pts = _check(fired_rsi, cfg["score_rsi_recover"])
rows.append(["RSI Recovery", f"{rsi_val:.1f}",
             f"{rsi_lo}–{rsi_hi} (recovery zone)", icon, pts,
             "RSI in recovery = momentum building without being overbought"])

# Stochastic crossover from oversold
fired_stoch = k_val > d_val and k_prev <= d_val and k_val < cfg["stoch_caution"] * 1.5
icon, pts = _check(fired_stoch, cfg["score_stoch_cross"])
rows.append(["Stoch %K crossover", f"K={k_val:.1f}  D={d_val:.1f}",
             f"K crosses above D from <{cfg['stoch_oversold']} zone", icon, pts,
             "Stochastic bullish crossover from oversold territory"])

# MACD fresh cross
fired_macd_fresh = cross_days is not None and cross_days <= 5
icon, pts = _check(fired_macd_fresh, cfg["score_macd_cross"])
rows.append(["MACD fresh cross", f"hist={hist_val:+.4f}  ({cross_days or 'no'} days ago)",
             "Histogram crosses above 0 within last 5 bars", icon, pts,
             "Fresh MACD bullish crossover — strongest short-term signal"])

# MACD histogram positive
fired_macd_pos = hist_val > 0
icon, pts = _check(fired_macd_pos, cfg["score_macd_positive"])
rows.append(["MACD positive", f"MACD={macd_val:.4f}  Signal={sig_val:.4f}",
             "Histogram > 0", icon, pts,
             "MACD line above signal — sustained bullish momentum"])

# SMA alignment
fired_sma = price > sma_f > sma_s
icon, pts = _check(fired_sma, cfg["score_sma_aligned"])
rows.append(["SMA aligned", f"Price=${price:.2f}  SMA{cfg['sma_fast']}=${sma_f:.2f}  SMA{cfg['sma_slow']}=${sma_s:.2f}",
             f"Price > SMA{cfg['sma_fast']} > SMA{cfg['sma_slow']}", icon, pts,
             "Price above both SMAs in correct order = uptrend confirmed"])

# Bollinger below
fired_bb_below = price < bb_lo
icon, pts = _check(fired_bb_below, cfg["score_bb_below"])
rows.append(["BB below lower", f"Price=${price:.2f}  Lower=${bb_lo:.2f}  (BB%={bb_pct:.0f}%)",
             "Price < lower Bollinger Band", icon, pts,
             "Price stretched below lower band = mean-reversion setup"])

# Bollinger near lower
fired_bb_near = not fired_bb_below and bb_pct < 25
icon, pts = _check(fired_bb_near, cfg["score_bb_near"])
rows.append(["BB near lower", f"BB% position: {bb_pct:.0f}%",
             "BB% < 25% (near lower band)", icon, pts,
             "Price approaching lower band — weaker version of above"])

# OBV divergence
icon, pts = _check(obv_diverge, cfg["score_obv_divergence"])
rows.append(["OBV divergence", f"OBV trend: {'↑' if obv_rising else '↓'}  Price trend: {'↑' if price > obv_price_old else '↓'}",
             f"OBV rising while price flat/down ({cfg['obv_window']}-bar window)", icon, pts,
             "Smart money accumulating while price lags — bullish divergence"])

# OBV rising
icon, pts = _check(obv_rising and not obv_diverge, cfg["score_obv_rising"])
rows.append(["OBV rising", f"OBV now={obv_now:,.0f}  OBV {cfg['obv_window']}d ago={obv_old:,.0f}",
             "OBV higher than N bars ago", icon, pts,
             "Volume-confirmed accumulation"])

# Volume surge
fired_vol_surge = vol_ratio >= 1.5
icon, pts = _check(fired_vol_surge, cfg["score_vol_surge"])
rows.append(["Volume surge", f"{vol_ratio:.2f}× ({cfg['vol_recent']}-day avg vs {cfg['vol_avg']}-day avg)",
             "≥ 1.5× average volume", icon, pts,
             "Above-average volume confirms institutional interest"])

# Volume above avg
fired_vol_above = 1.1 <= vol_ratio < 1.5
icon, pts = _check(fired_vol_above, cfg["score_vol_above"])
rows.append(["Volume above avg", f"{vol_ratio:.2f}×",
             "1.1×–1.5× average volume", icon, pts,
             "Modest above-average volume — partial signal"])

# ATR momentum
lo_atr, hi_atr = cfg["mom_atr_range"]
fired_atr = lo_atr <= atr_mom <= hi_atr
icon, pts = _check(fired_atr, cfg["score_momentum"])
rows.append(["ATR momentum", f"ATR=${atr_val:.2f}  ({atr_mom:.2f}% of price)",
             f"{lo_atr}%–{hi_atr}% of price", icon, pts,
             "ATR in healthy range — not too quiet, not too volatile"])

# RS vs SPY
if rs_diff >= 0.05:
    rs_label, rs_pts = "Leader", cfg["score_rs_leader"]
elif rs_diff >= 0.02:
    rs_label, rs_pts = "Outperform", cfg["score_rs_outperform"]
elif rs_diff < -0.05:
    rs_label, rs_pts = "Laggard", -10
else:
    rs_label, rs_pts = "Neutral", 0
icon = "✅" if rs_pts > 0 else "❌" if rs_pts < 0 else "—"
rows.append(["RS vs SPY", f"{ticker}: {price_ret*100:+.1f}%  SPY: {spy_ret*100:+.1f}%  Diff: {rs_diff*100:+.1f}%",
             f"Leader ≥+5%, Outperform ≥+2%, Laggard <-5% ({rs_n}-day)", icon, rs_pts,
             f"Relative strength vs S&P 500 — {rs_label}"])

# VIX impact
if vix_val > 35:
    vix_pts = -10; vix_label = "Panic"
elif vix_val > 25:
    vix_pts = 0; vix_label = "Fear"
elif vix_val < 15:
    vix_pts = -5; vix_label = "Complacent"
else:
    vix_pts = 0; vix_label = "Normal"
icon = "❌" if vix_pts < 0 else "—"
rows.append(["VIX", f"{vix_val:.1f} ({vix_label})",
             "Panic(>35): −10  Complacent(<15): −5", icon, vix_pts,
             "Market fear gauge — extreme readings penalise scores"])

# Breadth
if breadth_pct > 65:
    brd_pts = 10; brd_label = "Strong"
elif breadth_pct < 40:
    brd_pts = -8; brd_label = "Weak"
else:
    brd_pts = 0; brd_label = "Moderate"
icon = "✅" if brd_pts > 0 else "❌" if brd_pts < 0 else "—"
rows.append(["Breadth", f"{breadth_pct:.0f}% above SMA50 ({brd_label})",
             "Strong(>65%): +10  Weak(<40%): −8", icon, brd_pts,
             "% of S&P 500 stocks above their 50-day SMA"])

# Golden cross
if gc_lbk:
    icon, pts = _check(golden_recent, cfg["score_golden_cross"])
    rows.append(["Golden cross", f"SMA50={float(sma50_s.iloc[-1]):.2f}  SMA200={float(sma200_s.iloc[-1]):.2f}  "
                 f"Currently {'above' if golden else 'below'}",
                 f"SMA50 crossed above SMA200 in last {gc_lbk} bars", icon, pts,
                 "Long-term bullish signal — 50-day crosses above 200-day"])

# ── 4. Display ────────────────────────────────────────────────────────────────
val_df = pd.DataFrame(rows, columns=["Signal", "Raw Value", "Threshold", "Fired", "Points", "Explanation"])

total_scored   = val_df[val_df["Points"] > 0]["Points"].sum()
total_penalised = val_df[val_df["Points"] < 0]["Points"].sum()
total_score    = total_scored + total_penalised

# Summary metrics
mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("Ticker", ticker)
mc2.metric("Current Price", f"${price:.4f}")
mc3.metric("Calculated Score", int(total_score))
# Compare to screener result if available
screener_score = None
df_key = f"df_{tf_key}"
if df_key in st.session_state:
    df_sc = st.session_state[df_key]
    match = df_sc[df_sc["ticker"] == ticker]
    if not match.empty:
        screener_score = int(match.iloc[0]["score"])
mc4.metric("Screener Score", screener_score if screener_score is not None else "—",
           delta=f"{total_score - screener_score:+d} diff" if screener_score is not None else None)

st.caption("Validator score vs screener score may differ slightly due to regime multipliers and rounding applied in the full scoring engine.")

st.divider()

# Signal table
st.markdown("### Signal Breakdown")
st.dataframe(
    val_df.drop(columns=["Explanation"]),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Points": st.column_config.NumberColumn("Points", format="%+d"),
        "Fired":  st.column_config.TextColumn("Fired", width="small"),
    },
)

# Explanation expander
with st.expander("ℹ️ What each signal means"):
    for _, row in val_df.iterrows():
        st.markdown(f"**{row['Signal']}** — {row['Explanation']}")

st.divider()

# Score bar
st.markdown("### Score composition")
fired_rows   = val_df[val_df["Points"] > 0].sort_values("Points", ascending=False)
penalty_rows = val_df[val_df["Points"] < 0]

col_f, col_p = st.columns(2)
with col_f:
    st.markdown(f"**Signals fired** — +{int(total_scored)} pts")
    for _, r in fired_rows.iterrows():
        bar_w = int(r["Points"] / max(fired_rows["Points"]) * 100)
        st.markdown(
            f"""<div style="margin:3px 0">
            <span style="font-family:monospace;font-size:12px;color:#ccd6f6;display:inline-block;width:160px">{r['Signal']}</span>
            <span style="display:inline-block;background:#00ffa3;height:10px;width:{bar_w}%;border-radius:3px;vertical-align:middle"></span>
            <span style="font-family:monospace;font-size:12px;color:#00ffa3;margin-left:6px">+{int(r['Points'])}</span>
            </div>""",
            unsafe_allow_html=True,
        )
with col_p:
    st.markdown(f"**Penalties** — {int(total_penalised)} pts")
    if penalty_rows.empty:
        st.caption("No penalties")
    for _, r in penalty_rows.iterrows():
        st.markdown(
            f"""<div style="margin:3px 0">
            <span style="font-family:monospace;font-size:12px;color:#ccd6f6;display:inline-block;width:160px">{r['Signal']}</span>
            <span style="font-family:monospace;font-size:12px;color:#ff2d5b">{int(r['Points'])}</span>
            </div>""",
            unsafe_allow_html=True,
        )

st.divider()

# Raw indicator quick-reference
with st.expander("🔢 All raw indicator values (quick reference for TradingView cross-check)"):
    ref = {
        "RSI (14)": f"{rsi_val:.2f}",
        f"MACD Line (12/26/9)": f"{macd_val:.5f}",
        "MACD Signal": f"{sig_val:.5f}",
        "MACD Histogram": f"{hist_val:+.5f}",
        f"Stoch %K (14)": f"{k_val:.2f}",
        f"Stoch %D (3)": f"{d_val:.2f}",
        f"SMA {cfg['sma_fast']}": f"${sma_f:.4f}",
        f"SMA {cfg['sma_slow']}": f"${sma_s:.4f}",
        "SMA 50": f"${float(sma50_s.iloc[-1]):.4f}",
        "SMA 200": f"${float(sma200_s.iloc[-1]):.4f}",
        f"BB Upper ({cfg['bb_period']})": f"${bb_up:.4f}",
        f"BB Middle ({cfg['bb_period']})": f"${bb_mid:.4f}",
        f"BB Lower ({cfg['bb_period']})": f"${bb_lo:.4f}",
        "BB% Position": f"{bb_pct:.1f}%",
        "ATR (14)": f"${atr_val:.4f}",
        f"Volume ratio ({cfg['vol_recent']}d / {cfg['vol_avg']}d)": f"{vol_ratio:.3f}×",
        f"RS vs SPY ({rs_n}d)": f"{rs_diff*100:+.2f}%",
        "VIX": f"{vix_val:.1f}",
        "Market Breadth": f"{breadth_pct:.1f}%",
        "Golden cross active": str(golden),
    }
    ref_df = pd.DataFrame(list(ref.items()), columns=["Indicator", "Value"])
    st.dataframe(ref_df, use_container_width=True, hide_index=True)

st.caption("🔬 R&S Signal Validator — formulas match screener/indicators.py exactly")

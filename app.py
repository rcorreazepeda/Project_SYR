import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Inject Streamlit Cloud secrets into os.environ so all modules work unchanged
for _k in ("FMP_API_KEY",):
    if _k not in os.environ:
        try:
            os.environ[_k] = st.secrets.get(_k, "")
        except Exception:
            pass

from screener import (
    get_sp500_tickers,
    score_ticker,
    compute_targets,
    check_earnings_proximity,
    bollinger,
    TIMEFRAMES,
    DOWNLOAD_LOOKBACK,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="R&S Stock Plan",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 700; }
    .block-container { padding-top: 2rem; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def download_data(tickers: tuple) -> dict:
    raw = yf.download(
        list(tickers),
        period=DOWNLOAD_LOOKBACK,
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    return {
        "close":  raw["Close"],
        "high":   raw["High"],
        "low":    raw["Low"],
        "volume": raw["Volume"],
    }


def run_for_timeframe(
    tf_key: str,
    data: dict,
    tickers: list[str],
    top_n: int,
) -> tuple[pd.DataFrame, bool, float]:
    cfg = TIMEFRAMES[tf_key]
    close_all  = data["close"]
    high_all   = data["high"]
    low_all    = data["low"]
    vol_all    = data["volume"]

    spy_close     = close_all["SPY"].dropna()
    in_bull       = bool(spy_close.iloc[-1] > spy_close.rolling(50).mean().iloc[-1])
    rs_n          = cfg["rs_days"]
    spy_return    = float(spy_close.iloc[-1] / spy_close.iloc[-rs_n] - 1) if len(spy_close) >= rs_n + 1 else 0.0

    results = []
    for ticker in tickers:
        if ticker not in close_all.columns:
            continue
        close  = close_all[ticker].dropna()
        high   = high_all[ticker].dropna()
        low    = low_all[ticker].dropna()
        volume = vol_all[ticker].dropna()
        if len(close) < cfg["min_data_days"]:
            continue
        try:
            s, signals, meta = score_ticker(close, high, low, volume, spy_return, in_bull, cfg)
            results.append({"ticker": ticker, "score": s, "signals": signals, **meta})
        except Exception:
            continue

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    df.index += 1
    df = compute_targets(df, close_all, cfg)

    top_candidates = df.head(top_n * 2)["ticker"].tolist()
    near_earnings  = check_earnings_proximity(top_candidates)
    df["earnings_soon"] = df["ticker"].isin(near_earnings)

    return df, in_bull, spy_return


# ---------------------------------------------------------------------------
# Tab renderer
# ---------------------------------------------------------------------------

def render_tab(tf_key: str, data: dict, tickers: list[str],
               top_n: int, min_score: int) -> None:
    cfg = TIMEFRAMES[tf_key]

    key_df   = f"df_{tf_key}"
    key_bull = f"bull_{tf_key}"
    key_spy  = f"spy_{tf_key}"

    if key_df not in st.session_state:
        with st.spinner(f"Scoring {len(tickers)} stocks for {cfg['label']} view…"):
            df, in_bull, spy_ret = run_for_timeframe(tf_key, data, tickers, top_n)
        st.session_state[key_df]   = df
        st.session_state[key_bull] = in_bull
        st.session_state[key_spy]  = spy_ret

    df      = st.session_state[key_df]
    in_bull = st.session_state[key_bull]
    spy_ret = st.session_state[key_spy]

    # --- Regime row ---
    icon  = "🟢" if in_bull else "🔴"
    label = "BULL" if in_bull else "BEAR"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Market Regime", f"{icon} {label}",
              "SPY above 50-SMA" if in_bull else "SPY below 50-SMA")
    c2.metric(f"SPY {cfg['rs_days']}d Return", f"{spy_ret * 100:+.1f}%")
    c3.metric("Stocks Scored", str(len(df)))
    c4.metric("Hold Period", f"{cfg['hold_days']} trading days",
              f"TP +{cfg['take_profit_pct']}% / SL -{cfg['stop_loss_pct']}%")

    if not in_bull:
        st.warning("⚠ Bear market — mean-reversion scores halved. Size positions down.")

    st.divider()

    # --- Results table ---
    filtered = df[df["score"] >= min_score].head(top_n).copy()
    filtered["Top Signals"] = filtered["signals"].apply(lambda s: "  ·  ".join(s[:3]))
    filtered["Earnings"]    = filtered["earnings_soon"].apply(lambda x: "⚠ Soon" if x else "—")
    sf, ss = cfg["sma_fast"], cfg["sma_slow"]

    table = filtered.rename(columns={
        "ticker":           "Ticker",
        "score":            "Score",
        "price":            "Entry Price",
        "expected_price":   f"Expected ({cfg['hold_days']}d)",
        "expected_return_%": "Exp. Return",
        "RSI":              "RSI",
        "stoch_k":          "Stoch %K",
        "vol_ratio":        "Vol ×",
        "atr":              "ATR",
    })[["Ticker", "Score", "Entry Price", f"Expected ({cfg['hold_days']}d)",
        "Exp. Return", "RSI", "Stoch %K", "Vol ×", "Earnings", "Top Signals"]]

    st.subheader(f"Top {len(filtered)} setups — {cfg['label']} — {datetime.now().strftime('%Y-%m-%d')}")
    st.dataframe(
        table,
        use_container_width=True,
        column_config={
            "Score":                       st.column_config.ProgressColumn("Score", min_value=0, max_value=120, format="%d"),
            "Entry Price":                 st.column_config.NumberColumn("Entry Price", format="$%.2f"),
            f"Expected ({cfg['hold_days']}d)": st.column_config.NumberColumn(f"Expected ({cfg['hold_days']}d)", format="$%.2f"),
            "Exp. Return":                 st.column_config.NumberColumn("Exp. Return", format="+%.2f%%"),
            "RSI":                         st.column_config.NumberColumn("RSI", format="%.1f"),
            "Stoch %K":                    st.column_config.NumberColumn("Stoch %K", format="%.1f"),
            "Vol ×":                       st.column_config.NumberColumn("Vol ×", format="%.2f×"),
            "ATR":                         st.column_config.NumberColumn("ATR", format="$%.2f"),
        },
    )

    # --- Exit rules reminder ---
    with st.expander("Exit rules for this timeframe"):
        st.markdown(f"""
| Type | Rule |
|------|------|
| **Take profit** | +{cfg['take_profit_pct']}% from entry price |
| **Stop loss** | -{cfg['stop_loss_pct']}% from entry price |
| **Time stop** | Exit after {cfg['hold_days']} trading days regardless |
| **Earnings** | Exit before any scheduled earnings announcement |
        """)

    # --- Signal breakdown ---
    with st.expander("Full signal breakdown"):
        for _, row in filtered.iterrows():
            earn = "  ⚠ Earnings soon" if row["earnings_soon"] else ""
            st.markdown(f"**{row['ticker']}** &nbsp; Score {row['score']}{earn}")
            for sig in row["signals"]:
                st.markdown(f"&emsp;• {sig}")
            st.markdown("---")

    # --- Price chart ---
    st.subheader("Price Chart")
    chart_ticker = st.selectbox("Select ticker", options=filtered["ticker"].tolist(),
                                key=f"chart_{tf_key}")

    close_all = data["close"]
    vol_all   = data["volume"]

    if chart_ticker and chart_ticker in close_all.columns:
        close  = close_all[chart_ticker].dropna()
        volume = vol_all[chart_ticker].dropna()

        sma_f  = close.rolling(sf).mean()
        sma_s  = close.rolling(ss).mean()
        bb_up, _, bb_lo = bollinger(close, period=cfg["bb_period"])

        fig = make_subplots(rows=2, cols=1, row_heights=[0.75, 0.25],
                            shared_xaxes=True, vertical_spacing=0.04)

        fig.add_trace(go.Scatter(
            x=list(close.index) + list(close.index[::-1]),
            y=list(bb_up) + list(bb_lo[::-1]),
            fill="toself", fillcolor="rgba(100,149,237,0.10)",
            line=dict(width=0), name=f"BB{cfg['bb_period']}", hoverinfo="skip",
        ), row=1, col=1)
        fig.add_trace(go.Scatter(x=close.index, y=close,
                                 line=dict(color="#00b4d8", width=2), name="Close"), row=1, col=1)
        fig.add_trace(go.Scatter(x=sma_f.index, y=sma_f,
                                 line=dict(color="#f77f00", width=1.5, dash="dot"),
                                 name=f"SMA{sf}"), row=1, col=1)
        fig.add_trace(go.Scatter(x=sma_s.index, y=sma_s,
                                 line=dict(color="#e63946", width=1.5, dash="dot"),
                                 name=f"SMA{ss}"), row=1, col=1)

        bar_colors = ["#26a69a" if c >= o else "#ef5350"
                      for c, o in zip(close, close.shift(1).fillna(close))]
        vol_avg = volume.rolling(cfg["vol_avg"]).mean()
        fig.add_trace(go.Bar(x=volume.index, y=volume, marker_color=bar_colors,
                             name="Volume", opacity=0.7), row=2, col=1)
        fig.add_trace(go.Scatter(x=vol_avg.index, y=vol_avg,
                                 line=dict(color="#ffd166", width=1.5),
                                 name=f"Vol avg ({cfg['vol_avg']}d)"), row=2, col=1)

        fig.update_layout(
            height=520,
            title=dict(text=f"{chart_ticker} — {cfg['label']} view", x=0.01),
            template="plotly_dark",
            legend=dict(orientation="h", y=1.06, x=0),
            margin=dict(l=0, r=0, t=50, b=0),
            hovermode="x unified",
            xaxis_rangeslider_visible=False,
        )
        fig.update_yaxes(title_text="Price (USD)", row=1, col=1)
        fig.update_yaxes(title_text="Volume",      row=2, col=1)
        st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("📈 S&P 500 Screener")
    st.caption("Multi-timeframe momentum screener")
    st.divider()
    run_btn   = st.button("▶  Run Screener", type="primary", use_container_width=True)
    st.divider()
    top_n     = st.slider("Top N results", 5, 25, 10)
    min_score = st.slider("Min score filter", 0, 100, 0)
    st.divider()
    if "last_run" in st.session_state:
        st.caption(f"Last run: {st.session_state['last_run']}")
    st.caption("Data cached 1 hour after first run.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

st.title("R&S Stock Plan")
st.caption("Ranks every S&P 500 stock by statistical setup quality across three holding horizons.")

if run_btn:
    # Clear cached results so all tabs re-score with fresh settings
    for key in ["df_5d", "df_30d", "df_180d",
                "bull_5d", "bull_30d", "bull_180d",
                "spy_5d",  "spy_30d",  "spy_180d",
                "tickers", "raw_data"]:
        st.session_state.pop(key, None)
    st.session_state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")

if "raw_data" not in st.session_state:
    if not run_btn:
        st.info("👈 Click **Run Screener** in the sidebar to start. ~2–3 min first run, cached after that.")
        st.stop()

    tickers = get_sp500_tickers()
    download_list = tuple(["SPY"] + tickers)
    with st.spinner("Downloading 2 years of price data for 500+ stocks… (cached after first run)"):
        raw = download_data(download_list)

    if raw["close"].empty:
        st.error("Download returned no data. Check your internet connection.")
        st.stop()

    st.session_state["raw_data"] = raw
    st.session_state["tickers"]  = tickers

raw_data = st.session_state["raw_data"]
tickers  = st.session_state["tickers"]

tab1, tab2, tab3 = st.tabs(["📅 5-Day Trading", "📆 30-Day Trading", "📈 180-Day Trading"])

with tab1:
    render_tab("5d",  raw_data, tickers, top_n, min_score)

with tab2:
    render_tab("30d", raw_data, tickers, top_n, min_score)

with tab3:
    render_tab("180d", raw_data, tickers, top_n, min_score)

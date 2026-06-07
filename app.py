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
for _k in ("FMP_API_KEY", "ANTHROPIC_API_KEY"):
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
    fetch_recent_news,
    classify_news,
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
) -> tuple[pd.DataFrame, bool, float, float, float]:
    cfg = TIMEFRAMES[tf_key]
    close_all  = data["close"]
    high_all   = data["high"]
    low_all    = data["low"]
    vol_all    = data["volume"]

    spy_close  = close_all["SPY"].dropna()
    in_bull    = bool(spy_close.iloc[-1] > spy_close.rolling(50).mean().iloc[-1])
    rs_n       = cfg["rs_days"]
    spy_return = float(spy_close.iloc[-1] / spy_close.iloc[-rs_n] - 1) if len(spy_close) >= rs_n + 1 else 0.0

    # VIX
    vix_series = close_all["^VIX"].dropna() if "^VIX" in close_all.columns else pd.Series(dtype=float)
    vix_val    = float(vix_series.iloc[-1]) if len(vix_series) > 0 else 20.0

    # Breadth: % of S&P 500 tickers above their 50-day SMA
    valid_tickers  = [t for t in tickers if t in close_all.columns]
    sma50_last     = close_all[valid_tickers].rolling(50).mean().iloc[-1]
    above_sma50    = (close_all[valid_tickers].iloc[-1] > sma50_last).sum()
    breadth_pct    = round(above_sma50 / len(valid_tickers) * 100, 1) if valid_tickers else 50.0

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
            s, signals, meta = score_ticker(
                close, high, low, volume, spy_return, in_bull, cfg, vix_val, breadth_pct
            )
            results.append({"ticker": ticker, "score": s, "signals": signals, **meta})
        except Exception:
            continue

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    df.index += 1
    df = compute_targets(df, close_all, cfg)

    top_candidates = df.head(top_n * 2)["ticker"].tolist()
    near_earnings  = check_earnings_proximity(top_candidates)
    df["earnings_soon"] = df["ticker"].isin(near_earnings)

    return df, in_bull, spy_return, vix_val, breadth_pct


# ---------------------------------------------------------------------------
# Tab renderer
# ---------------------------------------------------------------------------

def render_tab(tf_key: str, data: dict, tickers: list[str],
               top_n: int, min_score: int, tax_rate: int = 0) -> None:
    cfg = TIMEFRAMES[tf_key]

    key_df   = f"df_{tf_key}"
    key_bull = f"bull_{tf_key}"
    key_spy  = f"spy_{tf_key}"

    key_vix     = f"vix_{tf_key}"
    key_breadth = f"breadth_{tf_key}"

    if key_df not in st.session_state:
        with st.spinner(f"Scoring {len(tickers)} stocks for {cfg['label']} view…"):
            df, in_bull, spy_ret, vix_val, breadth_pct = run_for_timeframe(tf_key, data, tickers, top_n)
        st.session_state[key_df]      = df
        st.session_state[key_bull]    = in_bull
        st.session_state[key_spy]     = spy_ret
        st.session_state[key_vix]     = vix_val
        st.session_state[key_breadth] = breadth_pct

    df          = st.session_state[key_df]
    in_bull     = st.session_state[key_bull]
    spy_ret     = st.session_state[key_spy]
    vix_val     = st.session_state.get(key_vix, 20.0)
    breadth_pct = st.session_state.get(key_breadth, 50.0)

    # --- Regime row ---
    regime_icon  = "🟢" if in_bull else "🔴"
    regime_label = "BULL" if in_bull else "BEAR"

    if vix_val > 35:
        vix_icon, vix_label = "🔴", "Panic"
    elif vix_val > 25:
        vix_icon, vix_label = "🟠", "Fear"
    elif vix_val < 15:
        vix_icon, vix_label = "🟡", "Complacent"
    else:
        vix_icon, vix_label = "🟢", "Normal"

    if breadth_pct > 65:
        breadth_icon, breadth_label = "🟢", "Strong"
    elif breadth_pct < 40:
        breadth_icon, breadth_label = "🔴", "Weak"
    else:
        breadth_icon, breadth_label = "🟡", "Moderate"

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Market Regime", f"{regime_icon} {regime_label}",
              "SPY above 50-SMA" if in_bull else "SPY below 50-SMA")
    c2.metric(f"SPY {cfg['rs_days']}d Return", f"{spy_ret * 100:+.1f}%")
    c3.metric("VIX", f"{vix_icon} {vix_val:.1f}", vix_label)
    c4.metric("Breadth", f"{breadth_icon} {breadth_pct:.0f}%", f"{breadth_label} — % above SMA50")
    c5.metric("Stocks Scored", str(len(df)))
    c6.metric("Hold Period", f"{cfg['hold_days']} trading days",
              f"TP +{cfg['take_profit_pct']}% / SL -{cfg['stop_loss_pct']}%")

    if not in_bull:
        st.warning("⚠ Bear market — mean-reversion scores halved. Size positions down.")

    st.divider()

    # --- Results table ---
    filtered = df[df["score"] >= min_score].head(top_n).copy()
    filtered["Top Signals"] = filtered["signals"].apply(lambda s: "  ·  ".join(s[:3]))
    filtered["Earnings"]    = filtered["earnings_soon"].apply(lambda x: "⚠ Soon" if x else "—")
    filtered["After-Tax Return"] = filtered["expected_return_%"] * (1 - tax_rate / 100)
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
        "Exp. Return", "After-Tax Return", "RSI", "Stoch %K", "Vol ×", "Earnings", "Top Signals"]]

    st.subheader(f"Top {len(filtered)} setups — {cfg['label']} — {datetime.now().strftime('%Y-%m-%d')}")
    st.dataframe(
        table,
        use_container_width=True,
        column_config={
            "Score":                           st.column_config.ProgressColumn("Score", min_value=0, max_value=120, format="%d"),
            "Entry Price":                     st.column_config.NumberColumn("Entry Price", format="$%.2f"),
            f"Expected ({cfg['hold_days']}d)": st.column_config.NumberColumn(f"Expected ({cfg['hold_days']}d)", format="$%.2f"),
            "Exp. Return":                     st.column_config.NumberColumn("Exp. Return", format="+%.2f%%"),
            "After-Tax Return":                st.column_config.NumberColumn(f"After-Tax ({tax_rate}%)", format="+%.2f%%"),
            "RSI":                             st.column_config.NumberColumn("RSI", format="%.1f"),
            "Stoch %K":                        st.column_config.NumberColumn("Stoch %K", format="%.1f"),
            "Vol ×":                           st.column_config.NumberColumn("Vol ×", format="%.2f×"),
            "ATR":                             st.column_config.NumberColumn("ATR", format="$%.2f"),
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
# News tab renderer
# ---------------------------------------------------------------------------

SENTIMENT_STYLE = {
    "GOOD":    ("🟢", "#1a3a1a", "#4caf50"),
    "BAD":     ("🔴", "#3a1a1a", "#ef5350"),
    "NEUTRAL": ("⚪", "#2a2a2a", "#9e9e9e"),
}


def render_news_tab() -> None:
    st.subheader("News Sentiment — Last 3 Days")
    st.caption("Fetches recent headlines via Yahoo Finance and classifies each one with Claude Haiku.")

    has_data = any(f"df_{tf}" in st.session_state for tf in ["5d", "30d", "180d"])
    if not has_data:
        st.info("Run the screener first to see news for your top picks.")
        return

    available = {
        TIMEFRAMES[tf]["label"]: tf
        for tf in ["5d", "30d", "180d"]
        if f"df_{tf}" in st.session_state
    }
    selected_label = st.selectbox("Timeframe", list(available.keys()), key="news_tf_select")
    selected_tf    = available[selected_label]

    n_stocks = st.slider("Stocks to analyze", 1, 10, 5, key="news_n_stocks")

    if st.button("Fetch & Classify News", type="primary", key="news_fetch_btn"):
        df      = st.session_state[f"df_{selected_tf}"]
        tickers = df.head(n_stocks)["ticker"].tolist()

        with st.spinner("Fetching headlines from Yahoo Finance…"):
            news_by_ticker = fetch_recent_news(tickers, days=3)

        results: dict[str, list[dict]] = {}
        progress = st.progress(0, text="Classifying with Claude Haiku…")
        for i, ticker in enumerate(tickers):
            articles = news_by_ticker.get(ticker, [])
            try:
                results[ticker] = classify_news(ticker, articles)
            except EnvironmentError as e:
                st.error(f"Setup error: {e}")
                return
            except Exception as e:
                results[ticker] = articles
                st.warning(f"{ticker}: classification failed — {e}")
            progress.progress((i + 1) / len(tickers), text=f"Classified {ticker}")
        progress.empty()

        st.session_state["news_results"]    = results
        st.session_state["news_tf_label"]   = selected_label

    if "news_results" not in st.session_state:
        return

    results    = st.session_state["news_results"]
    tf_label   = st.session_state.get("news_tf_label", selected_label)
    st.caption(f"Showing top-{len(results)} picks from {tf_label} screener — last 3 days")

    for ticker, articles in results.items():
        good    = sum(1 for a in articles if a["sentiment"] == "GOOD")
        bad     = sum(1 for a in articles if a["sentiment"] == "BAD")
        neutral = sum(1 for a in articles if a["sentiment"] == "NEUTRAL")
        total   = len(articles)

        if total == 0:
            summary = "No news in last 3 days"
        elif bad > good:
            summary = f"🔴 {bad} bad · {good} good · {neutral} neutral"
        elif good > bad:
            summary = f"🟢 {good} good · {bad} bad · {neutral} neutral"
        else:
            summary = f"⚪ Mixed — {good} good · {bad} bad · {neutral} neutral"

        with st.expander(f"**{ticker}** — {total} articles  {summary}", expanded=True):
            if total == 0:
                st.markdown("_No recent headlines found._")
                continue

            for article in articles:
                icon, bg, border = SENTIMENT_STYLE.get(article["sentiment"], SENTIMENT_STYLE["NEUTRAL"])
                reason = f" — {article['reason']}" if article["reason"] else ""
                st.markdown(
                    f"""<div style="background:{bg}; border-left:4px solid {border};
                    padding:8px 12px; margin:4px 0; border-radius:4px;">
                    {icon} <strong>{article['title']}</strong>{reason}<br>
                    <small style="color:#aaa;">{article['publisher']} · {article['published']}
                    &nbsp;·&nbsp; <a href="{article['link']}" target="_blank"
                    style="color:#aaa;">Read →</a></small></div>""",
                    unsafe_allow_html=True,
                )


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
    tax_rate  = st.slider("Capital gains tax rate %", 0, 50, 25)
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
                "vix_5d",  "vix_30d",  "vix_180d",
                "breadth_5d", "breadth_30d", "breadth_180d",
                "tickers", "raw_data"]:
        st.session_state.pop(key, None)
    st.session_state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")

if "raw_data" not in st.session_state:
    if not run_btn:
        st.info("👈 Click **Run Screener** in the sidebar to start. ~2–3 min first run, cached after that.")
        st.stop()

    tickers = get_sp500_tickers()
    download_list = tuple(["SPY", "^VIX"] + tickers)
    with st.spinner("Downloading 2 years of price data for 500+ stocks… (cached after first run)"):
        raw = download_data(download_list)

    if raw["close"].empty:
        st.error("Download returned no data. Check your internet connection.")
        st.stop()

    st.session_state["raw_data"] = raw
    st.session_state["tickers"]  = tickers

raw_data = st.session_state["raw_data"]
tickers  = st.session_state["tickers"]

tab1, tab2, tab3, tab4 = st.tabs([
    "📅 5-Day Trading", "📆 30-Day Trading", "📈 180-Day Trading", "📰 News"
])

with tab1:
    render_tab("5d",  raw_data, tickers, top_n, min_score, tax_rate)

with tab2:
    render_tab("30d", raw_data, tickers, top_n, min_score, tax_rate)

with tab3:
    render_tab("180d", raw_data, tickers, top_n, min_score, tax_rate)

with tab4:
    render_news_tab()

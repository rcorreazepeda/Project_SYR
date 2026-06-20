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
    compute_news_score,
    get_ticker_sector_etf_map,
    SECTOR_ETFS,
)
from screener.database import (
    get_client as db_client,
    get_all_trades,
    save_trades_bulk,
    get_recent_picks,
    get_latest_ai_analysis,
    get_picks_with_outcomes,
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
    # ffill() ensures weekends/holidays don't produce NaN last-row comparisons
    valid_tickers  = [t for t in tickers if t in close_all.columns]
    filled         = close_all[valid_tickers].ffill()
    sma50_last     = filled.rolling(50).mean().iloc[-1]
    above_sma50    = (filled.iloc[-1] > sma50_last).sum()
    breadth_pct    = round(float(above_sma50) / len(valid_tickers) * 100, 1) if valid_tickers else 50.0

    # Sector ETF map
    sector_map = st.session_state.get("sector_map", {})

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
            sector_etf = sector_map.get(ticker, "")
            if sector_etf and sector_etf in close_all.columns:
                sec_close      = close_all[sector_etf].dropna()
                sector_return  = float(sec_close.iloc[-1] / sec_close.iloc[-rs_n] - 1) \
                                 if len(sec_close) >= rs_n + 1 else 0.0
            else:
                sector_return = 0.0

            s, signals, meta = score_ticker(
                close, high, low, volume, spy_return, in_bull, cfg,
                vix_val, breadth_pct, sector_return, sector_etf,
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
    # Check if news scores have been computed and blend them in
    news_scores: dict = st.session_state.get("news_scores", {})
    has_news = bool(news_scores)

    score_col = "combined_score" if has_news else "score"
    filtered = df.copy()
    if has_news:
        filtered["news_score"]    = filtered["ticker"].map(lambda t: news_scores.get(t, 0))
        filtered["combined_score"] = filtered["score"] + filtered["news_score"]
        filtered = filtered.sort_values("combined_score", ascending=False)
    filtered = filtered[filtered[score_col] >= min_score].head(top_n).copy()

    filtered["Top Signals"] = filtered["signals"].apply(lambda s: "  ·  ".join(s[:3]))
    filtered["Earnings"]    = filtered["earnings_soon"].apply(lambda x: "⚠ Soon" if x else "—")
    filtered["After-Tax Return"] = filtered["expected_return_%"] * (1 - tax_rate / 100)
    sf, ss = cfg["sma_fast"], cfg["sma_slow"]

    base_cols = ["Ticker", "Tech Score"]
    if has_news:
        base_cols += ["News", "Combined"]
    base_cols += [
        "Entry Price", f"Expected ({cfg['hold_days']}d)",
        "Exp. Return", "After-Tax Return", "RSI", "Stoch %K", "Vol ×", "Earnings", "Top Signals",
    ]

    rename_map = {
        "ticker":            "Ticker",
        "score":             "Tech Score",
        "price":             "Entry Price",
        "expected_price":    f"Expected ({cfg['hold_days']}d)",
        "expected_return_%": "Exp. Return",
        "RSI":               "RSI",
        "stoch_k":           "Stoch %K",
        "vol_ratio":         "Vol ×",
        "atr":               "ATR",
    }
    if has_news:
        rename_map["news_score"]    = "News"
        rename_map["combined_score"] = "Combined"

    table = filtered.rename(columns=rename_map)[base_cols]

    col_cfg = {
        "Tech Score": st.column_config.ProgressColumn("Tech Score", min_value=0, max_value=120, format="%d"),
        "Entry Price":                     st.column_config.NumberColumn("Entry Price", format="$%.2f"),
        f"Expected ({cfg['hold_days']}d)": st.column_config.NumberColumn(f"Expected ({cfg['hold_days']}d)", format="$%.2f"),
        "Exp. Return":                     st.column_config.NumberColumn("Exp. Return", format="+%.2f%%"),
        "After-Tax Return":                st.column_config.NumberColumn(f"After-Tax ({tax_rate}%)", format="+%.2f%%"),
        "RSI":                             st.column_config.NumberColumn("RSI", format="%.1f"),
        "Stoch %K":                        st.column_config.NumberColumn("Stoch %K", format="%.1f"),
        "Vol ×":                           st.column_config.NumberColumn("Vol ×", format="%.2f×"),
        "ATR":                             st.column_config.NumberColumn("ATR", format="$%.2f"),
    }
    if has_news:
        col_cfg["News"]     = st.column_config.NumberColumn("News", format="%+d", help="Sentiment score: GOOD=+4, BAD=−6, capped ±20")
        col_cfg["Combined"] = st.column_config.ProgressColumn("Combined", min_value=0, max_value=140, format="%d")

    st.subheader(f"Top {len(filtered)} setups — {cfg['label']} — {datetime.now().strftime('%Y-%m-%d')}")
    if has_news:
        st.caption("News scores blended in — fetch news in the 📰 tab to update.")
    st.dataframe(table, use_container_width=True, column_config=col_cfg)

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

        # Compute and store per-ticker news scores so trading tabs can blend them in
        st.session_state["news_scores"] = {
            ticker: compute_news_score(articles)[0]
            for ticker, articles in results.items()
        }
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
# Portfolio tab renderer
# ---------------------------------------------------------------------------

_TRADE_COLS = [
    "date_entered", "ticker", "timeframe", "shares", "entry_price", "total_invested",
    "screener_target", "screener_return_pct", "score", "signals",
    "exit_date", "exit_price", "actual_return_pct", "held_days", "outcome", "notes",
]


def _fetch_live_prices(tickers: list[str]) -> dict[str, float]:
    """Return {ticker: last_close} for a list of tickers."""
    if not tickers:
        return {}
    try:
        raw = yf.download(tickers, period="2d", auto_adjust=True, progress=False)
        closes = raw["Close"].ffill().iloc[-1]
        return {str(t): float(closes[t]) for t in tickers if t in closes.index and not pd.isna(closes[t])}
    except Exception:
        return {}


def render_portfolio_tab() -> None:
    st.subheader("Portfolio Tracker")
    st.caption("Upload your trades as a CSV, or enter them manually. Live P&L is fetched from Yahoo Finance.")

    db = db_client()

    # --- Upload / manual entry ---
    col_upload, col_manual = st.columns([1, 1])
    with col_upload:
        uploaded = st.file_uploader("Upload trade CSV", type="csv", key="trade_upload")
        if uploaded:
            try:
                import io as _io
                raw_bytes = uploaded.read()
                new_df = None
                for _enc in ("utf-8", "utf-8-sig", "latin-1", "mac_roman", "cp1252"):
                    try:
                        new_df = pd.read_csv(_io.BytesIO(raw_bytes), encoding=_enc)
                        break
                    except UnicodeDecodeError:
                        continue
                if new_df is None:
                    raise ValueError("Could not decode CSV — try saving as UTF-8 from Excel (Save As → CSV UTF-8)")
                new_df.columns = [c.lower().strip().replace(" ", "_") for c in new_df.columns]
                for col in _TRADE_COLS:
                    if col not in new_df.columns:
                        new_df[col] = None
                new_df = new_df[_TRADE_COLS]
                if db:
                    rows = new_df.where(pd.notnull(new_df), None).to_dict("records")
                    save_trades_bulk(db, rows)
                    st.success(f"Saved {len(rows)} trades to Supabase.")
                else:
                    st.session_state["local_trades"] = new_df
                    st.success(f"Loaded {len(new_df)} trades (Supabase not connected — local only).")
            except Exception as e:
                st.error(f"CSV parse error: {e}")

    with col_manual:
        with st.expander("Add single trade"):
            with st.form("add_trade"):
                t1, t2 = st.columns(2)
                ticker     = t1.text_input("Ticker").upper().strip()
                tf         = t2.selectbox("Timeframe", ["5d", "30d", "180d", "— (no screener)"])
                d_entered  = t1.date_input("Date entered", value=datetime.today())
                entry_px   = t2.number_input("Entry price $", min_value=0.01, format="%.2f")
                shares     = t1.number_input("Shares bought", min_value=0.0, format="%.4f")
                target_px  = t2.number_input("Screener target $ (optional)", min_value=0.0, format="%.2f")
                target_ret = t1.number_input("Screener exp. return % (optional)", format="%.2f")
                notes      = st.text_input("Notes (optional)")
                submitted  = st.form_submit_button("Save trade")
                if submitted and ticker:
                    total_inv = round(float(shares) * float(entry_px), 2) if shares else None
                    row = {
                        "date_entered":      str(d_entered),
                        "ticker":            ticker,
                        "timeframe":         tf if tf != "— (no screener)" else None,
                        "shares":            float(shares) if shares else None,
                        "entry_price":       float(entry_px),
                        "total_invested":    total_inv,
                        "screener_target":   float(target_px) if target_px else None,
                        "screener_return_pct": float(target_ret) if target_ret else None,
                        "notes":             notes or None,
                        "outcome":           "OPEN",
                    }
                    if db:
                        from screener.database import save_trade
                        save_trade(db, row)
                        st.success(f"Trade {ticker} saved.")
                    else:
                        local = st.session_state.get("local_trades", pd.DataFrame(columns=_TRADE_COLS))
                        st.session_state["local_trades"] = pd.concat(
                            [local, pd.DataFrame([row])], ignore_index=True
                        )
                        st.success(f"Trade {ticker} saved locally.")

    st.divider()

    # --- Load trades ---
    trades_df = None
    if db:
        raw_trades = get_all_trades(db)
        if raw_trades:
            trades_df = pd.DataFrame(raw_trades)
    elif "local_trades" in st.session_state:
        trades_df = st.session_state["local_trades"]

    if trades_df is None or trades_df.empty:
        st.info("No trades yet — upload a CSV or add one above.")
        if not db:
            st.warning("Supabase not connected — trades only persist in this browser session.")
        return

    # --- Live P&L ---
    open_mask  = trades_df["outcome"].isin(["OPEN", None, ""]) | trades_df["outcome"].isna()
    open_df    = trades_df[open_mask].copy()
    closed_df  = trades_df[~open_mask].copy()

    if not open_df.empty:
        live_px = _fetch_live_prices(open_df["ticker"].unique().tolist())
        open_df["current_price"] = open_df["ticker"].map(live_px)
        open_df["entry_price"]   = pd.to_numeric(open_df["entry_price"],   errors="coerce")
        open_df["shares"]        = pd.to_numeric(open_df.get("shares"),    errors="coerce")
        open_df["total_invested"]= pd.to_numeric(open_df.get("total_invested"), errors="coerce")

        open_df["live_return_%"] = (
            (open_df["current_price"] - open_df["entry_price"]) / open_df["entry_price"] * 100
        ).round(2)

        # P&L in $ — use shares if available, else per-share difference
        open_df["P&L $"] = (
            (open_df["current_price"] - open_df["entry_price"]) * open_df["shares"]
        ).round(2)
        # Fill in P&L for rows without shares (show per-share instead)
        no_shares = open_df["shares"].isna()
        open_df.loc[no_shares, "P&L $"] = (
            open_df.loc[no_shares, "current_price"] - open_df.loc[no_shares, "entry_price"]
        ).round(2)

        # Current value
        open_df["current_value"] = (open_df["current_price"] * open_df["shares"]).round(2)

        st.subheader("Open Positions")
        show_open = open_df[[
            "ticker", "timeframe", "date_entered", "shares", "entry_price",
            "total_invested", "current_price", "current_value",
            "live_return_%", "P&L $", "screener_target", "screener_return_pct", "notes"
        ]].copy()
        show_open.columns = [
            "Ticker", "TF", "Entered", "Shares", "Entry $",
            "Invested $", "Now $", "Value $",
            "Return %", "P&L $", "Target $", "Screener %", "Notes"
        ]
        st.dataframe(
            show_open,
            use_container_width=True,
            column_config={
                "Shares":     st.column_config.NumberColumn("Shares",     format="%.2f"),
                "Return %":   st.column_config.NumberColumn("Return %",   format="%+.2f%%"),
                "P&L $":      st.column_config.NumberColumn("P&L $",      format="%+.2f"),
                "Entry $":    st.column_config.NumberColumn("Entry $",    format="$%.2f"),
                "Now $":      st.column_config.NumberColumn("Now $",      format="$%.2f"),
                "Invested $": st.column_config.NumberColumn("Invested $", format="$%.2f"),
                "Value $":    st.column_config.NumberColumn("Value $",    format="$%.2f"),
                "Target $":   st.column_config.NumberColumn("Target $",   format="$%.2f"),
                "Screener %": st.column_config.NumberColumn("Screener %", format="+%.1f%%"),
            },
        )

        # Portfolio summary metrics
        total_invested = open_df["total_invested"].fillna(
            open_df["entry_price"] * open_df["shares"]
        ).sum()
        total_value  = open_df["current_value"].sum()
        total_pnl    = open_df["P&L $"].sum()
        positive     = (open_df["live_return_%"] > 0).sum()
        avg_ret      = open_df["live_return_%"].mean()

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Open Positions", len(open_df))
        c2.metric("Total Invested", f"${total_invested:,.2f}" if total_invested > 0 else "—")
        c3.metric("Portfolio Value", f"${total_value:,.2f}" if total_value > 0 else "—")
        c4.metric("Total P&L", f"${total_pnl:+,.2f}")
        c5.metric("Winning", f"{positive}/{len(open_df)}  avg {avg_ret:+.1f}%")

    if not closed_df.empty:
        st.subheader("Closed Trades")
        closed_df["actual_return_pct"] = pd.to_numeric(closed_df["actual_return_pct"], errors="coerce")
        show_closed = closed_df[["ticker", "timeframe", "date_entered", "exit_date",
                                  "entry_price", "exit_price", "actual_return_pct",
                                  "held_days", "outcome", "notes"]].copy()
        show_closed.columns = ["Ticker", "TF", "Entered", "Exited",
                               "Entry $", "Exit $", "Return %", "Days", "Outcome", "Notes"]
        st.dataframe(
            show_closed,
            use_container_width=True,
            column_config={
                "Return %": st.column_config.NumberColumn("Return %", format="%+.2f%%"),
                "Entry $":  st.column_config.NumberColumn("Entry $",  format="$%.2f"),
                "Exit $":   st.column_config.NumberColumn("Exit $",   format="$%.2f"),
            },
        )
        wins = (closed_df["actual_return_pct"] > 0).sum()
        st.caption(f"Closed: {len(closed_df)} trades  |  Win rate: {wins}/{len(closed_df)} ({wins/len(closed_df)*100:.0f}%)  |  Avg return: {closed_df['actual_return_pct'].mean():+.2f}%")


# ---------------------------------------------------------------------------
# Performance dashboard tab renderer
# ---------------------------------------------------------------------------

def render_performance_tab() -> None:
    st.subheader("Performance Dashboard")
    st.caption("Screener accuracy over time — powered by Supabase historical data.")

    db = db_client()
    if not db:
        st.warning("Supabase not connected. Set SUPABASE_URL and SUPABASE_KEY to enable this tab.")
        with st.expander("How to set up Supabase (free)"):
            st.markdown("""
1. Go to [supabase.com](https://supabase.com) → New project (free tier)
2. In SQL Editor, run the schema from `screener/database.py` (the docstring at the top)
3. Settings → API → copy **Project URL** and **service_role** key
4. Add to Streamlit Cloud secrets and GitHub Actions secrets:
   ```
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "eyJ..."
   ```
5. The GitHub Actions job writes picks + outcomes + AI analysis automatically Mon-Fri
            """)
        return

    # --- AI latest analysis ---
    latest = get_latest_ai_analysis(db)
    if latest:
        st.subheader(f"AI Analysis — {latest['run_date']}")
        st.markdown(latest["analysis_text"])
        st.caption(
            f"Top 5d: {latest.get('top_picks_5d', '—')}  |  "
            f"30d: {latest.get('top_picks_30d', '—')}  |  "
            f"180d: {latest.get('top_picks_180d', '—')}"
        )
        st.divider()

    # --- Signal win rates (from resolved picks) ---
    resolved = get_picks_with_outcomes(db, days=90)
    if resolved:
        from collections import defaultdict
        _SIGNAL_LABELS = {
            "MACD bullish crossover (fresh)": "MACD fresh cross",
            "MACD histogram positive":        "MACD positive",
            "RSI":                            "RSI recovery",
            "Stochastic":                     "Stoch crossover",
            "OBV bullish divergence":         "OBV divergence",
            "OBV rising":                     "OBV rising",
            "Volume surge":                   "Volume surge",
            "Price > SMA":                    "SMA aligned",
            "Golden cross":                   "Golden cross",
            "lower BB":                       "Bollinger bounce",
            "RS vs SPY":                      "RS vs SPY",
            "sector leader":                  "Sector leader",
            "sector":                         "RS vs sector",
            "VIX":                            "VIX sentiment",
            "Breadth":                        "Breadth",
        }
        stats: dict = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
        for pick in resolved:
            outcome = pick.get("outcome")
            if outcome not in ("WIN", "LOSS", "PARTIAL"):
                continue
            sigs = pick.get("signals", "")
            for fragment, label in _SIGNAL_LABELS.items():
                if fragment.lower() in sigs.lower():
                    stats[label]["total"] += 1
                    if outcome == "WIN":
                        stats[label]["wins"] += 1
                    elif outcome == "LOSS":
                        stats[label]["losses"] += 1

        win_rate_rows = [
            {
                "Signal":   label,
                "Win %":    round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0,
                "Wins":     s["wins"],
                "Losses":   s["losses"],
                "Total":    s["total"],
            }
            for label, s in stats.items() if s["total"] >= 3
        ]

        if win_rate_rows:
            st.subheader("Signal Win Rates (last 90 days)")
            wr_df = pd.DataFrame(win_rate_rows).sort_values("Win %", ascending=False)
            st.dataframe(
                wr_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Win %": st.column_config.ProgressColumn(
                        "Win %", min_value=0, max_value=100, format="%.1f%%"
                    ),
                },
            )
            st.divider()

    # --- Outcome summary ---
    if resolved:
        res_df = pd.DataFrame(resolved)
        res_df["actual_return_pct"] = pd.to_numeric(res_df["actual_return_pct"], errors="coerce")

        st.subheader("Outcome Summary (last 90 days)")
        c1, c2, c3, c4 = st.columns(4)
        total  = len(res_df)
        wins   = (res_df["outcome"] == "WIN").sum()
        losses = (res_df["outcome"] == "LOSS").sum()
        avg    = res_df["actual_return_pct"].mean()
        c1.metric("Total resolved picks", total)
        c2.metric("Win rate", f"{wins/total*100:.0f}%", f"{wins}W / {losses}L")
        c3.metric("Avg actual return", f"{avg:+.2f}%")
        c4.metric("Avg expected return",
                  f"{pd.to_numeric(res_df['expected_return_pct'], errors='coerce').mean():+.2f}%")

        # Outcomes by timeframe
        st.subheader("Win Rate by Timeframe")
        tf_cols = st.columns(3)
        for col, tf in zip(tf_cols, ["5d", "30d", "180d"]):
            sub = res_df[res_df["timeframe"] == tf]
            if not sub.empty:
                w = (sub["outcome"] == "WIN").sum()
                col.metric(
                    f"{tf} — {len(sub)} picks",
                    f"{w/len(sub)*100:.0f}% win rate",
                    f"avg {sub['actual_return_pct'].mean():+.1f}%",
                )
        st.divider()

    # --- Screener pick history ---
    picks = get_recent_picks(db, days=60)
    if not picks:
        st.info("No historical screener runs yet. The GitHub Actions job runs Mon-Fri at 4:30 PM ET.")
        return

    picks_df = pd.DataFrame(picks)
    picks_df["run_date"] = pd.to_datetime(picks_df["run_date"])

    st.subheader("Most Frequently Picked Stocks (last 60 days)")
    freq = picks_df["ticker"].value_counts().head(20).reset_index()
    freq.columns = ["Ticker", "Times Picked"]
    st.dataframe(freq, use_container_width=True, hide_index=True)

    # Score distributions per timeframe
    st.subheader("Avg Combined Score by Timeframe")
    c1, c2, c3 = st.columns(3)
    for col, tf in zip([c1, c2, c3], ["5d", "30d", "180d"]):
        sub = picks_df[picks_df["timeframe"] == tf]["combined_score"]
        if not sub.empty:
            col.metric(f"{tf}", f"{sub.mean():.0f} avg", f"max {sub.max()}")

    with st.expander("Raw screener picks data"):
        show_cols = ["run_date", "timeframe", "ticker", "technical_score",
                     "news_score", "combined_score", "entry_price",
                     "expected_return_pct", "outcome", "actual_return_pct", "signals"]
        show_cols = [c for c in show_cols if c in picks_df.columns]
        st.dataframe(picks_df[show_cols], use_container_width=True)


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
                "tickers", "raw_data", "sector_map"]:
        st.session_state.pop(key, None)
    st.session_state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")

if "raw_data" not in st.session_state:
    if run_btn:
        tickers = get_sp500_tickers()
        download_list = tuple(["SPY", "^VIX"] + SECTOR_ETFS + tickers)
        with st.spinner("Downloading 2 years of price data for 500+ stocks… (cached after first run)"):
            raw = download_data(download_list)

        if raw["close"].empty:
            st.error("Download returned no data. Check your internet connection.")
            st.stop()

        with st.spinner("Building sector ETF map…"):
            st.session_state["sector_map"] = get_ticker_sector_etf_map()

        st.session_state["raw_data"] = raw
        st.session_state["tickers"]  = tickers

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📅 5-Day Trading", "📆 30-Day Trading", "📈 180-Day Trading",
    "📰 News", "💼 Portfolio", "📊 Performance",
])

_screener_ready = "raw_data" in st.session_state

with tab1:
    if _screener_ready:
        render_tab("5d", st.session_state["raw_data"], st.session_state["tickers"], top_n, min_score, tax_rate)
    else:
        st.info("👈 Click **Run Screener** in the sidebar to start. ~2–3 min first run, cached after that.")

with tab2:
    if _screener_ready:
        render_tab("30d", st.session_state["raw_data"], st.session_state["tickers"], top_n, min_score, tax_rate)
    else:
        st.info("👈 Click **Run Screener** in the sidebar to start.")

with tab3:
    if _screener_ready:
        render_tab("180d", st.session_state["raw_data"], st.session_state["tickers"], top_n, min_score, tax_rate)
    else:
        st.info("👈 Click **Run Screener** in the sidebar to start.")

with tab4:
    render_news_tab()

with tab5:
    render_portfolio_tab()

with tab6:
    render_performance_tab()

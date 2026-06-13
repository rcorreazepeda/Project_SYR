#!/usr/bin/env python3
"""Daily screener job — runs Mon-Fri at 4:30 PM ET via GitHub Actions.

What it does on each run:
  1. Downloads all S&P 500 price data
  2. Scores every stock for 5d / 30d / 180d timeframes
  3. Fetches + classifies news for top 20 picks
  4. Blends news score with technical score → combined score
  5. Saves today's results to Supabase (screener_picks table)
  6. Looks back at picks from previous runs and checks actual price performance
  7. Sends everything to Claude Sonnet for analysis + scoring suggestions
  8. Saves the AI analysis to Supabase (ai_analysis table)
"""
import os
import sys
import json
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
import anthropic

load_dotenv()

from screener import (
    get_sp500_tickers,
    score_ticker,
    compute_targets,
    check_earnings_proximity,
    TIMEFRAMES,
    DOWNLOAD_LOOKBACK,
    fetch_recent_news,
    classify_news,
    compute_news_score,
    get_ticker_sector_etf_map,
    SECTOR_ETFS,
)
from screener.database import (
    get_client,
    save_screener_run,
    save_ai_analysis,
    get_recent_picks,
)


# ---------------------------------------------------------------------------
# Screener logic (mirrors app.py run_for_timeframe — no Streamlit dependency)
# ---------------------------------------------------------------------------

def _run_timeframe(tf_key, data, tickers, sector_map):
    cfg        = TIMEFRAMES[tf_key]
    close_all  = data["close"]
    high_all   = data["high"]
    low_all    = data["low"]
    vol_all    = data["volume"]

    spy_close  = close_all["SPY"].dropna()
    in_bull    = bool(spy_close.iloc[-1] > spy_close.rolling(50).mean().iloc[-1])
    rs_n       = cfg["rs_days"]
    spy_return = float(spy_close.iloc[-1] / spy_close.iloc[-rs_n] - 1) if len(spy_close) >= rs_n + 1 else 0.0

    vix_series = close_all["^VIX"].dropna() if "^VIX" in close_all.columns else pd.Series(dtype=float)
    vix_val    = float(vix_series.iloc[-1]) if len(vix_series) > 0 else 20.0

    valid_tickers = [t for t in tickers if t in close_all.columns]
    filled        = close_all[valid_tickers].ffill()
    sma50_last    = filled.rolling(50).mean().iloc[-1]
    above_sma50   = (filled.iloc[-1] > sma50_last).sum()
    breadth_pct   = round(float(above_sma50) / len(valid_tickers) * 100, 1) if valid_tickers else 50.0

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
                sec_close     = close_all[sector_etf].dropna()
                sector_return = float(sec_close.iloc[-1] / sec_close.iloc[-rs_n] - 1) \
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

    top_candidates = df.head(40)["ticker"].tolist()
    near_earnings  = check_earnings_proximity(top_candidates)
    df["earnings_soon"] = df["ticker"].isin(near_earnings)

    return df, in_bull, spy_return, vix_val, breadth_pct


# ---------------------------------------------------------------------------
# AI analysis
# ---------------------------------------------------------------------------

def _build_ai_prompt(today_results, historical_picks, run_date):
    """Build the prompt that Claude uses to analyze today's results and history."""
    lines = [
        f"You are a quantitative analyst reviewing the S&P 500 screener results for {run_date}.",
        "The screener uses technical indicators (RSI, MACD, Bollinger Bands, SMA alignment,",
        "OBV, volume, ATR momentum, relative strength vs SPY and sector ETFs, VIX, breadth).",
        "",
        "## TODAY'S TOP PICKS",
    ]

    for tf_key in ["5d", "30d", "180d"]:
        r = today_results.get(tf_key, {})
        df = r.get("df")
        if df is None or df.empty:
            continue
        cfg = TIMEFRAMES[tf_key]
        lines.append(f"\n### {cfg['label']} (hold {cfg['hold_days']}d, TP +{cfg['take_profit_pct']}%, SL -{cfg['stop_loss_pct']}%)")
        lines.append(f"Market regime: {'BULL' if r['in_bull'] else 'BEAR'}  |  VIX: {r['vix_val']:.1f}  |  Breadth: {r['breadth_pct']:.0f}%")
        for _, row in df.head(10).iterrows():
            tech  = int(row.get("score", 0))
            news  = int(row.get("news_score", 0))
            combo = int(row.get("combined_score", tech))
            sigs  = "  ·  ".join(row.get("signals", [])[:3])
            news_tag = f" | News {news:+d}" if news != 0 else ""
            lines.append(f"  {row['ticker']:6s}  Tech:{tech:3d}{news_tag}  Combined:{combo:3d}  ${row['price']:.2f} → ${row.get('expected_price', 0):.2f}  [{sigs}]")

    if historical_picks:
        lines += ["", "## HISTORICAL PICKS (last 30 days, actual vs target)"]
        by_date: dict[str, list] = {}
        for p in historical_picks:
            by_date.setdefault(p["run_date"], []).append(p)

        for run_d in sorted(by_date.keys(), reverse=True)[:5]:
            picks = by_date[run_d]
            lines.append(f"\n### Run: {run_d}")
            for p in picks[:5]:
                lines.append(
                    f"  {p['ticker']:6s} tf:{p['timeframe']:5s}  "
                    f"entry:${p['entry_price']:.2f}  "
                    f"target:${p['target_price']:.2f} (+{p['expected_return_pct']:.1f}%)  "
                    f"signals:{p['signals'][:60]}"
                )
    else:
        lines += ["", "## HISTORICAL PICKS", "No historical data yet — this is the first run."]

    lines += [
        "",
        "## YOUR TASK",
        "Write a concise analyst note (300-500 words) covering:",
        "1. **Market context** — regime, VIX, breadth, what it means for entries today",
        "2. **Top 3 picks to watch** (across all timeframes) — name them and say why",
        "3. **Signal performance** — based on historical picks, which signals are working?",
        "   (e.g. 'MACD crossover entries have been consistent', 'Bollinger bounce setups have underperformed')",
        "4. **Scoring suggestion** — one specific, actionable suggestion to improve the model",
        "   (e.g. 'Increase OBV divergence weight for 30d since it precedes breakouts',",
        "   'Add a -5 penalty when earnings are within 3 weeks for 180d picks')",
        "5. **Risk flags** — anything to watch out for today",
        "",
        "Be specific and direct. No filler. Write as if you are briefing a trader at 4:35 PM.",
    ]

    return "\n".join(lines)


def run_ai_analysis(today_results, historical_picks, run_date):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[skip] ANTHROPIC_API_KEY not set — skipping AI analysis.")
        return None

    prompt = _build_ai_prompt(today_results, historical_picks, run_date)
    client = anthropic.Anthropic(api_key=api_key)

    print("  Sending to Claude Sonnet for analysis...")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    run_date = date.today()
    print(f"\n{'='*60}")
    print(f"Daily Screener Job — {run_date}  {datetime.now().strftime('%H:%M')} ET")
    print(f"{'='*60}")

    # 1. Download data
    print("\n[1/6] Fetching S&P 500 tickers...")
    tickers = get_sp500_tickers()

    print(f"[2/6] Downloading price data ({len(tickers) + len(SECTOR_ETFS) + 2} symbols)...")
    download_list = ["SPY", "^VIX"] + SECTOR_ETFS + tickers
    raw = yf.download(
        download_list,
        period=DOWNLOAD_LOOKBACK,
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    data = {
        "close":  raw["Close"],
        "high":   raw["High"],
        "low":    raw["Low"],
        "volume": raw["Volume"],
    }
    if data["close"].empty:
        print("[ERROR] Download returned empty data. Aborting.")
        sys.exit(1)

    print("[3/6] Building sector map...")
    sector_map = get_ticker_sector_etf_map()

    # 2. Score all timeframes
    print("[4/6] Scoring stocks...")
    today_results = {}
    for tf_key in ["5d", "30d", "180d"]:
        print(f"  {tf_key}...", end=" ", flush=True)
        df, in_bull, spy_ret, vix_val, breadth_pct = _run_timeframe(tf_key, data, tickers, sector_map)
        today_results[tf_key] = {
            "df": df,
            "in_bull": in_bull,
            "spy_ret": spy_ret,
            "vix_val": vix_val,
            "breadth_pct": breadth_pct,
        }
        print(f"top pick: {df.iloc[0]['ticker']} (score {df.iloc[0]['score']})")

    # 3. News enrichment for top 20 unique tickers
    all_top = list({
        t
        for tf_key in ["5d", "30d", "180d"]
        for t in today_results[tf_key]["df"].head(20)["ticker"].tolist()
    })
    print(f"[5/6] Fetching news for {len(all_top)} tickers...")
    news_by_ticker = fetch_recent_news(all_top, days=3)
    news_scores: dict[str, int] = {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    for i, ticker in enumerate(all_top):
        articles = news_by_ticker.get(ticker, [])
        if articles and api_key:
            try:
                articles = classify_news(ticker, articles)
            except Exception as e:
                print(f"  [warn] {ticker} news classification failed: {e}")
        score, label = compute_news_score(articles)
        news_scores[ticker] = score
        if score != 0:
            print(f"  {ticker}: {label}")

    # Apply news scores
    for tf_key in ["5d", "30d", "180d"]:
        df = today_results[tf_key]["df"].copy()
        df["news_score"]    = df["ticker"].map(lambda t: news_scores.get(t, 0))
        df["combined_score"] = df["score"] + df["news_score"]
        df = df.sort_values("combined_score", ascending=False).reset_index(drop=True)
        df.index += 1
        today_results[tf_key]["df"] = df

    # 4. Save to Supabase
    print("\n[6/6] Saving to Supabase + running AI analysis...")
    db = get_client()
    if db:
        for tf_key, result in today_results.items():
            save_screener_run(
                db, result["df"], tf_key, run_date,
                result["in_bull"], result["vix_val"], result["breadth_pct"],
                TIMEFRAMES[tf_key],
            )
        print("  Screener picks saved.")
        historical_picks = get_recent_picks(db, days=30)
        print(f"  Loaded {len(historical_picks)} historical picks for analysis.")
    else:
        historical_picks = []
        print("  [skip] Supabase not configured — skipping save.")

    # 5. AI analysis
    analysis = run_ai_analysis(today_results, historical_picks, run_date)
    if analysis and db:
        top_picks = {
            tf: ", ".join(today_results[tf]["df"].head(5)["ticker"].tolist())
            for tf in ["5d", "30d", "180d"]
        }
        save_ai_analysis(db, run_date, analysis, top_picks)
        print("  AI analysis saved.")

    # Print summary to GitHub Actions log
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for tf_key in ["5d", "30d", "180d"]:
        df = today_results[tf_key]["df"]
        top5 = df.head(5)[["ticker", "score", "news_score", "combined_score"]].to_string(index=False)
        print(f"\n{TIMEFRAMES[tf_key]['label']} — top 5:\n{top5}")
    if analysis:
        print(f"\nAI ANALYSIS:\n{analysis[:800]}...")
    print("\n=== Job complete ===")


if __name__ == "__main__":
    main()

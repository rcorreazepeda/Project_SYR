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
    get_picks_pending_outcome,
    update_pick_outcome,
    get_picks_with_outcomes,
    get_all_trades,
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
# Outcome tracking
# ---------------------------------------------------------------------------

# Maps signal text fragments → readable label for win-rate reporting
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


def check_outcomes(db, close_all: "pd.DataFrame") -> dict[str, list[dict]]:
    """For each timeframe, find picks from hold_days ago and record actual outcome.

    Returns summary dict {tf_key: [outcome_dicts]} for inclusion in AI prompt.
    """
    today = date.today()
    summary: dict[str, list[dict]] = {}

    for tf_key in ["5d", "30d", "180d"]:
        cfg       = TIMEFRAMES[tf_key]
        hold_days = cfg["hold_days"]
        tp        = cfg["take_profit_pct"] / 100
        sl        = cfg["stop_loss_pct"]   / 100
        check_for = today - timedelta(days=hold_days)

        picks = get_picks_pending_outcome(db, tf_key, str(check_for))
        if not picks:
            continue

        outcomes = []
        for pick in picks:
            ticker      = pick["ticker"]
            entry_price = float(pick["entry_price"])

            if ticker not in close_all.columns:
                continue

            exit_price    = float(close_all[ticker].ffill().iloc[-1])
            actual_return = (exit_price - entry_price) / entry_price

            if actual_return >= tp:
                outcome = "WIN"
            elif actual_return <= -sl:
                outcome = "LOSS"
            else:
                outcome = "PARTIAL"

            update_pick_outcome(
                db, pick["id"],
                actual_return * 100,
                exit_price,
                outcome,
                str(today),
            )
            outcomes.append({
                "ticker":      ticker,
                "outcome":     outcome,
                "actual_pct":  round(actual_return * 100, 2),
                "expected_pct": float(pick.get("expected_return_pct", 0)),
                "signals":     pick.get("signals", ""),
            })

        if outcomes:
            wins = sum(1 for o in outcomes if o["outcome"] == "WIN")
            print(f"  {tf_key}: checked {len(outcomes)} picks from {check_for} — {wins}/{len(outcomes)} wins")
            summary[tf_key] = outcomes

    return summary


def compute_signal_win_rates(picks_with_outcomes: list[dict]) -> dict[str, dict]:
    """Parse signals column across all resolved picks → per-signal win rate."""
    from collections import defaultdict
    stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})

    for pick in picks_with_outcomes:
        outcome = pick.get("outcome")
        if outcome not in ("WIN", "LOSS", "PARTIAL"):
            continue
        signals_str = pick.get("signals", "")
        is_win      = outcome == "WIN"

        for fragment, label in _SIGNAL_LABELS.items():
            if fragment.lower() in signals_str.lower():
                stats[label]["total"] += 1
                if is_win:
                    stats[label]["wins"] += 1
                elif outcome == "LOSS":
                    stats[label]["losses"] += 1

    return {
        label: {
            **s,
            "win_rate": round(s["wins"] / s["total"] * 100, 1) if s["total"] > 0 else 0,
        }
        for label, s in stats.items()
        if s["total"] >= 3  # only report signals with enough data points
    }


# ---------------------------------------------------------------------------
# AI analysis
# ---------------------------------------------------------------------------

def _build_ai_prompt(today_results, recent_outcomes, signal_win_rates, run_date):
    """Build the prompt Claude uses to analyze today's results, actual outcomes, and signal stats."""
    lines = [
        f"You are a quantitative analyst reviewing the S&P 500 screener for {run_date}.",
        "The screener scores stocks using: RSI, MACD, Bollinger Bands, SMA alignment,",
        "OBV, volume surge, ATR momentum, RS vs SPY, RS vs sector ETF, VIX, market breadth.",
        "A news sentiment score (GOOD=+4, BAD=−6, capped ±20) is blended into the final score.",
        "",
        "## TODAY'S TOP PICKS",
    ]

    for tf_key in ["5d", "30d", "180d"]:
        r   = today_results.get(tf_key, {})
        df  = r.get("df")
        cfg = TIMEFRAMES[tf_key]
        if df is None or df.empty:
            continue
        lines.append(
            f"\n### {cfg['label']}  "
            f"(hold {cfg['hold_days']}d | TP +{cfg['take_profit_pct']}% / SL −{cfg['stop_loss_pct']}%)"
        )
        lines.append(
            f"Regime: {'BULL' if r['in_bull'] else 'BEAR'}  |  "
            f"VIX: {r['vix_val']:.1f}  |  Breadth: {r['breadth_pct']:.0f}%"
        )
        for _, row in df.head(10).iterrows():
            tech  = int(row.get("score", 0))
            news  = int(row.get("news_score", 0))
            combo = int(row.get("combined_score", tech))
            sigs  = "  ·  ".join(row.get("signals", [])[:3])
            news_tag = f" News{news:+d}" if news != 0 else ""
            lines.append(
                f"  {row['ticker']:6s}  Tech:{tech:3d}{news_tag}  "
                f"Combined:{combo:3d}  "
                f"${row['price']:.2f}→${row.get('expected_price', 0):.2f}  [{sigs}]"
            )

    # --- Real outcomes from previous runs ---
    if recent_outcomes:
        lines += ["", "## ACTUAL OUTCOMES (picks that completed their hold period today)"]
        for tf_key, outcomes in recent_outcomes.items():
            wins    = sum(1 for o in outcomes if o["outcome"] == "WIN")
            losses  = sum(1 for o in outcomes if o["outcome"] == "LOSS")
            partial = sum(1 for o in outcomes if o["outcome"] == "PARTIAL")
            avg_ret = sum(o["actual_pct"] for o in outcomes) / len(outcomes)
            lines.append(
                f"\n### {TIMEFRAMES[tf_key]['label']} — "
                f"{wins}W / {losses}L / {partial}P  avg return: {avg_ret:+.1f}%"
            )
            for o in outcomes:
                beat = o["actual_pct"] - o["expected_pct"]
                lines.append(
                    f"  {o['ticker']:6s}  {o['outcome']:7s}  "
                    f"actual:{o['actual_pct']:+.1f}%  expected:{o['expected_pct']:+.1f}%  "
                    f"({'beat' if beat >= 0 else 'missed'} by {abs(beat):.1f}pp)  "
                    f"signals: {o['signals'][:70]}"
                )
    else:
        lines += ["", "## ACTUAL OUTCOMES", "No picks completed their hold period today."]

    # --- Signal win rates ---
    if signal_win_rates:
        lines += ["", "## SIGNAL WIN RATES (last 90 days, resolved picks only)"]
        sorted_signals = sorted(
            signal_win_rates.items(), key=lambda x: x[1]["win_rate"], reverse=True
        )
        for label, s in sorted_signals:
            bar = "█" * int(s["win_rate"] / 10)
            lines.append(
                f"  {label:25s}  {s['win_rate']:5.1f}%  "
                f"({s['wins']}W/{s['losses']}L of {s['total']})  {bar}"
            )
    else:
        lines += ["", "## SIGNAL WIN RATES", "Not enough resolved picks yet to compute win rates."]

    lines += [
        "",
        "## YOUR TASK",
        "Write a concise analyst note (300-500 words) in these sections:",
        "",
        "**1. Market context** — regime, VIX, breadth interpretation for today",
        "",
        "**2. Top 3 picks** — name specific tickers across timeframes and explain why",
        "   (reference their strongest signals and news score if notable)",
        "",
        "**3. Signal scorecard** — what the win-rate data is telling us:",
        "   - Which signals are consistently producing wins?",
        "   - Which are underperforming? Be specific with percentages.",
        "   - Has anything changed vs what you'd expect?",
        "",
        "**4. Scoring adjustment** — ONE specific, data-backed suggestion:",
        "   Format: 'Adjust [signal] weight for [timeframe] from [X] to [Y] because [evidence]'",
        "   Example: 'Increase score_macd_cross for 5d from 25 to 30 — MACD fresh cross",
        "   has 71% win rate (22/31), highest of all signals, consistently beating target.'",
        "   Only suggest if win-rate data supports it. Skip if data is thin.",
        "",
        "**5. Risk flags** — anything specific to watch today (earnings, VIX spike, weak breadth)",
        "",
        "Be direct. No filler. Write as if briefing a trader at 4:35 PM ET.",
    ]

    return "\n".join(lines)


def run_ai_analysis(today_results, recent_outcomes, signal_win_rates, run_date):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[skip] ANTHROPIC_API_KEY not set — skipping AI analysis.")
        return None

    prompt = _build_ai_prompt(today_results, recent_outcomes, signal_win_rates, run_date)
    client = anthropic.Anthropic(api_key=api_key)

    print("  Sending to Claude Sonnet for analysis...")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _build_portfolio_section(open_trades: list[dict], close_all, today_results) -> str:
    """Build the open positions HTML block for the daily email."""
    if not open_trades:
        return ""

    # All tickers in today's top 20 across any timeframe
    todays_picks = {
        t
        for tf in ["5d", "30d", "180d"]
        for t in today_results.get(tf, {}).get("df", pd.DataFrame()).head(20).get("ticker", pd.Series()).tolist()
    }

    rows = ""
    total_pnl = 0.0

    for trade in open_trades:
        ticker      = trade.get("ticker", "")
        entry_price = float(trade.get("entry_price") or 0)
        tf_key      = trade.get("timeframe", "")
        entered     = trade.get("date_entered", "")

        if not ticker or entry_price == 0:
            continue

        # Current price from already-downloaded data
        if ticker in close_all.columns:
            current = float(close_all[ticker].ffill().iloc[-1])
        else:
            continue

        shares  = float(trade.get("shares") or 1)
        pnl_pct = (current - entry_price) / entry_price * 100
        pnl_usd = (current - entry_price) * shares
        total_pnl += pnl_usd

        # Status vs take profit / stop loss
        cfg = TIMEFRAMES.get(tf_key, {})
        tp  = float(cfg.get("take_profit_pct", 20))
        sl  = float(cfg.get("stop_loss_pct", 10))

        if pnl_pct >= tp:
            status     = "✅ Hit target"
            status_col = "#4caf50"
        elif pnl_pct >= tp * 0.8:
            status     = "🎯 Near target"
            status_col = "#8bc34a"
        elif pnl_pct <= -sl:
            status     = "🔴 Stop triggered"
            status_col = "#ef5350"
        elif pnl_pct <= -sl * 0.8:
            status     = "⚠️ Near stop"
            status_col = "#ff9800"
        elif pnl_pct >= 0:
            status     = "🟢 Winning"
            status_col = "#4caf50"
        else:
            status     = "🔵 Holding"
            status_col = "#9e9e9e"

        still_liked = "⭐ Still in picks" if ticker in todays_picks else ""
        pnl_color   = "#4caf50" if pnl_pct >= 0 else "#ef5350"

        rows += f"""
        <tr style="border-bottom:1px solid #2a2a2a">
          <td style="padding:8px 12px;font-weight:700">{ticker}</td>
          <td style="padding:8px 12px;color:#aaa;font-size:12px">{tf_key or '—'}  {entered}</td>
          <td style="padding:8px 12px">${entry_price:.2f}</td>
          <td style="padding:8px 12px">${current:.2f}</td>
          <td style="padding:8px 12px;color:{pnl_color};font-weight:700">{pnl_pct:+.2f}%</td>
          <td style="padding:8px 12px;color:{status_col};font-size:13px">{status}</td>
          <td style="padding:8px 12px;font-size:12px;color:#f0c040">{still_liked}</td>
        </tr>"""

    if not rows:
        return ""

    pnl_color = "#4caf50" if total_pnl >= 0 else "#ef5350"
    return f"""
    <h3 style="color:#00b4d8;margin:32px 0 8px">
      💼 Open Positions
      <span style="font-weight:400;font-size:14px;color:{pnl_color}">
        — Total P&L {total_pnl:+.2f}$
      </span>
    </h3>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse;background:#1a1a1a;border-radius:6px">
      <tr style="background:#111;color:#aaa;font-size:12px">
        <th style="padding:6px 12px;text-align:left">Ticker</th>
        <th style="padding:6px 12px;text-align:left">TF / Entered</th>
        <th style="padding:6px 12px;text-align:left">Entry</th>
        <th style="padding:6px 12px;text-align:left">Now</th>
        <th style="padding:6px 12px;text-align:left">Return</th>
        <th style="padding:6px 12px;text-align:left">Status</th>
        <th style="padding:6px 12px;text-align:left">Screener</th>
      </tr>
      {rows}
    </table>"""


def _build_email_html(today_results, analysis, run_date, open_trades=None, close_all=None) -> str:
    regime_rows = ""
    picks_sections = ""

    for tf_key in ["5d", "30d", "180d"]:
        r   = today_results.get(tf_key, {})
        df  = r.get("df")
        cfg = TIMEFRAMES[tf_key]
        if df is None or df.empty:
            continue

        regime = "BULL 🟢" if r["in_bull"] else "BEAR 🔴"
        vix    = r["vix_val"]
        brd    = r["breadth_pct"]

        regime_rows += f"""
        <tr>
          <td style="padding:6px 12px;font-weight:600">{cfg['label']}</td>
          <td style="padding:6px 12px">{regime}</td>
          <td style="padding:6px 12px">VIX {vix:.1f}</td>
          <td style="padding:6px 12px">Breadth {brd:.0f}%</td>
        </tr>"""

        rows = ""
        for _, row in df.head(5).iterrows():
            tech  = int(row.get("score", 0))
            news  = int(row.get("news_score", 0))
            combo = int(row.get("combined_score", tech))
            news_tag = f" <span style='color:#4caf50'>+{news}</span>" if news > 0 \
                  else f" <span style='color:#ef5350'>{news}</span>" if news < 0 else ""
            sigs  = "  ·  ".join(row.get("signals", [])[:2])
            earn  = " ⚠" if row.get("earnings_soon") else ""
            rows += f"""
            <tr style="border-bottom:1px solid #2a2a2a">
              <td style="padding:8px 12px;font-weight:700">{row['ticker']}{earn}</td>
              <td style="padding:8px 12px">{tech}{news_tag}</td>
              <td style="padding:8px 12px;font-weight:700;color:#00b4d8">{combo}</td>
              <td style="padding:8px 12px">${row['price']:.2f} → ${row.get('expected_price', 0):.2f}</td>
              <td style="padding:8px 12px;font-size:12px;color:#aaa">{sigs}</td>
            </tr>"""

        picks_sections += f"""
        <h3 style="color:#00b4d8;margin:24px 0 8px">{cfg['label']}
          <span style="font-weight:400;font-size:14px;color:#aaa">
            — TP +{cfg['take_profit_pct']}% / SL −{cfg['stop_loss_pct']}%
          </span>
        </h3>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;background:#1a1a1a;border-radius:6px">
          <tr style="background:#111;color:#aaa;font-size:12px">
            <th style="padding:6px 12px;text-align:left">Ticker</th>
            <th style="padding:6px 12px;text-align:left">Tech / News</th>
            <th style="padding:6px 12px;text-align:left">Combined</th>
            <th style="padding:6px 12px;text-align:left">Price → Target</th>
            <th style="padding:6px 12px;text-align:left">Top signals</th>
          </tr>
          {rows}
        </table>"""

    analysis_html    = analysis.replace("\n", "<br>") if analysis else "No analysis generated."
    portfolio_section = _build_portfolio_section(open_trades or [], close_all, today_results) \
                        if close_all is not None else ""

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="background:#0d0d0d;color:#e0e0e0;font-family:Arial,sans-serif;padding:24px;max-width:800px;margin:0 auto">
      <h1 style="color:#00b4d8;margin-bottom:4px">R&S Stock Plan</h1>
      <p style="color:#aaa;margin-top:0">{run_date} — Daily Screener Results</p>

      <table style="border-collapse:collapse;background:#1a1a1a;border-radius:6px;margin-bottom:24px" width="100%">
        <tr style="background:#111;color:#aaa;font-size:12px">
          <th style="padding:6px 12px;text-align:left">Timeframe</th>
          <th style="padding:6px 12px;text-align:left">Regime</th>
          <th style="padding:6px 12px;text-align:left">VIX</th>
          <th style="padding:6px 12px;text-align:left">Breadth</th>
        </tr>
        {regime_rows}
      </table>

      {portfolio_section}

      {picks_sections}

      <h3 style="color:#00b4d8;margin:32px 0 8px">AI Analysis</h3>
      <div style="background:#1a1a1a;border-left:4px solid #00b4d8;padding:16px;border-radius:4px;line-height:1.7">
        {analysis_html}
      </div>

      <p style="color:#555;font-size:12px;margin-top:32px">
        This is an automated report from your R&S Stock Plan screener.<br>
        View full results at <a href="https://projectsyr.streamlit.app" style="color:#00b4d8">projectsyr.streamlit.app</a>
      </p>
    </body>
    </html>"""


def send_email(today_results, analysis, run_date, open_trades=None, close_all=None) -> None:
    api_key  = os.environ.get("RESEND_API_KEY", "")
    to_email = os.environ.get("ALERT_EMAIL", "raulcorreazepeda@gmail.com")
    if not api_key:
        print("  [skip] RESEND_API_KEY not set — skipping email.")
        return

    try:
        import resend
        resend.api_key = api_key

        top_5d  = ", ".join(today_results["5d"]["df"].head(3)["ticker"].tolist())
        top_30d = ", ".join(today_results["30d"]["df"].head(3)["ticker"].tolist())
        regime  = "BULL" if today_results["5d"]["in_bull"] else "BEAR"
        n_open  = len(open_trades) if open_trades else 0
        port_tag = f" | {n_open} open positions" if n_open else ""

        resend.Emails.send({
            "from":    "R&S Screener <screener@resend.dev>",
            "to":      [to_email],
            "subject": f"📈 Daily Picks {run_date} — {regime} — 5d: {top_5d} | 30d: {top_30d}{port_tag}",
            "html":    _build_email_html(today_results, analysis, run_date, open_trades, close_all),
        })
        print(f"  Email sent to {to_email}.")
    except Exception as e:
        print(f"  [warn] Email failed: {e}")


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
    for ticker in all_top:
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

    # 4. Save to Supabase + check outcomes
    print("\n[6/6] Saving to Supabase + checking outcomes + AI analysis...")
    db = get_client()
    recent_outcomes:   dict = {}
    signal_win_rates:  dict = {}

    if db:
        for tf_key, result in today_results.items():
            save_screener_run(
                db, result["df"], tf_key, run_date,
                result["in_bull"], result["vix_val"], result["breadth_pct"],
                TIMEFRAMES[tf_key],
            )
        print("  Screener picks saved.")

        # Check actual outcomes for picks that completed their hold period
        print("  Checking outcomes for matured picks...")
        recent_outcomes = check_outcomes(db, data["close"])

        # Compute signal win rates from all resolved picks (last 90 days)
        resolved_picks = get_picks_with_outcomes(db, days=90)
        if resolved_picks:
            signal_win_rates = compute_signal_win_rates(resolved_picks)
            print(f"  Signal win rates computed from {len(resolved_picks)} resolved picks.")
    else:
        print("  [skip] Supabase not configured — skipping save.")

    # 5. AI analysis
    analysis = run_ai_analysis(today_results, recent_outcomes, signal_win_rates, run_date)
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

    # 6. Send email (with open portfolio positions)
    open_trades = []
    if db:
        all_trades  = get_all_trades(db)
        open_trades = [
            t for t in all_trades
            if not t.get("outcome") or t.get("outcome") == "OPEN"
        ]
        print(f"  Loaded {len(open_trades)} open positions for email.")
    send_email(today_results, analysis, run_date, open_trades, data["close"])

    print("\n=== Job complete ===")


if __name__ == "__main__":
    main()

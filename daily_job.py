#!/usr/bin/env python3
"""Daily screener job — runs Mon-Fri at 4:30 PM ET via GitHub Actions.

What it does on each run:
  1. Downloads all S&P 500 price data
  2. Scores every stock for 30d / 180d / 1y timeframes
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

    for tf_key in ["1y", "30d", "180d"]:
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

    for tf_key in ["1y", "30d", "180d"]:
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
        "   Example: 'Increase score_macd_cross for 30d from 25 to 30 — MACD fresh cross",
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
        for tf in ["1y", "30d", "180d"]
        for t in today_results.get(tf, {}).get("df", pd.DataFrame()).head(20).get("ticker", pd.Series()).tolist()
    }

    # Pre-fetch prices for tickers not in the screener bulk download (ETFs, crypto, etc.)
    extra_tickers = [
        t for t in {tr.get("ticker", "") for tr in open_trades}
        if t and t not in close_all.columns
    ]
    extra_prices: dict[str, float] = {}
    if extra_tickers:
        try:
            raw = yf.download(extra_tickers, period="2d", auto_adjust=True, progress=False)
            closes = raw["Close"].ffill()
            if isinstance(closes, pd.Series):
                extra_prices[extra_tickers[0]] = float(closes.iloc[-1])
            else:
                last = closes.iloc[-1]
                extra_prices = {str(t): float(last[t]) for t in extra_tickers
                                if t in last.index and not pd.isna(last[t])}
        except Exception:
            pass

    rows = ""
    total_pnl       = 0.0
    total_invested  = 0.0
    total_current   = 0.0

    for trade in open_trades:
        ticker      = trade.get("ticker", "")
        entry_price = float(trade.get("entry_price") or 0)
        tf_key      = trade.get("timeframe", "")
        category    = trade.get("category", "") or ""

        if not ticker or entry_price == 0:
            continue

        if ticker in close_all.columns:
            current = float(close_all[ticker].ffill().iloc[-1])
        elif ticker in extra_prices:
            current = extra_prices[ticker]
        else:
            continue

        shares       = float(trade.get("shares") or 1)
        invested     = round(shares * entry_price, 2)
        current_val  = round(shares * current, 2)
        pnl_pct      = (current - entry_price) / entry_price * 100
        pnl_usd      = current_val - invested

        total_pnl      += pnl_usd
        total_invested += invested
        total_current  += current_val

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

        still_liked = " ⭐" if ticker in todays_picks else ""
        pnl_color   = "#00ffa3" if pnl_usd >= 0 else "#ff2d5b"
        cat_badge   = f"<span style='color:#4a6a8a;font-size:10px;font-family:monospace'>{category}</span>" if category else ""

        status_map = {
            "✅": "#00ffa3", "🎯": "#7dff9a", "🔴": "#ff2d5b",
            "⚠️": "#ffb800", "🟢": "#00ffa3", "🔵": "#4a6a8a",
        }
        status_col = next((v for k, v in status_map.items() if status.startswith(k)), "#4a6a8a")

        rows += f"""
        <tr style="border-bottom:1px solid #0f2035">
          <td style="padding:10px 14px">
            <span style="font-weight:700;color:#ccd6f6;font-size:14px">{ticker}{still_liked}</span><br>
            {cat_badge}
          </td>
          <td style="padding:10px 14px;color:#4a6a8a;font-size:12px;font-family:monospace">{tf_key or '—'}</td>
          <td style="padding:10px 14px;font-family:monospace;color:#8899aa">${invested:,.2f}</td>
          <td style="padding:10px 14px;font-family:monospace;color:#ccd6f6;font-weight:600">${current_val:,.2f}</td>
          <td style="padding:10px 14px;font-family:monospace;font-weight:700;color:{pnl_color}">
            {pnl_usd:+,.2f}<br>
            <span style="font-size:11px;font-weight:400">{pnl_pct:+.2f}%</span>
          </td>
          <td style="padding:10px 14px;font-size:12px;color:{status_col}">{status}</td>
        </tr>"""

    if not rows:
        return ""

    summary_color = "#00ffa3" if total_pnl >= 0 else "#ff2d5b"
    return f"""
    <div style="margin:32px 0 0">
      <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:10px">
        <span style="color:#00e5ff;font-size:16px;font-weight:700;letter-spacing:1px">💼 OPEN POSITIONS</span>
      </div>
      <div style="background:#080e1a;border:1px solid #1a3350;border-radius:8px;padding:12px 16px;margin-bottom:12px;font-family:monospace;font-size:13px">
        <span style="color:#4a6a8a">INVESTED</span>
        <span style="color:#ccd6f6;font-weight:600;margin:0 4px">${total_invested:,.2f}</span>
        <span style="color:#1a3350;margin:0 8px">|</span>
        <span style="color:#4a6a8a">VALUE</span>
        <span style="color:#ccd6f6;font-weight:600;margin:0 4px">${total_current:,.2f}</span>
        <span style="color:#1a3350;margin:0 8px">|</span>
        <span style="color:#4a6a8a">P&amp;L</span>
        <span style="color:{summary_color};font-weight:700;margin-left:4px">${total_pnl:+,.2f}</span>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="border-collapse:collapse;background:#0c1420;border:1px solid #1a3350;border-radius:8px">
        <tr style="background:#080e1a;border-bottom:1px solid #1a3350">
          <th style="padding:8px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">TICKER</th>
          <th style="padding:8px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">TF</th>
          <th style="padding:8px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">INVESTED</th>
          <th style="padding:8px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">VALUE</th>
          <th style="padding:8px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">P&amp;L</th>
          <th style="padding:8px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">STATUS</th>
        </tr>
        {rows}
      </table>
    </div>"""


def _build_headlines_section(news_by_ticker: dict, today_results: dict) -> str:
    """Return an HTML block with GOOD/BAD headlines for today's top picks."""
    top_tickers = list({
        t
        for tf in ["1y", "30d", "180d"]
        for t in today_results.get(tf, {}).get("df", pd.DataFrame()).head(5).get("ticker", pd.Series()).tolist()
    })

    blocks = ""
    for ticker in top_tickers:
        articles = news_by_ticker.get(ticker, [])
        notable  = [a for a in articles if a.get("sentiment") in ("GOOD", "BAD")]
        if not notable:
            continue

        items = ""
        for a in notable[:3]:
            good    = a["sentiment"] == "GOOD"
            badge   = f"<span style='background:{'#003d1f' if good else '#3d0012'};color:{'#00ffa3' if good else '#ff2d5b'};font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;font-family:monospace'>{'GOOD' if good else 'BAD'}</span>"
            title   = a.get("title", "")
            link    = a.get("link", "")
            reason  = f"<br><span style='color:#4a6a8a;font-size:11px;padding-left:4px'>{a['reason']}</span>" if a.get("reason") else ""
            pub     = a.get("published", "")
            title_html = f'<a href="{link}" style="color:#ccd6f6;text-decoration:none">{title}</a>' if link else title
            items += f"""
            <div style="margin:8px 0;padding:8px 10px;background:#070d18;border-left:2px solid {'#00ffa3' if good else '#ff2d5b'};border-radius:0 4px 4px 0">
              {badge} <span style="font-size:12px">{title_html}</span>
              <span style="color:#2a4a6a;font-size:10px;font-family:monospace;float:right">{pub}</span>
              {reason}
            </div>"""

        blocks += f"""
        <div style="margin-bottom:16px">
          <span style="color:#00e5ff;font-weight:700;font-size:13px;font-family:monospace">{ticker}</span>
          {items}
        </div>"""

    if not blocks:
        return ""

    return f"""
    <div style="margin:32px 0 0">
      <div style="color:#00e5ff;font-size:16px;font-weight:700;letter-spacing:1px;margin-bottom:12px">📰 KEY HEADLINES</div>
      <div style="background:#0c1420;border:1px solid #1a3350;border-radius:8px;padding:16px">
        {blocks}
      </div>
    </div>"""


def _get_current_prices(trades: list[dict], close_all) -> dict[str, float]:
    """Return {ticker: price} for all trades, fetching extras not in bulk download."""
    prices: dict[str, float] = {}
    missing = []
    for t in {tr.get("ticker", "") for tr in trades if tr.get("ticker")}:
        if t in close_all.columns:
            prices[t] = float(close_all[t].ffill().iloc[-1])
        else:
            missing.append(t)
    if missing:
        try:
            raw    = yf.download(missing, period="2d", auto_adjust=True, progress=False)
            closes = raw["Close"].ffill()
            if isinstance(closes, pd.Series):
                prices[missing[0]] = float(closes.iloc[-1])
            else:
                last = closes.iloc[-1]
                for t in missing:
                    if t in last.index and not pd.isna(last[t]):
                        prices[t] = float(last[t])
        except Exception:
            pass
    return prices


def _build_portfolio_ai_prompt(owner: str, trades: list[dict], prices: dict[str, float],
                                all_trades: dict[str, list]) -> str:
    lines = [
        f"You are a portfolio advisor reviewing {owner.title()}'s holdings as of market close today.",
        "Be direct, data-driven, and concise. No filler.",
        "",
        f"## {owner.upper()}'S OPEN POSITIONS",
    ]

    category_value: dict[str, float] = {}
    total_value = 0.0

    for trade in trades:
        ticker      = trade.get("ticker", "")
        entry_price = float(trade.get("entry_price") or 0)
        shares      = float(trade.get("shares") or 0)
        category    = trade.get("category") or "Other"
        invested    = float(trade.get("total_invested") or 0) or round(shares * entry_price, 2)
        current     = prices.get(ticker, 0)
        if not ticker or entry_price == 0 or current == 0:
            continue
        pnl_pct   = (current - entry_price) / entry_price * 100
        cur_val   = round(shares * current, 2) if shares else current
        pnl_usd   = cur_val - invested

        category_value[category] = category_value.get(category, 0) + cur_val
        total_value += cur_val

        lines.append(
            f"  {ticker:10s} [{category:14s}]  "
            f"entry ${entry_price:.2f}  now ${current:.2f}  "
            f"return {pnl_pct:+.1f}%  P&L ${pnl_usd:+.2f}"
        )

    # Category concentration
    if total_value > 0:
        lines += ["", "## CATEGORY CONCENTRATION"]
        for cat, val in sorted(category_value.items(), key=lambda x: -x[1]):
            pct = val / total_value * 100
            lines.append(f"  {cat:14s}  {pct:.1f}%  (${val:,.0f})")

    # Overlap with other portfolios
    my_tickers = {tr.get("ticker") for tr in trades}
    for other_owner, other_trades in all_trades.items():
        if other_owner == owner:
            continue
        shared = my_tickers & {tr.get("ticker") for tr in other_trades}
        if shared:
            lines.append(f"\n## OVERLAP WITH {other_owner.upper()}: {', '.join(sorted(shared))}")

    lines += [
        "",
        "## YOUR TASK",
        "1. **Position review** — for each ticker give one of: HOLD / SELL / BUY MORE",
        "   followed by one sentence reason. Format: `TICKER — ACTION: reason`",
        "",
        "2. **Concentration risk** — flag any category over 35% of portfolio.",
        "   Suggest one rebalancing move if needed.",
        "",
        "3. **Portfolio health** — score 1-10 and one sentence summary.",
        "",
        "4. **One action** — the single most important thing to do today.",
        "",
        "Keep it under 400 words. Be specific with numbers.",
    ]
    return "\n".join(lines)


def run_portfolio_analysis(trades_by_owner: dict, close_all) -> dict[str, str]:
    """Run Claude portfolio analysis for each owner. Returns {owner: analysis_text}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not trades_by_owner:
        return {}

    client  = anthropic.Anthropic(api_key=api_key)
    results = {}

    for owner, trades in trades_by_owner.items():
        if not trades:
            continue
        prices = _get_current_prices(trades, close_all)
        prompt = _build_portfolio_ai_prompt(owner, trades, prices, trades_by_owner)
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=900,
                messages=[{"role": "user", "content": prompt}],
            )
            results[owner] = resp.content[0].text
            print(f"  Portfolio analysis done for {owner}.")
        except Exception as e:
            print(f"  [warn] Portfolio analysis failed for {owner}: {e}")

    return results


def _build_email_html(today_results, analysis, run_date, open_trades=None, close_all=None,
                      news_by_ticker=None, portfolio_analysis: str = "") -> str:
    regime_rows = ""
    picks_sections = ""

    for tf_key in ["1y", "30d", "180d"]:
        r   = today_results.get(tf_key, {})
        df  = r.get("df")
        cfg = TIMEFRAMES[tf_key]
        if df is None or df.empty:
            continue

        bull       = r["in_bull"]
        regime_lbl = f"<span style='color:#00ffa3;font-weight:700'>● BULL</span>" if bull else f"<span style='color:#ff2d5b;font-weight:700'>● BEAR</span>"
        vix        = r["vix_val"]
        brd        = r["breadth_pct"]

        regime_rows += f"""
        <tr style="border-bottom:1px solid #0f2035">
          <td style="padding:9px 14px;font-family:monospace;color:#00e5ff;font-weight:700">{cfg['label']}</td>
          <td style="padding:9px 14px">{regime_lbl}</td>
          <td style="padding:9px 14px;font-family:monospace;color:#8899aa">VIX <span style="color:#ccd6f6">{vix:.1f}</span></td>
          <td style="padding:9px 14px;font-family:monospace;color:#8899aa">BRD <span style="color:#ccd6f6">{brd:.0f}%</span></td>
        </tr>"""

        rows = ""
        for _, row in df.head(5).iterrows():
            tech  = int(row.get("score", 0))
            news  = int(row.get("news_score", 0))
            combo = int(row.get("combined_score", tech))
            news_col  = "#00ffa3" if news > 0 else "#ff2d5b" if news < 0 else "#4a6a8a"
            news_tag  = f"<span style='color:{news_col};font-size:11px'> {news:+d}</span>" if news != 0 else ""
            sigs      = " · ".join(row.get("signals", [])[:2])
            earn      = "<span style='color:#ffb800'> ⚠</span>" if row.get("earnings_soon") else ""
            score_col = "#00ffa3" if combo >= 80 else "#00e5ff" if combo >= 60 else "#8899aa"
            rows += f"""
            <tr style="border-bottom:1px solid #0f2035">
              <td style="padding:9px 14px;font-weight:700;color:#ccd6f6;font-family:monospace">{row['ticker']}{earn}</td>
              <td style="padding:9px 14px;font-family:monospace;color:#8899aa">{tech}{news_tag}</td>
              <td style="padding:9px 14px;font-family:monospace;font-weight:700;color:{score_col}">{combo}</td>
              <td style="padding:9px 14px;font-family:monospace;color:#8899aa">${row['price']:.2f}<span style="color:#4a6a8a"> → </span><span style="color:#ccd6f6">${row.get('expected_price', 0):.2f}</span></td>
              <td style="padding:9px 14px;font-size:11px;color:#4a6a8a">{sigs}</td>
            </tr>"""

        picks_sections += f"""
        <div style="margin:28px 0 0">
          <div style="margin-bottom:10px">
            <span style="color:#00e5ff;font-size:15px;font-weight:700;letter-spacing:1px">{cfg['label']}</span>
            <span style="color:#4a6a8a;font-size:12px;font-family:monospace;margin-left:10px">TP +{cfg['take_profit_pct']}% / SL -{cfg['stop_loss_pct']}%</span>
          </div>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border-collapse:collapse;background:#0c1420;border:1px solid #1a3350;border-radius:8px">
            <tr style="background:#080e1a;border-bottom:1px solid #1a3350">
              <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">TICKER</th>
              <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">TECH/NEWS</th>
              <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">SCORE</th>
              <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">PRICE → TARGET</th>
              <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">SIGNALS</th>
            </tr>
            {rows}
          </table>
        </div>"""

    analysis_html     = analysis.replace("\n", "<br>") if analysis else "No analysis generated."
    portfolio_section = _build_portfolio_section(open_trades or [], close_all, today_results) \
                        if close_all is not None else ""
    headlines_section = _build_headlines_section(news_by_ticker or {}, today_results)
    port_ai_html      = portfolio_analysis.replace("\n", "<br>") if portfolio_analysis else ""
    port_ai_section   = f"""
    <div style="margin:32px 0 0">
      <div style="color:#9f5cff;font-size:16px;font-weight:700;letter-spacing:1px;margin-bottom:12px">🤖 PORTFOLIO ANALYSIS</div>
      <div style="background:#0c1420;border:1px solid #2a1a50;border-left:3px solid #9f5cff;border-radius:8px;padding:16px;line-height:1.8;color:#b8c8e0;font-size:13px">
        {port_ai_html}
      </div>
    </div>""" if port_ai_html else ""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#07090f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
  <div style="max-width:680px;margin:0 auto;padding:24px 20px">

    <!-- HEADER -->
    <div style="border-bottom:1px solid #1a3350;padding-bottom:16px;margin-bottom:24px">
      <div style="font-family:monospace;font-size:22px;font-weight:700;letter-spacing:3px;color:#00e5ff">R&amp;S STOCK PLAN</div>
      <div style="font-family:monospace;font-size:11px;color:#4a6a8a;margin-top:4px;letter-spacing:1px">
        {run_date} // DAILY SCREENER // AFTER MARKET CLOSE
      </div>
    </div>

    <!-- REGIME -->
    <div style="color:#00e5ff;font-size:12px;font-weight:700;letter-spacing:1px;margin-bottom:8px;font-family:monospace">MARKET REGIME</div>
    <table width="100%" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse;background:#0c1420;border:1px solid #1a3350;border-radius:8px;margin-bottom:8px">
      <tr style="background:#080e1a;border-bottom:1px solid #1a3350">
        <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">TIMEFRAME</th>
        <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">REGIME</th>
        <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">VIX</th>
        <th style="padding:7px 14px;text-align:left;color:#4a6a8a;font-size:10px;letter-spacing:1px;font-family:monospace">BREADTH</th>
      </tr>
      {regime_rows}
    </table>

    {portfolio_section}
    {port_ai_section}

    <!-- PICKS -->
    <div style="color:#00e5ff;font-size:12px;font-weight:700;letter-spacing:1px;margin:32px 0 4px;font-family:monospace">TODAY'S TOP PICKS</div>
    {picks_sections}

    {headlines_section}

    <!-- AI ANALYSIS -->
    <div style="margin:32px 0 0">
      <div style="color:#00e5ff;font-size:16px;font-weight:700;letter-spacing:1px;margin-bottom:12px">⚡ AI ANALYSIS</div>
      <div style="background:#0c1420;border:1px solid #1a3350;border-left:3px solid #00e5ff;border-radius:8px;padding:16px;line-height:1.8;color:#b8c8e0;font-size:13px">
        {analysis_html}
      </div>
    </div>

    <!-- FOOTER -->
    <div style="margin-top:32px;padding-top:16px;border-top:1px solid #0f2035;font-family:monospace;font-size:11px;color:#2a4a6a">
      AUTOMATED REPORT // R&amp;S STOCK PLAN //<br>
      <a href="https://projectsyr.streamlit.app" style="color:#00e5ff;text-decoration:none">projectsyr.streamlit.app</a>
    </div>

  </div>
</body>
</html>"""



def send_email(today_results, analysis, run_date, trades_by_owner: dict | None = None,
               close_all=None, news_by_ticker=None, portfolio_analyses: dict | None = None) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        print("  [skip] RESEND_API_KEY not set — skipping email.")
        return

    try:
        import resend
        resend.api_key = api_key

        top_1y  = ", ".join(today_results["1y"]["df"].head(3)["ticker"].tolist())
        top_30d = ", ".join(today_results["30d"]["df"].head(3)["ticker"].tolist())
        regime  = "BULL" if today_results["1y"]["in_bull"] else "BEAR"

        my_email        = os.environ.get("ALERT_EMAIL", "raulcorreazepeda@gmail.com")
        trades_by_owner = trades_by_owner or {}

        portfolio_analyses = portfolio_analyses or {}
        for owner in trades_by_owner:
            open_trades      = trades_by_owner.get(owner, [])
            port_analysis    = portfolio_analyses.get(owner, "")
            n_open           = len(open_trades)
            port_tag         = f" | {n_open} open" if n_open else ""
            resend.Emails.send({
                "from":    "R&S Screener <screener@resend.dev>",
                "to":      [my_email],
                "subject": f"📈 {owner.title()} — {run_date} — {regime} — 1y: {top_1y} | 30d: {top_30d}{port_tag}",
                "html":    _build_email_html(
                    today_results, analysis, run_date,
                    open_trades, close_all, news_by_ticker, port_analysis,
                ),
            })
            print(f"  Email sent to {my_email} ({owner}).")
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
    for tf_key in ["1y", "30d", "180d"]:
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
        for tf_key in ["1y", "30d", "180d"]
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
    for tf_key in ["1y", "30d", "180d"]:
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
            for tf in ["1y", "30d", "180d"]
        }
        save_ai_analysis(db, run_date, analysis, top_picks)
        print("  AI analysis saved.")

    # Print summary to GitHub Actions log
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    for tf_key in ["1y", "30d", "180d"]:
        df = today_results[tf_key]["df"]
        top5 = df.head(5)[["ticker", "score", "news_score", "combined_score"]].to_string(index=False)
        print(f"\n{TIMEFRAMES[tf_key]['label']} — top 5:\n{top5}")
    if analysis:
        print(f"\nAI ANALYSIS:\n{analysis[:800]}...")

    # 6. Send email (one per portfolio owner)
    trades_by_owner: dict[str, list] = {}
    if db:
        from screener.database import get_all_owners
        for owner in get_all_owners(db):
            owner_trades = get_all_trades(db, owner=owner)
            trades_by_owner[owner] = [
                t for t in owner_trades
                if not t.get("outcome") or t.get("outcome") == "OPEN"
            ]
            print(f"  Loaded {len(trades_by_owner[owner])} open positions for {owner}.")
    portfolio_analyses = run_portfolio_analysis(trades_by_owner, data["close"])
    send_email(today_results, analysis, run_date, trades_by_owner, data["close"], news_by_ticker, portfolio_analyses)

    print("\n=== Job complete ===")


if __name__ == "__main__":
    main()

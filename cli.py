"""
CLI runner — prints ranked results for a chosen timeframe.

Usage:
    python cli.py            # defaults to 5-day
    python cli.py --days 30
    python cli.py --days 180
"""

import sys
import argparse
import yfinance as yf
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from screener import (
    get_sp500_tickers,
    score_ticker,
    compute_targets,
    check_earnings_proximity,
    TIMEFRAMES,
    DOWNLOAD_LOOKBACK,
)

TOP_N = 10

TF_MAP = {"5": "5d", "30": "30d", "180": "180d"}


def main() -> None:
    parser = argparse.ArgumentParser(description="S&P 500 screener CLI")
    parser.add_argument("--days", choices=["5", "30", "180"], default="5",
                        help="Holding horizon in trading days (default: 5)")
    args = parser.parse_args()

    tf_key = TF_MAP[args.days]
    cfg    = TIMEFRAMES[tf_key]

    tickers = get_sp500_tickers()
    download_list = ["SPY"] + tickers
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] "
          f"{cfg['label']} view — screening {len(tickers)} stocks…\n")

    raw = yf.download(download_list, period=DOWNLOAD_LOOKBACK,
                      auto_adjust=True, progress=True, threads=True)

    if raw.empty:
        sys.exit("Download returned no data.")

    close_all = raw["Close"]
    high_all  = raw["High"]
    low_all   = raw["Low"]
    vol_all   = raw["Volume"]

    spy_close  = close_all["SPY"].dropna()
    in_bull    = bool(spy_close.iloc[-1] > spy_close.rolling(50).mean().iloc[-1])
    rs_n       = cfg["rs_days"]
    spy_return = float(spy_close.iloc[-1] / spy_close.iloc[-rs_n] - 1) \
                 if len(spy_close) >= rs_n + 1 else 0.0

    regime = "BULL" if in_bull else "BEAR"
    print(f"  Market regime: {regime} (SPY {'above' if in_bull else 'below'} SMA50)\n")

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

    if not results:
        print("No results — check your internet connection.")
        return

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    df.index += 1
    df = compute_targets(df, close_all, cfg)

    top_candidates = df.head(TOP_N * 2)["ticker"].tolist()
    print("  Checking earnings proximity for top candidates…")
    near_earnings = check_earnings_proximity(top_candidates)
    df["earnings_soon"] = df["ticker"].isin(near_earnings)

    hold = cfg["hold_days"]
    print(f"\n{'Rank':<5} {'Ticker':<7} {'Score':<7} {'Entry':>8} {'Exp(' + str(hold) + 'd)':>10} "
          f"{'Ret%':>6} {'RSI':>6} {'Vol×':>5} {'Earn?':>6}  Signals")
    print("-" * 115)

    for i, row in df.head(TOP_N).iterrows():
        earn   = " *** " if row["earnings_soon"] else "      "
        sig    = " | ".join(row["signals"][:3])
        ret    = row["expected_return_%"]
        print(f"{i:<5} {row['ticker']:<7} {row['score']:<7} "
              f"${row['price']:>7.2f} ${row['expected_price']:>8.2f} "
              f"{ret:>+6.1f}% {row['RSI']:>6.1f} {row['vol_ratio']:>5.2f} "
              f"{earn}  {sig}")

    print(f"\n  Scored {len(df)} stocks in {regime} regime.")
    print(f"  Top pick: {df.iloc[0]['ticker']} (score {df.iloc[0]['score']}, "
          f"exp. return {df.iloc[0]['expected_return_%']:+.1f}%)")
    if near_earnings:
        print(f"  *** Earnings within {hold} days: {', '.join(sorted(near_earnings))}")
    print(f"\n  Exit rules: TP +{cfg['take_profit_pct']}% / SL -{cfg['stop_loss_pct']}% / "
          f"time stop {hold} trading days")
    print("  Note: statistical signals only — not financial advice.\n")

    out = f"screener_results_{tf_key}_{datetime.now().strftime('%Y%m%d')}.csv"
    df.drop(columns=["signals"]).to_csv(out, index=False)
    print(f"  Full results saved to {out}")


if __name__ == "__main__":
    main()

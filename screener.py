"""
Daily stock screener — ranks S&P 500 stocks by statistical likelihood
of a positive return over the next 5 trading days.

Signals used (each contributes to a 0–100 score):
  - RSI(14)            : recovering from oversold (30–50) = bullish setup
  - MACD crossover     : line crossing above signal = momentum shift
  - SMA trend alignment: price > SMA20 > SMA50 = uptrend confirmed
  - Bollinger Bands    : price near/below lower band = mean-reversion setup
  - Volume surge       : recent volume > 1.5× 20-day avg = conviction
  - 5-day momentum     : small positive drift (0–5%) = sustained but not extended
"""

import sys
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

LOOKBACK = "3mo"
MIN_DATA_DAYS = 55
TOP_N = 10


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def get_sp500_tickers() -> list[str]:
    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        return table["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception as e:
        print(f"[warn] Could not fetch S&P 500 list ({e}), using fallback universe.")
        return [
            "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM",
            "V", "UNH", "XOM", "JNJ", "WMT", "PG", "MA", "HD", "CVX", "MRK",
            "ABBV", "PEP", "KO", "LLY", "BAC", "AVGO", "COST", "MCD", "TMO",
            "CSCO", "ACN", "ABT", "DHR", "NKE", "ADBE", "CRM", "TXN", "NEE",
            "QCOM", "PM", "LIN", "AMGN", "RTX", "SBUX", "INTU", "AMD", "GS",
            "BLK", "CAT", "AXP", "SPGI", "DE",
        ]


# ---------------------------------------------------------------------------
# Indicators (pure numpy/pandas — no external ta library needed)
# ---------------------------------------------------------------------------

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return sma + std_mult * std, sma, sma - std_mult * std


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_ticker(close: pd.Series, volume: pd.Series) -> tuple[int, list[str], dict]:
    signals: list[str] = []
    score = 0

    current_price = close.iloc[-1]

    # --- RSI ---
    rsi_val = rsi(close).iloc[-1]
    if 30 <= rsi_val <= 50:
        score += 25
        signals.append(f"RSI {rsi_val:.1f} — recovering from oversold")
    elif 50 < rsi_val <= 65:
        score += 10
        signals.append(f"RSI {rsi_val:.1f} — neutral-bullish")

    # --- MACD crossover ---
    macd_line, signal_line = macd(close)
    hist = (macd_line - signal_line)
    # bullish crossover: histogram turned positive in the last 2 bars
    if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
        score += 25
        signals.append("MACD bullish crossover (fresh)")
    elif hist.iloc[-1] > 0:
        score += 10
        signals.append("MACD histogram positive")

    # --- SMA trend alignment ---
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    if current_price > sma20 > sma50:
        score += 20
        signals.append("Price > SMA20 > SMA50 (uptrend)")
    elif current_price > sma20:
        score += 8
        signals.append("Price > SMA20")

    # --- Bollinger Band mean-reversion ---
    _, _, lower_band = bollinger(close)
    lb = lower_band.iloc[-1]
    if current_price <= lb:
        score += 15
        signals.append("Price below lower Bollinger Band (oversold)")
    elif current_price <= lb * 1.02:
        score += 10
        signals.append("Price near lower Bollinger Band")

    # --- Volume surge ---
    vol_avg = volume.rolling(20).mean().iloc[-1]
    vol_ratio = volume.iloc[-5:].mean() / vol_avg if vol_avg > 0 else 1.0
    if vol_ratio >= 1.5:
        score += 15
        signals.append(f"Volume surge {vol_ratio:.1f}× 20-day avg")
    elif vol_ratio >= 1.2:
        score += 7
        signals.append(f"Above-avg volume {vol_ratio:.1f}×")

    # --- 5-day momentum ---
    if len(close) >= 6:
        mom = (close.iloc[-1] / close.iloc[-6] - 1) * 100
        if 0.5 <= mom <= 5.0:
            score += 10
            signals.append(f"5d return +{mom:.1f}% (healthy)")
        elif mom < 0:
            # slight penalty for negative recent momentum unless RSI is low
            if rsi_val >= 50:
                score -= 5

    meta = {
        "price": round(current_price, 2),
        "RSI": round(rsi_val, 1),
        "SMA20": round(sma20, 2),
        "SMA50": round(sma50, 2),
        "vol_ratio": round(vol_ratio, 2),
    }
    return score, signals, meta


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    tickers = get_sp500_tickers()
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Screening {len(tickers)} stocks...\n")

    # Bulk download is much faster than ticker-by-ticker
    raw = yf.download(
        tickers,
        period=LOOKBACK,
        auto_adjust=True,
        progress=True,
        threads=True,
    )

    if raw.empty:
        sys.exit("Download returned no data.")

    close_all: pd.DataFrame = raw["Close"]
    volume_all: pd.DataFrame = raw["Volume"]

    results = []
    for ticker in tickers:
        if ticker not in close_all.columns:
            continue
        close = close_all[ticker].dropna()
        volume = volume_all[ticker].dropna()
        if len(close) < MIN_DATA_DAYS:
            continue
        try:
            s, signals, meta = score_ticker(close, volume)
            results.append({"ticker": ticker, "score": s, "signals": signals, **meta})
        except Exception:
            continue

    if not results:
        print("No results — check your internet connection or ticker data.")
        return

    df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)

    print(f"{'Rank':<5} {'Ticker':<8} {'Score':<7} {'Price':>8}  {'RSI':>6}  {'Vol×':>5}  Signals")
    print("-" * 90)
    for i, row in df.head(TOP_N).iterrows():
        signal_str = " | ".join(row["signals"])
        print(
            f"{i+1:<5} {row['ticker']:<8} {row['score']:<7} "
            f"${row['price']:>7.2f}  {row['RSI']:>6.1f}  {row['vol_ratio']:>5.2f}  {signal_str}"
        )

    print(f"\n  Scored {len(df)} stocks. Top pick: {df.iloc[0]['ticker']} (score {df.iloc[0]['score']})")
    print("  Note: statistical signals only — not financial advice.\n")

    out = f"screener_results_{datetime.now().strftime('%Y%m%d')}.csv"
    df.to_csv(out, index=False)
    print(f"  Full results saved to {out}")


if __name__ == "__main__":
    main()

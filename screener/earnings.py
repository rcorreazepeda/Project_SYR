import os
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta


def _via_fmp(tickers: list[str], days_ahead: int) -> set[str]:
    key = os.getenv("FMP_API_KEY", "")
    if not key:
        raise ValueError("FMP_API_KEY not set")
    today = datetime.now().date()
    cutoff = today + timedelta(days=days_ahead)
    url = (
        f"https://financialmodelingprep.com/api/v3/earning_calendar"
        f"?from={today}&to={cutoff}&apikey={key}"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    ticker_set = set(tickers)
    return {item["symbol"] for item in resp.json() if item.get("symbol") in ticker_set}


def _via_yfinance(tickers: list[str], days_ahead: int) -> set[str]:
    flagged: set[str] = set()
    today = datetime.now().date()
    cutoff = today + timedelta(days=days_ahead)
    for t in tickers:
        try:
            cal = yf.Ticker(t).calendar
            if cal is None:
                continue
            earn_date = (
                pd.to_datetime(cal.get("Earnings Date", [None])[0])
                if isinstance(cal, dict)
                else pd.to_datetime(cal.columns[0])
            )
            if earn_date is not pd.NaT and today <= earn_date.date() <= cutoff:
                flagged.add(t)
        except Exception:
            continue
    return flagged


def check_earnings_proximity(tickers: list[str], days_ahead: int = 5) -> set[str]:
    try:
        return _via_fmp(tickers, days_ahead)
    except Exception as e:
        print(f"[warn] FMP earnings failed ({e}), falling back to yfinance...")
    return _via_yfinance(tickers, days_ahead)

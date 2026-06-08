import io
import requests
import pandas as pd

_FALLBACK_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM",
    "V", "UNH", "XOM", "JNJ", "WMT", "PG", "MA", "HD", "CVX", "MRK",
    "ABBV", "PEP", "KO", "LLY", "BAC", "AVGO", "COST", "MCD", "TMO",
    "CSCO", "ACN", "ABT", "DHR", "NKE", "ADBE", "CRM", "TXN", "NEE",
    "QCOM", "PM", "LIN", "AMGN", "RTX", "SBUX", "INTU", "AMD", "GS",
    "BLK", "CAT", "AXP", "SPGI", "DE",
]


def _fetch_spdr() -> list[str]:
    url = (
        "https://www.ssga.com/us/en/individual/etfs/library-content/"
        "products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
    )
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    df = pd.read_excel(io.BytesIO(resp.content), skiprows=4, engine="openpyxl")
    col = next((c for c in df.columns if "ticker" in str(c).lower()), None)
    if col is None:
        raise ValueError("Ticker column not found in SPDR file")
    return (
        df[col].dropna().astype(str).str.strip()
        .loc[lambda s: s.str.match(r"^[A-Z]{1,5}$")]
        .tolist()
    )


def _fetch_wikipedia() -> list[str]:
    resp = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0 (compatible; stock-screener/1.0)"},
        timeout=10,
    )
    resp.raise_for_status()
    table = pd.read_html(io.StringIO(resp.text))[0]
    return table["Symbol"].str.replace(".", "-", regex=False).tolist()


def get_sp500_tickers() -> list[str]:
    for label, fetch in [("SPDR SPY holdings", _fetch_spdr),
                          ("Wikipedia", _fetch_wikipedia)]:
        try:
            tickers = fetch()
            print(f"  Universe: {len(tickers)} tickers from {label}")
            return tickers
        except Exception as e:
            print(f"[warn] {label} failed ({e}), trying next source...")
    print("[warn] All sources failed — using hardcoded fallback.")
    return _FALLBACK_TICKERS

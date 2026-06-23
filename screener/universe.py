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

# QQQ-exclusive names not typically in the S&P 500 fallback above
_FALLBACK_NASDAQ100_EXTRA = [
    "MSTR", "DDOG", "TEAM", "CRWD", "ZS", "PANW", "CDNS", "SNPS",
    "MRVL", "ON", "KLAC", "LRCX", "AMAT", "NXPI", "MCHP", "FTNT",
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


def _fetch_wikipedia_sp500() -> list[str]:
    resp = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "Mozilla/5.0 (compatible; stock-screener/1.0)"},
        timeout=10,
    )
    resp.raise_for_status()
    table = pd.read_html(io.StringIO(resp.text))[0]
    return table["Symbol"].str.replace(".", "-", regex=False).tolist()


def _fetch_wikipedia_nasdaq100() -> list[str]:
    resp = requests.get(
        "https://en.wikipedia.org/wiki/Nasdaq-100",
        headers={"User-Agent": "Mozilla/5.0 (compatible; stock-screener/1.0)"},
        timeout=10,
    )
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    for table in tables:
        cols = [str(c).lower() for c in table.columns]
        ticker_col = next(
            (c for c in table.columns if "ticker" in str(c).lower() or "symbol" in str(c).lower()),
            None,
        )
        if ticker_col is not None:
            return (
                table[ticker_col].dropna().astype(str)
                .str.replace(".", "-", regex=False)
                .loc[lambda s: s.str.match(r"^[A-Z]{1,5}$")]
                .tolist()
            )
    raise ValueError("No ticker table found on NASDAQ-100 Wikipedia page")


def _fetch_nasdaq100() -> list[str]:
    """Fetch NASDAQ-100 tickers, trying Wikipedia only (Invesco blocks cloud)."""
    return _fetch_wikipedia_nasdaq100()


def get_sp500_tickers() -> list[str]:
    """Return deduplicated S&P 500 + NASDAQ-100 universe."""
    # --- S&P 500 ---
    sp500: list[str] = []
    for label, fetch in [("SPDR SPY holdings", _fetch_spdr),
                          ("Wikipedia S&P 500", _fetch_wikipedia_sp500)]:
        try:
            sp500 = fetch()
            print(f"  S&P 500: {len(sp500)} tickers from {label}")
            break
        except Exception as e:
            print(f"  [warn] {label} failed ({e}), trying next source...")
    if not sp500:
        print("  [warn] All S&P 500 sources failed — using hardcoded fallback.")
        sp500 = _FALLBACK_TICKERS

    # --- NASDAQ-100 ---
    nasdaq100: list[str] = []
    try:
        nasdaq100 = _fetch_nasdaq100()
        print(f"  NASDAQ-100: {len(nasdaq100)} tickers from Wikipedia")
    except Exception as e:
        print(f"  [warn] NASDAQ-100 fetch failed ({e}) — using hardcoded extra list.")
        nasdaq100 = _FALLBACK_NASDAQ100_EXTRA

    # Merge and deduplicate, preserving S&P 500 order first
    seen = set(sp500)
    extras = [t for t in nasdaq100 if t not in seen]
    universe = sp500 + extras
    print(f"  Universe: {len(universe)} tickers total ({len(extras)} NASDAQ-100 additions)")
    return universe

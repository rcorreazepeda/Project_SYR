import io
import requests
import pandas as pd

GICS_TO_ETF: dict[str, str] = {
    "Information Technology": "XLK",
    "Financials":             "XLF",
    "Energy":                 "XLE",
    "Health Care":            "XLV",
    "Industrials":            "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":       "XLP",
    "Utilities":              "XLU",
    "Real Estate":            "XLRE",
    "Materials":              "XLB",
    "Communication Services": "XLC",
}

SECTOR_ETFS = list(set(GICS_TO_ETF.values()))


def get_ticker_sector_etf_map() -> dict[str, str]:
    """Return {ticker: sector_etf} for S&P 500 tickers via Wikipedia table."""
    try:
        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text), attrs={"id": "constituents"})
        df = tables[0]
        result: dict[str, str] = {}
        for _, row in df.iterrows():
            ticker = str(row["Symbol"]).replace(".", "-")
            etf    = GICS_TO_ETF.get(str(row["GICS Sector"]), "")
            if etf:
                result[ticker] = etf
        return result
    except Exception:
        return {}

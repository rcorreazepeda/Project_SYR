import time
import csv
import os
from datetime import datetime
import yfinance as yf

SYMBOLS = ["AAPL", "GOOGL", "MSFT"]
INTERVAL_SECONDS = 60
LOG_FILE = "stock_prices.csv"


def fetch_prices(symbols: list[str]) -> list[dict]:
    records = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            price = info.last_price
            records.append({"timestamp": now, "symbol": symbol, "price": round(price, 4)})
        except Exception as e:
            print(f"  Error fetching {symbol}: {e}")
    return records


def write_csv(records: list[dict], filepath: str):
    file_exists = os.path.isfile(filepath)
    with open(filepath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "price"])
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)


def main():
    print(f"Tracking: {', '.join(SYMBOLS)}")
    print(f"Interval: {INTERVAL_SECONDS}s  |  Log: {LOG_FILE}")
    print("Press Ctrl+C to stop.\n")

    while True:
        records = fetch_prices(SYMBOLS)
        if records:
            write_csv(records, LOG_FILE)
            for r in records:
                print(f"[{r['timestamp']}]  {r['symbol']:6s}  ${r['price']}")
            print()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

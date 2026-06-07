import os
import json
from datetime import datetime, timedelta
import yfinance as yf
import anthropic


def fetch_recent_news(tickers: list[str], days: int = 3) -> dict[str, list[dict]]:
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    result: dict[str, list[dict]] = {}
    for ticker in tickers:
        try:
            raw = yf.Ticker(ticker).news or []
            items = []
            for item in raw:
                ts = item.get("providerPublishTime", 0)
                if ts >= cutoff:
                    items.append({
                        "title":     item.get("title", ""),
                        "publisher": item.get("publisher", ""),
                        "link":      item.get("link", ""),
                        "published": datetime.fromtimestamp(ts).strftime("%b %d, %H:%M"),
                        "sentiment": "NEUTRAL",
                        "reason":    "",
                    })
            result[ticker] = items
        except Exception:
            result[ticker] = []
    return result


def classify_news(ticker: str, articles: list[dict]) -> list[dict]:
    if not articles:
        return articles

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set in environment.")

    headlines = "\n".join(f"{i + 1}. {a['title']}" for i, a in enumerate(articles))

    prompt = f"""You are a stock analyst. Classify each headline as GOOD, BAD, or NEUTRAL \
for ${ticker} stock price over the next 1-5 trading days.

Headlines:
{headlines}

Return ONLY a valid JSON array, one object per headline:
[{{"index": 1, "sentiment": "GOOD", "reason": "one short phrase"}}, ...]

Rules:
- GOOD: earnings beat, upgrade, new product, partnership, buyback, raised guidance
- BAD: earnings miss, downgrade, lawsuit, recall, layoff, guidance cut, investigation
- NEUTRAL: routine filing, minor market update, no direct price impact"""

    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        classifications = json.loads(response.content[0].text)
        idx_map = {c["index"]: c for c in classifications}
        for i, article in enumerate(articles):
            match = idx_map.get(i + 1, {})
            article["sentiment"] = match.get("sentiment", "NEUTRAL")
            article["reason"]    = match.get("reason", "")
    except Exception:
        pass

    return articles

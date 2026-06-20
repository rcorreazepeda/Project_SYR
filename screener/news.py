import os
import json
from datetime import datetime, timedelta
import yfinance as yf
import anthropic


def compute_news_score(articles: list[dict]) -> tuple[int, str]:
    """Return (news_score, label) for a classified article list.

    GOOD headline = +4, BAD = -6, NEUTRAL = 0. Capped at [-20, +20].
    Used to blend into the technical score for a combined ranking.
    """
    if not articles:
        return 0, "—"
    raw = sum(
        4 if a.get("sentiment") == "GOOD" else -6 if a.get("sentiment") == "BAD" else 0
        for a in articles
    )
    score = max(-20, min(20, raw))
    good = sum(1 for a in articles if a.get("sentiment") == "GOOD")
    bad  = sum(1 for a in articles if a.get("sentiment") == "BAD")
    if score > 0:
        label = f"+{score} ({good}G/{bad}B)"
    elif score < 0:
        label = f"{score} ({good}G/{bad}B)"
    else:
        label = f"0 ({good}G/{bad}B)"
    return score, label


def _parse_news_item(item: dict, cutoff: float) -> dict | None:
    """Handle both old yfinance flat format and new 1.4.x nested content format."""
    # New format: {id, content: {title, pubDate, provider, canonicalUrl, ...}}
    content = item.get("content", {})
    if content:
        title     = content.get("title", "")
        publisher = content.get("provider", {}).get("displayName", "")
        link      = (content.get("canonicalUrl") or content.get("clickThroughUrl") or {}).get("url", "")
        pub_str   = content.get("pubDate", "")
        try:
            ts = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = 0.0
    else:
        # Old flat format: {title, publisher, link, providerPublishTime}
        title     = item.get("title", "")
        publisher = item.get("publisher", "")
        link      = item.get("link", "")
        ts        = float(item.get("providerPublishTime", 0))

    if ts < cutoff or not title:
        return None
    return {
        "title":     title,
        "publisher": publisher,
        "link":      link,
        "published": datetime.fromtimestamp(ts).strftime("%b %d, %H:%M"),
        "sentiment": "NEUTRAL",
        "reason":    "",
    }


def fetch_recent_news(tickers: list[str], days: int = 3) -> dict[str, list[dict]]:
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    result: dict[str, list[dict]] = {}
    for ticker in tickers:
        try:
            raw = yf.Ticker(ticker).news or []
            items = [p for item in raw if (p := _parse_news_item(item, cutoff)) is not None]
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
        raw_text = response.content[0].text.strip()
        # Strip markdown code fences if present (```json ... ``` or ``` ... ```)
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        classifications = json.loads(raw_text)
        idx_map = {c["index"]: c for c in classifications}
        for i, article in enumerate(articles):
            match = idx_map.get(i + 1, {})
            article["sentiment"] = match.get("sentiment", "NEUTRAL")
            article["reason"]    = match.get("reason", "")
    except Exception:
        pass

    return articles

"""Supabase persistence layer — all DB reads and writes go through here.

Schema (run once in Supabase SQL editor):

    CREATE TABLE screener_picks (
        id            BIGSERIAL PRIMARY KEY,
        run_date      DATE        NOT NULL,
        timeframe     TEXT        NOT NULL,
        ticker        TEXT        NOT NULL,
        technical_score INTEGER   NOT NULL,
        news_score    INTEGER     DEFAULT 0,
        combined_score INTEGER    NOT NULL,
        entry_price   NUMERIC(10,2),
        target_price  NUMERIC(10,2),
        expected_return_pct NUMERIC(6,2),
        take_profit_pct     NUMERIC(5,1),
        stop_loss_pct       NUMERIC(5,1),
        hold_days     INTEGER,
        signals       TEXT,
        in_bull       BOOLEAN,
        vix_val       NUMERIC(6,2),
        breadth_pct   NUMERIC(5,1),
        created_at    TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(run_date, timeframe, ticker)
    );

    CREATE TABLE trades (
        id              BIGSERIAL PRIMARY KEY,
        date_entered    DATE        NOT NULL,
        ticker          TEXT        NOT NULL,
        timeframe       TEXT,
        score           INTEGER,
        entry_price     NUMERIC(10,2) NOT NULL,
        screener_target NUMERIC(10,2),
        screener_return_pct NUMERIC(6,2),
        signals         TEXT,
        exit_date       DATE,
        exit_price      NUMERIC(10,2),
        actual_return_pct NUMERIC(6,2),
        held_days       INTEGER,
        outcome         TEXT,
        notes           TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE ai_analysis (
        id            BIGSERIAL PRIMARY KEY,
        run_date      DATE        NOT NULL,
        analysis_text TEXT        NOT NULL,
        top_picks_5d  TEXT,
        top_picks_30d TEXT,
        top_picks_180d TEXT,
        model_used    TEXT        DEFAULT 'claude-sonnet-4-6',
        created_at    TIMESTAMPTZ DEFAULT NOW()
    );
"""
import os
from datetime import date
from typing import Optional

try:
    from supabase import create_client, Client
    _SUPABASE_AVAILABLE = True
except ImportError:
    _SUPABASE_AVAILABLE = False


def get_client() -> Optional["Client"]:
    if not _SUPABASE_AVAILABLE:
        return None
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    return create_client(url, key)


def save_screener_run(
    client: "Client",
    df,
    tf_key: str,
    run_date: date,
    in_bull: bool,
    vix_val: float,
    breadth_pct: float,
    cfg: dict,
    top_n: int = 20,
) -> None:
    rows = []
    for _, row in df.head(top_n).iterrows():
        tech_score = int(row.get("score", 0))
        news_score = int(row.get("news_score", 0))
        rows.append({
            "run_date":             str(run_date),
            "timeframe":            tf_key,
            "ticker":               row["ticker"],
            "technical_score":      tech_score,
            "news_score":           news_score,
            "combined_score":       int(row.get("combined_score", tech_score + news_score)),
            "entry_price":          float(row.get("price", 0)),
            "target_price":         float(row.get("expected_price", 0)),
            "expected_return_pct":  float(row.get("expected_return_%", 0)),
            "take_profit_pct":      float(cfg.get("take_profit_pct", 0)),
            "stop_loss_pct":        float(cfg.get("stop_loss_pct", 0)),
            "hold_days":            int(cfg.get("hold_days", 0)),
            "signals":              "  ·  ".join(row.get("signals", [])[:4]),
            "in_bull":              bool(in_bull),
            "vix_val":              round(float(vix_val), 2),
            "breadth_pct":          round(float(breadth_pct), 1),
        })
    if rows:
        client.table("screener_picks").upsert(rows, on_conflict="run_date,timeframe,ticker").execute()


def save_ai_analysis(
    client: "Client",
    run_date: date,
    analysis_text: str,
    top_picks: dict[str, str],
    model: str = "claude-sonnet-4-6",
) -> None:
    client.table("ai_analysis").upsert({
        "run_date":       str(run_date),
        "analysis_text":  analysis_text,
        "top_picks_5d":   top_picks.get("5d", ""),
        "top_picks_30d":  top_picks.get("30d", ""),
        "top_picks_180d": top_picks.get("180d", ""),
        "model_used":     model,
    }, on_conflict="run_date").execute()


def get_recent_picks(client: "Client", days: int = 30):
    """Return screener_picks from the last N days as a list of dicts."""
    from datetime import timedelta
    since = str(date.today() - timedelta(days=days))
    resp = (
        client.table("screener_picks")
        .select("*")
        .gte("run_date", since)
        .order("run_date", desc=True)
        .execute()
    )
    return resp.data or []


def get_ticker_consistency(client: "Client", timeframe: str = "180d", days: int = 30) -> dict[str, dict]:
    """Return how consistently each ticker appeared in top-20 picks over the last N screener runs.

    Result: {ticker: {"days": N, "total": M, "pct": int}}
    A ticker with pct=90 appeared in 9 out of 10 recent runs — much more reliable than a one-day spike.
    """
    from datetime import timedelta
    from collections import Counter
    since = str(date.today() - timedelta(days=days))
    resp = (
        client.table("screener_picks")
        .select("run_date,ticker")
        .eq("timeframe", timeframe)
        .gte("run_date", since)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return {}
    total_days = len({r["run_date"] for r in rows})
    counts = Counter(r["ticker"] for r in rows)
    return {
        ticker: {"days": cnt, "total": total_days, "pct": round(cnt / total_days * 100)}
        for ticker, cnt in counts.items()
    }


def get_latest_ai_analysis(client: "Client") -> Optional[dict]:
    resp = (
        client.table("ai_analysis")
        .select("*")
        .order("run_date", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_all_trades(client: "Client", owner: str = "raul"):
    resp = (
        client.table("trades")
        .select("*")
        .eq("owner", owner)
        .order("date_entered", desc=True)
        .execute()
    )
    return resp.data or []


def get_all_owners(client: "Client") -> list[str]:
    resp = client.table("trades").select("owner").execute()
    seen = set()
    owners = []
    for row in (resp.data or []):
        o = row.get("owner") or "raul"
        if o not in seen:
            seen.add(o)
            owners.append(o)
    return owners or ["raul"]


def save_trade(client: "Client", trade: dict) -> None:
    client.table("trades").upsert(trade).execute()


def close_trade(client: "Client", trade_id: int, exit_price: float,
                exit_date: str, actual_return_pct: float) -> None:
    outcome = "WIN" if actual_return_pct > 0 else "LOSS"
    client.table("trades").update({
        "exit_price":        round(exit_price, 6),
        "exit_date":         exit_date,
        "actual_return_pct": round(actual_return_pct, 4),
        "outcome":           outcome,
    }).eq("id", trade_id).execute()


def dca_trade(client: "Client", trade_id: int, added_shares: float,
              added_price: float, old_shares: float, old_price: float) -> None:
    total_shares = old_shares + added_shares
    avg_price    = (old_shares * old_price + added_shares * added_price) / total_shares
    client.table("trades").update({
        "shares":      round(total_shares, 8),
        "entry_price": round(avg_price, 6),
    }).eq("id", trade_id).execute()


def get_open_trade(client: "Client", ticker: str, owner: str) -> Optional[dict]:
    resp = (
        client.table("trades")
        .select("*")
        .eq("ticker", ticker)
        .eq("owner", owner)
        .eq("outcome", "OPEN")
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def save_trades_bulk(client: "Client", trades: list[dict]) -> None:
    if trades:
        client.table("trades").upsert(trades).execute()


def get_picks_pending_outcome(client: "Client", timeframe: str, run_date: str) -> list[dict]:
    """Return picks from a specific run date that haven't been outcome-checked yet."""
    resp = (
        client.table("screener_picks")
        .select("*")
        .eq("timeframe", timeframe)
        .eq("run_date", run_date)
        .is_("outcome", "null")
        .execute()
    )
    return resp.data or []


def update_pick_outcome(
    client: "Client",
    pick_id: int,
    actual_return_pct: float,
    exit_price: float,
    outcome: str,
    checked_date: str,
) -> None:
    client.table("screener_picks").update({
        "actual_return_pct":    round(actual_return_pct, 2),
        "exit_price":           round(exit_price, 2),
        "outcome":              outcome,
        "outcome_checked_date": checked_date,
    }).eq("id", pick_id).execute()


def get_picks_with_outcomes(client: "Client", days: int = 90) -> list[dict]:
    """Return all picks that have been outcome-checked in the last N days."""
    from datetime import timedelta
    since = str(date.today() - timedelta(days=days))
    resp = (
        client.table("screener_picks")
        .select("*")
        .gte("run_date", since)
        .not_.is_("outcome", "null")
        .order("run_date", desc=True)
        .execute()
    )
    return resp.data or []

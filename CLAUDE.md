# CLAUDE.md — S&P 500 Multi-Timeframe Stock Screener

This file gives Claude full context on the project so every session starts informed.

---

## What this project is

A personal stock screener that scores every S&P 500 stock daily across three holding
horizons (5-day, 30-day, 180-day) using technical indicators, market regime, VIX
sentiment, market breadth, and sector ETF relative strength. Deployed as a Streamlit
app at https://projectsyr.streamlit.app/

The owner (Raul) uses this every week to pick 3–5 stocks to trade. He is not a
professional trader — the screener is a decision-support tool, not an automated
trading system.

---

## Architecture

```
app.py                  Streamlit UI — 6 tabs (3 trading + news + portfolio + performance)
daily_job.py            GitHub Actions scheduled runner (runs after market close)
cli.py                  Terminal runner (same logic, CSV output)
screener/
  config.py             Timeframe configs and all scoring weights
  universe.py           S&P 500 ticker list (SPDR → Wikipedia → fallback)
  indicators.py         RSI, MACD, Bollinger, Stochastic, ATR, OBV
  scoring.py            score_ticker() — the core scoring engine
  forecast.py           Expected price target (ATR + mean-reversion blend)
  earnings.py           Earnings proximity check (FMP API → yfinance fallback)
  sectors.py            Maps each ticker to its SPDR sector ETF (XLK, XLF…)
  news.py               Fetches headlines, classifies with Claude, compute_news_score()
  database.py           Supabase client — save picks, trades, AI analysis; read history
.github/
  workflows/
    daily_screener.yml  Cron job: Mon-Fri 4:30 PM ET, runs daily_job.py
pages/
  1_📖_Metrics_Guide.py  In-app explanation of every metric
trade_log.csv           Manual trade log template
```

---

## Scoring model — how score_ticker() works

Each stock gets a raw score (0–120+). Higher = more signals aligned.

**Signals and weights (vary by timeframe — see config.py):**
- RSI in recovery zone: +15–25
- Stochastic crossover from oversold: +10–20
- MACD bullish crossover: +10–25 (fresh cross scores higher)
- Price > SMA_fast > SMA_slow (uptrend): +20–30
- Golden cross (SMA50 > SMA200, 180d only): +20
- Price below/near lower Bollinger Band: +5–15
- OBV bullish divergence: +15–20
- OBV rising (accumulation): +10
- Volume surge >1.5×: +10–15
- ATR momentum in healthy range: +10
- RS vs SPY — leader (+15–25), outperform (+7–12), laggard (−10)
- RS vs Sector ETF — leader (+12), outperform (+6), laggard (−8)
- VIX > 35 (panic): −10 | VIX 25–35 (fear): +8 | VIX < 15 (complacent): −5
- Market breadth > 65%: +10 | breadth < 40%: −8
- Bear regime multiplier: mean-reversion signals × 0.5

**Entry threshold:** Score ≥ 60 (bull) or ≥ 70 (bear), at least 3 signals, no earnings soon.

**Timeframes:**
- `5d`: SMA20/50, RSI 30–50, BB20, rs_days=21, TP+5% / SL−3%
- `30d`: SMA50/200, RSI 40–60, BB20, rs_days=63, TP+12% / SL−6%
- `180d`: SMA50/200, RSI 45–65, BB50, rs_days=126, TP+25% / SL−12%

---

## Data sources

| Data | Source | Fallback |
|------|--------|---------|
| Ticker universe | SPDR SPY holdings CSV | Wikipedia → hardcoded 50 |
| Price / OHLCV | Yahoo Finance (yfinance) | — |
| Earnings calendar | FMP API | yfinance per-ticker |
| Sector mapping | Wikipedia S&P 500 table | empty dict (sector RS skipped) |
| News headlines | Yahoo Finance (yfinance .news) | — |
| News sentiment | Claude Haiku (claude-haiku-4-5) | — |
| VIX | Yahoo Finance (^VIX) | defaults to 20.0 |

**Known API issues:**
- SPDR site blocks cloud servers — Wikipedia fallback always fires on Streamlit Cloud
- FMP earnings returns 403 on free plan — yfinance fallback always fires
- Both are expected and handled gracefully

---

## Deployment

- **Streamlit Community Cloud** at https://projectsyr.streamlit.app/
- **GitHub repo:** https://github.com/rcorreazepeda/Project_SYR (branch: main)
- Auto-deploys on every push to main
- Secrets set in Streamlit Cloud dashboard (not in code):
  - `FMP_API_KEY`
  - `ANTHROPIC_API_KEY`
- Local `.env` file has the same keys — never commit it

**Python version on Streamlit Cloud:** 3.14
**Key dependency issue:** `lxml` cannot compile on Streamlit Cloud — use `html5lib` for `pd.read_html()`

---

## Streamlit app — session state keys

| Key | Contains |
|-----|---------|
| `raw_data` | dict with close/high/low/volume DataFrames |
| `tickers` | list of S&P 500 tickers |
| `sector_map` | dict {ticker: sector_etf_symbol} |
| `df_{tf}` | scored DataFrame for timeframe tf (5d/30d/180d) |
| `bull_{tf}` | bool — is market in bull regime for tf |
| `spy_{tf}` | SPY return float for tf |
| `vix_{tf}` | VIX value float |
| `breadth_{tf}` | market breadth % float |
| `news_results` | {ticker: [articles]} from last news fetch |
| `last_run` | timestamp string |

---

## Owner preferences and style

- Keep code concise — no unnecessary abstractions
- No comments unless the WHY is non-obvious
- Button-triggered AI features (not auto-run) to control API costs
- Tax rate slider defaults to 25% (Raul's approximate bracket is ~32% federal)
- The app is called "R&S Stock Plan" in the UI title

---

## Known issues / tech debt

- `use_container_width` deprecation warnings in newer Streamlit (cosmetic only)
- FMP API key is on free plan — earnings endpoint returns 403
- Sector map fails silently if Wikipedia is down (sector RS just skipped)
- VIX defaults to 20.0 if ^VIX download fails

---

## News score blending

After running the News tab ("Fetch & Classify News"), each article is scored:
- GOOD headline: +4 points
- BAD headline: −6 points (penalty is heavier — bad news is more actionable)
- NEUTRAL: 0
- Cap: [−20, +20] per ticker

`news_scores` dict is stored in `st.session_state` and all three trading tabs
automatically add "News" and "Combined" columns, re-sorting by combined score.

`compute_news_score(articles)` in `screener/news.py` returns `(score, label)`.

## Daily automated job (GitHub Actions)

`daily_job.py` runs Mon–Fri at 4:30 PM ET via `.github/workflows/daily_screener.yml`.

Flow:
1. Downloads all S&P 500 data
2. Scores 5d / 30d / 180d timeframes
3. Fetches + classifies news for top 20 unique picks
4. Blends news scores → combined_score
5. Saves top 20 per timeframe to Supabase `screener_picks`
6. Loads last 30 days of picks from Supabase for context
7. Claude Sonnet analyzes results + suggests scoring improvements
8. Saves AI analysis to Supabase `ai_analysis`

GitHub Actions secrets needed: `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`

## Supabase schema (run once in SQL Editor)

See docstring at top of `screener/database.py` for the full CREATE TABLE statements.

Three tables:
- `screener_picks` — daily automated results (UNIQUE on run_date + timeframe + ticker)
- `trades` — Raul's actual trades (uploaded via Portfolio tab)
- `ai_analysis` — Claude's daily analysis text (UNIQUE on run_date)

## Streamlit secrets needed (Streamlit Cloud dashboard)

```
ANTHROPIC_API_KEY = "..."
SUPABASE_URL      = "https://xxxx.supabase.co"
SUPABASE_KEY      = "eyJ..."
FMP_API_KEY       = "..."   (optional — yfinance fallback used otherwise)
```

## Roadmap (remaining)

- **AI email** — send daily analysis via SendGrid / Resend after GitHub Actions job
- **Outcome tracking** — auto-compare picks to actual prices N days later in `price_snapshots` table
- **Signal win rate breakdown** — parse signals column to compute per-signal accuracy

---

## How to run locally

```bash
cd Project_SYR
streamlit run app.py        # UI at http://localhost:8501
python3 cli.py              # terminal, 5-day default
python3 cli.py --days 30
python3 cli.py --days 180
```

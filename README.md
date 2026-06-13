# R&S Stock Plan — S&P 500 Multi-Timeframe Screener

Ranks all S&P 500 stocks by technical setup quality across **three holding horizons** — 5-day swing, 30-day position, and 180-day trend. Runs automatically every weekday at 4:30 PM ET and saves results to a database for historical tracking.

Live app: **https://projectsyr.streamlit.app/**

---

## What it does

- Scores every S&P 500 stock using RSI, MACD, Bollinger Bands, OBV, volume, ATR momentum, relative strength vs SPY and sector ETFs, VIX sentiment, and market breadth
- Blends in **news sentiment** (Claude Haiku classifies last 3 days of headlines — GOOD/BAD/NEUTRAL) into a combined score
- Runs **automatically Mon-Fri at 4:30 PM ET** via GitHub Actions, saves picks to Supabase
- **Claude Sonnet analyzes** each daily run — reviews signal accuracy vs prior picks and suggests scoring improvements
- **Portfolio tab** — track your actual trades with live P&L vs screener targets
- **Performance tab** — screener accuracy history and AI analysis from each daily run

---

## Folder structure

```
Project_SYR/
├── app.py                         # Streamlit UI — 6 tabs
├── daily_job.py                   # GitHub Actions scheduled runner
├── cli.py                         # Terminal runner (CSV output)
├── screener/
│   ├── __init__.py                # Public API exports
│   ├── config.py                  # Timeframe configs & scoring weights
│   ├── universe.py                # S&P 500 tickers (SPDR → Wikipedia → fallback)
│   ├── indicators.py              # RSI, MACD, Bollinger, Stochastic, ATR, OBV
│   ├── scoring.py                 # score_ticker() — core scoring engine
│   ├── forecast.py                # Expected price target (ATR + mean-reversion)
│   ├── earnings.py                # Earnings proximity check
│   ├── news.py                    # Fetch headlines + Claude classification + news score
│   ├── sectors.py                 # Maps tickers to SPDR sector ETFs (XLK, XLF…)
│   └── database.py                # Supabase read/write helpers
├── .github/
│   └── workflows/
│       └── daily_screener.yml     # Cron job — Mon-Fri 4:30 PM ET
├── pages/
│   └── 1_📖_Metrics_Guide.py     # In-app explanation of every metric
├── .env                           # API keys — never commit
├── requirements.txt
└── README.md
```

---

## Tabs

| Tab | What it shows |
|-----|--------------|
| **📅 5-Day Trading** | Short-term swing setups, TP +5% / SL −3% |
| **📆 30-Day Trading** | Medium-term positions, TP +12% / SL −6% |
| **📈 180-Day Trading** | Long-term trend leaders, TP +25% / SL −12% |
| **📰 News** | Last 3 days of headlines per stock, classified by Claude Haiku |
| **💼 Portfolio** | Upload your trades (CSV or manual), live P&L from Yahoo Finance |
| **📊 Performance** | Screener history, most-picked stocks, AI analysis from daily runs |

---

## The three timeframes

| Timeframe | Moving averages | RSI zone | RS lookback | Take profit | Stop loss |
|-----------|----------------|----------|-------------|-------------|-----------|
| **5-Day** | SMA20 / SMA50 | 30–50 | 1 month | +5% | −3% |
| **30-Day** | SMA50 / SMA200 | 40–60 | 3 months | +12% | −6% |
| **180-Day** | SMA50 / SMA200 | 45–65 | 6 months | +25% | −12% |

---

## Scoring model

Each stock gets a raw technical score (0–120+). After news is fetched, a news score (−20 to +20) is blended in to produce a **combined score**.

| Signal | Points |
|--------|--------|
| RSI in recovery zone | +15–25 |
| Stochastic bullish crossover | +10–20 |
| MACD bullish crossover (fresh) | +10–25 |
| Price > SMA fast > SMA slow (uptrend) | +20–30 |
| Golden cross SMA50 > SMA200 | +20 (180d only) |
| Price near / below lower Bollinger Band | +5–15 |
| OBV bullish divergence | +15–20 |
| OBV rising (accumulation) | +10 |
| Volume surge >1.5× | +10–15 |
| ATR momentum in healthy range | +10 |
| RS vs SPY — leader / outperforming | +7–25 |
| RS vs sector ETF — leader / outperforming | +6–12 |
| VIX 25–35 (fear, mean-reversion favored) | +8 |
| Market breadth >65% above SMA50 | +10 |
| VIX >35 (panic) | −10 |
| VIX <15 (complacent) | −5 |
| Market breadth <40% | −8 |
| Stock lagging SPY / sector | −8 to −10 |
| Bear regime | mean-reversion signals ×0.5 |
| News — GOOD headline | +4 each (max +20) |
| News — BAD headline | −6 each (min −20) |

**Entry threshold:** Score ≥ 60 (bull) or ≥ 70 (bear), at least 3 signals, no earnings soon.

---

## Entry rules

**Enter if:**
- Combined score ≥ 60 (BULL) or ≥ 70 (BEAR)
- At least 3 different signals listed
- Earnings column is blank
- News score is neutral or positive

**Skip if:**
- `⚠ Soon` in Earnings — overnight gap risk
- RSI > 70 (overbought — chasing)
- News score is heavily negative (−10 or worse)
- Bear regime with only mean-reversion signals

**Best signal combos (priority order):**
1. Fresh MACD crossover + RSI recovering + OBV rising/divergence
2. Stochastic crossover from oversold + volume surge ≥ 1.5×
3. Price > SMA fast > SMA slow + RS vs SPY ≥ 1.3×
4. *(180-day only)* Golden cross + RS ≥ 1.3× + MACD positive

---

## Position sizing

| Score | Allocation |
|-------|-----------|
| 80+ | 30–35% of weekly budget |
| 60–79 | 20–25% |
| 40–59 | 10–15% (BULL only) |
| < 40 | Skip |

Spread across 3–5 stocks per timeframe. Never concentrate in one pick.

---

## One-time local setup

```bash
pip install -r requirements.txt
```

Create `.env` in the project root:
```
ANTHROPIC_API_KEY=sk-ant-...
FMP_API_KEY=...
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJ...
```

Run locally:
```bash
streamlit run app.py        # UI at http://localhost:8501
python cli.py               # terminal, 5-day default
python cli.py --days 30
python cli.py --days 180
```

---

## Cloud deployment

| Service | Purpose | Cost |
|---------|---------|------|
| Streamlit Community Cloud | Hosts the web app | Free |
| GitHub Actions | Runs daily screener Mon-Fri 4:30 PM ET | Free |
| Supabase | PostgreSQL database (picks, trades, AI analysis) | Free |
| Anthropic API | News classification (Haiku) + daily analysis (Sonnet) | ~$1–2/month |

### Streamlit Cloud secrets
Set in App settings → Secrets:
```toml
ANTHROPIC_API_KEY = "sk-ant-..."
SUPABASE_URL      = "https://xxxx.supabase.co"
SUPABASE_KEY      = "eyJ..."
FMP_API_KEY       = "..."
```

### GitHub Actions secrets
Set in Repo → Settings → Secrets → Actions:
```
ANTHROPIC_API_KEY
SUPABASE_URL
SUPABASE_KEY
```

### Supabase setup
Run the SQL schema from the docstring at the top of `screener/database.py` in the Supabase SQL Editor. Three tables: `screener_picks`, `trades`, `ai_analysis`. Leave RLS disabled (service_role key is used server-side by both GitHub Actions and Streamlit Cloud).

---

## Data sources

| Data | Source | Fallback |
|------|--------|---------|
| Ticker universe | SPDR SPY holdings CSV | Wikipedia → hardcoded 50 |
| Price / OHLCV | Yahoo Finance (yfinance, 2-year bulk) | — |
| Earnings calendar | FMP API | yfinance per-ticker |
| Sector mapping | Wikipedia S&P 500 table | empty (sector RS skipped) |
| News headlines | Yahoo Finance (yfinance .news) | — |
| News sentiment | Claude Haiku | — |
| Daily AI analysis | Claude Sonnet | — |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| SPDR holdings warning | Expected on cloud — Wikipedia fallback activates automatically |
| FMP earnings 403 | Expected on free plan — yfinance fallback activates automatically |
| Breadth showing 0% | Fixed — ffill() applied before SMA50 comparison |
| Performance tab empty | GitHub Actions job hasn't run yet — trigger manually via Actions tab |
| Supabase writes failing | Check service_role key is set (not anon key) and RLS is disabled |
| App hangs >5 min | Yahoo Finance throttling — wait a few minutes and retry |

---

> **Disclaimer:** This screener identifies statistical setups — not guaranteed outcomes.
> Past signal performance does not guarantee future results. Never invest money you cannot afford to lose.

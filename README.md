# R&S Stock Plan — S&P 500 Multi-Timeframe Screener

Ranks all S&P 500 stocks by technical setup quality across **three holding horizons** — 5-day swing, 30-day position, and 180-day trend. Runs automatically every weekday at 4:30 PM ET, saves results to a database, and emails a daily report with top picks, news headlines, and open portfolio P&L.

Live app: **https://projectsyr.streamlit.app/**

---

## What it does

- Scores every S&P 500 stock using RSI, MACD, Bollinger Bands, OBV, volume, ATR momentum, relative strength vs SPY and sector ETFs, VIX sentiment, and market breadth
- Blends in **news sentiment** (Claude Haiku classifies recent headlines — GOOD/BAD/NEUTRAL) into a combined score
- Runs **automatically Mon-Fri at 4:30 PM ET** via GitHub Actions, saves picks to Supabase
- **Claude Sonnet analyzes** each daily run — reviews signal accuracy vs prior picks and suggests scoring improvements
- **Daily email** — top picks, clickable news headlines, open portfolio P&L per owner, AI analysis
- **Multi-portfolio support** — separate trade logs for multiple people (Raul, Sofia, etc.), each receiving their own email
- **Portfolio tab** — track trades with live P&L, total invested vs current value, categories, per-owner view
- **Performance tab** — screener accuracy history and AI analysis from each daily run

---

## Folder structure

```
Project_SYR/
├── app.py                         # Streamlit UI — 6 tabs
├── daily_job.py                   # GitHub Actions scheduled runner + email
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
├── trade_log.csv                  # CSV template for uploading trades
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
| **📰 News** | Recent headlines per stock, classified by Claude Haiku. Adjustable window (3–14 days). News score blends into combined score on trading tabs. |
| **💼 Portfolio** | Per-owner trade log. Upload CSV or add manually. Shows invested, current value, P&L $, category. Live prices from Yahoo Finance. |
| **📊 Performance** | Screener history, signal win rates, AI analysis from daily runs |

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

## Multi-portfolio setup

Each person gets their own isolated trade log and a separate daily email with only their open positions. Screener picks and AI analysis are shared (they're market data).

**To add a new portfolio (e.g. Sofia):**

1. In the app Portfolio tab, select **＋ New portfolio…** and type `sofia`
2. Add a GitHub Actions secret: `ALERT_EMAIL_SOFIA = sofia@email.com`
3. Upload Sofia's trades via CSV or add them manually while "Sofia" is selected

The daily email automatically sends one per owner based on `ALERT_EMAIL_<NAME>` secrets.

---

## Daily email

Sent Mon-Fri after market close. Contains:
- **Market regime** — Bull/Bear, VIX, breadth per timeframe
- **Top 5 picks** per timeframe with tech score, news score, combined score, price → target, top signals
- **📰 Key Headlines** — clickable GOOD/BAD article titles for today's top picks (neutral headlines excluded)
- **💼 Open Positions** — per-owner: invested, current value, P&L $, status vs TP/SL, ⭐ still in picks flag
- **AI Analysis** — Claude Sonnet's commentary on the day's picks and signal trends

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
RESEND_API_KEY=re_...
```

Run locally:
```bash
streamlit run app.py        # UI at http://localhost:8501
python cli.py               # terminal, 5-day default
python cli.py --days 30
python cli.py --days 180
python daily_job.py         # run the full daily pipeline locally
```

---

## Cloud deployment

| Service | Purpose | Cost |
|---------|---------|------|
| Streamlit Community Cloud | Hosts the web app | Free |
| GitHub Actions | Runs daily screener Mon-Fri 4:30 PM ET | Free |
| Supabase | PostgreSQL database (picks, trades, AI analysis) | Free |
| Resend | Daily email delivery | Free (3,000/month) |
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
RESEND_API_KEY
ALERT_EMAIL              # your email (Raul)
ALERT_EMAIL_SOFIA        # Sofia's email (add more as ALERT_EMAIL_<NAME>)
```

### Supabase setup
Run once in the Supabase SQL Editor:
```sql
-- Full schema is in the docstring at the top of screener/database.py
-- Minimum required:
CREATE TABLE screener_picks (...);
CREATE TABLE trades (...);
CREATE TABLE ai_analysis (...);

-- Additional columns added after initial setup:
ALTER TABLE trades ADD COLUMN shares NUMERIC(12,4);
ALTER TABLE trades ADD COLUMN total_invested NUMERIC(12,2);
ALTER TABLE trades ADD COLUMN category TEXT;
ALTER TABLE trades ADD COLUMN owner TEXT NOT NULL DEFAULT 'raul';
```

Leave RLS disabled — service_role key is used server-side.

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
| Email delivery | Resend | — |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| SPDR holdings warning | Expected on cloud — Wikipedia fallback activates automatically |
| FMP earnings 403 | Expected on free plan — yfinance fallback activates automatically |
| News scores always 0 | Widen the news window slider (try 7–14 days); check ANTHROPIC_API_KEY is set |
| No email received | Check `RESEND_API_KEY` and `ALERT_EMAIL` are set in GitHub Actions secrets |
| Portfolio tab shows wrong owner | Select the correct portfolio from the dropdown at the top of the tab |
| Performance tab empty | GitHub Actions job hasn't run yet — trigger manually via Actions tab |
| Supabase writes failing | Check service_role key is set (not anon key) and RLS is disabled |
| App hangs >5 min | Yahoo Finance throttling — wait a few minutes and retry |

---

> **Disclaimer:** This screener identifies statistical setups — not guaranteed outcomes.
> Past signal performance does not guarantee future results. Never invest money you cannot afford to lose.

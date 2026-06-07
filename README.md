# S&P 500 Multi-Timeframe Screener

Ranks all S&P 500 stocks by technical setup quality across **three holding horizons** — 5-day swing, 30-day position, and 180-day trend. Targets statistically likely positive returns within each window.

---

## Folder Structure

```
Project_SYR/
├── app.py                      # Streamlit UI — run this every Sunday
├── cli.py                      # Terminal runner (optional, no browser needed)
├── screener/                   # Core engine (Python package)
│   ├── __init__.py             # Public API exports
│   ├── config.py               # Timeframe configurations & scoring weights
│   ├── universe.py             # S&P 500 ticker fetching (SPDR → Wikipedia → fallback)
│   ├── indicators.py           # RSI, MACD, Bollinger, Stochastic, ATR, OBV
│   ├── scoring.py              # Parameterized score_ticker() used by all timeframes
│   ├── forecast.py             # Expected price calculation (ATR + mean-reversion blend)
│   └── earnings.py             # Earnings proximity check (FMP API → yfinance fallback)
├── pages/
│   └── 1_📖_Metrics_Guide.py  # In-app explanation of every metric
├── .env                        # API keys — never commit this
├── .gitignore
├── requirements.txt
└── README.md
```

---

## One-Time Setup

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. Set your API key in `.env`**
```
FMP_API_KEY=your_key_here
```
Free key at https://financialmodelingprep.com/developer/docs

---

## Every Sunday — UI (Recommended)

```bash
streamlit run app.py
```
Opens at http://localhost:8501

1. Click **Run Screener** in the sidebar
2. Switch between the three tabs for each timeframe
3. Read the regime banner, results table, and chart

---

## Every Sunday — CLI (Terminal only)

```bash
python cli.py              # 5-day view (default)
python cli.py --days 30   # 30-day view
python cli.py --days 180  # 180-day view
```

Results are also saved to `screener_results_<tf>_<date>.csv`.

---

## The Three Timeframes

| Tab | Hold Period | Entry | Exit | Goal |
|-----|------------|-------|------|------|
| **📅 5-Day** | 5 trading days | Monday open | Friday close | Short-term reversal / momentum |
| **📆 30-Day** | ~1 calendar month | Week 1 open | ~Week 4-5 | Medium-term trend continuation |
| **📈 180-Day** | ~6 calendar months | Month 1 | ~Month 6 | Long-term uptrend / leader stocks |

Each timeframe uses different signal zones, moving average pairs, and scoring weights — all configured in `screener/config.py`.

| Config | 5-Day | 30-Day | 180-Day |
|--------|-------|--------|---------|
| Moving averages | SMA20 / SMA50 | SMA50 / SMA200 | SMA50 / SMA200 |
| RSI sweet spot | 30–50 | 40–60 | 45–65 |
| Bollinger period | 20 | 20 | 50 |
| Relative strength | 1-month vs SPY | 3-month vs SPY | 6-month vs SPY |
| Take profit | +5% | +12% | +25% |
| Stop loss | −3% | −6% | −12% |

---

## Reading the Results Table

| Column | What it means |
|--------|--------------|
| **Score** | 0–120+. Higher = more signals aligned |
| **Entry Price** | Last Friday's closing price |
| **Expected (Nd)** | Statistical price target at end of hold period |
| **Exp. Return** | `(Expected − Entry) / Entry × 100%` |
| **RSI** | 35–50 ideal for 5d; 40–60 for 30d; 45–65 for 180d |
| **Stoch %K** | <20 and crossing up = reversal confirmed |
| **Vol ×** | Recent volume vs average. >1.5 = conviction |
| **Earnings** | ⚠ Soon = report within hold window — skip this stock |
| **Top Signals** | Plain-English reasons behind the score |

---

## Entry Rules

**Enter if:**
- Score ≥ **60** (BULL) or ≥ **70** (BEAR)
- At least **3 different signals** listed
- Earnings column is **blank**
- RSI and Stoch %K are in the correct zone for the timeframe

**Skip if:**
- `⚠ Soon` in Earnings — unpredictable overnight gap risk
- RSI > 70 (overbought — chasing)
- Only mean-reversion signals in a BEAR regime
- Score < 60

**Best combos (in priority order):**
1. Fresh MACD crossover + RSI recovering + OBV rising/divergence
2. Stochastic crossover from oversold + volume surge ≥ 1.5×
3. Price > SMA fast > SMA slow + RS vs SPY ≥ 1.3×
4. *(180-day only)* Golden cross + RS ≥ 1.3× + MACD positive

---

## Position Sizing

Spread across 3–5 stocks per timeframe. Never concentrate in one pick.

| Score | Allocation |
|-------|-----------|
| 80+   | 30–35% of weekly budget |
| 60–79 | 20–25% |
| 40–59 | 10–15% (BULL only) |
| < 40  | Skip |

---

## Data Sources

| Data | Source | Fallback |
|------|--------|---------|
| Ticker universe | SPDR SPY holdings (official daily CSV) | Wikipedia → hardcoded 50 |
| Price / OHLCV | Yahoo Finance via yfinance (2-year bulk download) | — |
| Earnings calendar | Financial Modeling Prep API (one bulk call) | yfinance per-ticker |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Could not fetch S&P 500 list` | SPDR site down — Wikipedia fallback activates automatically |
| `Download returned no data` | No internet connection |
| `FMP earnings failed` | API limit or plan issue — yfinance fallback activates automatically |
| App hangs > 5 min | Yahoo Finance throttling — close and rerun |
| IDE import warnings | Select the correct Python interpreter: Cmd+Shift+P → "Python: Select Interpreter" |

---

> **Reminder:** This screener identifies statistical setups — not guaranteed outcomes.
> Past signal performance does not guarantee future results. Never invest money you cannot afford to lose.

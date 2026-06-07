import streamlit as st

st.set_page_config(page_title="Metrics Guide", page_icon="📖", layout="wide")

st.title("📖 Metrics & Signals Guide")
st.caption("Everything the screener measures, what it means, and how to use it.")

# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------

st.header("Score", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
The **Score** is the screener's single summary number. Each signal that fires adds points.
The more signals aligned in the same direction, the higher the score.

| Score | Meaning | Action |
|-------|---------|--------|
| **80+** | Strong multi-signal setup | Primary candidates — up to 35% of weekly budget |
| **60–79** | Solid setup, fewer confirmations | Secondary candidates — 20–25% |
| **40–59** | Weak — only trade in BULL market | Small position or skip |
| **< 40** | No setup | Skip |

**Max possible score:** ~120+ (all signals firing simultaneously, which is rare).
In practice, top setups score 80–100.
""")
with col2:
    st.info("**Rule of thumb:** Don't trade below 60 in a BULL market or below 70 in a BEAR market.")

# ---------------------------------------------------------------------------
# Price columns
# ---------------------------------------------------------------------------

st.header("Entry Price · Expected Price (5d) · Expected Return", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
| Column | What it is |
|--------|-----------|
| **Entry Price** | Last Friday's closing price — approximates what you'd pay Monday morning at open |
| **Expected Price (5d)** | Statistical price target for the following Friday |
| **Expected Return** | `(Expected − Entry) / Entry × 100%` |

**How Expected Price is calculated:**

The screener uses two methods depending on the setup type, then blends them:

1. **ATR Projection (momentum setups):** `Entry + (Score ÷ 80) × ATR`
   - If your score is 80, it projects a 1.0× ATR move upward
   - Score 100 → 1.25× ATR, Score 60 → 0.75× ATR
   - ATR is the stock's average daily range — this scales the target to the stock's own volatility

2. **Mean-reversion target (oversold setups):** `SMA20 (middle Bollinger Band)`
   - Used when price is at or near the lower Bollinger Band
   - Historically, prices that dip below the lower band tend to revert to the 20-day average within a week

When both apply, the expected price is the **average of the two targets**.

> ⚠️ This is a statistical estimate based on historical volatility patterns — not a guarantee. Use it to compare setups relative to each other, not as a precise price prediction.
""")
with col2:
    st.warning("""
**Example**

Stock price: $100
ATR: $2.50
Score: 80

ATR target:
$100 + 1.0 × $2.50 = **$102.50**

Expected return: **+2.5%**
""")

# ---------------------------------------------------------------------------
# Market Regime
# ---------------------------------------------------------------------------

st.header("Market Regime (BULL / BEAR)", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
The regime tells you whether the overall market is in an uptrend or downtrend.
It is determined by comparing SPY's current price to its 50-day moving average.

| Regime | Condition | Effect on screener |
|--------|-----------|-------------------|
| 🟢 **BULL** | SPY > SMA50 | All signals at full strength |
| 🔴 **BEAR** | SPY < SMA50 | Mean-reversion signal scores halved |

**Why it matters:** In a bear market, "oversold" stocks keep falling — catching them early
is called a falling knife. The screener automatically discounts those setups.
In bear markets, only act on momentum signals (MACD crossover, uptrend, OBV) not on oversold ones.
""")
with col2:
    st.error("In a BEAR regime: reduce all position sizes by half and skip any setup whose main signals are RSI oversold or Bollinger Band bounce only.")

# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

st.header("RSI — Relative Strength Index", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
RSI measures how fast and how much a stock has moved recently. It oscillates between 0 and 100.

**Formula:** Compares average gains vs average losses over the last 14 days.

| RSI Value | Meaning | Score contribution |
|-----------|---------|-------------------|
| 30–50 | Recovering from oversold — buyers stepping in | **+25 pts** (bull) / +12 pts (bear) |
| 50–65 | Neutral-bullish momentum | **+10 pts** |
| > 70 | Overbought — avoid entering here | 0 pts |
| < 30 | Deeply oversold | 0 pts (too early, no confirmation yet) |

**Sweet spot for entry: RSI between 35–50.**
It means the stock was oversold, selling pressure is fading, but it hasn't recovered so much that you're chasing it.
""")
with col2:
    st.success("**Best entry zone:** RSI 35–50 with a rising Stochastic confirms the turn.")
    st.warning("**Avoid:** RSI > 70 means you're late to the party.")

# ---------------------------------------------------------------------------
# Stochastic %K
# ---------------------------------------------------------------------------

st.header("Stochastic %K", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
The Stochastic Oscillator measures where the closing price sits within the recent high-low range.
It's a timing signal — it tells you *when* the reversal is starting, not just that the stock is cheap.

**Formula:** `%K = (Close − Lowest Low over 14 days) / (Highest High − Lowest Low) × 100`

`%D` is the 3-day smoothed average of %K.

| Value | Meaning | Score contribution |
|-------|---------|-------------------|
| %K < 20 crossing above %D | **Bullish crossover from oversold** — timing confirmed | **+20 pts** (bull) / +10 pts (bear) |
| %K < 30, rising above %D | Oversold and recovering | **+8 pts** |
| %K > 80 | Overbought — avoid | 0 pts |

**Why use it alongside RSI?** RSI tells you the stock is oversold. Stochastic tells you
the exact moment it starts turning up. Together they reduce false entries.
""")
with col2:
    st.success("**Strongest entry signal:** Stochastic %K crosses above %D while %K is below 20.")

# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

st.header("MACD — Moving Average Convergence Divergence", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
MACD measures the momentum of a stock by comparing two exponential moving averages.
It's one of the most reliable short-term momentum indicators.

**Components:**
- **MACD Line** = 12-day EMA − 26-day EMA
- **Signal Line** = 9-day EMA of the MACD Line
- **Histogram** = MACD Line − Signal Line

| Signal | Meaning | Score contribution |
|--------|---------|-------------------|
| Histogram crosses from negative to positive (fresh) | Momentum just flipped bullish — **strongest signal** | **+25 pts** |
| Histogram already positive | Momentum has been building — continuation | **+10 pts** |

**Fresh crossover is the most powerful signal in this screener.**
It means the short-term momentum has just turned. Catching it early (within 1–2 bars) is the goal.
""")
with col2:
    st.success("**Best setup:** Fresh MACD crossover + RSI 35–50 + rising OBV = high-conviction entry.")

# ---------------------------------------------------------------------------
# SMA Trend Alignment
# ---------------------------------------------------------------------------

st.header("SMA Trend Alignment (SMA20 · SMA50)", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
Simple Moving Averages show the average closing price over a period of time.
When price, SMA20, and SMA50 are stacked in order, the stock is in a confirmed uptrend.

| Condition | Meaning | Score contribution |
|-----------|---------|-------------------|
| Price > SMA20 > SMA50 | Full uptrend — all timeframes aligned | **+20 pts** |
| Price > SMA20 only | Short-term bullish, not confirmed on longer timeframe | **+8 pts** |

**SMA20** = 20-day average (about 1 month of trading)
**SMA50** = 50-day average (about 2.5 months of trading)

A stock above both moving averages is trending. A stock below both is in a downtrend — stay away.
""")
with col2:
    st.info("The chart on the main page shows SMA20 (orange) and SMA50 (red) overlaid on the price.")

# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

st.header("Bollinger Bands", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
Bollinger Bands create a price channel around a 20-day moving average using standard deviation.
They define what is statistically "cheap" or "expensive" for that stock right now.

**Components:**
- **Upper Band** = SMA20 + 2× standard deviation
- **Middle Band** = SMA20
- **Lower Band** = SMA20 − 2× standard deviation

Roughly 95% of all closes fall inside the bands. When price is outside, it's unusual.

| Condition | Meaning | Score contribution |
|-----------|---------|-------------------|
| Price ≤ Lower Band | Statistically oversold — bounce very likely | **+15 pts** (bull) / +7 pts (bear) |
| Price ≤ Lower Band × 1.02 (near lower) | Approaching oversold territory | **+10 pts** (bull) / +5 pts (bear) |

**The Expected Price (5d) for these setups targets the Middle Band (SMA20),** since that is historically
where prices revert to after touching the lower band.
""")
with col2:
    st.warning("In a BEAR market, price touching the lower band often means the band is itself moving down — don't rely on this signal alone.")

# ---------------------------------------------------------------------------
# OBV
# ---------------------------------------------------------------------------

st.header("OBV — On-Balance Volume", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
OBV tracks whether volume is flowing into or out of a stock. It adds volume on up days
and subtracts it on down days, creating a running total.

**Why it matters:** Large institutional investors (funds, banks) can't buy quietly —
their buying shows up as volume before the price moves. OBV catches this early.

| Signal | Meaning | Score contribution |
|--------|---------|-------------------|
| OBV rising while price is also rising | Volume confirms the move — conviction | **+10 pts** |
| OBV rising while price is falling | **Bullish divergence** — smart money accumulating despite price weakness. Strong reversal signal. | **+20 pts** |

**OBV divergence is the most underrated signal here.** When a stock is dropping but OBV is rising,
it means buyers are absorbing selling pressure. The price drop is often temporary.
""")
with col2:
    st.success("**OBV bullish divergence + MACD fresh crossover** is one of the highest-conviction setups the screener can find.")

# ---------------------------------------------------------------------------
# Volume ×
# ---------------------------------------------------------------------------

st.header("Volume ×", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
Volume × compares the average daily volume of the last 5 days to the 20-day average.
High relative volume means someone is paying attention to this stock right now.

| Value | Meaning | Score contribution |
|-------|---------|-------------------|
| ≥ 1.5× | Volume surge — significant unusual interest | **+15 pts** |
| 1.2–1.5× | Above-average activity | **+7 pts** |
| < 1.2× | Normal, no contribution | 0 pts |

Volume surge alone is not enough — it needs to be paired with a price direction signal
(MACD, OBV, or momentum). A volume surge on a down day is bearish, not bullish.
""")
with col2:
    st.info("**Vol × > 1.5 paired with MACD crossover** = institutional buying. High priority setup.")

# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

st.header("ATR — Average True Range", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
ATR measures a stock's average daily price range over the last 14 days. It's the volatility ruler.

**Formula:** Average of `max(High − Low, |High − Prev Close|, |Low − Prev Close|)` over 14 days.

**How the screener uses it:**

1. **5-day momentum normalization:** Instead of checking if a stock moved +2%, it checks if it moved
   +0.3 to +2.5 × ATR. This makes the signal fair across both low-vol and high-vol stocks.

2. **Expected Price calculation:** The ATR is multiplied by a score-based factor to project
   how far the stock could move in 5 days based on its own historical range.

| ATR Momentum | Meaning | Score |
|-------------|---------|-------|
| 0.3–2.5× ATR | Healthy move — not too small, not overextended | **+10 pts** |
| > 2.5× ATR | Overextended — likely due for a pause | 0 pts |
| Negative with RSI ≥ 50 | Weak momentum despite neutral RSI | **−5 pts** |

**A $5 ATR on a $500 stock is the same volatility as a $1 ATR on a $100 stock.**
Using ATR instead of raw % makes scores comparable across all S&P 500 names.
""")
with col2:
    st.info("ATR is shown in dollars. A stock with ATR $3.00 typically moves $3 per day on average.")

# ---------------------------------------------------------------------------
# Relative Strength
# ---------------------------------------------------------------------------

st.header("RS vs SPY — Relative Strength", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
Relative Strength compares how much this stock has returned vs the S&P 500 (SPY) over the past month.

**Formula:** `RS = Stock 1-month return ÷ SPY 1-month return`

| RS Ratio | Meaning | Score contribution |
|----------|---------|-------------------|
| ≥ 1.3× | **Market leader** — stock is significantly outperforming the index | **+15 pts** |
| 1.0–1.3× | Outperforming — moving with or slightly ahead of the market | **+7 pts** |
| < 0.7× | **Laggard** — underperforming the index | **−10 pts** |

**Why this matters:** Money rotates into the strongest sectors and stocks.
If a stock is already beating the market, institutional money is flowing in.
If it's lagging while the market goes up, something is wrong — avoid it.

The RS penalty (−10 pts) is an active deduction, so a laggard with otherwise
decent signals will score lower than it appears from other indicators alone.
""")
with col2:
    st.success("**RS 1.3×+ is the single best predictor of continued outperformance over 5 days.** Prioritize high-RS stocks when scores are otherwise equal.")
    st.error("**RS < 0.7× is a red flag.** Something is working against this stock even as the market rises.")

# ---------------------------------------------------------------------------
# Earnings
# ---------------------------------------------------------------------------

st.header("⚠ Earnings Proximity", divider="blue")
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown("""
This flag appears when a stock has an earnings report scheduled within the next 5 trading days
— the same window the screener is targeting.

**Why it's dangerous:** Earnings reports cause overnight gaps. A stock can move ±10–20%
in a single night regardless of any technical setup. All the signals become meaningless
because earnings introduce binary, unpredictable risk.

**Rule:** If you see `⚠ Soon` in the Earnings column:
- Skip the position entirely, **or**
- Wait until after the earnings report and re-run the screener

**Data source:** Financial Modeling Prep API (bulk calendar call). Falls back to Yahoo Finance
if the API is unavailable.
""")
with col2:
    st.error("**Never hold a 5-day position through an earnings date.** The technical setup is irrelevant — earnings override everything.")

# ---------------------------------------------------------------------------
# Summary cheat sheet
# ---------------------------------------------------------------------------

st.header("Quick Reference Cheat Sheet", divider="gray")
st.markdown("""
| Metric | Green (enter) | Yellow (caution) | Red (avoid / penalty) |
|--------|--------------|-----------------|----------------------|
| **Score** | ≥ 80 | 60–79 | < 60 |
| **Regime** | BULL | — | BEAR |
| **RSI** | 35–50 | 50–65 | > 70 or < 30 |
| **Stoch %K** | < 20 crossing up | 20–50 rising | > 80 |
| **MACD** | Fresh crossover | Histogram positive | Histogram negative |
| **OBV** | Rising / divergence | Flat | Falling |
| **Vol ×** | ≥ 1.5× | 1.2–1.5× | < 1.0× |
| **RS vs SPY** | ≥ 1.3× | 1.0–1.3× | < 0.7× |
| **Earnings** | Blank | — | ⚠ Soon |

**Best combo:** Fresh MACD crossover · RSI 35–50 · OBV rising/divergence · RS ≥ 1.3× · No earnings · BULL regime
""")

st.divider()
st.caption("Statistical signals only — not financial advice. Past patterns do not guarantee future results.")

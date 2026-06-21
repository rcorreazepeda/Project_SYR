import streamlit as st

st.set_page_config(
    page_title="User Guide — R&S Stock Plan",
    page_icon="📚",
    layout="wide",
)

st.markdown("""
<style>
  .stApp, [data-testid="stAppViewContainer"] { background-color: #07090f !important; }
  [data-testid="stHeader"] { background: #07090f !important; }
  [data-testid="stSidebar"] { background: #080e1a !important; border-right: 1px solid #1a3350; }
  .block-container { padding-top: 1.5rem; }
  body, p, span, div, label, li { color: #ccd6f6; }
  h1 { color: #00e5ff !important; font-family: monospace !important; letter-spacing: 2px; }
  h2, h3 { color: #00e5ff !important; font-family: monospace !important; letter-spacing: 1px; }
  hr { border-color: #1a3350 !important; }
  strong { color: #ccd6f6 !important; }
  code { background: #0c1420 !important; color: #00ffa3 !important; border: 1px solid #1a3350; border-radius: 4px; padding: 1px 6px; }
  [data-testid="stCaptionContainer"] { color: #4a6a8a !important; font-family: monospace !important; }
  [data-testid="stExpander"] { border: 1px solid #1a3350 !important; border-radius: 8px !important; background: #0c1420 !important; }
  [data-testid="stExpander"] summary { color: #ccd6f6 !important; font-family: monospace !important; }
</style>
""", unsafe_allow_html=True)

st.title("USER GUIDE")
st.markdown('<p style="font-family:monospace;font-size:11px;color:#4a6a8a;margin-top:-12px">R&S STOCK PLAN // HOW IT WORKS</p>', unsafe_allow_html=True)

st.divider()

# ── OVERVIEW ──────────────────────────────────────────────────────────────────
st.markdown("""
## What is R&S Stock Plan?

R&S Stock Plan is a personal stock screener that scores every S&P 500 stock daily across
three holding horizons — short (5 days), medium (30 days), and long (180 days).

It combines **technical indicators**, **market regime**, **VIX sentiment**, **market breadth**,
**sector strength**, and **AI news sentiment** to rank stocks by statistical setup quality —
not tips, not predictions, just data on which stocks are showing the strongest signals right now.

There are **two portfolios**: Raul and Sofia. Each has its own trades, live P&L, and gets its own daily email.
""")

st.divider()

# ── DAILY AUTOMATION ──────────────────────────────────────────────────────────
st.markdown("## ⚙️ What Runs Automatically (You Don't Touch This)")

col1, col2 = st.columns([1, 1])

with col1:
    st.markdown("""
**Every weekday at 4:30 PM ET** — after market close — GitHub Actions runs automatically:

1. Downloads fresh price data for all 500+ S&P 500 stocks
2. Scores each stock across 5d / 30d / 180d timeframes
3. Saves the top 20 picks per timeframe to the database
4. Fetches and classifies recent news headlines with AI
5. Runs Claude AI analysis on the day's results
6. Sends a **neon-styled email** to both Raul and Sofia with:
   - Market regime (Bull/Bear) per timeframe
   - Top picks with scores and signals
   - Portfolio P&L summary
   - AI analysis and portfolio suggestions
   - Key news headlines with sentiment
""")

with col2:
    st.markdown("""
**Why this matters for you:**

- You don't need to open the app every day
- The email gives you everything you need at a glance
- The database accumulates history — the longer it runs, the better the **consistency scores** get
- The Investment Advisor gets more reliable over time as the history grows

**GitHub Actions schedule:**
```
Monday – Friday
4:30 PM Eastern Time
(after NYSE market close)
```
Runs are skipped on US market holidays.
""")

st.divider()

# ── WORKFLOW ──────────────────────────────────────────────────────────────────
st.markdown("## 🗺️ Recommended Workflow")

st.markdown("""
### When you want to invest new money

| Step | Action | Tab |
|------|--------|-----|
| 1 | Click **▶ Run Screener** in the sidebar | Any tab |
| 2 | Go to **📰 News** → Fetch & Classify News for the top picks | News tab |
| 3 | Go to **💡 Advisor** → enter amount, horizon, portfolio | Advisor tab |
| 4 | Review the plan, pick your trades | — |
| 5 | Log the trades in **💼 Portfolio** | Portfolio tab |

### On a normal weekday
Just check your **email at ~5 PM ET**. The automated job sends everything you need.
Open the app only if you want to drill into a specific stock's chart or signals.

### When you close a trade
Go to **💼 Portfolio** → expand **🔴 Close a position** → select the ticker → the exit price auto-fills with the live price → confirm.
""")

st.divider()

# ── TABS ──────────────────────────────────────────────────────────────────────
st.markdown("## 📱 Tab-by-Tab Guide")

with st.expander("📅 5-Day / 📆 30-Day / 📈 180-Day Trading  —  The Screener"):
    st.markdown("""
These three tabs show the same thing at different holding horizons.

**When to use:** After clicking **▶ Run Screener** in the sidebar.

**What you see:**
- **Market Regime** — is the market in Bull or Bear mode? (SPY above/below its 50-day moving average)
- **VIX** — fear gauge. High VIX (>25) = fear/volatility. Low (<15) = complacency.
- **Breadth** — % of S&P 500 stocks above their 50-day SMA. High = broad rally. Low = narrow or weak market.
- **Top picks table** — sorted by combined score (tech + news). Higher = better setup.
- **Price chart** — select any ticker to see price, Bollinger Bands, SMAs, and volume.

**Score column guide:**
- `Tech Score` — pure technical signals (0–120+)
- `News` — sentiment bonus/penalty from AI-classified headlines (+4 per GOOD, −6 per BAD)
- `Combined` — tech + news, the final ranking score

**Timeframe guide:**
| Tab | Hold for | Take profit | Stop loss |
|-----|----------|-------------|-----------|
| 5-Day | ~1 week | +5% | −3% |
| 30-Day | ~1 month | +12% | −6% |
| 180-Day | ~6 months | +25% | −12% |
""")

with st.expander("📰 News  —  AI Sentiment Classification"):
    st.markdown("""
Fetches recent headlines from Yahoo Finance and classifies each one with **Claude Haiku**.

**When to use:** Before running the Investment Advisor, or whenever you want to know what's happening with the top picks.

**How it works:**
- Each headline gets classified as `GOOD`, `BAD`, or `NEUTRAL` for that stock's price over the next 1–5 days
- `GOOD` = +4 points to the stock's score
- `BAD` = −6 points (penalty is higher — bad news is more actionable)
- Total news score capped at [−20, +20]
- These scores immediately update the Combined column in the trading tabs

**Tips:**
- Fetch news after running the screener — the updated combined scores will re-sort the picks
- Set the news window to 7 days for more signal; 3 days for only the freshest news
""")

with st.expander("💼 Portfolio  —  Trade Tracking & Live P&L"):
    st.markdown("""
Track your open and closed trades with live prices from Yahoo Finance.

**Supported assets:** S&P 500 stocks, ETFs (VOO, BATT, SGOV…), and crypto (enter as `BTC-USD`, `ETH-USD`).

**Adding a trade:**
- Expand **Add trade** → fill in ticker, date, shares, entry price
- Leave category as `— (none)` and AI will auto-classify it
- If you already have an **open position** in that ticker, it automatically does a **DCA** (weighted average cost) instead of creating a duplicate

**DCA (Dollar Cost Averaging):**
When you buy more of a stock you already own, the system recalculates:
`new avg price = (old shares × old price + new shares + new price) / total shares`

**Closing a trade:**
Expand **🔴 Close a position** → select ticker → exit price auto-fills with live price → confirm.
The trade is marked WIN or LOSS automatically.

**Computed values (never stored manually):**
- `Invested $` = shares × entry price
- `Value $` = shares × live price
- `P&L $` = Value − Invested

**Portfolio selector:** Switch between Raul and Sofia portfolios at the top of the tab.
""")

with st.expander("💡 Advisor  —  AI Investment Plan"):
    st.markdown("""
Generates a specific allocation plan for new money you want to invest.

**When to use:** When you have cash ready to deploy — not daily.

**Inputs:**
- **Amount** — how much you want to invest (e.g. $10,000)
- **Time horizon** — 6 months or 1 year
- **Portfolio** — Raul or Sofia (affects which existing positions are considered)

**What the AI considers:**
- Your current open positions and category concentration
- Top screener picks ranked by **blended score** (60% today's score + 40% 30-day consistency)
- News sentiment scores
- Historical screener win rates
- Latest AI screener analysis

**Consistency score explained:**
A stock with 90% consistency appeared in the top 20 picks on 9 out of the last 10 screener runs.
That's a sustained signal — not a one-day spike. The advisor favors high-consistency picks for 6m/1y horizons.

**Output:**
- Allocation table (ticker, $ amount, % of deployment, consistency, rationale)
- Key reasoning per pick
- Portfolio fit analysis (how concentration changes)
- Risks to watch

**Tip:** Run the screener + fetch news first for the most accurate recommendations.
""")

with st.expander("📊 Performance  —  Historical Accuracy"):
    st.markdown("""
Tracks how well the screener has performed over time.

**What you see:**
- **Latest AI analysis** — Claude's commentary from the most recent daily run
- **Signal win rates** — which technical signals (MACD crossover, RSI recovery, etc.) have the highest real-world win rate
- **Outcome summary** — overall win rate, average return, wins/losses by timeframe
- **Most frequently picked stocks** — what the screener has liked most over the last 60 days
- **Avg score by timeframe** — how competitive recent picks have been

This tab gets more useful the longer the system runs. After 3–6 months you'll have meaningful accuracy data per signal type.
""")

st.divider()

# ── EMAIL ──────────────────────────────────────────────────────────────────────
st.markdown("## 📧 Daily Email")

st.markdown("""
Both portfolios receive a daily email every weekday at ~4:45 PM ET (after the GitHub Actions job finishes).

**Email sections:**
1. **Market Regime** — Bull/Bear, VIX, and Breadth across all three timeframes
2. **Open Positions** — your portfolio with live prices, invested $, current value, and P&L
3. **Portfolio AI Analysis** — HOLD / SELL / BUY MORE suggestions per position, concentration risk
4. **Today's Top Picks** — top 5 per timeframe with scores, signals, and price targets
5. **Key Headlines** — GOOD/BAD news for the picked tickers with links
6. **AI Analysis** — Claude Sonnet's daily market commentary and scoring suggestions

Both Raul and Sofia's emails are sent to Raul's address. Sofia's email is automatically
forwarded by a Gmail filter (subject contains "Sofia").
""")

st.divider()

# ── TIPS ──────────────────────────────────────────────────────────────────────
st.markdown("## 💡 Tips & Best Practices")

st.markdown("""
- **Don't invest on score alone.** Check the signals breakdown and news before acting.
- **Bear market warning** — when the regime shows 🔴 BEAR, scores are penalized and position sizes should be smaller.
- **⚠ Earnings soon** flag — avoid entering positions just before earnings announcements. The stock can move violently in either direction.
- **Crypto precision** — crypto entry prices support 6 decimal places. Enter as `BTC-USD`, `ETH-USD`, etc.
- **The advisor improves over time** — the more daily screener runs in the database, the more reliable the consistency scores become. After 60 days, a 90% consistency score is very meaningful.
- **Run Screener only when you need it** — the screener takes 2–3 minutes. The daily email already has the automated results. Only run it manually if you want live charts or are about to use the Advisor.
- **Check the Performance tab monthly** — see which signals are actually working and adjust your conviction accordingly.
""")

st.divider()
st.caption("R&S STOCK PLAN // USER GUIDE // projectsyr.streamlit.app")

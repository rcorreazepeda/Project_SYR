import pandas as pd
from .indicators import rsi, macd, bollinger, stochastic, atr, obv


def score_ticker(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    spy_return: float,
    in_bull: bool,
    cfg: dict,
    vix_val: float = 20.0,
    breadth_pct: float = 50.0,
    sector_return: float = 0.0,
    sector_etf: str = "",
) -> tuple[int, list[str], dict]:
    signals: list[str] = []
    score = 0
    price = close.iloc[-1]
    rm = 1.0 if in_bull else 0.5  # regime multiplier for mean-reversion signals

    # --- RSI ---
    rsi_val = rsi(close).iloc[-1]
    lo, hi = cfg["rsi_recover"]
    bl, bh = cfg["rsi_bullish"]
    if lo <= rsi_val <= hi:
        score += round(cfg["score_rsi_recover"] * rm)
        note = "" if in_bull else " ⚠ bear mkt"
        signals.append(f"RSI {rsi_val:.1f} — in recovery zone{note}")
    elif bl < rsi_val <= bh:
        score += 10
        signals.append(f"RSI {rsi_val:.1f} — bullish momentum zone")

    # --- Stochastic ---
    k, d = stochastic(high, low, close)
    k_val, d_val = k.iloc[-1], d.iloc[-1]
    k_prev, d_prev = k.iloc[-2], d.iloc[-2]
    if k_prev < cfg["stoch_oversold"] and k_val > d_val and k_prev <= d_prev:
        score += round(cfg["score_stoch_cross"] * rm)
        signals.append(f"Stochastic %K {k_val:.0f} — bullish crossover from oversold")
    elif k_val < cfg["stoch_caution"] and k_val > d_val:
        score += 8
        signals.append(f"Stochastic %K {k_val:.0f} — oversold + rising")

    # --- MACD ---
    macd_line, signal_line = macd(close)
    hist = macd_line - signal_line
    if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
        score += cfg["score_macd_cross"]
        signals.append("MACD bullish crossover (fresh)")
    elif hist.iloc[-1] > 0:
        score += cfg["score_macd_positive"]
        signals.append("MACD histogram positive")

    # --- SMA trend alignment ---
    sf, ss = cfg["sma_fast"], cfg["sma_slow"]
    sma_fast = close.rolling(sf).mean().iloc[-1]
    sma_slow = close.rolling(ss).mean().iloc[-1]
    if price > sma_fast > sma_slow:
        score += cfg["score_sma_aligned"]
        signals.append(f"Price > SMA{sf} > SMA{ss} (uptrend)")
    elif price > sma_fast:
        score += 8
        signals.append(f"Price > SMA{sf}")

    # --- Golden cross (long-term only) ---
    gc_lb = cfg.get("golden_cross_lookback")
    if gc_lb and cfg["score_golden_cross"] > 0:
        sma_fast_s = close.rolling(sf).mean()
        sma_slow_s = close.rolling(ss).mean()
        for i in range(2, min(gc_lb + 2, len(sma_fast_s))):
            if (sma_fast_s.iloc[-i] > sma_slow_s.iloc[-i] and
                    sma_fast_s.iloc[-i - 1] <= sma_slow_s.iloc[-i - 1]):
                score += cfg["score_golden_cross"]
                signals.append(f"Golden cross: SMA{sf} crossed above SMA{ss} recently")
                break

    # --- Bollinger Bands ---
    _, _, lb_series = bollinger(close, period=cfg["bb_period"])
    lb = lb_series.iloc[-1]
    if price <= lb:
        score += round(cfg["score_bb_below"] * rm)
        note = "" if in_bull else " ⚠ bear mkt"
        signals.append(f"Price below lower BB{cfg['bb_period']} (oversold){note}")
    elif price <= lb * 1.02:
        score += round(cfg["score_bb_near"] * rm)
        signals.append(f"Price near lower BB{cfg['bb_period']}")

    # --- OBV ---
    obv_series = obv(close, volume)
    win = cfg["obv_window"]
    if len(obv_series) >= win:
        obv_slope = obv_series.iloc[-1] - obv_series.iloc[-win]
        price_slope = close.iloc[-1] - close.iloc[-win]
        if obv_slope > 0 and price_slope < 0:
            score += cfg["score_obv_divergence"]
            signals.append("OBV bullish divergence (accumulation despite price dip)")
        elif obv_slope > 0:
            score += cfg["score_obv_rising"]
            signals.append("OBV rising — accumulation confirmed")

    # --- Volume surge ---
    vol_avg = volume.rolling(cfg["vol_avg"]).mean().iloc[-1]
    vol_ratio = volume.iloc[-cfg["vol_recent"]:].mean() / vol_avg if vol_avg > 0 else 1.0
    if vol_ratio >= 1.5:
        score += cfg["score_vol_surge"]
        signals.append(f"Volume surge {vol_ratio:.1f}× {cfg['vol_avg']}-day avg")
    elif vol_ratio >= 1.2:
        score += cfg["score_vol_above"]
        signals.append(f"Above-avg volume {vol_ratio:.1f}×")

    # --- ATR momentum ---
    atr_val = atr(high, low, close).iloc[-1]
    n = cfg["mom_days"]
    if len(close) >= n + 1 and atr_val > 0:
        mom_pts = close.iloc[-1] - close.iloc[-n]
        mom_atr = mom_pts / atr_val
        lo_m, hi_m = cfg["mom_atr_range"]
        if lo_m <= mom_atr <= hi_m:
            score += cfg["score_momentum"]
            signals.append(f"{n - 1}d momentum +{mom_atr:.1f}× ATR (healthy)")
        elif mom_atr < 0 and rsi_val >= 50:
            score -= 5

    # --- Relative strength ---
    rs_n = cfg["rs_days"]
    if len(close) >= rs_n + 1:
        stock_ret = close.iloc[-1] / close.iloc[-rs_n] - 1

        # vs SPY
        if spy_return != 0:
            rs_spy = stock_ret / spy_return if spy_return > 0 else -stock_ret / abs(spy_return)
            if rs_spy >= 1.3:
                score += cfg["score_rs_leader"]
                signals.append(f"RS vs SPY {rs_spy:.1f}× — market leader")
            elif rs_spy >= 1.0:
                score += cfg["score_rs_outperform"]
                signals.append(f"RS vs SPY {rs_spy:.1f}× — outperforming")
            elif rs_spy < 0.7:
                score -= 10
                signals.append(f"RS vs SPY {rs_spy:.1f}× — laggard (penalty)")

        # vs Sector ETF
        if sector_return != 0:
            rs_sec = stock_ret / sector_return if sector_return > 0 else -stock_ret / abs(sector_return)
            label  = sector_etf if sector_etf else "sector"
            if rs_sec >= 1.3:
                score += 12
                signals.append(f"RS vs {label} {rs_sec:.1f}× — sector leader")
            elif rs_sec >= 1.0:
                score += 6
                signals.append(f"RS vs {label} {rs_sec:.1f}× — outperforming sector")
            elif rs_sec < 0.7:
                score -= 8
                signals.append(f"RS vs {label} {rs_sec:.1f}× — sector laggard (penalty)")

    # --- VIX sentiment ---
    if vix_val > 35:
        score -= 10
        signals.append(f"VIX {vix_val:.0f} — market panic, high risk of continued selling")
    elif vix_val > 25:
        score += 8
        signals.append(f"VIX {vix_val:.0f} — elevated fear, mean-reversion setups favored")
    elif vix_val < 15:
        score -= 5
        signals.append(f"VIX {vix_val:.0f} — complacency, mean-reversion odds reduced")

    # --- Market breadth ---
    if breadth_pct > 65:
        score += 10
        signals.append(f"Breadth {breadth_pct:.0f}% above SMA50 — broad market participation")
    elif breadth_pct < 40:
        score -= 8
        signals.append(f"Breadth {breadth_pct:.0f}% above SMA50 — narrow market, selective risk")

    meta = {
        "price":     round(price, 2),
        "RSI":       round(rsi_val, 1),
        "stoch_k":   round(k_val, 1),
        "sma_fast":  round(sma_fast, 2),
        "sma_slow":  round(sma_slow, 2),
        "vol_ratio": round(vol_ratio, 2),
        "atr":       round(atr_val, 2),
    }
    return score, signals, meta

import math
import pandas as pd
from .indicators import bollinger


def compute_targets(df: pd.DataFrame, close_all: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Entry price  = last close.
    Expected price = score-weighted ATR projection scaled to the forecast period,
                     blended with SMA-fast mean-reversion target when price is
                     near the lower Bollinger Band.
    """
    expected: list[float] = []

    for _, row in df.iterrows():
        price    = row["price"]
        atr_val  = row["atr"]
        sma_fast = row["sma_fast"]
        sc       = row["score"]
        ticker   = row["ticker"]

        # Scale ATR to the forecast window (square-root-of-time rule)
        atr_scaled = atr_val * math.sqrt(cfg["forecast_atr_days"] / 14)
        atr_mult   = max(0.3, sc / cfg["atr_score_div"])
        atr_target = price + atr_mult * atr_scaled

        # Blend with mean-reversion target when price is near/below lower BB
        if ticker in close_all.columns:
            close = close_all[ticker].dropna()
            _, _, lb_s = bollinger(close, period=cfg["bb_period"])
            if price <= lb_s.iloc[-1] * 1.03:
                atr_target = (atr_target + sma_fast) / 2

        expected.append(round(atr_target, 2))

    out = df.copy()
    out["expected_price"]    = expected
    out["expected_return_%"] = ((out["expected_price"] - out["price"]) / out["price"] * 100).round(2)
    return out

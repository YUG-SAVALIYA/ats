"""
app.trading.monthly_rsi
=======================
Monthly RSI Strategy — Signal Generation & Supertrend Calculations.
"""

from typing import Dict, Any, List, Optional
from datetime import date, datetime
import logging

logger = logging.getLogger("ats.monthly_rsi")


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 0.0

    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change >= 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def calculate_supertrend(candles: List[Dict[str, float]], period: int = 10, multiplier: float = 3.0) -> List[int]:
    if len(candles) < period + 1:
        return []

    tr = []
    for i in range(len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        if i == 0:
            tr.append(high - low)
        else:
            prev_close = float(candles[i - 1]["close"])
            tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr = [0.0] * len(candles)
    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, len(candles)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    upper_band = [0.0] * len(candles)
    lower_band = [0.0] * len(candles)
    for i in range(len(candles)):
        hl2 = (float(candles[i]["high"]) + float(candles[i]["low"])) / 2.0
        upper_band[i] = hl2 + (multiplier * atr[i])
        lower_band[i] = hl2 - (multiplier * atr[i])

    final_upper = [0.0] * len(candles)
    final_lower = [0.0] * len(candles)
    directions = [1] * len(candles)

    for i in range(period, len(candles)):
        close = float(candles[i]["close"])
        prev_close = float(candles[i - 1]["close"])

        if upper_band[i] < final_upper[i - 1] or prev_close > final_upper[i - 1]:
            final_upper[i] = upper_band[i]
        else:
            final_upper[i] = final_upper[i - 1]

        if lower_band[i] > final_lower[i - 1] or prev_close < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = final_lower[i - 1]

        if directions[i - 1] == 1:
            if close < final_lower[i]:
                directions[i] = -1
            else:
                directions[i] = 1
        else:
            if close > final_upper[i]:
                directions[i] = 1
            else:
                directions[i] = -1

    return directions


def evaluate_monthly_rsi_signal(
    symbol: str,
    security_id: str,
    exchange_segment: str,
    daily_candles: List[Dict[str, Any]],
    monthly_candles: List[Dict[str, Any]],
    current_date: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """Evaluates Monthly RSI signal generation rules."""
    from app.services.settings import get_strategy_settings
    settings = get_strategy_settings(strategy_type="MONTHLY_RSI")
    
    if len(monthly_candles) < max(settings.get("rsi_period", 14), settings.get("supertrend_period", 10)) + 6:
        return None

    signal_candle = monthly_candles[-1]
    monthly_closes = [float(c["close"]) for c in monthly_candles]
    
    rsi = calculate_rsi(monthly_closes, period=settings.get("rsi_period", 14))
    st_directions = calculate_supertrend(monthly_candles, period=settings.get("supertrend_period", 10), multiplier=settings.get("supertrend_multiplier", 3.0))
    st_green = st_directions[-1] == 1 if st_directions else False
    
    if len(monthly_closes) >= 7:
        roc6 = ((monthly_closes[-1] / monthly_closes[-7]) - 1) * 100
    else:
        roc6 = 0
        
    if len(monthly_closes) >= 12:
        sma12 = sum(monthly_closes[-12:]) / 12
        close_above_sma12_pct = ((monthly_closes[-1] / sma12) - 1) * 100
    else:
        close_above_sma12_pct = 0
    
    min_rsi = settings.get("min_rsi", 55.0)
    max_rsi = settings.get("max_rsi", 70.0)
    
    if not (min_rsi <= rsi <= max_rsi):
        return None
        
    if not st_green:
        return None
        
    if roc6 < settings.get("min_roc6_pct", 25.0):
        return None
        
    if close_above_sma12_pct < settings.get("min_close_above_sma12_pct", 10.0):
        return None
        
    swing_window = settings.get("swing_window", 10)
    if len(daily_candles) < swing_window:
        return None
        
    swing_low = min([float(c["low"]) for c in daily_candles[-swing_window:]])
    swing_buffer_pct = settings.get("swing_buffer_pct", 0.5)
    stop_price = swing_low * (1 - swing_buffer_pct / 100)
    
    high = float(signal_candle["high"])
    low = float(signal_candle["low"])
    open_p = float(signal_candle.get("open", high))
    close_p = float(signal_candle["close"])
    
    sig_date_val = signal_candle.get("date", date.today())
    if isinstance(sig_date_val, str):
        sig_date_val = datetime.strptime(sig_date_val[:10], "%Y-%m-%d").date()
    elif isinstance(sig_date_val, datetime):
        sig_date_val = sig_date_val.date()
        
    sig_date_str = sig_date_val.strftime("%Y-%m-%d")

    return {
        "symbol": symbol.upper(),
        "security_id": str(security_id),
        "exchange_segment": exchange_segment,
        "strategy": "Monthly RSI Swing Breakout",
        "signal_type": "BUY",
        "signal_date": sig_date_str,
        "signal_high": high,
        "signal_low": low,
        "signal_open": open_p,
        "signal_close": close_p,
        "raw_signal_data": {
            "rsi": rsi,
            "roc6": roc6,
            "close_above_sma12_pct": close_above_sma12_pct,
            "swing_low": swing_low,
            "stop_price": stop_price,
            "supertrend_green": st_green
        }
    }

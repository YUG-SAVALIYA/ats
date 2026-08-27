"""
trading/strategies.py — Technical Indicators & Strategy Evaluation Logic
========================================================================
Implements Wilder's smoothed RSI, Supertrend (ATR band flips), ROC(6), SMA12,
and multi-timeframe strategy qualification rules for daily and monthly signals.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, Any, List, Optional

from market.weekly import filter_completed_weekly_candles
from trading.risk import get_strategy_settings

logger = logging.getLogger("ats.trading.strategies")


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculates Relative Strength Index (RSI) using Wilder's smoothed moving average (RMA/SMMA).
    Matches TradingView's `ta.rsi(close, 14)` with exact floating-point precision.
    """
    if len(prices) < period + 1:
        return 0.0

    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        elif change < 0:
            gains.append(0.0)
            losses.append(abs(change))
        else:
            gains.append(0.0)
            losses.append(0.0)

    # 1. Initial 14-period Simple Moving Average (SMA of first 14 changes)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # 2. Wilder's Exponential Smoothing (RMA/SMMA) for every subsequent bar
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    # 3. Edge-case handling consistent with TradingView
    if avg_gain + avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    if avg_gain == 0.0:
        return 0.0

    # 4. Relative Strength (RS) and RSI calculation without internal rounding
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi)


def calculate_rsi_series(prices: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Calculates the full time-series of Wilder's RSI(14) matching TradingView bar-by-bar.
    Returns `None` for the first `period` bars before initial SMA seeding.
    """
    n = len(prices)
    if n < period + 1:
        return [None] * n

    rsi_series: List[Optional[float]] = [None] * period

    gains = [0.0] * (n - 1)
    losses = [0.0] * (n - 1)
    for i in range(1, n):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains[i - 1] = change
        elif change < 0:
            losses[i - 1] = -change

    # Initial SMA at bar index `period`
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_gain + avg_loss == 0.0:
        first_rsi = 50.0
    elif avg_loss == 0.0:
        first_rsi = 100.0
    elif avg_gain == 0.0:
        first_rsi = 0.0
    else:
        rs = avg_gain / avg_loss
        first_rsi = 100.0 - (100.0 / (1.0 + rs))

    rsi_series.append(first_rsi)

    # Wilder smoothing for remaining bars
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

        if avg_gain + avg_loss == 0.0:
            val = 50.0
        elif avg_loss == 0.0:
            val = 100.0
        elif avg_gain == 0.0:
            val = 0.0
        else:
            rs = avg_gain / avg_loss
            val = 100.0 - (100.0 / (1.0 + rs))
        rsi_series.append(val)

    return rsi_series


def calculate_supertrend(
    candles: List[Dict[str, float]], period: int = 21, multiplier: float = 1.5
) -> List[int]:
    """
    Computes Supertrend direction series for OHLC candles.
    Returns +1 for GREEN (bullish) and -1 for RED (bearish).
    """
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


def evaluate_stock_signal(
    symbol: str,
    security_id: str,
    exchange_segment: str,
    daily_candles: List[Dict[str, Any]],
    weekly_candles: List[Dict[str, Any]],
    market_cap_cr: float,
    current_date: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """
    Evaluates Supertrend Flip signal generation rules using completed candles.
    """
    settings = get_strategy_settings(strategy_type="SUPERTREND")
    
    # 1. Market Cap Filter
    if market_cap_cr <= settings["market_cap_min_cr"]:
        return None

    if len(daily_candles) < 22:
        return None

    # Completed weekly candles only for weekly RSI
    completed_weekly = filter_completed_weekly_candles(weekly_candles, current_date=current_date)
    if len(completed_weekly) < 15:
        return None

    # Daily RSI
    daily_closes = [float(c["close"]) for c in daily_candles]
    daily_rsi = calculate_rsi(daily_closes, period=settings["daily_rsi_period"])

    # Weekly RSI
    weekly_closes = [float(c["close"]) for c in completed_weekly]
    weekly_rsi = calculate_rsi(weekly_closes, period=settings["weekly_rsi_period"])

    # 2. Daily & Weekly RSI bounds
    if not (settings["daily_rsi_lower"] <= daily_rsi <= settings["daily_rsi_upper"]):
        return None

    if not (settings["weekly_rsi_lower"] <= weekly_rsi <= settings["weekly_rsi_upper"]):
        return None

    # 3. Supertrend flip from RED (-1) to GREEN (+1)
    st_directions = calculate_supertrend(
        daily_candles,
        period=settings["supertrend_period"],
        multiplier=settings["supertrend_multiplier"]
    )
    if len(st_directions) < 2:
        return None

    prev_st = st_directions[-2]
    curr_st = st_directions[-1]

    if not (prev_st == -1 and curr_st == 1):
        return None

    # 4. Candle Range bounds
    signal_candle = daily_candles[-1]
    high = float(signal_candle["high"])
    low = float(signal_candle["low"])
    open_p = float(signal_candle.get("open", high))
    close_p = float(signal_candle["close"])

    if low <= 0:
        return None

    candle_range = round(((high - low) / low) * 100.0, 2)
    if not (settings["candle_range_min"] <= candle_range <= settings["candle_range_max"]):
        return None

    sig_date_val = signal_candle.get("date", date.today())
    if isinstance(sig_date_val, str):
        sig_date_val = datetime.strptime(sig_date_val[:10], "%Y-%m-%d").date()
    elif isinstance(sig_date_val, datetime):
        sig_date_val = sig_date_val.date()
        
    c_date = current_date or date.today()
    if sig_date_val < c_date:
        return None

    sig_date_str = sig_date_val.strftime("%Y-%m-%d")

    return {
        "symbol": symbol.upper(),
        "security_id": str(security_id),
        "exchange_segment": exchange_segment,
        "strategy": "Supertrend(21,1.5) Flip + Daily RSI(50-90) + Weekly RSI(65-85) + Range(3-12%)",
        "signal_type": "BUY",
        "signal_date": sig_date_str,
        "signal_high": high,
        "signal_low": low,
        "signal_open": open_p,
        "signal_close": close_p,
        "daily_rsi": daily_rsi,
        "weekly_rsi": weekly_rsi,
        "candle_range": candle_range,
        "supertrend_flip": True,
        "market_cap_cr": market_cap_cr,
        "status": "PENDING"
    }


def evaluate_monthly_rsi_signal(
    symbol: str,
    security_id: str,
    exchange_segment: str,
    daily_candles: List[Dict[str, Any]],
    monthly_candles: List[Dict[str, Any]],
    current_date: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """
    Evaluates Monthly RSI signal generation rules.
    """
    settings = get_strategy_settings(strategy_type="MONTHLY_RSI")
    
    if len(monthly_candles) < max(settings.get("rsi_period", 14), settings.get("supertrend_period", 10)) + 6:
        return None

    signal_candle = monthly_candles[-1]
    monthly_closes = [float(c["close"]) for c in monthly_candles]
    
    # RSI
    rsi = calculate_rsi(monthly_closes, period=settings.get("rsi_period", 14))
    
    # Supertrend
    st_directions = calculate_supertrend(
        monthly_candles,
        period=settings.get("supertrend_period", 10),
        multiplier=settings.get("supertrend_multiplier", 3.0)
    )
    st_green = st_directions[-1] == 1 if st_directions else False
    
    # ROC(6)
    if len(monthly_closes) >= 7:
        roc6 = ((monthly_closes[-1] / monthly_closes[-7]) - 1) * 100
    else:
        roc6 = 0.0
        
    # SMA12
    if len(monthly_closes) >= 12:
        sma12 = sum(monthly_closes[-12:]) / 12
        close_above_sma12_pct = ((monthly_closes[-1] / sma12) - 1) * 100
    else:
        close_above_sma12_pct = 0.0
    
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
        
    # Swing low in daily candles
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

import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, BigInteger, Boolean, Date, DateTime,
    ForeignKey, Text, JSON, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.database import Base

def generate_uuid() -> str:
    return str(uuid.uuid4())

# ═══════════════════════════════════════════════════════════════════════════════
# ENUM DEFINITIONS (Matching table_structure.txt)
# ═══════════════════════════════════════════════════════════════════════════════

class SignalStatus(str, enum.Enum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

class RejectionReason(str, enum.Enum):
    SIGNAL_LOW_BROKEN = "SIGNAL_LOW_BROKEN"
    SIGNAL_HIGH_5_PERCENT_DRAWDOWN = "SIGNAL_HIGH_5_PERCENT_DRAWDOWN"
    SUPERTREND_FLIPPED_RED = "SUPERTREND_FLIPPED_RED"

class ExpiryReason(str, enum.Enum):
    TARGET_3_PERCENT_NOT_REACHED = "TARGET_3_PERCENT_NOT_REACHED"
    CLOSE_NOT_ABOVE_SIGNAL_HIGH = "CLOSE_NOT_ABOVE_SIGNAL_HIGH"
    TARGET_AND_CLOSE_NOT_MET = "TARGET_AND_CLOSE_NOT_MET"

class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


# ── ATS-managed trade lifecycle state machine ────────────────────────────────
class AtsTradeState(str, enum.Enum):
    """Internal ATS trade lifecycle — independent from broker Super Order states."""
    SIGNAL         = "SIGNAL"         # Signal detected, not yet entered
    ENTRY_PENDING  = "ENTRY_PENDING"  # Entry order placed, awaiting fill
    OPEN           = "OPEN"           # Entry filled; monitoring for exits
    PARTIAL_EXIT   = "PARTIAL_EXIT"   # First target hit; 50% exited
    EXIT_REQUESTED = "EXIT_REQUESTED" # Market SELL order sent, awaiting broker confirmation
    EXIT_FAILED    = "EXIT_FAILED"    # Market SELL rejected by broker
    EXIT_UNKNOWN   = "EXIT_UNKNOWN"   # Network timeout/error, position state uncertain
    CLOSED         = "CLOSED"         # Trade fully closed (broker confirmed fill)
    CANCELLED      = "CANCELLED"      # Entry cancelled or not filled
    FAILED         = "FAILED"         # Unrecoverable error; needs manual attention


# ── Purpose tag for AtsOrder rows ───────────────────────────────────────────
class OrderPurpose(str, enum.Enum):
    ENTRY         = "ENTRY"
    PARTIAL_EXIT  = "PARTIAL_EXIT"
    FINAL_EXIT    = "FINAL_EXIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"

# ═══════════════════════════════════════════════════════════════════════════════
# ORM MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ActiveSubscription(Base):
    __tablename__ = "active_subscriptions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    security_id = Column(String(64), index=True, nullable=False, unique=True)
    added_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Company(Base):
    __tablename__ = "companies"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    dhan_security_id = Column(String(64), index=True, nullable=False)
    trading_symbol = Column(String(64), index=True, nullable=False)
    company_name = Column(String(255), nullable=False)
    exchange = Column(String(32), default="NSE")
    isin = Column(String(64), nullable=True)
    segment = Column(String(32), nullable=True)
    market_cap = Column(Float, nullable=True)
    is_mtf = Column(Boolean, default=False, nullable=True)
    mtf_leverage = Column(String(32), nullable=True)
    img_url = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class Signal(Base):
    __tablename__ = "signals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    strategy_type = Column(String(32), default="SUPERTREND", index=True)
    date = Column(Date, nullable=False, index=True)
    raw_signal_data = Column(JSON, nullable=True)
    status = Column(String(32), default="PENDING", index=True)
    rejection_reason = Column(String(64), nullable=True)
    expiry_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    execution_date = Column(DateTime(timezone=True), nullable=True)
    rejection_date = Column(DateTime(timezone=True), nullable=True)
    expiry_date = Column(DateTime(timezone=True), nullable=True)

    company = relationship("Company", backref="signals")


class Trade(Base):
    __tablename__ = "trades"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    strategy_type = Column(String(32), default="SUPERTREND", index=True)
    signal_id = Column(String(36), ForeignKey("signals.id"), nullable=True, index=True)
    trade_date = Column(Date, nullable=False)
    allocated_capital = Column(Float, nullable=True)
    allocated_quantity = Column(Integer, nullable=True)   # qty when position was OPENED
    entry_price = Column(Float, nullable=True)            # price at which we entered
    entry_value = Column(Float, nullable=True)            # entry_price × allocated_quantity
    exit_price = Column(Float, nullable=True)             # average exit price (filled at close)
    exit_qty = Column(Integer, nullable=True)             # qty exited (should match allocated_quantity)
    realized_pnl = Column(Float, nullable=True)          # actual P&L at close (from Dhan)
    target_pct = Column(Float, nullable=True)             # target % (e.g. 3.0, 4.0, 5.0, 12.0)
    stoploss_pct = Column(Float, nullable=True)           # stoploss % (e.g. 5.0, 4.0, 3.0)
    exit_pct = Column(Float, nullable=True)               # realized return % achieved (e.g. +3.15%, -4.90%)
    exit_reason = Column(String(64), nullable=True, index=True) # SUPERTREND_RED, TARGET1_HIT, TARGET2_HIT, SL_HIT, BREAKEVEN_HIT, MANUAL
    trade_status = Column(String(32), default="OPEN", index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    # ── ATS-managed execution fields (added for ATS-as-Execution-Manager) ──
    ats_state = Column(String(32), default="SIGNAL", index=True)  # AtsTradeState
    security_id = Column(String(64), nullable=True, index=True)   # Dhan security ID for WS subscription
    remaining_quantity = Column(Integer, nullable=True)            # qty still held after partial exit
    target1_price = Column(Float, nullable=True)                   # first target price (e.g. entry × 1.12)
    target2_price = Column(Float, nullable=True)                   # second target price (e.g. entry × 1.17)
    stop_price = Column(Float, nullable=True)                      # current active stop-loss price
    breakeven_price = Column(Float, nullable=True)                 # entry price — SL moves here after Target1
    partial_exit_completed = Column(Boolean, default=False)        # True once first partial exit filled
    gap_detected = Column(Boolean, default=False)                  # True if market-open gap was detected
    gap_pct = Column(Float, nullable=True)                         # gap percentage vs expected entry
    # ── New trailing SL stage tracker (0=initial -5%, 1=+2%, 2=+4%, 3=+5%) ──
    sl_stage = Column(Integer, default=0, nullable=False)          # Current SL stage (0-3)

    company = relationship("Company", backref="trades")
    signal = relationship("Signal", backref="trades")


class TradeOrder(Base):
    """Legacy Super Order tracking — preserved for backward compatibility.

    New trades use `AtsOrder` instead. This table is kept for historical records
    of trades placed before the ATS-as-Execution-Manager refactor.
    """
    __tablename__ = "trade_orders"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    trade_id = Column(String(36), ForeignKey("trades.id"), nullable=True, index=True)
    dhan_order_id = Column(String(64), index=True, nullable=True)
    correlation_id = Column(String(64), index=True, nullable=True)
    security_id = Column(String(64), index=True, nullable=True)
    quantity = Column(Integer, nullable=False)
    executed_quantity = Column(Integer, default=0)
    price = Column(Float, nullable=False)
    average_execution_price = Column(Float, nullable=True)
    target_price = Column(Float, nullable=False)
    stop_loss_price = Column(Float, nullable=False)
    trailing_jump = Column(Float, default=0.0)
    order_status = Column(String(32), default="PENDING")
    trade_status = Column(String(32), default="OPEN")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    trade = relationship("Trade", backref="orders")


class TradeOrderModification(Base):
    """Legacy Super Order SL modification audit log — preserved for backward compatibility.
    
    Internal ATS state machine modifications use `trade_events` instead.
    """
    __tablename__ = "trade_order_modifications"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    trade_order_id = Column(String(36), ForeignKey("trade_orders.id"), nullable=False, index=True)
    old_sl_price = Column(Float, nullable=False)
    new_sl_price = Column(Float, nullable=False)
    reason = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="PENDING")  # PENDING, SUCCESS, FAILED
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    trade_order = relationship("TradeOrder", backref="modifications")


class BrokerCredential(Base):
    __tablename__ = "creds"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    client_id = Column(String(64), index=True, nullable=False, unique=True)
    access_token = Column(Text, nullable=True)          # Encrypted Fernet AES string
    refresh_token = Column(Text, nullable=True)         # Encrypted Fernet AES string
    pin = Column(Text, nullable=True)                   # Encrypted Fernet AES string
    totp_secret = Column(Text, nullable=True)            # Encrypted Fernet AES string
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class Portfolio(Base):
    __tablename__ = "portfolio"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    dhan_client_id = Column(String(64), index=True, nullable=False)
    available_balance = Column(Float, default=0.0)
    sod_limit = Column(Float, default=0.0)
    collateral_amount = Column(Float, default=0.0)
    receivable_amount = Column(Float, default=0.0)
    utilized_amount = Column(Float, default=0.0)
    blocked_payout_amount = Column(Float, default=0.0)
    withdrawable_balance = Column(Float, default=0.0)
    captured_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Holding(Base):
    __tablename__ = "holdings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    dhan_client_id = Column(String(64), index=True, nullable=False)
    exchange = Column(String(32), default="NSE")
    trading_symbol = Column(String(64), index=True, nullable=False)
    security_id = Column(String(64), index=True, nullable=False)
    isin = Column(String(64), nullable=True)
    total_qty = Column(Integer, default=0)
    dp_qty = Column(Integer, default=0)
    t1_qty = Column(Integer, default=0)
    mtf_t1_qty = Column(Integer, default=0)
    mtf_qty = Column(Integer, default=0)
    available_qty = Column(Integer, default=0)
    collateral_qty = Column(Integer, default=0)
    avg_cost_price = Column(Float, default=0.0)
    last_traded_price = Column(Float, default=0.0)
    captured_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    company = relationship("Company", backref="holdings")


class Position(Base):
    __tablename__ = "positions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    dhan_client_id = Column(String(64), index=True, nullable=False)
    trading_symbol = Column(String(64), index=True, nullable=False)
    security_id = Column(String(64), index=True, nullable=False)
    position_type = Column(String(32), default="LONG")
    exchange_segment = Column(String(32), default="NSE_EQ")
    product_type = Column(String(32), default="CNC")
    buy_avg = Column(Float, default=0.0)
    buy_qty = Column(Integer, default=0)
    cost_price = Column(Float, default=0.0)
    sell_avg = Column(Float, default=0.0)
    sell_qty = Column(Integer, default=0)
    net_qty = Column(Integer, default=0)
    realized_profit = Column(Float, default=0.0)
    unrealized_profit = Column(Float, default=0.0)
    carry_forward_buy_qty = Column(Integer, default=0)
    carry_forward_sell_qty = Column(Integer, default=0)
    carry_forward_buy_value = Column(Float, default=0.0)
    carry_forward_sell_value = Column(Float, default=0.0)
    day_buy_qty = Column(Integer, default=0)
    day_sell_qty = Column(Integer, default=0)
    day_buy_value = Column(Float, default=0.0)
    day_sell_value = Column(Float, default=0.0)
    captured_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    company = relationship("Company", backref="positions")


class DailyCandle(Base):
    __tablename__ = "daily_candles"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    company = relationship("Company", backref="daily_candles")


class WeeklyCandle(Base):
    __tablename__ = "weekly_candles"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    week_start_date = Column(Date, nullable=False, index=True)
    week_end_date = Column(Date, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(BigInteger, default=0)
    trading_days = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint('company_id', 'week_start_date', name='uq_weekly_candle_company_week'),)

    company = relationship("Company", backref="weekly_candles")


class MonthlyCandle(Base):
    __tablename__ = "monthly_candles"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    month_start_date = Column(Date, nullable=False, index=True)
    month_end_date = Column(Date, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(BigInteger, default=0)
    trading_days = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint('company_id', 'month_start_date', name='uq_monthly_candle_company_month'),)

    company = relationship("Company", backref="monthly_candles")


class MarketHoliday(Base):
    __tablename__ = "market_holidays"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    date = Column(Date, unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# ATS EXECUTION ENGINE — NEW MODELS (ATS-as-Execution-Manager refactor)
# ═══════════════════════════════════════════════════════════════════════════════

class AtsOrder(Base):
    """Tracks normal Dhan orders placed by the ATS execution engine.

    Replaces the Super-Order-based approach. Each row represents a single
    order placed via POST /v2/orders. The `order_purpose` field distinguishes
    entry orders from exit orders.

    Idempotency: before placing any order, the engine checks for an existing
    AtsOrder with (trade_id, order_purpose) that is not FAILED/CANCELLED.
    If one exists, the order is NOT re-placed.
    """
    __tablename__ = "ats_orders"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    trade_id = Column(String(36), ForeignKey("trades.id"), nullable=False, index=True)
    strategy_type = Column(String(32), default="SUPERTREND", index=True)
    dhan_order_id = Column(String(64), index=True, nullable=True)   # returned by Dhan on placement
    correlation_id = Column(String(64), index=True, nullable=True)  # sent as correlationId to Dhan

    # What this order is for: ENTRY / PARTIAL_EXIT / FINAL_EXIT / EMERGENCY_EXIT
    order_purpose = Column(String(32), nullable=False, default="ENTRY")

    transaction_type = Column(String(8), nullable=False, default="BUY")  # BUY or SELL
    security_id = Column(String(64), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)              # None for MARKET orders
    order_type = Column(String(16), nullable=False, default="MARKET")  # MARKET or LIMIT
    product_type = Column(String(16), nullable=False, default="CNC")
    exchange_segment = Column(String(16), nullable=False, default="NSE_EQ")

    # Fill tracking
    status = Column(String(32), default="PENDING", index=True)  # PENDING / FILLED / CANCELLED / REJECTED / FAILED
    fill_price = Column(Float, nullable=True)
    fill_qty = Column(Integer, default=0)

    # Error tracking for retry logic
    error_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)

    # Timestamps
    placed_at = Column(DateTime(timezone=True), nullable=True)
    filled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    trade = relationship("Trade", backref="ats_orders")


class TradeEvent(Base):
    """Append-only event log for the ATS trade lifecycle."""
    __tablename__ = "trade_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(36), ForeignKey("trades.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    detail = Column(Text, nullable=True)   # free-form description
    price = Column(Float, nullable=True)   # relevant price at event time (LTP, fill price, etc.)
    quantity = Column(Integer, nullable=True)  # relevant quantity
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    trade = relationship("Trade", backref="events")


class OrderAttempt(Base):
    """Audit log of order placement attempts sent to Dhan API ('what did our system try to do')."""
    __tablename__ = "order_attempts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    trade_id = Column(String(36), ForeignKey("trades.id"), nullable=False, index=True)
    ats_order_id = Column(String(36), ForeignKey("ats_orders.id"), nullable=True, index=True)
    correlation_id = Column(String(64), index=True, nullable=True)
    order_purpose = Column(String(32), nullable=False)
    transaction_type = Column(String(8), nullable=False, default="SELL")
    requested_quantity = Column(Integer, nullable=False)
    endpoint = Column(String(255), nullable=False)
    request_payload = Column(JSON, nullable=True)
    status = Column(String(32), default="INITIATED", index=True)  # INITIATED, REQUEST_SENT, ACCEPTED, REJECTED, UNKNOWN
    response_status_code = Column(Integer, nullable=True)
    response_body = Column(JSON, nullable=True)
    dhan_order_id = Column(String(64), index=True, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    trade = relationship("Trade", backref="order_attempts")
    ats_order = relationship("AtsOrder", backref="attempts")


class AppConfig(Base):
    """Stores application-wide configuration like the encrypted master password."""
    __tablename__ = "app_config"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    config_key = Column(String(128), index=True, nullable=False, unique=True)
    config_value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class StrategySettings(Base):
    """Stores dynamic parameters for signal generation and trade management."""
    __tablename__ = "strategy_settings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # Signal Generation Parameters
    daily_rsi_period = Column(Integer, default=14)
    daily_rsi_lower = Column(Float, default=50.0)
    daily_rsi_upper = Column(Float, default=90.0)
    weekly_rsi_period = Column(Integer, default=14)
    weekly_rsi_lower = Column(Float, default=65.0)
    weekly_rsi_upper = Column(Float, default=85.0)
    supertrend_period = Column(Integer, default=21)
    supertrend_multiplier = Column(Float, default=1.5)
    candle_range_min = Column(Float, default=3.0)
    candle_range_max = Column(Float, default=12.0)
    market_cap_min_cr = Column(Float, default=8000.0)
    entry_high_breakout_pct = Column(Float, default=3.0) # 1.03
    
    # Trade Management Parameters
    initial_sl_pct = Column(Float, default=-5.0) # -5%
    target1_pct = Column(Float, default=17.0) # +17%
    trade_stages = Column(JSON, default=[
        {"trigger": 5.0, "trail": 2.0, "qty": 0.0},
        {"trigger": 8.0, "trail": 4.0, "qty": 0.0},
        {"trigger": 12.0, "trail": 5.0, "qty": 50.0}
    ])
    
    capital_allocation_pct = Column(Float, default=20.0)
    
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class MonthlyRsiSettings(Base):
    """Stores dynamic parameters for Monthly RSI strategy."""
    __tablename__ = "monthly_rsi_settings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # Signal Generation Parameters
    rsi_period = Column(Integer, default=14)
    min_rsi = Column(Float, default=55.0)
    max_rsi = Column(Float, default=70.0)
    swing_window = Column(Integer, default=10)
    swing_buffer_pct = Column(Float, default=0.5)
    min_roc6_pct = Column(Float, default=25.0)
    min_close_above_sma12_pct = Column(Float, default=10.0)
    max_entry_gap_pct = Column(Float, default=5.0)
    
    # Exit / Risk Parameters
    rsi_exit_below = Column(Float, default=55.0)
    rsi_exit_trail_points = Column(Float, default=5.0)
    min_stop_distance_pct = Column(Float, default=10.0)
    max_stop_distance_pct = Column(Float, default=25.0)
    supertrend_period = Column(Integer, default=10)
    supertrend_multiplier = Column(Float, default=3.0)
    supertrend_exit_enabled = Column(Boolean, default=True)
    
    # Trade Management Parameters
    target_pct = Column(Float, default=100.0)
    partial_exit_qty_pct = Column(Float, default=0.0)
    partial_exit_profit_pct = Column(Float, default=10.0)
    partial_stop_profit_pct = Column(Float, default=0.0)
    
    capital_allocation_pct = Column(Float, default=20.0)
    
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
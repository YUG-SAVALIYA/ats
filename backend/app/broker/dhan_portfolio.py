"""
app.broker.dhan_portfolio
=========================
Live Portfolio & Account Sync Service using Dhan API v2.
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.broker.dhan_client import get_account_context, is_error_response, is_empty_portfolio_response
from app.data.database import SessionLocal
from app.data.models import Portfolio, Holding, Position, Company, Trade, TradeOrder

logger = logging.getLogger("ats.dhan_portfolio")


def _resolve_account_context(account_id: str):
    import sys
    for mod_name in ("app.api.router", "app.broker.dhan_portfolio", "app.broker.dhan_client"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "get_account_context"):
            fn = getattr(mod, "get_account_context")
            from app.broker.dhan_client import get_account_context as default_get_ctx
            if fn is not default_get_ctx:
                return fn(account_id)
    return get_account_context(account_id)


class PortfolioService:
    """Live Portfolio & Account Sync Service using Dhan API v2."""

    def __init__(self, client: Optional[Any] = None, dhan_account_id: Optional[str] = None):
        if client is not None:
            self.client = client
        elif dhan_account_id is not None:
            self.client = _resolve_account_context(dhan_account_id)
        else:
            self.client = None


    def _get_company_id(self, db: Session, security_id: str, trading_symbol: str) -> Optional[str]:
        """Look up a Company in DB by dhan_security_id or trading_symbol."""
        company = None
        if security_id:
            company = db.query(Company).filter(Company.dhan_security_id == str(security_id)).first()
        if not company and trading_symbol:
            company = db.query(Company).filter(Company.trading_symbol == trading_symbol.upper()).first()
        return company.id if company else None

    def _save_fund_snapshot(self, db: Session, data: Dict[str, Any]) -> None:
        """Persist fund limits snapshot into `portfolio` table."""
        cfg = self.client.config

        new_available   = float(data.get("availabelBalance",    data.get("available_balance",    0.0)) or 0.0)
        new_sod         = float(data.get("sodLimit",            data.get("sod_limit",            0.0)) or 0.0)
        new_collateral  = float(data.get("collateralAmount",    data.get("collateral_amount",    0.0)) or 0.0)
        new_receivable  = float(data.get("receiveableAmount",   data.get("receivable_amount",    0.0)) or 0.0)
        new_utilized    = float(data.get("utilizedAmount",      data.get("utilized_amount",      0.0)) or 0.0)
        new_blocked     = float(data.get("blockedPayoutAmount", data.get("blocked_payout_amount",0.0)) or 0.0)
        new_withdrawable= float(data.get("withdrawableBalance", data.get("withdrawable_balance", 0.0)) or 0.0)

        last = (
            db.query(Portfolio)
            .filter(Portfolio.dhan_client_id == cfg.client_id)
            .order_by(Portfolio.captured_at.desc())
            .first()
        )

        if last and (
            last.available_balance    == new_available   and
            last.sod_limit            == new_sod         and
            last.collateral_amount    == new_collateral  and
            last.receivable_amount    == new_receivable  and
            last.utilized_amount      == new_utilized    and
            last.blocked_payout_amount== new_blocked     and
            last.withdrawable_balance == new_withdrawable
        ):
            logger.info(f"[PORTFOLIO][DB] Fund snapshot unchanged — skipping insert.")
            return

        record = Portfolio(
            id=str(uuid.uuid4()),
            dhan_client_id=cfg.client_id,
            available_balance=new_available,
            sod_limit=new_sod,
            collateral_amount=new_collateral,
            receivable_amount=new_receivable,
            utilized_amount=new_utilized,
            blocked_payout_amount=new_blocked,
            withdrawable_balance=new_withdrawable,
            captured_at=datetime.utcnow()
        )
        db.add(record)
        db.commit()
        logger.info(f"[PORTFOLIO][DB] Fund snapshot saved. Available balance: {new_available}")

    def _upsert_holdings(self, db: Session, holdings: List[Dict[str, Any]]) -> None:
        """Upsert each holding into `holdings` table (match on security_id)."""
        cfg = self.client.config
        for h in holdings:
            security_id = str(h.get("securityId", h.get("security_id", ""))).strip()
            trading_symbol = str(h.get("tradingSymbol", h.get("trading_symbol", ""))).strip().upper()

            if not security_id:
                continue

            company_id = self._get_company_id(db, security_id, trading_symbol)

            existing = db.query(Holding).filter(
                Holding.dhan_client_id == cfg.client_id,
                Holding.security_id == security_id
            ).first()

            values = dict(
                company_id=company_id,
                dhan_client_id=cfg.client_id,
                exchange=str(h.get("exchange", "NSE")),
                trading_symbol=trading_symbol,
                security_id=security_id,
                isin=str(h.get("isin", "") or ""),
                total_qty=int(h.get("totalQty", h.get("total_qty", 0)) or 0),
                dp_qty=int(h.get("dpQty", h.get("dp_qty", 0)) or 0),
                t1_qty=int(h.get("t1Qty", h.get("t1_qty", 0)) or 0),
                mtf_t1_qty=int(h.get("mtfT1Qty", h.get("mtf_t1_qty", 0)) or 0),
                mtf_qty=int(h.get("mtfQty", h.get("mtf_qty", 0)) or 0),
                available_qty=int(h.get("availableQty", h.get("available_qty", 0)) or 0),
                collateral_qty=int(h.get("collateralQty", h.get("collateral_qty", 0)) or 0),
                avg_cost_price=float(h.get("avgCostPrice", h.get("avg_cost_price", 0.0)) or 0.0),
                last_traded_price=float(h.get("ltp", h.get("last_traded_price", 0.0)) or 0.0),
                captured_at=datetime.utcnow()
            )

            if existing:
                for k, v in values.items():
                    setattr(existing, k, v)
            else:
                db.add(Holding(id=str(uuid.uuid4()), **values))

        db.commit()
        logger.info(f"[PORTFOLIO][DB] Upserted {len(holdings)} holdings into DB.")

    def _close_trade_on_exit(self, db: Session, security_id: str, realized_profit: float, position_data: Dict[str, Any]) -> None:
        """Called when a position's net_qty drops to 0 (trade fully exited)."""
        try:
            company = db.query(Company).filter(Company.dhan_security_id == str(security_id)).first()
            if not company:
                return

            open_trade = (
                db.query(Trade)
                .filter(
                    Trade.company_id == company.id,
                    Trade.trade_status == "OPEN"
                )
                .order_by(Trade.created_at.desc())
                .first()
            )

            if not open_trade:
                return

            sell_avg = float(position_data.get("sellAvg", position_data.get("sell_avg", 0.0)) or 0.0)
            sell_qty = int(position_data.get("sellQty", position_data.get("sell_qty", 0)) or 0)
            buy_qty  = int(position_data.get("buyQty",  position_data.get("buy_qty",  0)) or 0)
            exited_qty = sell_qty if sell_qty > 0 else buy_qty

            linked_orders = (
                db.query(TradeOrder)
                .filter(TradeOrder.trade_id == open_trade.id)
                .all()
            )
            target_p = None
            sl_p = None
            for o in linked_orders:
                if o.target_price and o.target_price > 0:
                    target_p = o.target_price
                if o.stop_loss_price and o.stop_loss_price > 0:
                    sl_p = o.stop_loss_price

            entry_p = open_trade.entry_price or sell_avg
            exit_pct = round(((sell_avg - entry_p) / entry_p) * 100, 2) if entry_p > 0 else 0.0

            if sell_avg > 0 and target_p and sell_avg >= target_p * 0.998:
                exit_reason = f"TARGET_HIT (+{open_trade.target_pct or 3.0:.1f}%)"
            elif sell_avg > 0 and sl_p and sell_avg <= sl_p * 1.002:
                exit_reason = f"STOPLOSS_HIT (-{open_trade.stoploss_pct or 5.0:.1f}%)"
            elif exit_pct > 0:
                exit_reason = f"TARGET_HIT ({exit_pct:+.2f}%)"
            elif exit_pct < 0:
                exit_reason = f"STOPLOSS_HIT ({exit_pct:+.2f}%)"
            else:
                exit_reason = f"CLOSED ({exit_pct:+.2f}%)"

            open_trade.trade_status = "CLOSED"
            open_trade.exit_price = sell_avg if sell_avg > 0 else None
            open_trade.exit_qty = exited_qty if exited_qty > 0 else open_trade.allocated_quantity
            open_trade.realized_pnl = realized_profit
            open_trade.exit_pct = exit_pct
            open_trade.exit_reason = exit_reason
            open_trade.closed_at = datetime.utcnow()

            for order in linked_orders:
                order.trade_status = "CLOSED"
                order.order_status = exit_reason
                order.executed_quantity = open_trade.exit_qty or 0
                order.average_execution_price = sell_avg if sell_avg > 0 else None
                order.executed_at = datetime.utcnow()
                order.closed_at = datetime.utcnow()
                order.updated_at = datetime.utcnow()

            db.commit()
            logger.info(
                f"[PORTFOLIO][DB] EXIT DETECTED: {company.trading_symbol} | "
                f"Reason: {exit_reason} | Realized P&L: {realized_profit:+.2f}"
            )
        except Exception as exc:
            logger.warning(f"[PORTFOLIO][DB] Could not close trade on exit: {exc}")

    def _upsert_positions(self, db: Session, positions: List[Dict[str, Any]]) -> None:
        """Upsert each position into `positions` table."""
        cfg = self.client.config
        for p in positions:
            security_id = str(p.get("securityId", p.get("security_id", ""))).strip()
            trading_symbol = str(p.get("tradingSymbol", p.get("trading_symbol", ""))).strip().upper()
            product_type = str(p.get("productType", p.get("product_type", "CNC"))).strip()

            if not security_id:
                continue

            company_id = self._get_company_id(db, security_id, trading_symbol)
            new_net_qty = int(p.get("netQty", p.get("net_qty", 0)) or 0)
            realized_profit = float(p.get("realizedProfit", p.get("realized_profit", 0.0)) or 0.0)

            existing = db.query(Position).filter(
                Position.dhan_client_id == cfg.client_id,
                Position.security_id == security_id,
                Position.product_type == product_type
            ).first()

            was_open = existing and existing.net_qty != 0
            is_now_closed = new_net_qty == 0

            if was_open and is_now_closed:
                self._close_trade_on_exit(db, security_id, realized_profit, p)

            values = dict(
                company_id=company_id,
                dhan_client_id=cfg.client_id,
                trading_symbol=trading_symbol,
                security_id=security_id,
                position_type=str(p.get("positionType", p.get("position_type", "LONG"))),
                exchange_segment=str(p.get("exchangeSegment", p.get("exchange_segment", "NSE_EQ"))),
                product_type=product_type,
                buy_avg=float(p.get("buyAvg", p.get("buy_avg", 0.0)) or 0.0),
                buy_qty=int(p.get("buyQty", p.get("buy_qty", 0)) or 0),
                cost_price=float(p.get("costPrice", p.get("cost_price", 0.0)) or 0.0),
                sell_avg=float(p.get("sellAvg", p.get("sell_avg", 0.0)) or 0.0),
                sell_qty=int(p.get("sellQty", p.get("sell_qty", 0)) or 0),
                net_qty=new_net_qty,
                realized_profit=realized_profit,
                unrealized_profit=float(p.get("unrealizedProfit", p.get("unrealized_profit", 0.0)) or 0.0),
                carry_forward_buy_qty=int(p.get("carryForwardBuyQty", 0) or 0),
                carry_forward_sell_qty=int(p.get("carryForwardSellQty", 0) or 0),
                carry_forward_buy_value=float(p.get("carryForwardBuyValue", 0.0) or 0.0),
                carry_forward_sell_value=float(p.get("carryForwardSellValue", 0.0) or 0.0),
                day_buy_qty=int(p.get("dayBuyQty", 0) or 0),
                day_sell_qty=int(p.get("daySellQty", 0) or 0),
                day_buy_value=float(p.get("dayBuyValue", 0.0) or 0.0),
                day_sell_value=float(p.get("daySellValue", 0.0) or 0.0),
                captured_at=datetime.utcnow()
            )

            if existing:
                for k, v in values.items():
                    setattr(existing, k, v)
            else:
                db.add(Position(id=str(uuid.uuid4()), **values))

        db.commit()
        logger.info(f"[PORTFOLIO][DB] Upserted {len(positions)} positions into DB.")

    def get_fund_limits(self) -> Dict[str, Any]:
        """Fetch live fund limits from Dhan (GET /v2/fundlimit)."""
        url = "https://api.dhan.co/v2/fundlimit"
        res = self.client.execute_v2_get(url)
        if is_empty_portfolio_response(res):
            return {}
        if is_error_response(res):
            logger.error(f"[PORTFOLIO] Error fetching fund limits: {res.get('remarks')}")
            raise RuntimeError(res.get("remarks", "Failed to fetch fund limits from Dhan"))

        if isinstance(res, dict) and "data" in res and isinstance(res["data"], dict):
            res = res["data"]
        if isinstance(res, dict):
            res.pop("dhanClientId", None)
            res.pop("dhan_client_id", None)

        data = res if isinstance(res, dict) else {}

        if data:
            try:
                db = SessionLocal()
                self._save_fund_snapshot(db, data)
                db.close()
            except Exception as exc:
                logger.warning(f"[PORTFOLIO][DB] Could not persist fund snapshot: {exc}")

        return data

    def get_holdings(self) -> List[Dict[str, Any]]:
        """Fetch live holdings from Dhan (GET /v2/holdings)."""
        url = "https://api.dhan.co/v2/holdings"
        res = self.client.execute_v2_get(url)
        if is_empty_portfolio_response(res):
            return []
        if is_error_response(res):
            logger.error(f"[PORTFOLIO] Error fetching holdings: {res.get('remarks')}")
            raise RuntimeError(res.get("remarks", "Failed to fetch holdings from Dhan"))

        if isinstance(res, list):
            holdings = res
        elif isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            holdings = res["data"]
        else:
            holdings = []

        if holdings:
            try:
                db = SessionLocal()
                self._upsert_holdings(db, holdings)
                db.close()
            except Exception as exc:
                logger.warning(f"[PORTFOLIO][DB] Could not upsert holdings: {exc}")

        return holdings

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch live positions from Dhan (GET /v2/positions)."""
        url = "https://api.dhan.co/v2/positions"
        res = self.client.execute_v2_get(url)
        if is_empty_portfolio_response(res):
            return []
        if is_error_response(res):
            logger.error(f"[PORTFOLIO] Error fetching positions: {res.get('remarks')}")
            raise RuntimeError(res.get("remarks", "Failed to fetch positions from Dhan"))

        if isinstance(res, list):
            positions = res
        elif isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            positions = res["data"]
        else:
            positions = []

        if positions:
            try:
                db = SessionLocal()
                self._upsert_positions(db, positions)
                db.close()
            except Exception as exc:
                logger.warning(f"[PORTFOLIO][DB] Could not upsert positions: {exc}")

        return positions

    def get_trades(self) -> List[Dict[str, Any]]:
        """Fetch executed trade history (GET /v2/trades)."""
        url = "https://api.dhan.co/v2/trades"
        res = self.client.execute_v2_get(url)
        if is_empty_portfolio_response(res):
            return []
        if is_error_response(res):
            logger.error(f"[PORTFOLIO] Error fetching trade history: {res.get('remarks')}")
            raise RuntimeError(res.get("remarks", "Failed to fetch trade history from Dhan"))

        trades_list = []
        if isinstance(res, list):
            trades_list = res
        elif isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            trades_list = res["data"]

        for t in trades_list:
            if isinstance(t, dict):
                tid = str(t.get("tradeId") or t.get("exchangeTradeId") or t.get("trade_id") or t.get("orderId") or "")
                t["tradeId"] = tid
                t["exchangeTradeId"] = str(t.get("exchangeTradeId") or tid)

        return trades_list

    def get_orders(self) -> List[Dict[str, Any]]:
        """Fetch live regular orders from Dhan (GET /v2/orders)."""
        url = "https://api.dhan.co/v2/orders"
        res = self.client.execute_v2_get(url)
        if is_empty_portfolio_response(res):
            return []
        if is_error_response(res):
            logger.error(f"[PORTFOLIO] Error fetching orders: {res.get('remarks')}")
            return []

        if isinstance(res, list):
            return res
        if isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            return res["data"]
        return []

    def get_full_broker_summary(self) -> Dict[str, Any]:
        """Unified snapshot of account funds, holdings, positions, orders, and trades."""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=5) as executor:
            f_funds = executor.submit(self.get_fund_limits)
            f_holdings = executor.submit(self.get_holdings)
            f_positions = executor.submit(self.get_positions)
            f_trades = executor.submit(self.get_trades)
            f_orders = executor.submit(self.get_orders)

            funds = f_funds.result()
            holdings = f_holdings.result()
            positions = f_positions.result()
            trades = f_trades.result()
            orders = f_orders.result()

        return {
            "funds": funds,
            "holdings": holdings,
            "positions": positions,
            "orders": orders,
            "trades": trades
        }

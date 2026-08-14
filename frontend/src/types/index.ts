export interface AuthStatus {
  status: 'connected' | 'disconnected';
  client_id: string;
  token_active: boolean;
  totp_configured: boolean;
  mode: string;
}

export interface FundLimits {
  availabelBalance?: number;
  sodLimit?: number;
  collateralAmount?: number;
  receiveableAmount?: number;
  utilizedAmount?: number;
  blockedPayoutAmount?: number;
  withdrawableBalance?: number;
  dhanClientId?: string;
}

export interface HoldingItem {
  securityId: string;
  tradingSymbol: string;
  exchange?: string;
  totalQty: number;
  dpQty: number;
  t1Qty: number;
  mtf_qty: number;
  mtf_t1_qty: number;
  avgCostPrice: number;
  lastTradedPrice?: number;
  closePrice?: number;
}

export interface PositionItem {
  positionType?: 'LONG' | 'SHORT';
  productType?: string;
  exchangeSegment?: string;
  tradingSymbol: string;
  securityId: string;
  netQty: number;
  dayBuyQty?: number;
  daySellQty?: number;
  carryForwardBuyQty?: number;
  carryForwardSellQty?: number;
  buyAvg?: number;
  sellAvg?: number;
  costPrice?: number;
  realizedProfit?: number;
  unrealizedProfit?: number;
  drvOptionType?: string;
  drvStrikePrice?: number;
  drvExpiryDate?: string;
}

export interface SuperOrderItem {
  orderId: string;
  correlationId?: string;
  orderStatus: string;
  transactionType: string;
  exchangeSegment: string;
  productType: string;
  orderType: string;
  tradingSymbol: string;
  securityId: string;
  quantity: number;
  filledQty?: number;
  price: number;
  targetPrice?: number;
  stopLossPrice?: number;
  trailingJump?: number;
  legName?: string;
  legDetails?: Array<{
    orderId: string;
    legName: string;
    transactionType: string;
    price: number;
    orderStatus: string;
  }>;
}

export interface RegularOrderItem {
  orderId: string;
  orderStatus: string;
  transactionType: string;
  exchangeSegment: string;
  productType: string;
  orderType: string;
  tradingSymbol: string;
  securityId: string;
  quantity: number;
  price: number;
  triggerPrice?: number;
  createTime?: string;
  filledQty?: number;
  tradedQuantity?: number;
}

export interface TradeItem {
  tradeId?: string;
  exchangeTradeId?: string;
  orderId: string;
  tradingSymbol: string;
  securityId: string;
  transactionType: string;
  exchangeSegment: string;
  tradedQuantity: number;
  tradedPrice: number;
  createTime?: string;
  productType?: string;
  orderType?: string;
}

export interface StockMasterItem {
  security_id: string;
  symbol: string;
  name: string;
  exchange_segment: string;
  lot_size: number;
}

export interface DbTradeItem {
  id: string;
  company_id: string;
  signal_id: string;
  trade_date: string;
  allocated_quantity: number;
  entry_price: number;
  entry_value: number;
  target_pct: number;
  stoploss_pct: number;
  exit_pct?: number;
  exit_price?: number;
  exit_qty?: number;
  realized_pnl?: number;
  exit_reason?: string;
  trade_status: string;
  created_at: string;
  executed_at?: string;
  closed_at?: string;
}

export interface DbOrderItem {
  id: string;
  trade_id: string;
  dhan_order_id?: string;
  security_id: string;
  quantity: number;
  price: number;
  target_price?: number;
  stop_loss_price?: number;
  trailing_jump?: number;
  order_status: string;
  trade_status: string;
  submitted_at?: string;
  executed_at?: string;
  closed_at?: string;
}

export interface DbModificationItem {
  id: string;
  trade_order_id: string;
  old_sl_price: number;
  new_sl_price: number;
  reason: string;
  status: string;
  created_at: string;
  executed_at?: string;
  error_message?: string;
}

export interface PortfolioSummary {
  funds: FundLimits;
  holdings: HoldingItem[];
  positions: PositionItem[];
  super_orders: SuperOrderItem[];
  orders: RegularOrderItem[];
  trades: TradeItem[];
  db_trades?: DbTradeItem[];
  db_orders?: DbOrderItem[];
  db_modifications?: DbModificationItem[];
}

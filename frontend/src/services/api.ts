import {
  AuthStatus,
  PortfolioSummary,
  FundLimits,
  HoldingItem,
  PositionItem,
  SuperOrderItem,
  TradeItem,
  StockMasterItem,
} from '../types';

// If VITE_BACKEND_URL is provided in .env, it will use that explicit domain/port.
// Otherwise, it intelligently falls back to relative '/api' for unified hosting.
const env = (import.meta as any).env;
export const API_BASE = env.VITE_BACKEND_URL ? `${env.VITE_BACKEND_URL}/api` : '/api';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const token = localStorage.getItem('ats_admin_token');
  const res = await fetch(`${API_BASE}${url}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      ...options?.headers,
    },
    ...options,
  });

  if (res.status === 401 || res.status === 403) {
    localStorage.removeItem('ats_admin_token');
    window.location.href = '/lock';
    throw new Error('Unauthorized');
  }

  let data: any;
  try {
    data = await res.json();
  } catch (_) {
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: Failed to parse response from server`);
    }
    return {} as T;
  }

  if (!res.ok) {
    let errMessage = `HTTP ${res.status}`;
    if (data && data.detail) {
      errMessage = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } else if (data && data.remarks) {
      errMessage = data.remarks;
    } else if (data && data.message) {
      errMessage = data.message;
    }
    throw new Error(errMessage);
  }

  if (data && typeof data === 'object' && !Array.isArray(data)) {
    if (data.status === 'failure' || data.status === 'error') {
      throw new Error(data.remarks || data.message || 'API operation failed');
    }
  }

  return data as T;
}

export interface EngineStatus {
  enabled: boolean;
  mode: string;
  entry_gate: string;
  trailing_sl: string;
  details: {
    status: string;
    active_signals: number;
    open_positions: number;
    last_tick: string;
  };
}

export interface StrategySignal {
  id: string;
  symbol?: string;
  trading_symbol?: string;
  company_name?: string;
  security_id?: string;
  exchange_segment?: string;
  signal_date?: string;
  signal_high?: number;
  signal_low?: number;
  ref_price?: number;
  daily_rsi?: number;
  weekly_rsi?: number;
  market_cap_cr?: number;
  target_price?: number;
  stop_loss?: number;
  quantity?: number;
  status: string;
  strategy?: string;
  rejection_reason?: string;
  created_at?: string | number;
}

export interface StrategySettings {
  daily_rsi_period: number;
  daily_rsi_lower: number;
  daily_rsi_upper: number;
  weekly_rsi_period: number;
  weekly_rsi_lower: number;
  weekly_rsi_upper: number;
  supertrend_period: number;
  supertrend_multiplier: number;
  candle_range_min: number;
  candle_range_max: number;
  market_cap_min_cr: number;
  entry_high_breakout_pct: number;
  initial_sl_pct: number;
  target1_pct: number;
  target2_pct: number;
  sl_stage1_trigger: number;
  sl_stage1_trail: number;
  sl_stage2_trigger: number;
  sl_stage2_trail: number;
  sl_stage3_trigger: number;
  sl_stage3_trail: number;
}

export const api = {
  // Automated Engine
  getEngineStatus: () => fetchJson<EngineStatus>('/engine/status'),
  toggleEngine: (enabled: boolean) =>
    fetchJson<{ enabled: boolean; message: string }>('/engine/toggle', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),
  getSignals: () => fetchJson<StrategySignal[]>('/signals'),

  // Auth
  getAuthStatus: () => fetchJson<AuthStatus>('/auth/status'),
  renewToken: (totp?: string) =>
    fetchJson<{ status: string; message: string }>('/auth/renew', {
      method: 'POST',
      body: JSON.stringify({ totp }),
    }),

  // Portfolio & Account Sync
  getPortfolioSummary: () => fetchJson<PortfolioSummary>('/portfolio/summary'),
  getFunds: () => fetchJson<FundLimits>('/portfolio/funds'),
  getHoldings: () => fetchJson<HoldingItem[]>('/portfolio/holdings'),
  getPositions: () => fetchJson<PositionItem[]>('/portfolio/positions'),
  getTrades: () => fetchJson<TradeItem[]>('/portfolio/trades'),

  // Super Orders Only
  getSuperOrders: () => fetchJson<SuperOrderItem[]>('/orders/super'),
  cancelSuperOrder: (orderId: string, legName: string) =>
    fetchJson<any>(`/orders/super/${orderId}/${legName}`, {
      method: 'DELETE',
    }),

  // Stock Master
  searchStocks: (query: string) => fetchJson<StockMasterItem[]>(`/stocks/search?q=${encodeURIComponent(query)}`),
  getStockBySymbol: (symbol: string) => fetchJson<StockMasterItem>(`/stocks/${encodeURIComponent(symbol)}`),

  // Strategy Settings
  getStrategySettings: () => fetchJson<StrategySettings>('/settings/strategy'),
  updateStrategySettings: (settings: StrategySettings) => fetchJson<{ status: string; message: string }>('/settings/strategy', {
    method: 'PUT',
    body: JSON.stringify(settings),
  }),

  // Manual Controls
  manualTradeExit: (tradeId: string, quantity: number) => fetchJson<{ status: string; message: string; ats_order_id: string }>(`/trades/${tradeId}/exit`, {
    method: 'POST',
    body: JSON.stringify({ quantity }),
  }),
  manualExitBySecurity: (securityId: string, quantity: number) => fetchJson<{ status: string; message: string; ats_order_id: string }>('/trades/exit-by-security', {
    method: 'POST',
    body: JSON.stringify({ security_id: securityId, quantity }),
  }),
};

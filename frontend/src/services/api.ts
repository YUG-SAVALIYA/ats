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
  const isAuthEndpoint = url.startsWith('/app-auth/');
  
  const res = await fetch(`${API_BASE}${url}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      ...options?.headers,
    },
    ...options,
  });

  if ((res.status === 401 || res.status === 403) && !isAuthEndpoint) {
    localStorage.removeItem('ats_admin_token');
    window.location.href = '/lock';
    const authErr = new Error('Authentication required or session expired');
    (authErr as any).status = res.status;
    throw authErr;
  }

  let data: any;
  try {
    data = await res.json();
  } catch (_) {
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status}: Server returned non-JSON error response`);
      (err as any).status = res.status;
      throw err;
    }
    return {} as T;
  }

  if (!res.ok) {
    let errMessage = `HTTP ${res.status}: ${res.statusText || 'Request Failed'}`;
    if (data && data.detail) {
      errMessage = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } else if (data && data.remarks) {
      errMessage = typeof data.remarks === 'string' ? data.remarks : JSON.stringify(data.remarks);
    } else if (data && data.message) {
      errMessage = data.message;
    } else if (data && data.error) {
      errMessage = typeof data.error === 'string' ? data.error : JSON.stringify(data.error);
    }
    const err = new Error(errMessage);
    (err as any).status = res.status;
    (err as any).data = data;
    throw err;
  }

  if (data && typeof data === 'object' && !Array.isArray(data)) {
    if (data.status === 'failure' || data.status === 'error') {
      const errDetail = data.detail || data.remarks || data.message || data.error || 'API operation failed';
      const err = new Error(typeof errDetail === 'string' ? errDetail : JSON.stringify(errDetail));
      (err as any).status = data.status_code || 400;
      (err as any).data = data;
      throw err;
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
  candle_range?: number;
  supertrend_flip?: boolean;
  market_cap_cr?: number;
  target_price?: number;
  stop_loss?: number;
  quantity?: number;
  status: string;
  strategy?: string;
  rejection_reason?: string;
  evaluation?: any;
  executed_price?: number;
  new_target_pct?: number;
  new_sl_pct?: number;
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
  capital_allocation_pct: number;
  trade_stages: {
    trigger: number;
    trail: number;
    qty: number;
  }[];
}

export interface MonthlyRsiSettings {
  rsi_period: number;
  min_rsi: number;
  max_rsi: number;
  swing_window: number;
  swing_buffer_pct: number;
  min_roc6_pct: number;
  min_close_above_sma12_pct: number;
  max_entry_gap_pct: number;
  
  rsi_exit_below: number;
  rsi_exit_trail_points: number;
  min_stop_distance_pct: number;
  max_stop_distance_pct: number;
  supertrend_period: number;
  supertrend_multiplier: number;
  supertrend_exit_enabled: boolean;
  
  target_pct: number;
  partial_exit_qty_pct: number;
  partial_exit_profit_pct: number;
  partial_stop_profit_pct: number;
  capital_allocation_pct: number;
}

export const api = {
  // Automated Engine
  getEngineStatus: () => fetchJson<EngineStatus>('/engine/status'),
  toggleEngine: (enabled: boolean) =>
    fetchJson<{ enabled: boolean; message: string }>('/engine/toggle', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),
  getSignals: (strategy?: string) => fetchJson<StrategySignal[]>(`/signals${strategy ? `?strategy_type=${strategy}` : ''}`),

  // Auth
  getAuthStatus: () => fetchJson<AuthStatus>('/auth/status'),
  renewToken: (totp?: string) =>
    fetchJson<{ status: string; message: string }>('/auth/renew', {
      method: 'POST',
      body: JSON.stringify({ totp }),
    }),

  // Portfolio & Account Sync
  getPortfolioSummary: (strategy?: string) => fetchJson<PortfolioSummary>(`/portfolio/summary${strategy ? `?strategy_type=${strategy}` : ''}`),
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
  getStrategySettings: (strategy?: string) => {
    if (strategy === 'MONTHLY_RSI') {
      return fetchJson<any>('/settings/monthly_rsi');
    }
    return fetchJson<any>('/settings/strategy');
  },
  updateStrategySettings: (settings: any, strategy?: string) => {
    if (strategy === 'MONTHLY_RSI') {
      return fetchJson<{ status: string; message: string }>('/settings/monthly_rsi', {
        method: 'PUT',
        body: JSON.stringify(settings),
      });
    }
    return fetchJson<{ status: string; message: string }>('/settings/strategy', {
      method: 'PUT',
      body: JSON.stringify(settings),
    });
  },

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

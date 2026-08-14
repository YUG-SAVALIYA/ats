import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Routes, Route } from 'react-router-dom';
import {
  AuthStatus,
  FundLimits,
  HoldingItem,
  PositionItem,
  SuperOrderItem,
  RegularOrderItem,
  TradeItem,
  DbTradeItem,
  DbOrderItem,
  DbModificationItem,
} from './types';
import { api, EngineStatus, StrategySignal } from './services/api';
import { ToastProvider, useToast } from './context/ToastContext';
import { CompanyImageProvider } from './context/CompanyImageContext';
import { Header } from './components/Header';
import { MetricCards } from './components/MetricCards';
import { SignalsTable } from './components/SignalsTable';
import { PositionsTable } from './components/PositionsTable';
import { HoldingsTable } from './components/HoldingsTable';
import { OrderBookTable } from './components/OrderBookTable';
import { LockScreen } from './pages/LockScreen';
import { Layers, FileText, History, Database } from 'lucide-react';

function AppContent() {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [signals, setSignals] = useState<StrategySignal[]>([]);
  const [funds, setFunds] = useState<FundLimits | null>(null);
  const [holdings, setHoldings] = useState<HoldingItem[]>([]);
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [superOrders, setSuperOrders] = useState<SuperOrderItem[]>([]);
  const [regularOrders, setRegularOrders] = useState<RegularOrderItem[]>([]);
  const [trades, setTrades] = useState<TradeItem[]>([]);
  const [dbTrades, setDbTrades] = useState<DbTradeItem[]>([]);
  const [dbOrders, setDbOrders] = useState<DbOrderItem[]>([]);
  const [dbModifications, setDbModifications] = useState<DbModificationItem[]>([]);

  // Dark / Light Mode state
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('ats_theme') as 'dark' | 'light') || 'dark';
  });

  const handleToggleTheme = () => {
    const nextTheme = theme === 'dark' ? 'light' : 'dark';
    setTheme(nextTheme);
    localStorage.setItem('ats_theme', nextTheme);
  };

  const isLight = theme === 'light';

  // Trades page sub-tab state
  const [tradesSubTab, setTradesSubTab] = useState<'OPEN_POSITIONS' | 'ORDERBOOK' | 'TRADE_HISTORY'>('OPEN_POSITIONS');
  
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const isFetchingRef = useRef(false);
  const { addToast } = useToast();

  const syncAccountData = useCallback(async () => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;
    setIsSyncing(true);
    setSyncError(null);
    try {
      const [authRes, engineRes, signalsRes, summaryRes] = await Promise.all([
        api.getAuthStatus(),
        api.getEngineStatus(),
        api.getSignals(),
        api.getPortfolioSummary(),
      ]);

      setAuthStatus(authRes);
      setEngineStatus(engineRes);
      setSignals(signalsRes || []);
      setFunds(summaryRes.funds);
      setHoldings(summaryRes.holdings || []);
      setPositions(summaryRes.positions || []);
      setSuperOrders(summaryRes.super_orders || []);
      setRegularOrders(summaryRes.orders || []);
      setTrades(summaryRes.trades || []);
      setDbTrades(summaryRes.db_trades || []);
      setDbOrders(summaryRes.db_orders || []);
      setDbModifications(summaryRes.db_modifications || []);
      
      addToast('Account portfolio synced cleanly from Dhan HQ API & DB', 'success', 'Portfolio Synced', 2500);
    } catch (err: any) {
      console.error('Account sync error:', err);
      const msg = err.message || 'Failed to sync Dhan account portfolio';
      setSyncError(msg);
      addToast(msg, 'error', 'Sync Failed');
    } finally {
      setIsSyncing(false);
      isFetchingRef.current = false;
    }
  }, [addToast]);

  useEffect(() => {
    // Initial fetch on page mount / browser refresh
    syncAccountData();

    // Check for scheduled 09:15 AM IST and 03:30 PM IST sync times (checking once per minute)
    const minuteInterval = setInterval(() => {
      const now = new Date();
      // IST conversion offset: UTC+5:30
      const utc = now.getTime() + now.getTimezoneOffset() * 60000;
      const istTime = new Date(utc + 3600000 * 5.5);
      const hours = istTime.getHours();
      const minutes = istTime.getMinutes();
      const day = istTime.getDay(); // 0 is Sunday, 6 is Saturday

      if (day !== 0 && day !== 6) {
        if ((hours === 9 && minutes === 15) || (hours === 15 && minutes === 30)) {
          console.log('[SCHEDULED SYNC] Triggering 09:15 AM / 03:30 PM IST sync...');
          syncAccountData();
        }
      }
    }, 60000);

    return () => clearInterval(minuteInterval);
  }, [syncAccountData]);

  return (
    <div className={`min-h-screen flex flex-col font-['Outfit'] transition-colors duration-300 ${
      isLight ? 'bg-slate-100 text-slate-900' : 'bg-black text-white'
    }`}>
      {/* Top Header & Page Navigation */}
      <Header
        authStatus={authStatus}
        onRefresh={syncAccountData}
        isSyncing={isSyncing}
        signalsCount={signals.length}
        openTradesCount={positions.length}
        theme={theme}
        onToggleTheme={handleToggleTheme}
      />

      {/* Main Content Area */}
      <main className="flex-1 w-full p-4 sm:p-6 space-y-6">
        {/* Error Alert Banner */}
        {syncError && (
          <div className={`rounded-xl p-4 flex items-center justify-between text-xs font-mono border transition-all ${
            isLight
              ? 'bg-rose-50 border-rose-200 text-rose-800'
              : 'bg-red-500/10 border-red-500/30 text-red-400'
          }`}>
            <span><strong>Sync Error:</strong> {syncError}</span>
            <button
              onClick={syncAccountData}
              className="px-3 py-1 bg-red-500/20 hover:bg-red-500/30 text-red-300 rounded font-sans font-bold transition-all"
            >
              Retry Sync
            </button>
          </div>
        )}

        <Routes>
          {/* PAGE 1: DASHBOARD */}
          <Route path="/" element={
            <div className="space-y-6">
              {/* Metric Cards Banner */}
              <MetricCards funds={funds} positions={positions} holdings={holdings} isLight={isLight} />

              {/* Holdings & Orderbook Overview */}
              <div className="space-y-6">
                <div>
                  <h2 className={`text-xs font-bold mb-3 tracking-wider uppercase font-['Outfit'] ${isLight ? 'text-slate-700' : 'text-zinc-400'}`}>Delivered & T1 Holdings</h2>
                  <HoldingsTable holdings={holdings} isLight={isLight} />
                </div>

                <div>
                  <h2 className={`text-xs font-bold mb-3 tracking-wider uppercase font-['Outfit'] ${isLight ? 'text-slate-700' : 'text-zinc-400'}`}>Live Orderbook Overview</h2>
                  <OrderBookTable
                    superOrders={superOrders}
                    regularOrders={regularOrders}
                    dbOrders={dbOrders}
                    onOrderCancelled={syncAccountData}
                    isLight={isLight}
                  />
                </div>
              </div>
            </div>
          } />

          {/* PAGE 2: SIGNALS */}
          <Route path="/signals" element={
            <div className="space-y-4">
              <div className={`flex items-center justify-between border-b pb-3 ${isLight ? 'border-slate-200' : 'border-zinc-800'}`}>
                <div>
                  <h2 className={`text-lg font-bold ${isLight ? 'text-slate-900' : 'text-white'}`}>Automated Strategy Signals Stream</h2>
                  <p className={`text-xs ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Live multi-timeframe strategy scanner and breakout signal queue</p>
                </div>
                <span className={`px-3 py-1 text-xs font-mono font-bold rounded-full border ${
                  isLight ? 'bg-slate-200 text-slate-800 border-slate-300' : 'bg-zinc-900 text-zinc-300 border-zinc-800'
                }`}>
                  Total Signals: {signals.length}
                </span>
              </div>
              <SignalsTable signals={signals} isLight={isLight} />
            </div>
          } />

          {/* PAGE 3: TRADES (OPEN TRADES & HISTORY) */}
          <Route path="/trades" element={
            <div className="space-y-6">
              {/* Trades Sub-Navigation Bar - Sleek Borderless Pill Tabs */}
              <div className="flex items-center gap-2 overflow-x-auto">
                <button
                  onClick={() => setTradesSubTab('OPEN_POSITIONS')}
                  className={`flex items-center gap-2 px-5 py-2 text-xs font-bold rounded-full transition-all duration-200 ${
                    tradesSubTab === 'OPEN_POSITIONS'
                      ? isLight
                        ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                        : 'bg-zinc-800 text-white shadow-md'
                      : isLight
                      ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                      : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
                  }`}
                >
                  <Layers className="h-4 w-4" />
                  <span>Open Trades / Live Positions ({positions.length})</span>
                </button>

                <button
                  onClick={() => setTradesSubTab('ORDERBOOK')}
                  className={`flex items-center gap-2 px-5 py-2 text-xs font-bold rounded-full transition-all duration-200 ${
                    tradesSubTab === 'ORDERBOOK'
                      ? isLight
                        ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                        : 'bg-zinc-800 text-white shadow-md'
                      : isLight
                      ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                      : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
                  }`}
                >
                  <FileText className="h-4 w-4" />
                  <span>Orderbook ({regularOrders.length || dbOrders.length})</span>
                </button>

                <button
                  onClick={() => setTradesSubTab('TRADE_HISTORY')}
                  className={`flex items-center gap-2 px-5 py-2 text-xs font-bold rounded-full transition-all duration-200 ${
                    tradesSubTab === 'TRADE_HISTORY'
                      ? isLight
                        ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                        : 'bg-zinc-800 text-white shadow-md'
                      : isLight
                      ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                    : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
                }`}
              >
                <History className="h-4 w-4" />
                <span>Trade History ({trades.length})</span>
              </button>
            </div>

            {/* Sub-Tab Content */}
            {tradesSubTab === 'OPEN_POSITIONS' && <PositionsTable positions={positions} isLight={isLight} />}
            {tradesSubTab === 'ORDERBOOK' && (
              <OrderBookTable
                superOrders={superOrders}
                regularOrders={regularOrders}
                dbOrders={dbOrders}
                onOrderCancelled={syncAccountData}
                isLight={isLight}
              />
            )}
            {tradesSubTab === 'TRADE_HISTORY' && (
              <div className="space-y-6">
                {/* Dhan Intraday Executed Trades Table */}
                <div className={`border rounded-2xl p-5 overflow-x-auto shadow-sm transition-colors ${
                  isLight ? 'bg-white border-slate-200 text-slate-900' : 'bg-black border-white/20 text-white'
                }`}>
                  <div className={`flex items-center justify-between border-b pb-3 mb-4 ${isLight ? 'border-slate-200' : 'border-zinc-800'}`}>
                    <h3 className={`text-sm font-bold ${isLight ? 'text-slate-900' : 'text-white'}`}>Dhan Live Intraday Executed Trades</h3>
                    <span className={`text-xs font-mono ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Dhan `/v2/trades`</span>
                  </div>
                  <table className="w-full text-xs font-mono">
                    <thead>
                      <tr className={`border-b text-left font-sans ${
                        isLight ? 'border-slate-200 bg-slate-100 text-slate-600' : 'border-zinc-800 bg-zinc-950 text-zinc-400'
                      }`}>
                        <th className="px-3 py-2.5">Trade ID</th>
                        <th className="px-3 py-2.5">Order ID</th>
                        <th className="px-3 py-2.5">Symbol</th>
                        <th className="px-3 py-2.5">Action</th>
                        <th className="px-3 py-2.5 text-right">Traded Qty</th>
                        <th className="px-3 py-2.5 text-right">Price</th>
                      </tr>
                    </thead>
                    <tbody className={`divide-y ${isLight ? 'divide-slate-200' : 'divide-zinc-800/50'}`}>
                      {trades.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="py-8 text-center text-zinc-500 text-xs">
                            No executed trades reported by Dhan API for today
                          </td>
                        </tr>
                      ) : (
                        trades.map((t, idx) => {
                          const tradeIdDisplay = t.exchangeTradeId || t.tradeId || t.orderId || `TRADE-${idx}`;
                          const rowKey = t.exchangeTradeId || t.tradeId || `${t.orderId}-${idx}`;
                          return (
                            <tr key={rowKey} className={`transition-colors duration-150 border-b cursor-pointer ${
                              isLight ? 'hover:bg-slate-200/90 border-slate-200' : 'hover:bg-zinc-800/80 border-zinc-800/40'
                            }`}>
                              <td className={`px-3 py-2.5 font-semibold font-mono ${isLight ? 'text-slate-800' : 'text-zinc-200'}`}>{tradeIdDisplay}</td>
                              <td className={`px-3 py-2.5 font-mono ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>{t.orderId}</td>
                              <td className={`px-3 py-2.5 font-bold ${isLight ? 'text-slate-900' : 'text-white'}`}>{t.tradingSymbol}</td>
                              <td className="px-3 py-2.5">
                                <span
                                  className={`px-1.5 py-0.5 text-[10px] rounded font-bold ${
                                    t.transactionType === 'BUY'
                                      ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/30'
                                      : 'bg-rose-500/10 text-rose-500 border border-rose-500/30'
                                  }`}
                                >
                                  {t.transactionType}
                                </span>
                              </td>
                              <td className={`px-3 py-2.5 text-right font-bold ${isLight ? 'text-slate-900' : 'text-white'}`}>{t.tradedQuantity}</td>
                              <td className={`px-3 py-2.5 text-right font-semibold ${isLight ? 'text-slate-900' : 'text-white'}`}>₹{t.tradedPrice}</td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
          } />
        </Routes>
      </main>
    </div>
  );
}

export function App() {
  return (
    <CompanyImageProvider>
      <ToastProvider>
        <Routes>
          <Route path="/lock" element={<LockScreen />} />
          <Route path="/*" element={<AppContent />} />
        </Routes>
      </ToastProvider>
    </CompanyImageProvider>
  );
}

export default App;

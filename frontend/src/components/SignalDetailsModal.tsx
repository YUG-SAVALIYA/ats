import React from 'react';
import { StrategySignal } from '../services/api';
import { X, CheckCircle2, Clock, XCircle, TrendingUp, ShieldAlert, AlertTriangle, Coins } from 'lucide-react';

interface SignalDetailsModalProps {
  signal: StrategySignal;
  isOpen: boolean;
  onClose: () => void;
  isLight?: boolean;
}

function fmt(n?: number) {
  if (n === undefined || isNaN(n)) return 'N/A';
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export const SignalDetailsModal: React.FC<SignalDetailsModalProps> = ({ signal, isOpen, onClose, isLight = false }) => {
  if (!isOpen || !signal) return null;

  const isQueued = ['ENTRY_READY', 'PENDING', 'QUEUED'].includes((signal.status || '').toUpperCase());
  const isExecuted = ['ENTERED', 'EXECUTED', 'FILLED'].includes((signal.status || '').toUpperCase());
  const isRejected = ['REJECTED', 'CANCELLED', 'FAILED'].includes((signal.status || '').toUpperCase());

  const entryHigh = signal.signal_high || signal.ref_price || 0;
  const targetPct = signal.target_pct ?? signal.new_target_pct ?? 20;
  const slPct = signal.sl_pct ?? signal.new_sl_pct ?? 3;
  const targetPrice = signal.target_price ?? (entryHigh ? entryHigh * (1 + targetPct / 100) : 0);
  const stopLoss = signal.stop_loss ?? (entryHigh ? entryHigh * (1 - slPct / 100) : 0);
  
  const evalData = signal.evaluation || {};
  const hasEval = Object.keys(evalData).length > 0;
  const isBrokerFailed = signal.rejection_reason?.startsWith('ORDER_FAILED') || 
                         signal.rejection_reason?.startsWith('EXECUTION_FAILED') ||
                         evalData?.broker_order_status === 'FAILED';

  // Calculate executed stop loss / target if available
  const executedTarget = signal.executed_price && signal.new_target_pct 
    ? signal.executed_price * (1 + signal.new_target_pct / 100) 
    : targetPrice;
    
  const executedSL = signal.executed_price && signal.new_sl_pct 
    ? signal.executed_price * (1 - signal.new_sl_pct / 100) 
    : stopLoss;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div 
        className={`relative w-full max-w-xl max-h-[90vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden font-sans border ${
          isLight ? 'bg-white border-slate-200 text-slate-800' : 'bg-zinc-950 border-zinc-800 text-zinc-200'
        }`}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className={`p-5 flex items-center justify-between border-b shrink-0 ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-black border-zinc-800'}`}>
          <div>
            <h2 className={`text-xl font-bold flex items-center gap-2 ${isLight ? 'text-slate-900' : 'text-white'}`}>
              {signal.trading_symbol || signal.symbol}
              <span className={`px-2.5 py-0.5 text-xs font-bold rounded-full flex items-center gap-1 ${
                isQueued ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20' : 
                isExecuted ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 
                isRejected ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20' : 
                'bg-zinc-500/10 text-zinc-400 border border-zinc-500/20'
              }`}>
                {isQueued && <Clock className="w-3.5 h-3.5" />}
                {isExecuted && <CheckCircle2 className="w-3.5 h-3.5" />}
                {isRejected && <XCircle className="w-3.5 h-3.5" />}
                {signal.status}
              </span>
            </h2>
            <p className={`text-xs mt-1 ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>
              Signal Date: <span className="font-mono font-semibold">{signal.signal_date || 'N/A'}</span>
            </p>
          </div>
          <button 
            onClick={onClose} 
            className={`p-2 rounded-full transition-colors ${
              isLight ? 'hover:bg-slate-200 text-slate-500' : 'hover:bg-zinc-800 text-zinc-400'
            }`}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Scrollable Content Body */}
        <div className="p-5 space-y-5 overflow-y-auto">
          {/* Broker Order Placement Alert (if order failed) */}
          {isBrokerFailed && (
            <div className="border border-amber-500/30 bg-amber-500/10 rounded-xl p-4">
              <h3 className="text-xs font-bold mb-1.5 uppercase tracking-wider text-amber-400 flex items-center gap-1.5">
                <AlertTriangle className="w-4 h-4" /> Broker Order Dispatch Notice
              </h3>
              <p className="text-xs font-mono font-semibold text-amber-300">
                {signal.rejection_reason || evalData?.broker_error || 'Broker order was rejected.'}
              </p>
            </div>
          )}

          {/* Strategy Indicator Conditions */}
          <div>
            <h3 className={`text-xs font-bold mb-3 uppercase tracking-wider ${isLight ? 'text-slate-700' : 'text-zinc-400'}`}>Strategy Indicators</h3>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 font-mono text-xs">
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Daily RSI (50-90)</p>
                <p className="font-bold text-sm">{signal.daily_rsi ? signal.daily_rsi.toFixed(1) : 'N/A'}</p>
              </div>
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Weekly RSI (65-85)</p>
                <p className="font-bold text-sm">{signal.weekly_rsi ? signal.weekly_rsi.toFixed(1) : 'N/A'}</p>
              </div>
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Candle Range</p>
                <p className="font-bold text-sm">{signal.candle_range ? `${signal.candle_range.toFixed(2)}%` : 'N/A'}</p>
              </div>
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Supertrend Flip</p>
                <p className={`font-bold text-sm ${signal.supertrend_flip ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {signal.supertrend_flip ? 'YES (GREEN)' : 'NO'}
                </p>
              </div>
            </div>
          </div>

          {/* Live 3:25 PM Evaluation Metrics (if evaluated) */}
          {hasEval && (
            <div>
              <h3 className={`text-xs font-bold mb-3 uppercase tracking-wider flex items-center justify-between ${isLight ? 'text-slate-700' : 'text-zinc-400'}`}>
                <span>3:25 PM Entry Evaluation</span>
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                  evalData.passed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'
                }`}>
                  {evalData.passed ? 'CRITERIA PASSED' : 'CRITERIA FAILED'}
                </span>
              </h3>
              <div className={`p-4 rounded-xl border space-y-3 ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/40 border-zinc-800'}`}>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-xs">
                  <div>
                    <span className="text-[10px] text-zinc-500 block">Today Open</span>
                    <span className="font-bold">{fmt(evalData.today_open)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-zinc-500 block">Today High</span>
                    <span className="font-bold">{fmt(evalData.today_high)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-zinc-500 block">Today Low</span>
                    <span className="font-bold">{fmt(evalData.today_low)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-zinc-500 block">3:25 PM LTP</span>
                    <span className="font-bold text-emerald-400">{fmt(evalData.today_ltp)}</span>
                  </div>
                </div>

                <div className="pt-2 border-t border-zinc-800/60 grid grid-cols-2 gap-2 text-xs font-mono">
                  <div>
                    <span className="text-[10px] text-zinc-500 block">Ref Price (+3% Req High)</span>
                    <span className="font-bold">{fmt(evalData.req_high || evalData.ref_price)}</span>
                  </div>
                  <div>
                    <span className="text-[10px] text-zinc-500 block">Breakout & LTP State</span>
                    <span className={`font-bold ${evalData.passed ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {evalData.passed ? 'Breakout Met ✅' : 'Breakout Failed ❌'}
                    </span>
                  </div>
                </div>

                {evalData.total_purchasing_power && (
                  <div className="pt-2 border-t border-zinc-800/60 grid grid-cols-3 gap-2 text-xs font-mono">
                    <div>
                      <span className="text-[10px] text-zinc-500 block">Margin Budget</span>
                      <span className="font-bold">{fmt(evalData.allocated_margin)}</span>
                    </div>
                    <div>
                      <span className="text-[10px] text-zinc-500 block">MTF Leverage</span>
                      <span className="font-bold text-blue-400">{evalData.leverage}x</span>
                    </div>
                    <div>
                      <span className="text-[10px] text-zinc-500 block">Purchasing Power</span>
                      <span className="font-bold text-emerald-400">{fmt(evalData.total_purchasing_power)}</span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Pre-Defined Trade Plan */}
          <div>
            <h3 className={`text-xs font-bold mb-3 uppercase tracking-wider ${isLight ? 'text-slate-700' : 'text-zinc-400'}`}>Pre-Defined Trade Plan</h3>
            <div className={`flex justify-between items-center p-4 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
              <div>
                <p className={`text-xs mb-1 font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Signal High (Entry)</p>
                <p className={`font-mono font-bold text-base ${isLight ? 'text-slate-900' : 'text-white'}`}>{fmt(entryHigh)}</p>
              </div>
              <div className="text-center">
                <p className={`text-xs mb-1 font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Target (+{targetPct}%)</p>
                <p className="font-mono font-bold text-base text-emerald-400">{fmt(targetPrice)}</p>
              </div>
              <div className="text-right">
                <p className={`text-xs mb-1 font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Stop Loss (-{slPct}%)</p>
                <p className="font-mono font-bold text-base text-rose-400">{fmt(stopLoss)}</p>
              </div>
            </div>
          </div>

          {/* Rejection Details (if rejected without broker order) */}
          {isRejected && !isBrokerFailed && (
            <div className="border border-rose-500/30 bg-rose-500/10 rounded-xl p-4">
              <h3 className="text-xs font-bold mb-2 uppercase tracking-wider text-rose-400 flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4" /> Rejection Reason
              </h3>
              <p className="text-xs font-mono font-semibold text-rose-300">
                {signal.rejection_reason || 'Signal did not meet 3:25 PM entry criteria.'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};


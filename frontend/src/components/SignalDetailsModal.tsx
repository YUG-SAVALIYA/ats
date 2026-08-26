import React from 'react';
import { StrategySignal } from '../services/api';
import { X, CheckCircle2, Clock, XCircle, TrendingUp, ShieldAlert } from 'lucide-react';

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
  if (!isOpen) return null;

  const isQueued = ['ENTRY_READY', 'PENDING', 'QUEUED'].includes((signal.status || '').toUpperCase());
  const isExecuted = ['ENTERED', 'EXECUTED', 'FILLED'].includes((signal.status || '').toUpperCase());
  const isRejected = ['REJECTED', 'CANCELLED', 'FAILED'].includes((signal.status || '').toUpperCase());

  const entryHigh = signal.signal_high || signal.ref_price || 0;
  const targetPrice = signal.target_price ?? (entryHigh ? entryHigh * 1.17 : 0);
  const stopLoss = signal.stop_loss ?? (entryHigh ? entryHigh * 0.95 : 0);
  
  // Calculate executed stop loss / target if available
  const executedTarget = signal.executed_price && signal.new_target_pct 
    ? signal.executed_price * (1 + signal.new_target_pct / 100) 
    : null;
    
  const executedSL = signal.executed_price && signal.new_sl_pct 
    ? signal.executed_price * (1 - signal.new_sl_pct / 100) 
    : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div 
        className={`relative w-full max-w-lg rounded-2xl shadow-xl overflow-hidden font-sans border ${
          isLight ? 'bg-white border-slate-200 text-slate-800' : 'bg-zinc-950 border-zinc-800 text-zinc-200'
        }`}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className={`p-5 flex items-center justify-between border-b ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-black border-zinc-800'}`}>
          <div>
            <h2 className={`text-xl font-bold flex items-center gap-2 ${isLight ? 'text-slate-900' : 'text-white'}`}>
              {signal.trading_symbol || signal.symbol}
              <span className={`px-2 py-0.5 text-xs font-bold rounded flex items-center gap-1 ${
                isQueued ? 'bg-blue-500/10 text-blue-500 border border-blue-500/20' : 
                isExecuted ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20' : 
                isRejected ? 'bg-rose-500/10 text-rose-500 border border-rose-500/20' : 
                'bg-zinc-500/10 text-zinc-500 border border-zinc-500/20'
              }`}>
                {isQueued && <Clock className="w-3 h-3" />}
                {isExecuted && <CheckCircle2 className="w-3 h-3" />}
                {isRejected && <XCircle className="w-3 h-3" />}
                {signal.status}
              </span>
            </h2>
            <p className={`text-xs mt-1 ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>
              Signal Date: <span className="font-mono">{signal.signal_date || 'N/A'}</span>
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

        <div className="p-5 space-y-6">
          {/* Strategy Conditions Breakdown */}
          <div>
            <h3 className={`text-xs font-bold mb-3 uppercase tracking-wider ${isLight ? 'text-slate-700' : 'text-zinc-400'}`}>Strategy Conditions</h3>
            <div className="grid grid-cols-2 gap-3 font-mono text-sm">
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Score</p>
                <p className={`font-bold ${signal.score && signal.score >= 80 ? 'text-emerald-500' : ''}`}>{signal.score ?? 'N/A'}</p>
              </div>
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Daily RSI</p>
                <p className="font-bold">{signal.daily_rsi ? signal.daily_rsi.toFixed(2) : 'N/A'}</p>
              </div>
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Weekly RSI</p>
                <p className="font-bold">{signal.weekly_rsi ? signal.weekly_rsi.toFixed(2) : 'N/A'}</p>
              </div>
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Candle Range</p>
                <p className="font-bold">{signal.candle_range ? `${signal.candle_range.toFixed(2)}%` : 'N/A'}</p>
              </div>
              <div className={`p-3 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
                <p className={`text-[10px] mb-1 font-sans font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Supertrend Flip</p>
                <p className={`font-bold ${signal.supertrend_flip ? 'text-emerald-500' : 'text-rose-500'}`}>
                  {signal.supertrend_flip ? 'YES (GREEN)' : 'NO'}
                </p>
              </div>
            </div>
          </div>

          {/* Pre-Defined Trade Plan */}
          <div>
            <h3 className={`text-xs font-bold mb-3 uppercase tracking-wider ${isLight ? 'text-slate-700' : 'text-zinc-400'}`}>Pre-Defined Trade Plan</h3>
            <div className={`flex justify-between items-center p-4 rounded-xl border ${isLight ? 'bg-slate-50 border-slate-200' : 'bg-zinc-900/50 border-zinc-800'}`}>
              <div>
                <p className={`text-xs mb-1 font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Trigger Entry</p>
                <p className={`font-mono font-bold text-lg ${isLight ? 'text-slate-900' : 'text-white'}`}>{fmt(entryHigh)}</p>
              </div>
              <div className="text-center">
                <p className={`text-xs mb-1 font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Target Price</p>
                <p className="font-mono font-bold text-lg text-emerald-500">{fmt(targetPrice)}</p>
              </div>
              <div className="text-right">
                <p className={`text-xs mb-1 font-semibold ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>Stop Loss</p>
                <p className="font-mono font-bold text-lg text-rose-500">{fmt(stopLoss)}</p>
              </div>
            </div>
          </div>

          {/* Execution Details (Conditional) */}
          {isExecuted && (
            <div className="mt-4 border border-emerald-500/30 bg-emerald-500/10 rounded-xl p-4">
              <h3 className="text-xs font-bold mb-3 uppercase tracking-wider text-emerald-400 flex items-center gap-1.5">
                <TrendingUp className="w-4 h-4" /> Live Execution Status
              </h3>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <p className="text-[10px] font-semibold text-emerald-500/70 uppercase">Executed At</p>
                  <p className="font-mono font-bold text-emerald-400 text-base">{fmt(signal.executed_price)}</p>
                </div>
                <div>
                  <p className="text-[10px] font-semibold text-emerald-500/70 uppercase">Active Target</p>
                  <p className="font-mono font-bold text-emerald-400 text-base">
                    {executedTarget ? fmt(executedTarget) : fmt(targetPrice)}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] font-semibold text-emerald-500/70 uppercase">Trailing SL</p>
                  <p className="font-mono font-bold text-emerald-400 text-base">
                    {executedSL ? fmt(executedSL) : fmt(stopLoss)}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Rejection Details (Conditional) */}
          {isRejected && (
            <div className="mt-4 border border-rose-500/30 bg-rose-500/10 rounded-xl p-4">
              <h3 className="text-xs font-bold mb-2 uppercase tracking-wider text-rose-400 flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4" /> Rejection Reason
              </h3>
              <p className="text-sm font-semibold text-rose-300">
                {signal.rejection_reason || 'Signal was rejected or cancelled before execution.'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

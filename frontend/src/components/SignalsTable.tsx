import React, { useState } from 'react';
import { StrategySignal } from '../services/api';
import { Activity, CheckCircle2, Clock, XCircle } from 'lucide-react';
import { useCompanyImages } from '../context/CompanyImageContext';
import { SignalDetailsModal } from './SignalDetailsModal';

interface SignalsTableProps {
  signals: StrategySignal[];
  isLight?: boolean;
}

function fmt(n?: number) {
  if (n === undefined || isNaN(n)) return '₹0.00';
  return `₹${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export const SignalsTable: React.FC<SignalsTableProps> = ({ signals = [], isLight = false }) => {
  const [activeTab, setActiveTab] = useState<'ALL' | 'QUEUED' | 'EXECUTED' | 'REJECTED'>('ALL');
  const [selectedSignal, setSelectedSignal] = useState<StrategySignal | null>(null);
  const images = useCompanyImages();

  const isQueued = (status?: string) => ['ENTRY_READY', 'PENDING', 'QUEUED'].includes((status || '').toUpperCase());
  const isExecuted = (status?: string) => ['ENTERED', 'EXECUTED', 'FILLED'].includes((status || '').toUpperCase());
  const isRejected = (status?: string) => ['REJECTED', 'CANCELLED', 'FAILED'].includes((status || '').toUpperCase());

  const queuedCount = (signals || []).filter((s) => isQueued(s?.status)).length;
  const executedCount = (signals || []).filter((s) => isExecuted(s?.status)).length;
  const rejectedCount = (signals || []).filter((s) => isRejected(s?.status)).length;

  const filteredSignals = signals.filter((sig) => {
    const status = sig.status || '';
    if (activeTab === 'QUEUED') return isQueued(status);
    if (activeTab === 'EXECUTED') return isExecuted(status);
    if (activeTab === 'REJECTED') return isRejected(status);
    return true;
  });

  return (
    <>
      <div className={`rounded-2xl overflow-hidden border transition-colors ${
        isLight ? 'bg-white border-slate-200 text-slate-900 shadow-sm' : 'bg-black border-white/20 text-white shadow-sm'
      }`}>
        {/* Header & Sub-Tabs */}
        <div className={`p-3.5 border-b flex flex-wrap items-center justify-between gap-3 ${
          isLight ? 'bg-slate-50 border-slate-200' : 'bg-black border-white/10'
        }`}>
          <span className={`text-xs font-bold flex items-center gap-1.5 font-['Outfit'] ${isLight ? 'text-slate-900' : 'text-white'}`}>
            <Activity className={`h-4 w-4 ${isLight ? 'text-slate-500' : 'text-zinc-400'}`} /> Strategy Execution Signals
          </span>

          {/* Filter Tabs - Borderless Pill Tabs */}
          <div className="flex items-center gap-2 font-['Outfit']">
            <button
              onClick={() => setActiveTab('ALL')}
              className={`px-4 py-1.5 text-xs font-bold rounded-full transition-all duration-200 ${
                activeTab === 'ALL'
                  ? isLight
                    ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                    : 'bg-zinc-800 text-white shadow-md'
                  : isLight
                  ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
              }`}
            >
              All Signals ({signals.length})
            </button>

            <button
              onClick={() => setActiveTab('QUEUED')}
              className={`px-4 py-1.5 text-xs font-bold rounded-full transition-all duration-200 ${
                activeTab === 'QUEUED'
                  ? isLight
                    ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                    : 'bg-zinc-800 text-white shadow-md'
                  : isLight
                  ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
              }`}
            >
              Queued / Ready ({queuedCount})
            </button>

            <button
              onClick={() => setActiveTab('EXECUTED')}
              className={`px-4 py-1.5 text-xs font-bold rounded-full transition-all duration-200 ${
                activeTab === 'EXECUTED'
                  ? isLight
                    ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                    : 'bg-zinc-800 text-white shadow-md'
                  : isLight
                  ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
              }`}
            >
              Executed ({executedCount})
            </button>

            <button
              onClick={() => setActiveTab('REJECTED')}
              className={`px-4 py-1.5 text-xs font-bold rounded-full transition-all duration-200 ${
                activeTab === 'REJECTED'
                  ? isLight
                    ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                    : 'bg-zinc-800 text-white shadow-md'
                  : isLight
                  ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
              }`}
            >
              Rejected ({rejectedCount})
            </button>
          </div>
        </div>

        {filteredSignals.length === 0 ? (
          <div className="p-10 text-center text-zinc-400 text-xs">
            No {activeTab !== 'ALL' ? activeTab.toLowerCase() : ''} strategy signals found
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className={`border-b text-left font-sans ${
                  isLight ? 'border-slate-200 bg-slate-100 text-slate-600' : 'border-zinc-800 bg-zinc-950 text-zinc-400'
                }`}>
                  <th className="px-4 py-3">Date</th>
                  <th className="px-4 py-3">Signal ID</th>
                  <th className="px-3 py-3">Symbol</th>
                  <th className="px-3 py-3">Strategy</th>
                  <th className="px-3 py-3 text-right">Entry (High)</th>
                  <th className="px-3 py-3 text-right">Target Price</th>
                  <th className="px-3 py-3 text-right">Stop Loss</th>
                  <th className="px-3 py-3 text-right">Qty</th>
                  <th className="px-4 py-3 text-center">Status</th>
                </tr>
              </thead>
              <tbody className={`divide-y ${isLight ? 'divide-slate-200' : 'divide-zinc-800/60'}`}>
                {filteredSignals.map((sig) => {
                  const statusStr = sig.status || '';
                  const ready = isQueued(statusStr);
                  const entered = isExecuted(statusStr);
                  const rejected = isRejected(statusStr);

                  const symbol = sig.trading_symbol || sig.symbol || 'UNKNOWN';
                  const strategyName = sig.strategy_type || sig.strategy || 'SUPERTREND';
                  const entryHigh = sig.signal_high || sig.ref_price || 0;
                  const targetPct = sig.target_pct ?? sig.new_target_pct ?? 20;
                  const slPct = sig.sl_pct ?? sig.new_sl_pct ?? 3;
                  const targetPrice = sig.target_price ?? (entryHigh ? entryHigh * (1 + targetPct / 100) : 0);
                  const stopLoss = sig.stop_loss ?? (entryHigh ? entryHigh * (1 - slPct / 100) : 0);
                  const qty = sig.quantity ?? '-';

                  return (
                    <tr 
                      key={sig.id} 
                      onClick={() => setSelectedSignal(sig)}
                      className={`transition-colors duration-150 border-b cursor-pointer ${
                        isLight ? 'hover:bg-slate-200/90 border-slate-200' : 'hover:bg-zinc-800/80 border-zinc-800/40'
                      }`}
                    >
                      <td className={`px-4 py-3 font-semibold ${isLight ? 'text-slate-600' : 'text-zinc-400'}`}>
                        {sig.signal_date || 'N/A'}
                      </td>
                      <td className={`px-4 py-3 font-semibold ${isLight ? 'text-slate-600' : 'text-zinc-400'}`}>
                        {sig.id.length > 12 ? `${sig.id.slice(0, 8)}...` : sig.id}
                      </td>
                      <td className={`px-3 py-3 font-sans font-bold flex items-center gap-1.5 ${isLight ? 'text-slate-900' : 'text-white'}`}>
                        {images[symbol] ? (
                          <img src={images[symbol]} alt={symbol} className="w-5 h-5 rounded-full object-cover" />
                        ) : (
                          <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold ${isLight ? 'bg-slate-200 text-slate-700' : 'bg-zinc-800 text-zinc-300'}`}>
                            {symbol.charAt(0)}
                          </div>
                        )}
                        {symbol}
                      </td>
                      <td className={`px-3 py-3 font-sans ${isLight ? 'text-slate-600' : 'text-zinc-400'}`}>{strategyName}</td>
                      <td className={`px-3 py-3 text-right font-bold ${isLight ? 'text-slate-900' : 'text-white'}`}>{fmt(entryHigh)}</td>
                      <td className="px-3 py-3 text-right text-emerald-500 font-bold">{fmt(targetPrice)}</td>
                      <td className="px-3 py-3 text-right text-rose-500 font-bold">{fmt(stopLoss)}</td>
                      <td className={`px-3 py-3 text-right font-bold ${isLight ? 'text-slate-900' : 'text-white'}`}>{qty}</td>
                      <td className="px-4 py-3 text-center font-sans">
                        <span
                          className={`px-2 py-0.5 text-[10px] font-bold rounded flex items-center justify-center gap-1 mx-auto max-w-[110px] ${
                            ready
                              ? 'bg-zinc-900 text-blue-400 border border-zinc-800'
                              : entered
                              ? 'bg-zinc-900 text-emerald-400 border border-zinc-800'
                              : rejected
                              ? 'bg-zinc-900 text-red-400 border border-zinc-800'
                              : 'bg-zinc-900 text-zinc-400 border border-zinc-800'
                          }`}
                        >
                          {ready && <Clock className="h-3 w-3" />}
                          {entered && <CheckCircle2 className="h-3 w-3" />}
                          {rejected && <XCircle className="h-3 w-3" />}
                          {sig.status}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedSignal && (
        <SignalDetailsModal 
          signal={selectedSignal} 
          isOpen={!!selectedSignal} 
          onClose={() => setSelectedSignal(null)}
          isLight={isLight}
        />
      )}
    </>
  );
};

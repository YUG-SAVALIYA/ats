import React from 'react';
import { PositionItem } from '../types';
import { Layers } from 'lucide-react';
import { useCompanyImages } from '../context/CompanyImageContext';

interface PositionsTableProps {
  positions: PositionItem[];
  isLight?: boolean;
}

function fmt(n?: number) {
  if (n === undefined || isNaN(n)) return '₹0.00';
  return `₹${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export const PositionsTable: React.FC<PositionsTableProps> = ({ positions, isLight = false }) => {
  const images = useCompanyImages();
  
  if (positions.length === 0) {
    return (
      <div className={`rounded-2xl p-12 text-center shadow-sm border ${
        isLight ? 'bg-white border-slate-200 text-slate-900' : 'bg-black border-white/20 text-white'
      }`}>
        <Layers className={`h-10 w-10 mx-auto mb-3 opacity-40 ${isLight ? 'text-slate-400' : 'text-zinc-600'}`} />
        <p className={`font-medium ${isLight ? 'text-slate-800' : 'text-zinc-300'}`}>No open positions reported by Dhan API</p>
        <p className={`text-xs mt-1 ${isLight ? 'text-slate-500' : 'text-zinc-500'}`}>Intraday, MTF carry-forward, or F&O day positions will appear here live upon order fill.</p>
      </div>
    );
  }

  return (
    <div className={`rounded-2xl overflow-hidden shadow-sm border transition-colors ${
      isLight ? 'bg-white border-slate-200 text-slate-900' : 'bg-black border-white/20 text-white'
    }`}>
      <div className={`p-3.5 border-b flex items-center justify-between ${
        isLight ? 'bg-slate-50 border-slate-200' : 'bg-black border-white/10'
      }`}>
        <span className={`text-xs font-bold flex items-center gap-1.5 font-['Outfit'] ${isLight ? 'text-slate-900' : 'text-white'}`}>
          <Layers className={`h-4 w-4 ${isLight ? 'text-slate-500' : 'text-zinc-400'}`} /> Live Day & Carryforward Positions (Directly from Dhan `/v2/positions`)
        </span>
        <span className={`px-2 py-0.5 text-[10px] rounded font-mono font-bold border ${
          isLight ? 'bg-slate-200 text-slate-800 border-slate-300' : 'bg-zinc-900 text-zinc-300 border-zinc-800'
        }`}>
          LIVE FEED
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className={`border-b text-left font-sans ${
              isLight ? 'border-slate-200 bg-slate-100 text-slate-600' : 'border-zinc-800 bg-zinc-950 text-zinc-400'
            }`}>
              <th className="px-4 py-3">Trading Symbol</th>
              <th className="px-3 py-3">Type</th>
              <th className="px-3 py-3">Product</th>
              <th className="px-3 py-3 text-right">Net Qty</th>
              <th className="px-3 py-3 text-right">Day (B/S)</th>
              <th className="px-3 py-3 text-right">Carry (B/S)</th>
              <th className="px-3 py-3 text-right">Buy Avg</th>
              <th className="px-3 py-3 text-right">Sell Avg</th>
              <th className="px-3 py-3 text-right">Cost Price</th>
              <th className="px-3 py-3 text-right">Realized PnL</th>
              <th className="px-4 py-3 text-right">Unrealized MTM</th>
            </tr>
          </thead>
          <tbody className={`divide-y ${isLight ? 'divide-slate-200' : 'divide-zinc-800/60'}`}>
            {positions.map((p, idx) => {
              const unrl = p.unrealizedProfit || 0;
              const rl = p.realizedProfit || 0;
              const isLong = (p.positionType || 'LONG') === 'LONG';

              return (
                <tr key={idx} className={`transition-colors duration-150 border-b cursor-pointer ${
                  isLight ? 'hover:bg-slate-200/90 border-slate-200' : 'hover:bg-zinc-800/80 border-zinc-800/40'
                }`}>
                  <td className={`px-4 py-3 font-semibold font-sans flex items-center gap-1.5 ${isLight ? 'text-slate-900' : 'text-white'}`}>
                    {images[p.tradingSymbol] ? (
                      <img src={images[p.tradingSymbol]} alt={p.tradingSymbol} className="w-5 h-5 rounded-full object-cover" />
                    ) : (
                      <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold ${isLight ? 'bg-slate-200 text-slate-700' : 'bg-zinc-800 text-zinc-300'}`}>
                        {(p.tradingSymbol || '').charAt(0)}
                      </div>
                    )}
                    {p.tradingSymbol}
                  </td>
                  <td className="px-3 py-3 font-sans">
                    <span
                      className={`px-1.5 py-0.5 text-[10px] rounded font-bold ${
                        isLong
                          ? 'bg-zinc-900 text-emerald-400 border border-zinc-800'
                          : 'bg-zinc-900 text-red-400 border border-zinc-800'
                      }`}
                    >
                      {p.positionType || 'LONG'}
                    </span>
                  </td>
                  <td className="px-3 py-3 font-sans">
                    <span className="px-1.5 py-0.5 text-[10px] bg-zinc-900 text-zinc-300 border border-zinc-800 rounded">
                      {p.productType || 'MTF'}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-right font-bold text-white">{p.netQty}</td>
                  <td className="px-3 py-3 text-right text-zinc-400">
                    {p.dayBuyQty || 0} / {p.daySellQty || 0}
                  </td>
                  <td className="px-3 py-3 text-right text-zinc-300">
                    {p.carryForwardBuyQty || 0} / {p.carryForwardSellQty || 0}
                  </td>
                  <td className="px-3 py-3 text-right text-zinc-300">{fmt(p.buyAvg)}</td>
                  <td className="px-3 py-3 text-right text-zinc-300">{fmt(p.sellAvg)}</td>
                  <td className="px-3 py-3 text-right font-medium text-zinc-300">{fmt(p.costPrice)}</td>
                  <td className={`px-3 py-3 text-right font-bold ${rl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {rl !== 0 ? `${rl >= 0 ? '+' : '-'}${fmt(rl)}` : '₹0.00'}
                  </td>
                  <td className={`px-4 py-3 text-right font-bold ${unrl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {unrl !== 0 ? `${unrl >= 0 ? '+' : '-'}${fmt(unrl)}` : '₹0.00'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

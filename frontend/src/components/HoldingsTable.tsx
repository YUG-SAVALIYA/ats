import React from 'react';
import { HoldingItem } from '../types';
import { Briefcase } from 'lucide-react';
import { useCompanyImages } from '../context/CompanyImageContext';

interface HoldingsTableProps {
  holdings: HoldingItem[];
  isLight?: boolean;
}

function fmt(n?: number) {
  if (n === undefined || isNaN(n)) return '₹0.00';
  return `₹${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(n?: number) {
  if (n === undefined || isNaN(n)) return '0.00%';
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

export const HoldingsTable: React.FC<HoldingsTableProps> = ({ holdings, isLight = false }) => {
  const images = useCompanyImages();
  
  if (holdings.length === 0) {
    return (
      <div className={`rounded-2xl p-12 text-center shadow-sm border ${
        isLight ? 'bg-white border-slate-200 text-slate-900' : 'bg-black border-white/20 text-white'
      }`}>
        <Briefcase className={`h-10 w-10 mx-auto mb-3 opacity-40 ${isLight ? 'text-slate-400' : 'text-zinc-600'}`} />
        <p className={`font-medium ${isLight ? 'text-slate-800' : 'text-zinc-300'}`}>No holdings returned by Dhan API</p>
        <p className={`text-xs mt-1 ${isLight ? 'text-slate-500' : 'text-zinc-500'}`}>Delivered equity, MTF carry-forward, or T1 holdings will appear here.</p>
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
          <Briefcase className={`h-4 w-4 ${isLight ? 'text-slate-500' : 'text-zinc-400'}`} /> Live T1 and Delivered Holdings (Directly from Dhan `/v2/holdings`)
        </span>
        <span className={`px-2 py-0.5 text-[10px] rounded font-mono font-bold border ${
          isLight ? 'bg-slate-200 text-slate-800 border-slate-300' : 'bg-zinc-900 text-zinc-300 border-zinc-800'
        }`}>
          BROKER SYNC
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className={`border-b text-left font-sans ${
              isLight ? 'border-slate-200 bg-slate-100 text-slate-600' : 'border-zinc-800 bg-zinc-950 text-zinc-400'
            }`}>
              <th className="px-4 py-3">Trading Symbol</th>
              <th className="px-3 py-3">Exchange</th>
              <th className="px-3 py-3 text-right">Total Qty</th>
              <th className="px-3 py-3 text-right">DP Qty</th>
              <th className="px-3 py-3 text-right">T1 Qty</th>
              <th className="px-3 py-3 text-right">MTF Qty</th>
              <th className="px-3 py-3 text-right">Avg Cost</th>
              <th className="px-3 py-3 text-right">LTP</th>
              <th className="px-3 py-3 text-right">Live MTM</th>
              <th className="px-4 py-3 text-right">MTM %</th>
            </tr>
          </thead>
          <tbody className={`divide-y ${isLight ? 'divide-slate-200' : 'divide-zinc-800/60'}`}>
            {holdings.map((h, idx) => {
              const ltp = h.lastTradedPrice || 0;
              const avg = h.avgCostPrice || 0;
              const mtm = (ltp - avg) * h.totalQty;
              const mtmPct = avg > 0 ? ((ltp - avg) / avg) * 100 : 0;
              const isMtf = h.mtf_qty > 0 || h.mtf_t1_qty > 0;

              return (
                <tr key={idx} className={`transition-colors duration-150 border-b cursor-pointer ${
                  isLight ? 'hover:bg-slate-200/90 border-slate-200' : 'hover:bg-zinc-800/80 border-zinc-800/40'
                }`}>
                  <td className={`px-4 py-3 font-semibold font-sans flex items-center gap-1.5 ${isLight ? 'text-slate-900' : 'text-white'}`}>
                    {images[h.tradingSymbol] ? (
                      <img src={images[h.tradingSymbol]} alt={h.tradingSymbol} className="w-5 h-5 rounded-full object-cover" />
                    ) : (
                      <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold ${isLight ? 'bg-slate-200 text-slate-700' : 'bg-zinc-800 text-zinc-300'}`}>
                        {(h.tradingSymbol || '').charAt(0)}
                      </div>
                    )}
                    {h.tradingSymbol}
                    {isMtf && (
                      <span className={`px-1.5 py-0.2 text-[9px] border rounded font-bold ${
                        isLight ? 'bg-slate-100 text-slate-600 border-slate-300' : 'bg-zinc-900 text-zinc-300 border-zinc-800'
                      }`}>
                        MTF
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-zinc-400 font-sans">{h.exchange || 'NSE'}</td>
                  <td className="px-3 py-3 text-right font-bold text-white">{h.totalQty}</td>
                  <td className="px-3 py-3 text-right text-zinc-300">{h.dpQty}</td>
                  <td className="px-3 py-3 text-right text-zinc-300">{h.t1Qty}</td>
                  <td className="px-3 py-3 text-right text-zinc-300">{h.mtf_qty}</td>
                  <td className="px-3 py-3 text-right text-zinc-300">{fmt(avg)}</td>
                  <td className="px-3 py-3 text-right font-semibold text-white">{fmt(ltp)}</td>
                  <td className={`px-3 py-3 text-right font-bold ${mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {mtm >= 0 ? '+' : '-'}{fmt(mtm)}
                  </td>
                  <td className={`px-4 py-3 text-right font-bold ${mtmPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {fmtPct(mtmPct)}
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

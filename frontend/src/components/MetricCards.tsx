import React from 'react';
import { FundLimits, PositionItem, HoldingItem } from '../types';
import { Wallet, TrendingUp, TrendingDown, Layers, Briefcase, Activity } from 'lucide-react';

interface MetricCardsProps {
  funds: FundLimits | null;
  positions: PositionItem[];
  holdings: HoldingItem[];
  isLight?: boolean;
}

function fmt(n?: number) {
  if (n === undefined || isNaN(n)) return '₹0.00';
  return `₹${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export const MetricCards: React.FC<MetricCardsProps> = ({ funds, positions, holdings, isLight = false }) => {
  const availCash = funds?.availabelBalance ?? funds?.withdrawableBalance ?? 0;
  const sodLimit = funds?.sodLimit ?? 0;
  const utilized = funds?.utilizedAmount ?? 0;

  // Calculate live unrealized MTM across positions & holdings
  const positionsUnrealized = positions.reduce((sum, p) => sum + (p.unrealizedProfit || 0), 0);
  const holdingsUnrealized = holdings.reduce((sum, h) => {
    if (h.lastTradedPrice && h.avgCostPrice) {
      return sum + (h.lastTradedPrice - h.avgCostPrice) * h.totalQty;
    }
    return sum;
  }, 0);

  const totalUnrealized = positionsUnrealized + holdingsUnrealized;
  const realizedProfit = positions.reduce((sum, p) => sum + (p.realizedProfit || 0), 0);

  const cards = [
    {
      title: 'Available Funds',
      value: fmt(availCash),
      subtext: `SOD Capital: ${fmt(sodLimit)}`,
      icon: Wallet,
      color: isLight ? 'text-slate-900' : 'text-white',
    },
    {
      title: 'Utilized Margin',
      value: fmt(utilized),
      subtext: `${sodLimit > 0 ? ((utilized / sodLimit) * 100).toFixed(1) : 0}% of SOD Capital`,
      icon: Activity,
      color: isLight ? 'text-slate-900' : 'text-white',
    },
    {
      title: 'Live Unrealized MTM',
      value: `${totalUnrealized >= 0 ? '+' : '-'}${fmt(totalUnrealized)}`,
      subtext: 'Positions + Delivery Holdings',
      icon: totalUnrealized >= 0 ? TrendingUp : TrendingDown,
      color: totalUnrealized >= 0 ? (isLight ? 'text-emerald-600' : 'text-emerald-400') : (isLight ? 'text-rose-600' : 'text-red-400'),
    },
    {
      title: 'Realized Day P&L',
      value: `${realizedProfit >= 0 ? '+' : '-'}${fmt(realizedProfit)}`,
      subtext: 'Booked intraday PnL',
      icon: realizedProfit >= 0 ? TrendingUp : TrendingDown,
      color: realizedProfit >= 0 ? (isLight ? 'text-emerald-600' : 'text-emerald-400') : (isLight ? 'text-rose-600' : 'text-red-400'),
    },
    {
      title: 'Open Positions',
      value: `${positions.length} Positions`,
      subtext: 'Live Dhan day & carryforward',
      icon: Layers,
      color: isLight ? 'text-slate-900' : 'text-white',
    },
    {
      title: 'Dhan Holdings',
      value: `${holdings.length} Holdings`,
      subtext: 'T1 & Delivered MTF',
      icon: Briefcase,
      color: isLight ? 'text-slate-900' : 'text-white',
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {cards.map((c, idx) => {
        const IconComponent = c.icon;
        return (
          <div
            key={idx}
            className={`rounded-2xl p-4 transition-all duration-200 border shadow-sm ${
              isLight
                ? 'bg-white border-slate-200 hover:bg-slate-50 hover:border-slate-300'
                : 'bg-zinc-900/90 border-zinc-800/80 hover:bg-zinc-800/70 hover:border-zinc-700 text-white'
            }`}
          >
            <div className="flex items-center justify-between mb-2">
              <span className={`text-[10px] font-bold uppercase tracking-wider ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>{c.title}</span>
              <IconComponent className={`h-4 w-4 ${isLight ? 'text-slate-400' : 'text-zinc-400'}`} />
            </div>
            <div className={`text-base font-bold font-mono tracking-tight ${c.color}`}>{c.value}</div>
            <div className={`text-[11px] mt-1 ${isLight ? 'text-slate-400' : 'text-zinc-500'}`}>{c.subtext}</div>
          </div>
        );
      })}
    </div>
  );
};

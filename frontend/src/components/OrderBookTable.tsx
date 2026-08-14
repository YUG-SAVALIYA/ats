import React, { useState } from 'react';
import { SuperOrderItem, DbOrderItem, RegularOrderItem } from '../types';
import { api } from '../services/api';
import { useToast } from '../context/ToastContext';
import { Trash2, Zap, Database } from 'lucide-react';
import { useCompanyImages } from '../context/CompanyImageContext';

interface OrderBookTableProps {
  superOrders: SuperOrderItem[];
  regularOrders?: RegularOrderItem[];
  dbOrders?: DbOrderItem[];
  onOrderCancelled: () => void;
  isLight?: boolean;
}

function fmt(n?: number) {
  if (n === undefined || isNaN(n)) return 'N/A';
  return `₹${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export const OrderBookTable: React.FC<OrderBookTableProps> = ({
  superOrders,
  regularOrders = [],
  dbOrders = [],
  onOrderCancelled,
  isLight = false,
}) => {
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'DHAN_LIVE' | 'DB_RECORD'>('DHAN_LIVE');
  const { addToast, confirmAction } = useToast();
  const images = useCompanyImages();

  const handleCancelSuperOrder = async (orderId: string, legName: string = 'ENTRY_LEG') => {
    const confirmed = await confirmAction({
      title: 'Cancel Super Order',
      message: `Are you sure you want to cancel order ${orderId} (${legName}) on Dhan?`,
      confirmText: 'Yes, Cancel Order',
      cancelText: 'Keep Order',
      type: 'danger',
    });

    if (!confirmed) return;

    setCancellingId(orderId);
    try {
      await api.cancelSuperOrder(orderId, legName);
      addToast(`Super Order ${orderId} cancellation request sent to Dhan.`, 'success', 'Order Cancel Request Sent');
      onOrderCancelled();
    } catch (e: any) {
      addToast(e.message || 'Failed to cancel order', 'error', 'Order Cancellation Error');
    } finally {
      setCancellingId(null);
    }
  };

  const displayOrders = [
    ...superOrders,
    ...regularOrders.map((ro) => ({
      ...ro,
      legName: 'REGULAR',
      targetPrice: undefined,
      stopLossPrice: undefined,
      filledQty: ro.filledQty ?? ro.tradedQuantity ?? 0,
    }) as unknown as SuperOrderItem),
  ];

  const activeCount = viewMode === 'DHAN_LIVE' ? displayOrders.length : dbOrders.length;

  return (
    <div className={`rounded-2xl overflow-hidden border transition-colors ${
      isLight ? 'bg-white border-slate-200 text-slate-900 shadow-sm' : 'bg-black border-white/20 text-white shadow-sm'
    }`}>
      {/* Table Top Toolbar */}
      <div className={`p-3.5 border-b flex flex-wrap items-center justify-between gap-2 ${
        isLight ? 'bg-slate-50 border-slate-200' : 'bg-black border-white/10'
      }`}>
        <div className="flex items-center gap-2">
          <Zap className={`h-4 w-4 ${isLight ? 'text-slate-500' : 'text-zinc-400'}`} />
          <span className={`text-xs font-bold font-['Outfit'] ${isLight ? 'text-slate-900' : 'text-white'}`}>
            Live Orderbook Overview ({displayOrders.length})
          </span>
        </div>
      </div>

      {displayOrders.length === 0 ? (
        <div className={`p-10 text-center text-xs ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>No active orders on Dhan</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className={`border-b text-left font-sans ${
                isLight ? 'border-slate-200 bg-slate-100 text-slate-600' : 'border-zinc-800 bg-zinc-950 text-zinc-400'
              }`}>
                <th className="px-4 py-3">Order ID</th>
                <th className="px-3 py-3">Symbol</th>
                <th className="px-3 py-3">Action</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3 text-right">Qty (Filled)</th>
                <th className="px-3 py-3 text-right">Price</th>
                <th className="px-3 py-3 text-right">Target</th>
                <th className="px-3 py-3 text-right">Stop Loss</th>
                <th className="px-4 py-3 text-center">Action</th>
              </tr>
            </thead>
            <tbody className={`divide-y ${isLight ? 'divide-slate-200' : 'divide-zinc-800/60'}`}>
              {displayOrders.map((o) => {
                const status = (o.orderStatus || '').toUpperCase();
                let statusStyle = isLight ? 'bg-slate-100 text-slate-700 border-slate-300' : 'bg-zinc-900 text-zinc-300 border-zinc-700';
                if (status === 'REJECTED' || status === 'CANCELLED' || status === 'EXPIRED') {
                  statusStyle = 'bg-red-500/10 text-red-500 border-red-500/30 font-bold';
                } else if (status === 'EXECUTED' || status === 'TRADED' || status === 'FILLED') {
                  statusStyle = 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30 font-bold';
                } else if (status === 'PENDING' || status === 'TRANSIT' || status === 'TRIGGERED') {
                  statusStyle = 'bg-amber-500/10 text-amber-600 border-amber-500/30 font-bold';
                }

                return (
                  <tr key={o.orderId} className={`transition-colors duration-150 border-b cursor-pointer ${
                    isLight ? 'hover:bg-slate-200/90 border-slate-200' : 'hover:bg-zinc-800/80 border-zinc-800/40'
                  }`}>
                    <td className={`px-4 py-3 font-semibold font-mono tracking-wide ${isLight ? 'text-slate-800' : 'text-zinc-100'}`}>{o.orderId}</td>
                    <td className={`px-3 py-3 font-sans font-bold flex items-center gap-1.5 ${isLight ? 'text-slate-900' : 'text-white'}`}>
                      {images[o.tradingSymbol || ''] ? (
                        <img src={images[o.tradingSymbol || '']} alt={o.tradingSymbol} className="w-5 h-5 rounded-full object-cover" />
                      ) : (
                        <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold ${isLight ? 'bg-slate-200 text-slate-700' : 'bg-zinc-800 text-zinc-300'}`}>
                          {(o.tradingSymbol || '').charAt(0)}
                        </div>
                      )}
                      {o.tradingSymbol}
                    </td>
                    <td className="px-3 py-3 font-sans">
                      <span
                        className={`px-1.5 py-0.5 text-[10px] rounded font-bold ${
                          o.transactionType === 'BUY'
                            ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/30'
                            : 'bg-rose-500/10 text-rose-500 border border-rose-500/30'
                        }`}
                      >
                        {o.transactionType}
                      </span>
                    </td>
                    <td className="px-3 py-3 font-sans">
                      <span className={`px-2 py-0.5 text-[10px] rounded border font-mono ${statusStyle}`}>
                        {o.orderStatus}
                      </span>
                    </td>
                    <td className={`px-3 py-3 text-right font-semibold ${isLight ? 'text-slate-900' : 'text-zinc-100'}`}>
                      {o.quantity} <span className={isLight ? 'text-slate-400 font-normal' : 'text-zinc-400 font-normal'}>({o.filledQty || 0})</span>
                    </td>
                    <td className={`px-3 py-3 text-right ${isLight ? 'text-slate-700' : 'text-zinc-300'}`}>{fmt(o.price)}</td>
                    <td className="px-3 py-3 text-right text-emerald-500 font-semibold">{fmt(o.targetPrice)}</td>
                    <td className="px-3 py-3 text-right text-rose-500 font-semibold">{fmt(o.stopLossPrice)}</td>
                    <td className="px-4 py-3 text-center">
                      <button
                        onClick={() => handleCancelSuperOrder(o.orderId, o.legName || 'ENTRY_LEG')}
                        disabled={cancellingId === o.orderId}
                        className="px-2.5 py-1 text-[10px] font-bold bg-rose-500/10 hover:bg-rose-500/20 text-rose-500 border border-rose-500/30 rounded flex items-center gap-1 mx-auto hover:scale-105 transition-all duration-150"
                      >
                        <Trash2 className="h-3 w-3" /> Cancel
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

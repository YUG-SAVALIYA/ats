import React, { useState, useEffect } from 'react';
import { StockMasterItem } from '../types';
import { api } from '../services/api';
import { Search, Copy, Check } from 'lucide-react';

interface StockSearchModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const StockSearchModal: React.FC<StockSearchModalProps> = ({ isOpen, onClose }) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<StockMasterItem[]>([]);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    const fetchPopular = async () => {
      try {
        const res = await api.searchStocks(query);
        setResults(res);
      } catch (e) {
        console.error(e);
      }
    };
    fetchPopular();
  }, [isOpen, query]);

  const handleCopySecId = (id: string) => {
    navigator.clipboard.writeText(id);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-black border border-zinc-800 rounded-xl max-w-2xl w-full p-6 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <h3 className="font-bold text-white text-base">Dhan Stock Master & Security ID Resolver</h3>
          <button onClick={onClose} className="text-zinc-400 hover:text-white font-bold text-lg">
            ×
          </button>
        </div>

        <div className="relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search stock symbol e.g. INFY, TATAMOTORS, NIFTY..."
            className="w-full bg-black border border-zinc-800 rounded-lg pl-9 pr-4 py-2 text-sm text-white focus:outline-none focus:border-zinc-700 font-mono"
          />
        </div>

        <div className="max-h-80 overflow-y-auto divide-y divide-zinc-800 border border-zinc-800 rounded-lg bg-black">
          {results.map((stk) => (
            <div key={stk.security_id} className="p-3 hover:bg-zinc-900/60 flex items-center justify-between text-xs">
              <div>
                <div className="font-bold text-white font-mono text-sm">{stk.symbol}</div>
                <div className="text-zinc-400">{stk.name}</div>
              </div>

              <div className="flex items-center gap-3">
                <span className="px-2 py-0.5 bg-zinc-900 text-zinc-300 border border-zinc-800 rounded text-[10px]">
                  {stk.exchange_segment}
                </span>
                <div className="flex items-center gap-1 bg-zinc-900 px-2 py-1 rounded border border-zinc-800 font-mono">
                  <span className="text-zinc-500">ID:</span>
                  <span className="text-emerald-400 font-bold">{stk.security_id}</span>
                  <button
                    onClick={() => handleCopySecId(stk.security_id)}
                    className="ml-1 text-zinc-400 hover:text-white"
                    title="Copy Security ID"
                  >
                    {copiedId === stk.security_id ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

import React, { useState } from 'react';
import { EngineStatus, api } from '../services/api';
import { Cpu, Power, Clock, ShieldCheck, Activity, TrendingUp, Zap } from 'lucide-react';

interface AutomatedEngineCardProps {
  engineStatus: EngineStatus | null;
  onRefresh: () => void;
}

export const AutomatedEngineCard: React.FC<AutomatedEngineCardProps> = ({ engineStatus, onRefresh }) => {
  const [isToggling, setIsToggling] = useState(false);

  const handleToggle = async () => {
    if (!engineStatus) return;
    setIsToggling(true);
    try {
      const res = await api.toggleEngine(!engineStatus.enabled);
      alert(res.message);
      onRefresh();
    } catch (e: any) {
      alert(`Engine toggle error: ${e.message}`);
    } finally {
      setIsToggling(false);
    }
  };

  const isRunning = engineStatus?.enabled ?? true;

  return (
    <div className="bg-black border border-zinc-800 rounded-xl p-5 shadow-xl relative overflow-hidden">
      {/* Top Banner Header */}
      <div className="flex items-center justify-between border-b border-zinc-800 pb-4 mb-4">
        <div className="flex items-center gap-3">
          <div
            className={`h-11 w-11 rounded-xl flex items-center justify-center border transition-all ${
              isRunning
                ? 'bg-zinc-900 text-emerald-400 border-zinc-800'
                : 'bg-zinc-900 text-amber-400 border-zinc-800'
            }`}
          >
            <Cpu className="h-6 w-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold text-white tracking-tight">Automated Strategy Execution Engine</h2>
              <span
                className={`px-2.5 py-0.5 text-[10px] font-bold rounded-full border flex items-center gap-1.5 ${
                  isRunning
                    ? 'bg-zinc-900 text-emerald-400 border-zinc-800'
                    : 'bg-zinc-900 text-amber-400 border-zinc-800'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${isRunning ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`}
                ></span>
                {isRunning ? 'ENGINE ACTIVE & SCANNING' : 'ENGINE PAUSED'}
              </span>
            </div>
            <p className="text-xs text-zinc-400 mt-0.5">
              Automated Signal Claiming • Dhan MTF Super Orders • Auto-Trailing Stop Loss • 03:25 PM IST Gate
            </p>
          </div>
        </div>

        {/* Engine Toggle Switch */}
        <button
          onClick={handleToggle}
          disabled={isToggling}
          className={`px-4 py-2.5 rounded-xl font-bold text-xs flex items-center gap-2 transition-all shadow-md ${
            isRunning
              ? 'bg-zinc-900 hover:bg-zinc-800 text-amber-400 border border-zinc-800'
              : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/20'
          } disabled:opacity-50`}
        >
          <Power className="h-4 w-4" />
          <span>{isToggling ? 'Updating...' : isRunning ? 'Pause Engine' : 'Activate Engine'}</span>
        </button>
      </div>

      {/* Automated Pipeline Execution Rules */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
        <div className="bg-black border border-zinc-800 p-3.5 rounded-xl space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-300">
            <Clock className="h-4 w-4 text-zinc-400" />
            <span>Automated Entry Gate</span>
          </div>
          <div className="text-base font-bold font-mono text-white">03:25 PM IST</div>
          <div className="text-[11px] text-zinc-400">Atomically claims ready signals & checks LTP &gt; high</div>
        </div>

        <div className="bg-black border border-zinc-800 p-3.5 rounded-xl space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-300">
            <Zap className="h-4 w-4 text-zinc-400" />
            <span>Dhan Super Orders</span>
          </div>
          <div className="text-base font-bold font-mono text-white">Target & SL</div>
          <div className="text-[11px] text-zinc-400">Automated Bracket Target + Dynamic Trailing SL</div>
        </div>

        <div className="bg-zinc-800/30 border border-zinc-700/50 rounded-lg p-3">
          <div className="text-xs text-zinc-500 mb-1 flex items-center justify-between">
            <span>Trailing Stop Loss</span>
            <span className="text-[10px] bg-zinc-800 px-1.5 py-0.5 rounded text-zinc-400">Dynamic</span>
          </div>
          <div className="text-base font-bold font-mono text-white">Configured Stages</div>
          <div className="text-[11px] text-zinc-400">Automatically modifies stop loss on Dhan OMS</div>
        </div>

        <div className="bg-black border border-zinc-800 p-3.5 rounded-xl space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-300">
            <ShieldCheck className="h-4 w-4 text-zinc-400" />
            <span>Risk Guardrails</span>
          </div>
          <div className="text-base font-bold font-mono text-white">Max 25 Orders / Day</div>
          <div className="text-[11px] text-zinc-400">₹10,000 daily loss limit & emergency kill switch</div>
        </div>
      </div>

      {/* Ticker Banner */}
      <div className="flex items-center justify-between bg-black border border-zinc-800 rounded-lg p-2.5 text-xs text-zinc-300">
        <div className="flex items-center gap-2 font-mono">
          <Activity className="h-4 w-4 text-zinc-400 animate-spin" />
          <span>
            Last Iteration Heartbeat:{' '}
            <strong className="text-white">{engineStatus?.details?.last_tick || 'Active'} IST</strong>
          </span>
        </div>
        <div className="text-zinc-400 text-[11px]">
          100% Fully Automated Trading Engine connected to Dhan HQ
        </div>
      </div>
    </div>
  );
};

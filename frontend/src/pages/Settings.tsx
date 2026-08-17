import React, { useEffect, useState } from 'react';
import { api, StrategySettings, MonthlyRsiSettings } from '../services/api';
import { useToast } from '../context/ToastContext';
import { Save, RefreshCw } from 'lucide-react';
import { useStrategy } from '../context/StrategyContext';

interface SettingsProps {
  isLight: boolean;
}

export function Settings({ isLight }: SettingsProps) {
  const { activeStrategy } = useStrategy();
  const [settings, setSettings] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const { addToast } = useToast();

  useEffect(() => {
    fetchSettings();
  }, [activeStrategy]);

  const fetchSettings = async () => {
    setIsLoading(true);
    try {
      const data = await api.getStrategySettings(activeStrategy);
      setSettings(data);
    } catch (err: any) {
      addToast(err.message || 'Failed to load settings', 'error');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!settings) return;
    
    setIsSaving(true);
    try {
      await api.updateStrategySettings(settings, activeStrategy);
      addToast('Settings updated successfully', 'success');
    } catch (err: any) {
      addToast(err.message || 'Failed to update settings', 'error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setSettings((prev: any) => prev ? { ...prev, [name]: value === '' ? 0 : parseFloat(value) } : null);
  };

  if (isLoading) {
    return <div className="p-8 text-center text-sm font-mono text-zinc-500">Loading settings...</div>;
  }

  if (!settings) return null;

  const cardClass = `p-6 rounded-2xl border ${isLight ? 'bg-white border-slate-200' : 'bg-zinc-950 border-zinc-800'}`;
  const inputClass = `w-full px-3 py-2 text-sm font-mono rounded-lg border focus:outline-none focus:ring-1 transition-all ${
    isLight 
      ? 'bg-slate-50 border-slate-200 text-slate-900 focus:border-blue-500 focus:ring-blue-500' 
      : 'bg-black border-zinc-800 text-white focus:border-emerald-500 focus:ring-emerald-500'
  }`;
  const labelClass = `block text-xs font-bold mb-1.5 ${isLight ? 'text-slate-600' : 'text-zinc-400'}`;
  const sectionTitleClass = `text-sm font-bold uppercase tracking-wider mb-4 pb-2 border-b ${isLight ? 'text-slate-800 border-slate-200' : 'text-zinc-300 border-zinc-800'}`;

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className={`text-2xl font-bold ${isLight ? 'text-slate-900' : 'text-white'}`}>Strategy Configuration</h1>
          <p className={`text-sm mt-1 ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>
            Dynamically adjust signal generation and trade management parameters.
          </p>
        </div>
        <button
          onClick={fetchSettings}
          className={`p-2 rounded-lg transition-colors ${
            isLight ? 'hover:bg-slate-200 text-slate-600' : 'hover:bg-zinc-800 text-zinc-400'
          }`}
          title="Reload Settings"
        >
          <RefreshCw className="w-5 h-5" />
        </button>
      </div>

      {activeStrategy === 'MONTHLY_RSI' && (
        <div className="mb-4 p-4 rounded-xl border border-purple-500/30 bg-purple-500/10 text-purple-400 text-sm font-bold">
          Note: Monthly RSI is an isolated strategy. These settings do not affect Supertrend Breakout.
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {activeStrategy === 'SUPERTREND' ? (
            <>
              {/* Signal Generation Section */}
              <div className={cardClass}>
                <h2 className={sectionTitleClass}>Signal Generation</h2>
                
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Daily RSI Period</label>
                      <input type="number" name="daily_rsi_period" value={settings.daily_rsi_period || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Weekly RSI Period</label>
                      <input type="number" name="weekly_rsi_period" value={settings.weekly_rsi_period || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Daily RSI Lower Bound</label>
                      <input type="number" step="0.1" name="daily_rsi_lower" value={settings.daily_rsi_lower || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Daily RSI Upper Bound</label>
                      <input type="number" step="0.1" name="daily_rsi_upper" value={settings.daily_rsi_upper || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Weekly RSI Lower Bound</label>
                      <input type="number" step="0.1" name="weekly_rsi_lower" value={settings.weekly_rsi_lower || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Weekly RSI Upper Bound</label>
                      <input type="number" step="0.1" name="weekly_rsi_upper" value={settings.weekly_rsi_upper || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>
                  
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Supertrend Period</label>
                      <input type="number" name="supertrend_period" value={settings.supertrend_period || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Supertrend Multiplier</label>
                      <input type="number" step="0.1" name="supertrend_multiplier" value={settings.supertrend_multiplier || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Min Candle Range (%)</label>
                      <input type="number" step="0.1" name="candle_range_min" value={settings.candle_range_min || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Max Candle Range (%)</label>
                      <input type="number" step="0.1" name="candle_range_max" value={settings.candle_range_max || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Min Market Cap (Cr)</label>
                      <input type="number" name="market_cap_min_cr" value={settings.market_cap_min_cr || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Entry High Breakout (%)</label>
                      <input type="number" step="0.1" name="entry_high_breakout_pct" value={settings.entry_high_breakout_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>
                </div>
              </div>

              {/* Trade Management Section */}
              <div className={cardClass}>
                <h2 className={sectionTitleClass}>Trade Management (SL & Targets)</h2>
                
                <div className="space-y-4">
                  <div className="grid grid-cols-3 gap-4">
                    <div>
                      <label className={labelClass}>Initial SL (%)</label>
                      <input type="number" step="0.1" name="initial_sl_pct" value={settings.initial_sl_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Target 1 (%)</label>
                      <input type="number" step="0.1" name="target1_pct" value={settings.target1_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Target 2 (%)</label>
                      <input type="number" step="0.1" name="target2_pct" value={settings.target2_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4 mt-6">
                    <div>
                      <label className={labelClass}>Stage 1 Trigger (%)</label>
                      <input type="number" step="0.1" name="sl_stage1_trigger" value={settings.sl_stage1_trigger || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Stage 1 SL Trail (%)</label>
                      <input type="number" step="0.1" name="sl_stage1_trail" value={settings.sl_stage1_trail || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Stage 2 Trigger (%)</label>
                      <input type="number" step="0.1" name="sl_stage2_trigger" value={settings.sl_stage2_trigger || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Stage 2 SL Trail (%)</label>
                      <input type="number" step="0.1" name="sl_stage2_trail" value={settings.sl_stage2_trail || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Stage 3 Trigger (%)</label>
                      <input type="number" step="0.1" name="sl_stage3_trigger" value={settings.sl_stage3_trigger || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Stage 3 SL Trail (%)</label>
                      <input type="number" step="0.1" name="sl_stage3_trail" value={settings.sl_stage3_trail || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <>
              {/* Signal Generation Section */}
              <div className={cardClass}>
                <h2 className={sectionTitleClass}>Signal Generation</h2>
                
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Monthly RSI Period</label>
                      <input type="number" name="rsi_period" value={settings.rsi_period || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Min ROC(6) %</label>
                      <input type="number" step="0.1" name="min_roc6_pct" value={settings.min_roc6_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>RSI Lower Bound</label>
                      <input type="number" step="0.1" name="min_rsi" value={settings.min_rsi || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>RSI Upper Bound</label>
                      <input type="number" step="0.1" name="max_rsi" value={settings.max_rsi || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Swing Low Window</label>
                      <input type="number" name="swing_window" value={settings.swing_window || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Swing Low Buffer %</label>
                      <input type="number" step="0.1" name="swing_buffer_pct" value={settings.swing_buffer_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Min Close &gt; SMA12 %</label>
                      <input type="number" step="0.1" name="min_close_above_sma12_pct" value={settings.min_close_above_sma12_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Max Entry Gap %</label>
                      <input type="number" step="0.1" name="max_entry_gap_pct" value={settings.max_entry_gap_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>
                </div>
              </div>

              {/* Trade Management Section */}
              <div className={cardClass}>
                <h2 className={sectionTitleClass}>Exit & Trade Management</h2>
                
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Target (%)</label>
                      <input type="number" step="0.1" name="target_pct" value={settings.target_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>RSI Exit Below</label>
                      <input type="number" step="0.1" name="rsi_exit_below" value={settings.rsi_exit_below || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelClass}>Min Stop Distance %</label>
                      <input type="number" step="0.1" name="min_stop_distance_pct" value={settings.min_stop_distance_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Max Stop Distance %</label>
                      <input type="number" step="0.1" name="max_stop_distance_pct" value={settings.max_stop_distance_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4 mt-6">
                    <div>
                      <label className={labelClass}>Supertrend Period (Exit)</label>
                      <input type="number" name="supertrend_period" value={settings.supertrend_period || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Supertrend Multiplier</label>
                      <input type="number" step="0.1" name="supertrend_multiplier" value={settings.supertrend_multiplier || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>
                  
                  <div className="grid grid-cols-3 gap-4 mt-6">
                    <div>
                      <label className={labelClass}>Partial Qty %</label>
                      <input type="number" step="0.1" name="partial_exit_qty_pct" value={settings.partial_exit_qty_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Partial Profit %</label>
                      <input type="number" step="0.1" name="partial_exit_profit_pct" value={settings.partial_exit_profit_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                    <div>
                      <label className={labelClass}>Partial Trail %</label>
                      <input type="number" step="0.1" name="partial_stop_profit_pct" value={settings.partial_stop_profit_pct || ''} onChange={handleChange} className={inputClass} />
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="flex justify-end pt-4">
          <button
            type="submit"
            disabled={isSaving}
            className={`flex items-center gap-2 px-6 py-2.5 rounded-xl font-bold text-sm shadow-sm transition-all
              ${isLight 
                ? 'bg-blue-600 hover:bg-blue-700 text-white' 
                : 'bg-emerald-600 hover:bg-emerald-500 text-white'
              } ${isSaving ? 'opacity-70 cursor-not-allowed' : ''}
            `}
          >
            <Save className="w-4 h-4" />
            {isSaving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </form>
    </div>
  );
}

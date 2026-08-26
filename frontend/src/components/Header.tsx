import React from 'react';
import { AuthStatus } from '../types';
import { ShieldCheck, RefreshCw, Zap, LayoutDashboard, Activity, TrendingUp, Sun, Moon, LogOut, Wifi, WifiOff } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { Settings as SettingsIcon } from 'lucide-react';
import { useStrategy } from '../context/StrategyContext';
import { useAuth } from '../context/AuthContext';
import { api } from '../services/api';

interface HeaderProps {
  authStatus: AuthStatus | null;
  onRefresh: () => void;
  isSyncing: boolean;
  signalsCount: number;
  openTradesCount: number;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  authStatus,
  onRefresh,
  isSyncing,
  signalsCount,
  openTradesCount,
  theme,
  onToggleTheme,
}) => {
  const isLight = theme === 'light';
  const location = useLocation();
  const currentPath = location.pathname;
  const { activeStrategy, setActiveStrategy } = useStrategy();
  const { logout, onDhanDisconnected } = useAuth();

  const handleDhanDisconnect = async () => {
    if (!window.confirm('Disconnect your Dhan account? The Dashboard will be locked until you reconnect.')) return;
    try {
      await api.disconnectDhan();
      onDhanDisconnected();
    } catch (e: any) {
      alert(`Disconnect failed: ${e.message || e}`);
    }
  };

  return (
    <header className={`sticky top-0 z-40 border-b transition-colors duration-300 ${
      isLight ? 'bg-white border-slate-200 text-slate-900 shadow-sm' : 'bg-black border-zinc-800 text-white'
    }`}>
      <div className="w-full px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-4">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className={`h-10 w-10 rounded-xl border flex items-center justify-center transition-colors ${
            isLight ? 'bg-slate-100 border-slate-200' : 'bg-zinc-900 border-zinc-800'
          }`}>
            <Zap className={`h-5 w-5 ${isLight ? 'text-slate-900' : 'text-white'}`} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className={`text-xl font-bold tracking-tight font-['Outfit'] ${isLight ? 'text-slate-900' : 'text-white'}`}>
                ATS <span className={isLight ? 'text-slate-500 font-normal' : 'text-zinc-400 font-normal'}>Dhan Platform</span>
              </h1>
              <span className={`px-2.5 py-0.5 text-[10px] font-bold rounded-full border flex items-center gap-1.5 ${
                isLight ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-zinc-900 text-emerald-400 border-zinc-800'
              }`}>
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                100% REAL LIVE
              </span>
            </div>
            <p className={`text-xs ${isLight ? 'text-slate-500' : 'text-zinc-400'}`}>
              Direct connection to Dhan HQ API v2 • Zero paper mode
            </p>
          </div>
        </div>

        {/* Strategy Switcher */}
        <div className={`flex items-center gap-1 p-1 rounded-xl border ${isLight ? 'bg-slate-100 border-slate-200' : 'bg-zinc-900 border-zinc-800'}`}>
          <button
            onClick={() => setActiveStrategy('SUPERTREND')}
            className={`px-4 py-1.5 text-xs font-bold rounded-lg transition-all ${
              activeStrategy === 'SUPERTREND' 
                ? 'bg-blue-600 text-white shadow-sm' 
                : isLight ? 'text-slate-600 hover:bg-slate-200' : 'text-zinc-400 hover:bg-zinc-800'
            }`}
          >
            Supertrend Breakout
          </button>
          <button
            onClick={() => setActiveStrategy('MONTHLY_RSI')}
            className={`px-4 py-1.5 text-xs font-bold rounded-lg transition-all ${
              activeStrategy === 'MONTHLY_RSI' 
                ? 'bg-purple-600 text-white shadow-sm' 
                : isLight ? 'text-slate-600 hover:bg-slate-200' : 'text-zinc-400 hover:bg-zinc-800'
            }`}
          >
            Monthly RSI
          </button>
        </div>

        {/* Navigation Tabs (Dashboard, Signals, Trades) - Sleek Pill Tabs */}
        <nav className="flex items-center gap-2">
          <Link
            to="/"
            className={`flex items-center gap-2 px-5 py-2 text-xs font-bold rounded-full transition-all duration-200 ${
              currentPath === '/'
                ? isLight
                  ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                  : 'bg-zinc-800 text-white shadow-md'
                : isLight
                ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
            }`}
          >
            <LayoutDashboard className="h-4 w-4" />
            <span>Dashboard</span>
          </Link>

          <Link
            to="/signals"
            className={`flex items-center gap-2 px-5 py-2 text-xs font-bold rounded-full transition-all duration-200 ${
              currentPath === '/signals'
                ? isLight
                  ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                  : 'bg-zinc-800 text-white shadow-md'
                : isLight
                ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
            }`}
          >
            <Activity className="h-4 w-4" />
            <span>Signals ({signalsCount})</span>
          </Link>

          <Link
            to="/trades"
            className={`flex items-center gap-2 px-5 py-2 text-xs font-bold rounded-full transition-all duration-200 ${
              currentPath === '/trades'
                ? isLight
                  ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                  : 'bg-zinc-800 text-white shadow-md'
                : isLight
                ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
            }`}
          >
            <TrendingUp className="h-4 w-4" />
            <span>Trades ({openTradesCount})</span>
          </Link>

          <Link
            to="/settings"
            className={`flex items-center gap-2 px-5 py-2 text-xs font-bold rounded-full transition-all duration-200 ${
              currentPath === '/settings'
                ? isLight
                  ? 'bg-slate-200 text-slate-900 border-transparent shadow-sm font-bold'
                  : 'bg-zinc-800 text-white shadow-md'
                : isLight
                ? 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/60'
                : 'text-zinc-400 hover:text-white hover:bg-zinc-800/60'
            }`}
          >
            <SettingsIcon className="h-4 w-4" />
            <span>Settings</span>
          </Link>
        </nav>

        {/* Status & Actions */}
        <div className="flex items-center gap-2.5">
          {/* Account Status Badge */}
          {authStatus && (
            <div className={`hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs ${
              isLight ? 'bg-slate-100 border-slate-200 text-slate-700' : 'bg-zinc-900 border-zinc-800 text-zinc-300'
            }`}>
              <ShieldCheck className="h-4 w-4 text-zinc-400" />
              <div>
                <span className="opacity-70">Client ID: </span>
                <span className="font-mono font-semibold">{authStatus.client_id}</span>
              </div>
            </div>
          )}

          {/* Dhan Connection Status */}
          <div
            className={`hidden lg:flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs cursor-pointer group relative ${
              isLight
                ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                : 'bg-emerald-950/40 border-emerald-900/60 text-emerald-400'
            }`}
            title="Dhan Connected — click to disconnect"
          >
            <Wifi className="h-3.5 w-3.5" />
            <span className="font-semibold">Dhan</span>
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
            {/* Hover: show disconnect option */}
            <button
              onClick={handleDhanDisconnect}
              className="absolute inset-0 flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100 rounded-lg bg-rose-950/80 text-rose-400 text-[10px] font-bold transition-opacity duration-150"
            >
              <WifiOff size={12} /> Disconnect
            </button>
          </div>

          {/* Emergency Kill Switch Button */}
          <button
            onClick={async () => {
              try {
                const cur = await api.getKillSwitchStatus();
                const nextState = !cur.kill_switch_active;
                const prompt = nextState
                  ? 'ARE YOU SURE? This will immediately BLOCK all automated and manual entry orders!'
                  : 'Resume normal automated trading?';
                if (window.confirm(prompt)) {
                  await api.toggleKillSwitch(nextState);
                  alert(`Kill Switch is now ${nextState ? 'ACTIVE' : 'DISABLED'}`);
                  onRefresh();
                }
              } catch (e: any) {
                alert(`Kill switch error: ${e.message || e}`);
              }
            }}
            title="Emergency Kill Switch (Halts all new entries immediately)"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-xl bg-red-600/20 hover:bg-red-600/30 text-red-400 border border-red-800 transition-all hover:scale-105"
          >
            <span className="w-2 h-2 rounded-full bg-red-500 animate-ping"></span>
            <span>Kill Switch</span>
          </button>

          {/* Theme Toggle Button (Sun / Moon) */}
          <button
            onClick={onToggleTheme}
            title={`Switch to ${isLight ? 'Dark' : 'Light'} Mode`}
            className={`p-2 rounded-xl border transition-all hover:scale-105 ${
              isLight
                ? 'bg-slate-100 hover:bg-slate-200 border-slate-300 text-slate-800'
                : 'bg-zinc-900 hover:bg-zinc-800 border-zinc-800 text-zinc-200'
            }`}
          >
            {isLight ? <Moon className="h-4 w-4 text-slate-700" /> : <Sun className="h-4 w-4 text-amber-400" />}
          </button>

          {/* Logout / Lock Screen Button */}
          <button
            onClick={() => {
              if (window.confirm('Lock session and log out?')) {
                logout();
              }
            }}
            title="Lock Session / Logout"
            className={`p-2 rounded-xl border transition-all hover:scale-105 ${
              isLight
                ? 'bg-slate-100 hover:bg-rose-100 border-slate-300 text-slate-700 hover:text-rose-700 hover:border-rose-300'
                : 'bg-zinc-900 hover:bg-rose-950/40 border-zinc-800 text-zinc-400 hover:text-rose-400 hover:border-rose-900/50'
            }`}
          >
            <LogOut className="h-4 w-4" />
          </button>

          {/* Sync Account Button */}
          <button
            onClick={onRefresh}
            disabled={isSyncing}
            className="flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-xl bg-blue-600 hover:bg-blue-500 text-white transition-all shadow-md hover:scale-105 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
            <span>{isSyncing ? 'Syncing...' : 'Sync Account'}</span>
          </button>
        </div>
      </div>
    </header>
  );
};

import React, { useState, useEffect } from 'react';
import { Lock, Unlock, KeyRound, ShieldCheck } from 'lucide-react';
import { useToast } from '../context/ToastContext';
import { useAuth } from '../context/AuthContext';
import { api } from '../services/api';

export function LockScreen() {
  const [isSetupMode, setIsSetupMode] = useState<boolean | null>(null);
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const { addToast } = useToast();
  const { login } = useAuth();

  useEffect(() => {
    // Check if password is set up
    api.getAppAuthStatus()
      .then(data => {
        setIsSetupMode(!data.is_setup);
      })
      .catch(err => {
        console.error('Backend status check failed:', err);
        addToast('Failed to connect to backend', 'error');
        // Stop spinning if it fails
        setIsSetupMode(true); 
      });
  }, [addToast]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;

    if (isSetupMode) {
      if (password !== confirmPassword) {
        addToast('Passwords do not match', 'error');
        return;
      }
      if (password.length < 4) {
        addToast('Password must be at least 4 characters', 'error');
        return;
      }
    }

    setLoading(true);

    try {
      if (isSetupMode) {
        await api.setupAppPassword(password);
        addToast('Password set successfully. Please login.', 'success');
        setIsSetupMode(false);
        setPassword('');
        setConfirmPassword('');
      } else {
        const data = await api.loginApp(password);
        await login(data.access_token);
        addToast('Unlocked successfully', 'success');
      }
    } catch (err: any) {
      addToast(err.message || 'Authentication failed', 'error');
    } finally {
      setLoading(false);
    }
  };

  if (isSetupMode === null) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-emerald-500"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 flex items-center justify-center relative overflow-hidden">
      {/* Background decorations */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-emerald-500/20 rounded-full blur-[100px] pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-blue-500/20 rounded-full blur-[100px] pointer-events-none" />

      <div className="relative z-10 w-full max-w-md p-8 bg-zinc-900/60 backdrop-blur-xl border border-zinc-800 rounded-3xl shadow-2xl">
        <div className="flex flex-col items-center mb-8">
          <div className={`p-4 rounded-full mb-4 ${isSetupMode ? 'bg-blue-500/20 text-blue-400' : 'bg-emerald-500/20 text-emerald-400'}`}>
            {isSetupMode ? <ShieldCheck size={32} /> : <Lock size={32} />}
          </div>
          <h2 className="text-2xl font-bold text-white mb-2">
            {isSetupMode ? 'System Setup' : 'ATS Locked'}
          </h2>
          <p className="text-zinc-400 text-center">
            {isSetupMode 
              ? 'Set your master password to secure the system.'
              : 'Enter your master password to access the trading terminal.'}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <KeyRound size={18} className="text-zinc-500" />
              </div>
              <input
                type="password"
                required
                autoFocus
                className="block w-full pl-10 pr-3 py-3 border border-zinc-800 rounded-xl bg-zinc-950/50 text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 transition-all"
                placeholder={isSetupMode ? 'Create Master Password' : 'Master Password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>

          {isSetupMode && (
            <div>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <KeyRound size={18} className="text-zinc-500" />
                </div>
                <input
                  type="password"
                  required
                  className="block w-full pl-10 pr-3 py-3 border border-zinc-800 rounded-xl bg-zinc-950/50 text-white placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 transition-all"
                  placeholder="Confirm Master Password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
              </div>
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !password || (isSetupMode && !confirmPassword)}
            className="w-full flex justify-center items-center py-3 px-4 border border-transparent rounded-xl shadow-sm text-sm font-bold text-white bg-emerald-600 hover:bg-emerald-500 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {loading ? (
              <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white"></div>
            ) : isSetupMode ? (
              'Set Password'
            ) : (
              <>
                <Unlock size={18} className="mr-2" />
                Unlock System
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}

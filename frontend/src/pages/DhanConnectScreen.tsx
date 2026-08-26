import React, { useState, useEffect } from 'react';
import { Link2, ShieldCheck, AlertTriangle, ExternalLink, Eye, EyeOff, Loader2, Wifi } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { api } from '../services/api';

export function DhanConnectScreen() {
  const { appState, onDhanConnected, setDhanConnecting, logout } = useAuth();
  const { addToast } = useToast();
  const isReconnect = appState === 'DHAN_AUTH_REQUIRED';

  // Partner flow state
  const [partnerAvailable, setPartnerAvailable] = useState(false);
  const [consentUrl, setConsentUrl] = useState<string | null>(null);

  // Manual connect form
  const [showManual, setShowManual] = useState(false);
  const [clientId, setClientId] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    // Check if partner flow is available and get consent URL
    api.getDhanConnection()
      .then(conn => {
        setPartnerAvailable(conn.partner_flow_available);
        if (conn.partner_flow_available) {
          api.getDhanConnectUrl()
            .then(res => setConsentUrl(res.consent_url))
            .catch(() => setPartnerAvailable(false));
        }
        setChecking(false);
      })
      .catch(() => {
        setPartnerAvailable(false);
        setChecking(false);
      });
  }, []);

  const handlePartnerConnect = () => {
    if (!consentUrl) return;
    setDhanConnecting(true);
    // Redirect to Dhan's login page — Dhan redirects back to /api/dhan/callback
    window.location.href = consentUrl;
  };

  const handleManualConnect = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!clientId.trim() || !accessToken.trim()) {
      addToast('Client ID and Access Token are required', 'error');
      return;
    }
    setLoading(true);
    try {
      const result = await api.connectDhan(clientId.trim(), accessToken.trim());
      if (result.status === 'DHAN_CONNECTED') {
        addToast(`Dhan account ${result.client_id_masked} connected successfully!`, 'success');
        onDhanConnected();
      } else {
        addToast('Connection failed. Please check your credentials.', 'error');
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to connect Dhan account', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-950 flex items-center justify-center relative overflow-hidden">
      {/* Ambient glows */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-blue-500/10 rounded-full blur-[120px] pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-emerald-500/10 rounded-full blur-[120px] pointer-events-none" />

      <div className="relative z-10 w-full max-w-lg px-4">
        {/* Card */}
        <div className="bg-zinc-900/70 backdrop-blur-xl border border-zinc-800 rounded-3xl shadow-2xl p-8">
          {/* Header */}
          <div className="flex flex-col items-center mb-8">
            <div className={`p-4 rounded-2xl mb-4 ${isReconnect ? 'bg-amber-500/20 text-amber-400' : 'bg-blue-500/20 text-blue-400'}`}>
              {isReconnect ? <AlertTriangle size={32} /> : <Link2 size={32} />}
            </div>
            <h2 className="text-2xl font-bold text-white mb-2 text-center">
              {isReconnect ? 'Dhan Reconnection Required' : 'Connect Dhan Account'}
            </h2>
            <p className="text-zinc-400 text-sm text-center leading-relaxed">
              {isReconnect
                ? 'Your Dhan authorization has expired or become invalid. Reconnect to restore access to the trading terminal.'
                : 'Connect your Dhan trading account to access the ATS Dashboard, live portfolio, and automated execution.'}
            </p>
          </div>

          {checking ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="animate-spin text-blue-400" size={28} />
            </div>
          ) : (
            <>
              {/* Partner OAuth flow (primary) */}
              {partnerAvailable && consentUrl && (
                <div className="mb-4">
                  <button
                    onClick={handlePartnerConnect}
                    className="w-full flex items-center justify-center gap-3 py-3.5 px-6 bg-blue-600 hover:bg-blue-500 active:scale-95 text-white font-bold rounded-xl transition-all shadow-lg shadow-blue-500/20"
                  >
                    <ShieldCheck size={20} />
                    Connect with Dhan
                    <ExternalLink size={16} className="opacity-70" />
                  </button>
                  <p className="text-xs text-zinc-500 text-center mt-2">
                    You will be redirected to Dhan's secure login page
                  </p>
                </div>
              )}

              {/* Divider */}
              {partnerAvailable && (
                <div className="flex items-center gap-3 my-5">
                  <div className="flex-1 h-px bg-zinc-800" />
                  <span className="text-xs text-zinc-600">or connect manually</span>
                  <div className="flex-1 h-px bg-zinc-800" />
                </div>
              )}

              {/* Manual form toggle */}
              {!partnerAvailable && !showManual && (
                <button
                  onClick={() => setShowManual(true)}
                  className="w-full py-3.5 border border-zinc-700 hover:border-zinc-500 rounded-xl text-zinc-300 hover:text-white text-sm font-semibold transition-all"
                >
                  Enter Credentials Manually
                </button>
              )}

              {/* Manual form */}
              {(showManual || !partnerAvailable) && (
                <form onSubmit={handleManualConnect} className="space-y-4">
                  <div>
                    <label className="block text-xs font-medium text-zinc-400 mb-1.5">Dhan Client ID</label>
                    <input
                      type="text"
                      required
                      autoFocus={!partnerAvailable}
                      value={clientId}
                      onChange={e => setClientId(e.target.value)}
                      placeholder="e.g. 1234567890"
                      className="w-full px-4 py-3 bg-zinc-950/60 border border-zinc-800 rounded-xl text-white placeholder-zinc-600 focus:outline-none focus:ring-2 focus:ring-blue-500/50 text-sm transition-all"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-zinc-400 mb-1.5">
                      Access Token
                      <span className="ml-1 text-zinc-600">(from Dhan Portal → API Access)</span>
                    </label>
                    <div className="relative">
                      <input
                        type={showToken ? 'text' : 'password'}
                        required
                        value={accessToken}
                        onChange={e => setAccessToken(e.target.value)}
                        placeholder="Paste your Dhan access token"
                        className="w-full px-4 py-3 pr-11 bg-zinc-950/60 border border-zinc-800 rounded-xl text-white placeholder-zinc-600 focus:outline-none focus:ring-2 focus:ring-blue-500/50 text-sm font-mono transition-all"
                      />
                      <button
                        type="button"
                        onClick={() => setShowToken(!showToken)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 transition-colors"
                      >
                        {showToken ? <EyeOff size={18} /> : <Eye size={18} />}
                      </button>
                    </div>
                    <p className="mt-1.5 text-xs text-zinc-600">
                      Token is validated then stored encrypted. Never shared.
                    </p>
                  </div>
                  <button
                    type="submit"
                    disabled={loading}
                    className="w-full flex items-center justify-center gap-2 py-3.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold rounded-xl transition-all active:scale-95"
                  >
                    {loading ? (
                      <><Loader2 className="animate-spin" size={18} /> Verifying &amp; Connecting...</>
                    ) : (
                      <><Wifi size={18} /> Connect Account</>
                    )}
                  </button>
                </form>
              )}

              {/* Show manual toggle when partner IS available */}
              {partnerAvailable && !showManual && (
                <button
                  onClick={() => setShowManual(true)}
                  className="w-full mt-2 py-2.5 text-xs text-zinc-600 hover:text-zinc-400 transition-colors"
                >
                  Use manual credentials instead
                </button>
              )}
            </>
          )}

          {/* Footer */}
          <div className="mt-8 pt-6 border-t border-zinc-800/60 flex items-center justify-between">
            <p className="text-xs text-zinc-600">
              ATS requires a Dhan account for live trading.
            </p>
            <button
              onClick={logout}
              className="text-xs text-zinc-600 hover:text-zinc-400 transition-colors underline underline-offset-2"
            >
              Sign out
            </button>
          </div>
        </div>

        {/* Helper text */}
        <div className="mt-4 bg-zinc-900/40 border border-zinc-800/50 rounded-2xl p-4">
          <p className="text-xs text-zinc-500 font-medium mb-2">How to get your Access Token:</p>
          <ol className="text-xs text-zinc-600 space-y-1 list-decimal list-inside">
            <li>Log in to <span className="text-zinc-400">web.dhan.co</span></li>
            <li>Go to <span className="text-zinc-400">My Profile → API Access → Generate Access Token</span></li>
            <li>Copy the token and paste it above</li>
            <li>Tokens are valid for 18 hours and auto-renewed via TOTP if configured</li>
          </ol>
        </div>
      </div>
    </div>
  );
}

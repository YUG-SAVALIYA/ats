import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { api, UserProfile, setUnauthorizedHandler } from '../services/api';

// Full application state machine
export type AppState =
  | 'AUTH_UNKNOWN'         // Initial boot — checking stored token
  | 'UNAUTHENTICATED'      // No valid ATS JWT
  | 'DHAN_NOT_CONNECTED'   // Authenticated, but no Dhan account linked
  | 'DHAN_CONNECTING'      // In the Dhan connect flow
  | 'DHAN_CONNECTED'       // Authenticated + Dhan connected → show Dashboard
  | 'DHAN_AUTH_REQUIRED'   // Dhan token expired/invalid → reconnect screen
  | 'ERROR';               // Unrecoverable error

// Keep backward compat export
export type AuthState = AppState;

interface AuthContextType {
  appState: AppState;
  /** @deprecated use appState */
  authState: AppState;
  user: UserProfile | null;
  login: (token: string) => Promise<void>;
  logout: () => void;
  checkAuth: () => Promise<void>;
  setDhanConnecting: (connecting: boolean) => void;
  onDhanConnected: () => void;
  onDhanDisconnected: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [appState, setAppState] = useState<AppState>('AUTH_UNKNOWN');
  const [user, setUser] = useState<UserProfile | null>(null);

  const logout = useCallback(() => {
    localStorage.removeItem('ats_admin_token');
    setUser(null);
    setAppState('UNAUTHENTICATED');
  }, []);

  const _resolveDhanState = (dhan_status: string | undefined): AppState => {
    if (dhan_status === 'DHAN_CONNECTED')      return 'DHAN_CONNECTED';
    if (dhan_status === 'DHAN_AUTH_REQUIRED')  return 'DHAN_AUTH_REQUIRED';
    return 'DHAN_NOT_CONNECTED';
  };

  const checkAuth = useCallback(async () => {
    const token = localStorage.getItem('ats_admin_token');
    if (!token) {
      setUser(null);
      setAppState('UNAUTHENTICATED');
      return;
    }

    try {
      const profile = await api.getMe();
      if (profile && profile.authenticated) {
        setUser(profile);
        // /me now returns dhan_status inline — no second round-trip needed
        const dhanState = _resolveDhanState((profile as any).dhan_status);
        setAppState(dhanState);
      } else {
        logout();
      }
    } catch (err) {
      console.warn('[AUTH] Token verification failed:', err);
      logout();
    }
  }, [logout]);

  const login = useCallback(async (token: string) => {
    localStorage.setItem('ats_admin_token', token);
    try {
      const profile = await api.getMe();
      setUser(profile);
      const dhanState = _resolveDhanState((profile as any).dhan_status);
      setAppState(dhanState);
    } catch (err) {
      console.error('[AUTH] Failed to fetch profile after login:', err);
      setUser({
        authenticated: true,
        sub: 'admin',
        user_id: null,
        role: 'admin',
        is_admin: true,
        account_ids: null,
      });
      // Default to Dhan gate — will check on next render
      setAppState('DHAN_NOT_CONNECTED');
    }
  }, []);

  const setDhanConnecting = useCallback((connecting: boolean) => {
    setAppState(connecting ? 'DHAN_CONNECTING' : 'DHAN_NOT_CONNECTED');
  }, []);

  const onDhanConnected = useCallback(() => {
    setAppState('DHAN_CONNECTED');
  }, []);

  const onDhanDisconnected = useCallback(() => {
    setAppState('DHAN_NOT_CONNECTED');
  }, []);

  useEffect(() => {
    // Register global 401 callback — any protected API 401 logs the user out
    setUnauthorizedHandler(() => {
      setUser(null);
      setAppState('UNAUTHENTICATED');
    });

    // Handle Dhan OAuth callback: ?dhan_connected=1 in URL
    const params = new URLSearchParams(window.location.search);
    if (params.get('dhan_connected') === '1') {
      // Clean up the URL param
      window.history.replaceState({}, '', window.location.pathname);
      // Re-run checkAuth which will read /me and get DHAN_CONNECTED
      checkAuth();
    } else if (params.get('dhan_error')) {
      window.history.replaceState({}, '', window.location.pathname);
      checkAuth();
    } else {
      checkAuth();
    }
  }, [checkAuth]);

  return (
    <AuthContext.Provider value={{
      appState,
      authState: appState,  // backward compat
      user,
      login,
      logout,
      checkAuth,
      setDhanConnecting,
      onDhanConnected,
      onDhanDisconnected,
    }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

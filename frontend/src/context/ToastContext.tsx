import React, { createContext, useContext, useState, useCallback } from 'react';
import { CheckCircle2, XCircle, AlertCircle, Info, X } from 'lucide-react';

export type ToastType = 'success' | 'error' | 'info' | 'warning';

export interface ToastMessage {
  id: string;
  type: ToastType;
  title?: string;
  message: string;
  duration?: number;
}

interface ToastContextType {
  toasts: ToastMessage[];
  addToast: (message: string, type?: ToastType, title?: string, duration?: number) => void;
  removeToast: (id: string) => void;
  confirmAction: (options: ConfirmOptions) => Promise<boolean>;
}

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  type?: 'danger' | 'info' | 'warning';
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export const ToastProvider: React.FC<{ children: React.ReactNode; isLight?: boolean }> = ({
  children,
  isLight = false,
}) => {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [confirmState, setConfirmState] = useState<{
    isOpen: boolean;
    options: ConfirmOptions;
    resolve: (value: boolean) => void;
  } | null>(null);

  const addToast = useCallback(
    (message: string, type: ToastType = 'info', title?: string, duration: number = 4000) => {
      const id = Math.random().toString(36).substring(2, 9);
      const newToast: ToastMessage = { id, type, title, message, duration };

      setToasts((prev) => [...prev, newToast]);

      if (duration > 0) {
        setTimeout(() => {
          removeToast(id);
        }, duration);
      }
    },
    []
  );

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const confirmAction = useCallback((options: ConfirmOptions): Promise<boolean> => {
    return new Promise<boolean>((resolve) => {
      setConfirmState({
        isOpen: true,
        options,
        resolve,
      });
    });
  }, []);

  const handleConfirm = (value: boolean) => {
    if (confirmState) {
      confirmState.resolve(value);
      setConfirmState(null);
    }
  };

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast, confirmAction }}>
      {children}

      {/* Toast Floating Notification Container */}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2.5 max-w-sm w-full pointer-events-none px-4 sm:px-0">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`pointer-events-auto flex items-start gap-3 p-4 rounded-xl border shadow-2xl backdrop-blur-md transition-all duration-300 transform translate-y-0 animate-in slide-in-from-right-8 ${
              isLight
                ? toast.type === 'success'
                  ? 'bg-emerald-50/95 border-emerald-300 text-emerald-950 shadow-emerald-900/10'
                  : toast.type === 'error'
                  ? 'bg-rose-50/95 border-rose-300 text-rose-950 shadow-rose-900/10'
                  : toast.type === 'warning'
                  ? 'bg-amber-50/95 border-amber-300 text-amber-950 shadow-amber-900/10'
                  : 'bg-white/95 border-slate-300 text-slate-900 shadow-slate-900/10'
                : toast.type === 'success'
                ? 'bg-zinc-900/95 border-emerald-500/40 text-emerald-300 shadow-emerald-950/50'
                : toast.type === 'error'
                ? 'bg-zinc-900/95 border-rose-500/40 text-rose-300 shadow-rose-950/50'
                : toast.type === 'warning'
                ? 'bg-zinc-900/95 border-amber-500/40 text-amber-300 shadow-amber-950/50'
                : 'bg-zinc-900/95 border-zinc-700/60 text-zinc-200 shadow-black/60'
            }`}
          >
            <div className="shrink-0 mt-0.5">
              {toast.type === 'success' && <CheckCircle2 className="h-5 w-5 text-emerald-400" />}
              {toast.type === 'error' && <XCircle className="h-5 w-5 text-rose-400" />}
              {toast.type === 'warning' && <AlertCircle className="h-5 w-5 text-amber-400" />}
              {toast.type === 'info' && <Info className="h-5 w-5 text-sky-400" />}
            </div>

            <div className="flex-1 text-xs">
              {toast.title && <div className="font-bold text-sm mb-0.5 font-['Outfit']">{toast.title}</div>}
              <div className="font-mono leading-relaxed opacity-95">{toast.message}</div>
            </div>

            <button
              onClick={() => removeToast(toast.id)}
              className="shrink-0 text-zinc-400 hover:text-white transition-colors p-1 rounded-md hover:bg-zinc-800/60"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>

      {/* Theme-Matching Confirmation Modal */}
      {confirmState && confirmState.isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-md animate-in fade-in duration-200">
          <div
            className={`max-w-md w-full rounded-2xl border p-6 shadow-2xl transition-all duration-300 transform scale-100 ${
              isLight
                ? 'bg-white border-slate-200 text-slate-900 shadow-slate-900/20'
                : 'bg-zinc-900 border-zinc-800 text-white shadow-black/80'
            }`}
          >
            <div className="flex items-start gap-4">
              <div
                className={`p-3 rounded-xl border ${
                  confirmState.options.type === 'danger'
                    ? 'bg-rose-500/10 border-rose-500/30 text-rose-400'
                    : confirmState.options.type === 'warning'
                    ? 'bg-amber-500/10 border-amber-500/30 text-amber-400'
                    : 'bg-sky-500/10 border-sky-500/30 text-sky-400'
                }`}
              >
                <AlertCircle className="h-6 w-6" />
              </div>
              <div className="flex-1">
                <h3 className="text-base font-bold font-['Outfit'] tracking-tight">
                  {confirmState.options.title}
                </h3>
                <p
                  className={`text-xs mt-1.5 leading-relaxed ${
                    isLight ? 'text-slate-600' : 'text-zinc-400'
                  }`}
                >
                  {confirmState.options.message}
                </p>
              </div>
            </div>

            <div className="mt-6 flex items-center justify-end gap-3">
              <button
                onClick={() => handleConfirm(false)}
                className={`px-4 py-2 text-xs font-bold rounded-xl border transition-all ${
                  isLight
                    ? 'bg-slate-100 hover:bg-slate-200 border-slate-200 text-slate-700'
                    : 'bg-zinc-800 hover:bg-zinc-700 border-zinc-700 text-zinc-300'
                }`}
              >
                {confirmState.options.cancelText || 'Cancel'}
              </button>
              <button
                onClick={() => handleConfirm(true)}
                className={`px-4 py-2 text-xs font-bold rounded-xl transition-all shadow-md ${
                  confirmState.options.type === 'danger'
                    ? 'bg-rose-600 hover:bg-rose-500 text-white'
                    : 'bg-blue-600 hover:bg-blue-500 text-white'
                }`}
              >
                {confirmState.options.confirmText || 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </ToastContext.Provider>
  );
};

export const useToast = (): ToastContextType => {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
};

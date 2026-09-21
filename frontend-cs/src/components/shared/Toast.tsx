"use client";

import { useEffect, useState, useCallback, createContext, useContext } from "react";

type ToastType = "info" | "success" | "warning" | "error";

interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}

interface ToastContextValue {
  show: (message: string, type?: ToastType, duration?: number) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  warning: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // 兜底：未 Provider 时用 alert 兼容老代码
    return {
      show: (m, t) => { if (typeof window !== "undefined") console.log(`[Toast ${t}]`, m); },
      success: (m) => { if (typeof window !== "undefined") console.log("[Toast success]", m); },
      error: (m) => { if (typeof window !== "undefined") console.error("[Toast error]", m); },
      warning: (m) => { if (typeof window !== "undefined") console.warn("[Toast warning]", m); },
      info: (m) => { if (typeof window !== "undefined") console.info("[Toast info]", m); },
    };
  }
  return ctx;
}

const COLORS: Record<ToastType, { bg: string; border: string; icon: string }> = {
  info:    { bg: "bg-blue-50",   border: "border-blue-200",   icon: "ℹ️" },
  success: { bg: "bg-emerald-50", border: "border-emerald-200", icon: "✅" },
  warning: { bg: "bg-amber-50",   border: "border-amber-200",   icon: "⚠️" },
  error:   { bg: "bg-red-50",     border: "border-red-200",     icon: "❌" },
};

/**
 * Toast Provider + Container：在 layout 顶层放一次即可
 * 使用：const toast = useToast(); toast.success("已保存");
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const remove = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback((message: string, type: ToastType = "info", duration = 3000) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    setToasts((prev) => [...prev, { id, type, message, duration }]);
    if (duration > 0) {
      setTimeout(() => remove(id), duration);
    }
  }, [remove]);

  const ctx: ToastContextValue = {
    show,
    success: (m) => show(m, "success"),
    error: (m) => show(m, "error", 5000),
    warning: (m) => show(m, "warning"),
    info: (m) => show(m, "info"),
  };

  return (
    <ToastContext.Provider value={ctx}>
      {children}
      <ToastContainer toasts={toasts} onRemove={remove} />
    </ToastContext.Provider>
  );
}

function ToastContainer({
  toasts,
  onRemove,
}: {
  toasts: ToastItem[];
  onRemove: (id: string) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => {
        const c = COLORS[t.type];
        return (
          <div
            key={t.id}
            className={`flex items-start gap-2 ${c.bg} border ${c.border} rounded-lg px-3 py-2 shadow-sm animate-in fade-in slide-in-from-top-2`}
          >
            <span className="text-sm shrink-0">{c.icon}</span>
            <p className="text-xs text-slate-700 flex-1">{t.message}</p>
            <button
              onClick={() => onRemove(t.id)}
              className="text-slate-400 hover:text-slate-600 text-xs shrink-0"
            >
              ✕
            </button>
          </div>
        );
      })}
    </div>
  );
}
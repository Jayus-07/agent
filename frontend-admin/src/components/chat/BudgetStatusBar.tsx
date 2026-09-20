"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, LockKeyhole, ShieldCheck } from "lucide-react";
import { budgetTone, formatUsd, type BudgetStatus } from "@/api/budgets";
import { request } from "@/api/client";

export default function BudgetStatusBar({ onBlockedChange }: { onBlockedChange?: (blocked: boolean) => void }) {
  const [status, setStatus] = useState<BudgetStatus | null>(null);
  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try { const next = await request<BudgetStatus>("/api/budgets/me"); if (!disposed) setStatus(next); } catch { /* 管理聊天不因预算接口不可用而阻塞 */ }
    };
    void load();
    const timer = setInterval(load, 60_000);
    return () => { disposed = true; clearInterval(timer); };
  }, []);
  const ratio = Math.max(status?.daily?.ratio ?? 0, status?.monthly?.ratio ?? 0);
  const tone = status ? budgetTone({ ratio, enforcement: status.enforcement, blocked: status.blocked || status.tenant_blocked }) : "normal";
  useEffect(() => { onBlockedChange?.(tone === "blocked"); }, [onBlockedChange, tone]);
  if (!status) return null;
  const window = (status.daily?.ratio ?? 0) >= (status.monthly?.ratio ?? 0) ? status.daily : status.monthly;
  const Icon = tone === "blocked" ? LockKeyhole : tone === "normal" ? ShieldCheck : AlertTriangle;
  return (
    <div className={`mx-5 mt-3 rounded-xl border px-3 py-2 text-xs ${tone === "blocked" ? "border-red-200 bg-red-50 text-red-800" : tone === "warning" ? "border-amber-200 bg-amber-50 text-amber-900" : "border-slate-200 bg-slate-50 text-slate-700"}`} role="status">
      <div className="flex items-center gap-2"><Icon size={14} /><span className="font-medium">预算 {formatUsd(window?.used)} / {formatUsd(window?.limit)}</span><span className="ml-auto">{tone === "blocked" ? "硬额度已阻断" : tone === "warning" ? "接近额度" : "状态正常"}</span></div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-black/10"><div className="h-full rounded-full bg-current" style={{ width: `${Math.min(100, ratio * 100)}%` }} /></div>
    </div>
  );
}

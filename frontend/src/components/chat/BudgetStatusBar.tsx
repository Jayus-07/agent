"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, LockKeyhole, ShieldCheck } from "lucide-react";
import { budgetTone, formatUsd, getBudgetMe, type BudgetStatus } from "@/api/budgets";

export default function BudgetStatusBar({ onBlockedChange }: { onBlockedChange?: (blocked: boolean) => void }) {
  const [status, setStatus] = useState<BudgetStatus | null>(null);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      try {
        const next = await getBudgetMe();
        if (disposed) return;
        setStatus(next);
        const urgent = Math.max(next.daily?.ratio ?? 0, next.monthly?.ratio ?? 0) >= 0.8;
        timer = setTimeout(load, urgent ? 15_000 : 60_000);
      } catch {
        if (!disposed) timer = setTimeout(load, 60_000);
      }
    };
    void load();
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, []);

  const tone = useMemo(() => status ? budgetTone({
    ratio: Math.max(status.daily?.ratio ?? 0, status.monthly?.ratio ?? 0),
    enforcement: status.enforcement,
    blocked: status.blocked || status.tenant_blocked,
  }) : "normal", [status]);

  useEffect(() => { onBlockedChange?.(tone === "blocked"); }, [onBlockedChange, tone]);
  if (!status) return null;

  const window = (status.daily?.ratio ?? 0) >= (status.monthly?.ratio ?? 0) ? status.daily : status.monthly;
  const period = window === status.daily ? "今日" : "本月";
  const label = tone === "blocked"
    ? "已达到硬额度，模型调用与写操作已暂停"
    : tone === "warning"
      ? "预算已使用 80% 以上"
      : tone === "soft"
        ? "测试环境软额度：超额仍可继续"
        : tone === "audit"
          ? "审计豁免：本次仅记录，不代表无限额度"
          : "预算状态正常";
  const Icon = tone === "blocked" ? LockKeyhole : tone === "warning" || tone === "soft" ? AlertTriangle : ShieldCheck;
  const colors = {
    blocked: "border-red-200 bg-red-50 text-red-800",
    warning: "border-amber-200 bg-amber-50 text-amber-900",
    soft: "border-sky-200 bg-sky-50 text-sky-900",
    audit: "border-slate-200 bg-slate-50 text-slate-700",
    normal: "border-indigo-100 bg-indigo-50 text-indigo-900",
  }[tone];
  return (
    <div className={`mx-5 mt-3 rounded-xl border px-3 py-2.5 ${colors}`} role="status" aria-label="预算状态">
      <div className="flex items-center gap-2">
        <Icon size={15} aria-hidden="true" />
        <span className="text-xs font-medium">{label}</span>
        <span className="ml-auto text-xs tabular-nums">{period} {formatUsd(window?.used)} / {formatUsd(window?.limit)}</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-black/10" aria-hidden="true">
        <div className={`h-full rounded-full ${tone === "blocked" ? "bg-red-500" : tone === "warning" ? "bg-amber-500" : "bg-indigo-500"}`} style={{ width: `${Math.min(100, Math.max(0, (window?.ratio ?? 0) * 100))}%` }} />
      </div>
      <p className="mt-1 text-[10px] opacity-75">下次重置：{new Date(window?.reset_at ?? "").toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</p>
    </div>
  );
}

"use client";

/**
 * useBudgetStatus — 预算状态轮询（每页单一数据源）
 *
 * 从 BudgetStatusBar 抽出的取数逻辑：常规 60s 轮询，任一窗口用量 ≥80%
 * 收紧到 15s；tone 变为 blocked 时经 onBlockedChange 上抛（调用方借此
 * 禁用输入）。展示组件见 BudgetRing（输入框内圆圈指示器）。
 */
import { useEffect, useMemo, useState } from "react";
import { budgetTone, getBudgetMe, type BudgetStatus } from "@/api/budgets";

export type BudgetTone = ReturnType<typeof budgetTone>;

export function useBudgetStatus(
  onBlockedChange?: (blocked: boolean) => void,
): { status: BudgetStatus | null; tone: BudgetTone } {
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

  return { status, tone };
}

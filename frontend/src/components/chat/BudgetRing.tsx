"use client";

/**
 * BudgetRing — 输入框内的预算状态圆圈指示器
 *
 * 取代原顶部横幅（BudgetStatusBar，已随本组件迁移删除）：环形弧度 =
 * 较紧窗口的已用比例，中心显示百分比；hover/键盘聚焦弹层展示今日/本月
 * 用量、执行模式与额度来源。纯 CSS hover 弹层（项目无 tooltip 依赖），
 * tabIndex 保证触屏与键盘可达。数据由 useBudgetStatus 轮询提供。
 */
import { budgetTone, formatCny, type BudgetStatus } from "@/api/budgets";
import type { BudgetTone } from "@/hooks/useBudgetStatus";

const TONE_ARC: Record<BudgetTone, string> = {
  blocked: "stroke-red-500",
  warning: "stroke-amber-500",
  soft: "stroke-sky-500",
  audit: "stroke-slate-400",
  normal: "stroke-indigo-500",
};

const TONE_LABEL: Record<BudgetTone, string> = {
  blocked: "已达到硬额度，模型调用与写操作已暂停",
  warning: "预算已使用 80% 以上",
  soft: "测试环境软额度：超额仍可继续",
  audit: "审计豁免：本次仅记录，不代表无限额度",
  normal: "预算状态正常",
};

function WindowRow({ label, win }: { label: string; win?: BudgetStatus["daily"] }) {
  const ratio = Math.min(1, Math.max(0, win?.ratio ?? 0));
  return (
    <div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-text-secondary">{label}</span>
        <span className="tabular-nums font-medium">
          {formatCny(win?.used)} / {formatCny(win?.limit)}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-black/10" aria-hidden>
        <div className="h-full rounded-full bg-accent" style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}

export default function BudgetRing({
  status,
  tone = "normal",
}: {
  status: BudgetStatus | null;
  tone?: BudgetTone;
}) {
  if (!status) return null;
  const daily = status.daily;
  const monthly = status.monthly;
  const ratio = Math.max(daily?.ratio ?? 0, monthly?.ratio ?? 0);
  const arc = Math.min(1, Math.max(0, ratio));
  const tighter = (daily?.ratio ?? 0) >= (monthly?.ratio ?? 0) ? daily : monthly;
  const periodLabel = tighter === daily ? "今日" : "本月";
  const R = 9;
  const C = 2 * Math.PI * R;

  return (
    <div className="relative group shrink-0" tabIndex={0} role="status" aria-label="预算状态">
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden>
        <circle cx="11" cy="11" r={R} fill="none" strokeWidth="2.5" className="stroke-black/10" />
        <circle
          cx="11" cy="11" r={R} fill="none" strokeWidth="2.5" strokeLinecap="round"
          className={TONE_ARC[tone]}
          strokeDasharray={`${arc * C} ${C}`}
          transform="rotate(-90 11 11)"
        />
        <text
          x="11" y="11" textAnchor="middle" dominantBaseline="central"
          className="fill-current text-[7px] font-semibold tabular-nums text-text-secondary"
        >
          {Math.round(ratio * 100)}%
        </text>
      </svg>

      {/* hover / 聚焦弹层：向上展开（输入框贴近视口底部） */}
      <div
        className="hidden group-hover:block group-focus-within:block absolute bottom-full mb-2 left-0 z-50
          w-64 rounded-xl border border-black/10 bg-white p-3 shadow-lg"
        role="tooltip"
      >
        <p className={`text-xs font-semibold ${tone === "blocked" ? "text-red-600" : tone === "warning" ? "text-amber-600" : "text-text-primary"}`}>
          {TONE_LABEL[tone]}
        </p>
        <div className="mt-2.5 space-y-2.5">
          <WindowRow label="今日" win={daily} />
          <WindowRow label="本月" win={monthly} />
        </div>
        <dl className="mt-2.5 space-y-1 text-[11px] text-text-secondary">
          <div className="flex justify-between">
            <dt>执行模式</dt>
            <dd className="text-text-primary">
              {status.enforcement === "hard" ? "hard：超限阻断" : status.enforcement === "soft" ? "soft：仅记录" : "audit：仅审计"}
              {status.audit_exempt ? "（审计豁免）" : ""}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt>额度来源</dt>
            <dd className="text-text-primary">
              {status.policy_source?.label || `${status.policy_source?.scope_type ?? "unknown"}:${status.policy_source?.scope_id ?? ""}`}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt>下次重置</dt>
            <dd className="text-text-primary tabular-nums">
              {periodLabel}{tighter?.reset_at ? new Date(tighter.reset_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "—"}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

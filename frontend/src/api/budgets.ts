import { mutationRequest, request } from "@/api/client";

export type BudgetEnforcement = "hard" | "soft" | "audit";

export interface BudgetWindow {
  used: string;
  reserved: string;
  limit: string;
  ratio: number;
  reset_at: string;
}

export interface BudgetStatus {
  currency: "CNY";
  fx_usd_cny: string;
  mode: "off" | "observe" | "enforce" | string;
  enforcement: BudgetEnforcement;
  audit_exempt: boolean;
  blocked: boolean;
  tenant_blocked: boolean;
  policy_source: { scope_type: string; scope_id: string; label?: string };
  daily: BudgetWindow;
  monthly: BudgetWindow;
  user?: { daily: BudgetWindow; monthly: BudgetWindow; blocked: boolean };
  tenant?: { daily: BudgetWindow; monthly: BudgetWindow; blocked: boolean };
}

export interface BudgetSummary {
  currency: "CNY";
  fx_usd_cny: string;
  total_cost: string;
  cost_status_counts: { priced: number; unpriced: number; price_unknown: number; missing_status: number };
  price_coverage_ratio: number;
  near_limit_subjects: number;
  blocked_subjects: number;
  unsettled_reserved: string;
}

export interface BudgetSubject {
  scope: string;
  id: string;
  display_name?: string;
  daily: BudgetWindow;
  monthly: BudgetWindow;
  enforcement: BudgetEnforcement;
  ratio: number;
  policy_source: { scope_type: string; scope_id: string; label?: string };
}

export interface BudgetPolicy {
  scope_type: "user" | "tenant" | "tenant_default" | "platform" | string;
  scope_id: string;
  daily_limit_cny: string;
  monthly_limit_cny: string;
  enforcement: BudgetEnforcement;
  timezone: string;
  audit_exempt: boolean;
  updated_by?: string;
  updated_at?: string;
}

export interface BudgetEvent {
  scope_type: string;
  scope_id: string;
  period_type: "day" | "month" | string;
  threshold: number;
  created_at: string;
}

// 金额为记账本位币 CNY（2026-10-01 起，后端定价出口统一折算）
export function formatCny(value: string | number | null | undefined): string {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? `¥${number.toFixed(2)}` : "¥0.00";
}

export function budgetTone(input: { ratio: number; enforcement: BudgetEnforcement; blocked: boolean }): "blocked" | "warning" | "soft" | "audit" | "normal" {
  if (input.enforcement === "audit") return "audit";
  if (input.enforcement === "soft" && input.ratio >= 1) return "soft";
  if (input.blocked || (input.enforcement === "hard" && input.ratio >= 1)) return "blocked";
  if (input.ratio >= 0.8) return "warning";
  return "normal";
}

const scopeLabels: Record<string, string> = {
  request: "本次请求预算",
  user: "用户",
  tenant: "租户",
  pricing: "价格覆盖",
};
const periodLabels: Record<string, string> = {
  cost: "成本上限",
  daily: "日额度",
  day: "日额度",
  monthly: "月额度",
  month: "月额度",
  missing_price: "缺价熔断",
};

export function describeBudgetExceeded(details: Record<string, unknown> = {}): string {
  const scope = String(details.budget_kind ?? "budget");
  const period = String(details.limit_kind ?? details.period_type ?? "cost");
  if (scope === "pricing") return "价格覆盖不足，暂不可计算成本；请联系管理员补齐价格版本";
  return `${scopeLabels[scope] ?? "预算"}${periodLabels[period] ?? "额度"}已达到上限`;
}

export async function getBudgetMe(): Promise<BudgetStatus> {
  return request<BudgetStatus>("/api/budgets/me");
}

export async function getBudgetSummary(): Promise<BudgetSummary> {
  return request<BudgetSummary>("/api/admin/budgets/summary");
}

export async function listBudgetSubjects(params: { scope?: string; period?: string } = {}): Promise<{ items: BudgetSubject[] }> {
  const query = new URLSearchParams();
  if (params.scope) query.set("scope", params.scope);
  if (params.period) query.set("period", params.period);
  return request<{ items: BudgetSubject[] }>(`/api/admin/budgets/subjects?${query.toString()}`);
}

export async function listBudgetPolicies(): Promise<{ items: BudgetPolicy[] }> {
  return request<{ items: BudgetPolicy[] }>("/api/admin/budgets/policies");
}

export async function listBudgetEvents(): Promise<{ items: BudgetEvent[] }> {
  return request<{ items: BudgetEvent[] }>("/api/admin/budgets/events");
}

export async function saveBudgetPolicy(
  scope: string,
  id: string,
  body: Pick<BudgetPolicy, "daily_limit_cny" | "monthly_limit_cny" | "enforcement" | "audit_exempt"> & { reason: string; expected_updated_at?: string },
): Promise<BudgetPolicy> {
  const result = await mutationRequest<{ policy: BudgetPolicy }>(`/api/admin/budgets/policies/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`, {
    operation: `budget-policy:${scope}:${id}`,
    method: "PUT",
    body,
  });
  return result.policy;
}

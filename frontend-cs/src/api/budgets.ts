import { mutationRequest, request } from "@/api/client";

export type BudgetEnforcement = "hard" | "soft" | "audit";
export interface BudgetWindow { used: string; reserved: string; limit: string; ratio: number; reset_at: string }
export interface BudgetStatus {
  currency: "USD"; mode: string; enforcement: BudgetEnforcement; audit_exempt: boolean;
  blocked: boolean; tenant_blocked: boolean;
  policy_source: { scope_type: string; scope_id: string; label?: string };
  daily: BudgetWindow; monthly: BudgetWindow;
}
export interface BudgetSummary {
  currency: "USD"; total_cost_usd: string; price_coverage_ratio: number;
  near_limit_subjects: number; blocked_subjects: number; unsettled_reserved_usd: string;
}
export interface BudgetSubject {
  scope: string; id: string; display_name?: string; daily: BudgetWindow; monthly: BudgetWindow;
  enforcement: BudgetEnforcement; ratio: number;
  explicit_policy?: BudgetPolicyView | null; effective_policy?: BudgetPolicyView | null;
  policy_source: { scope_type: string; scope_id: string; label?: string };
}
export interface BudgetPolicyView {
  scope_type: string; scope_id: string; daily_limit_usd: string; monthly_limit_usd: string;
  enforcement: BudgetEnforcement; timezone: string; audit_exempt: boolean;
}
export interface BudgetPolicy {
  scope_type: string; scope_id: string; daily_limit_usd: string; monthly_limit_usd: string;
  enforcement: BudgetEnforcement; timezone: string; audit_exempt: boolean;
  updated_by?: string; updated_at?: string;
}
export interface BudgetEvent { scope_type: string; scope_id: string; period_type: string; threshold: number; created_at: string }
export interface BudgetPolicyAudit { scope_type: string; scope_id: string; before_value: Record<string, unknown> | null; after_value: Record<string, unknown>; reason: string; updated_by: string; created_at: string }

export function formatUsd(value: string | number | null | undefined): string {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? `$${number.toFixed(2)}` : "$0.00";
}

export function budgetTone(input: { ratio: number; enforcement: BudgetEnforcement; blocked: boolean }): "blocked" | "warning" | "soft" | "audit" | "normal" {
  if (input.enforcement === "audit") return "audit";
  if (input.enforcement === "soft" && input.ratio >= 1) return "soft";
  if (input.blocked || (input.enforcement === "hard" && input.ratio >= 1)) return "blocked";
  if (input.ratio >= 0.8) return "warning";
  return "normal";
}

export function describeBudgetExceeded(details: Record<string, unknown> = {}): string {
  const scope = String(details.budget_kind ?? "预算");
  const period = String(details.limit_kind ?? details.period_type ?? "额度");
  if (scope === "pricing") return "价格覆盖不足，暂不可计算成本；请联系管理员补齐价格版本";
  const scopeLabel = scope === "request" ? "本次请求预算" : scope === "user" ? "用户" : scope === "tenant" ? "租户" : scope;
  const periodLabel = period === "daily" || period === "day" ? "日额度" : period === "monthly" || period === "month" ? "月额度" : period;
  return `${scopeLabel}${periodLabel}已达到上限`;
}

export async function getBudgetSummary(): Promise<BudgetSummary> { return request<BudgetSummary>("/api/admin/budgets/summary"); }
export async function listBudgetSubjects(params: { scope?: string; period?: string } = {}): Promise<{ items: BudgetSubject[] }> {
  const query = new URLSearchParams();
  if (params.scope) query.set("scope", params.scope);
  if (params.period) query.set("period", params.period);
  return request<{ items: BudgetSubject[] }>(`/api/admin/budgets/subjects?${query.toString()}`);
}
export async function listBudgetPolicies(): Promise<{ items: BudgetPolicy[] }> { return request<{ items: BudgetPolicy[] }>("/api/admin/budgets/policies"); }
export async function listBudgetEvents(): Promise<{ items: BudgetEvent[] }> { return request<{ items: BudgetEvent[] }>("/api/admin/budgets/events"); }
export async function listBudgetPolicyAudit(): Promise<{ items: BudgetPolicyAudit[] }> { return request<{ items: BudgetPolicyAudit[] }>("/api/admin/budgets/audit"); }
export async function saveBudgetPolicy(scope: string, id: string, body: Record<string, unknown>): Promise<BudgetPolicy> {
  const result = await mutationRequest<{ policy: BudgetPolicy }>(`/api/admin/budgets/policies/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`, {
    operation: `budget-policy:${scope}:${id}`, method: "PUT", body,
  });
  return result.policy;
}

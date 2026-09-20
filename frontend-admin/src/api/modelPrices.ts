import { mutationRequest, request } from "@/api/client";

export type PriceDimension = "input" | "output" | "cache_read" | "cache_write" | "reasoning" | "tool_call";
export type PriceUnit = "per_1m_tokens" | "per_call";
export interface PriceRow {
  model_name: string; component: "llm" | "embedding" | "rerank"; dimension: PriceDimension;
  price_per_unit: string; unit: PriceUnit; currency: "USD" | string;
  effective_to?: string | null;
}
export interface PriceVersion {
  version: string; source: string; imported_by?: string; status: "pending" | "reviewed_1" | "scheduled" | "canary" | "active" | "rejected" | "expired" | string;
  reviewer_1?: string | null; reviewer_2?: string | null; effective_from?: string; effective_to?: string | null;
  coverage_ratio: number; missing_price_count?: number; price_calculation_error_count?: number; missing?: Array<{ model_name: string; component: string; dimension: string }>;
  created_at: string;
}
export interface PriceValidation { valid: boolean; errors: string[]; rows: PriceRow[] }

const dimensions = new Set<PriceDimension>(["input", "output", "cache_read", "cache_write", "reasoning", "tool_call"]);
const units = new Set<PriceUnit>(["per_1m_tokens", "per_call"]);

export function validatePriceRows(rows: PriceRow[]): PriceValidation {
  const errors: string[] = [];
  const keys = new Set<string>();
  rows.forEach((row, index) => {
    const key = `${row.model_name}:${row.component}:${row.dimension}`;
    if (row.currency !== "USD") errors.push(`第 ${index + 1} 行必须使用 USD`);
    if (!dimensions.has(row.dimension)) errors.push(`第 ${index + 1} 行维度非法`);
    if (!units.has(row.unit)) errors.push(`第 ${index + 1} 行单位非法`);
    if (row.dimension === "tool_call" && row.unit !== "per_call") errors.push(`第 ${index + 1} 行 tool_call 必须按 call 计价`);
    if (row.dimension !== "tool_call" && row.unit !== "per_1m_tokens") errors.push(`第 ${index + 1} 行 token 维度必须按 1M token 计价`);
    const price = Number(row.price_per_unit);
    if (!Number.isFinite(price) || price < 0) errors.push(`第 ${index + 1} 行价格不能为负数或非数字`);
    if (keys.has(key)) errors.push(`复合键重复：${key}`);
    keys.add(key);
  });
  return { valid: errors.length === 0, errors, rows };
}

export function formatPrice(value: string | number): string {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(6) : "0.000000";
}

export async function listPriceVersions(): Promise<{ items: PriceVersion[] }> {
  return request<{ items: PriceVersion[] }>("/api/admin/model-prices/versions");
}
export async function validatePriceImport(body: { version: string; source: string; rows: PriceRow[] }): Promise<PriceValidation> {
  const result = await request<{
    version: string;
    source: string;
    valid: boolean;
    errors: Array<string | { message?: string }>;
    rows: PriceRow[];
  }>(
    "/api/admin/model-prices/imports/validate",
    { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } },
  );
  return {
    ...result,
    errors: result.errors.map((item) => typeof item === "string" ? item : item.message ?? "价格行校验失败"),
  };
}
export async function importPriceVersion(body: { version: string; source: string; effective_from?: string; rows: PriceRow[] }): Promise<{ version: string; status: string; row_count: number }> {
  return mutationRequest<{ version: string; status: string; row_count: number }>("/api/admin/model-prices/imports", { operation: `price-import:${body.version}`, method: "POST", body });
}
export async function reviewPriceVersion(version: string, decision: "approve" | "reject", reason: string): Promise<{ version: string; status: string; reviewer: string }> {
  return mutationRequest<{ version: string; status: string; reviewer: string }>(`/api/admin/model-prices/versions/${encodeURIComponent(version)}/reviews`, { operation: `price-review:${version}:${decision}`, method: "POST", body: { decision, reason } });
}
export async function runPriceCanary(version: string, action: "start" | "complete"): Promise<{ version: string; status: string; row_count?: number }> {
  return mutationRequest<{ version: string; status: string; row_count?: number }>(`/api/admin/model-prices/versions/${encodeURIComponent(version)}/canary`, { operation: `price-canary:${version}:${action}`, method: "POST", body: { action } });
}

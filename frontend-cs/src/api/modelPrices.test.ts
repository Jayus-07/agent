import { describe, expect, it, vi } from "vitest";
import { request } from "@/api/client";
import { validatePriceImport, validatePriceRows } from "./modelPrices";

vi.mock("@/api/client", () => ({
  request: vi.fn(),
  mutationRequest: vi.fn(),
}));

describe("价格导入校验", () => {
  it("拒绝非 USD、负数、非法单位和复合键重复", () => {
    expect(validatePriceRows([
      { model_name: "m", component: "llm", dimension: "input", price_per_unit: "1", unit: "per_1m_tokens", currency: "EUR" },
    ]).valid).toBe(false);
    expect(validatePriceRows([
      { model_name: "m", component: "llm", dimension: "input", price_per_unit: "-1", unit: "per_1m_tokens", currency: "USD" },
    ]).valid).toBe(false);
    expect(validatePriceRows([
      { model_name: "m", component: "llm", dimension: "input", price_per_unit: "1", unit: "per_call", currency: "USD" },
    ]).valid).toBe(false);
    expect(validatePriceRows([
      { model_name: "m", component: "llm", dimension: "input", price_per_unit: "1", unit: "per_1m_tokens", currency: "USD" },
      { model_name: "m", component: "llm", dimension: "input", price_per_unit: "2", unit: "per_1m_tokens", currency: "USD" },
    ]).valid).toBe(false);
  });

  it("接受六位 USD token 价格", () => {
    expect(validatePriceRows([
      { model_name: "m", component: "llm", dimension: "input", price_per_unit: "0.125000", unit: "per_1m_tokens", currency: "USD" },
    ])).toMatchObject({ valid: true, errors: [] });
  });

  it("服务端校验价格导入内容，不只依赖浏览器本地校验", async () => {
    vi.mocked(request).mockResolvedValueOnce({
      version: "v1",
      source: "official",
      valid: true,
      errors: [],
      rows: [],
    });
    const body = {
      version: "v1",
      source: "official",
      rows: [{
        model_name: "m",
        component: "llm" as const,
        dimension: "input" as const,
        price_per_unit: "1.000000",
        unit: "per_1m_tokens" as const,
        currency: "USD" as const,
      }],
    };

    await expect(validatePriceImport(body)).resolves.toMatchObject({ valid: true });
    expect(request).toHaveBeenCalledWith(
      "/api/admin/model-prices/imports/validate",
      { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } },
    );
  });
});

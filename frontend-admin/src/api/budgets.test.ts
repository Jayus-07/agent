import { describe, expect, it } from "vitest";
import { budgetTone, describeBudgetExceeded, formatUsd } from "./budgets";

describe("管理端预算展示口径", () => {
  it("金额只显示 USD 两位", () => {
    expect(formatUsd("60.000000")).toBe("$60.00");
  });

  it("soft 和 audit 超额仍保留可执行状态", () => {
    expect(budgetTone({ ratio: 1.1, enforcement: "soft", blocked: false })).toBe("soft");
    expect(budgetTone({ ratio: 1.1, enforcement: "audit", blocked: false })).toBe("audit");
  });

  it("缺价单独说明", () => {
    expect(describeBudgetExceeded({ budget_kind: "pricing", limit_kind: "missing_price" })).toContain("价格");
  });
});

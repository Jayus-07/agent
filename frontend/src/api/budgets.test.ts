import { describe, expect, it } from "vitest";
import { budgetTone, describeBudgetExceeded, formatCny } from "./budgets";

describe("预算展示口径", () => {
  it("金额字符串固定显示两位人民币（记账本位币 CNY）", () => {
    expect(formatCny("3.000000")).toBe("¥3.00");
    expect(formatCny("0.000001")).toBe("¥0.00");
  });

  it("hard 100% 阻断、80% 预警、soft/audit 不显示无限", () => {
    expect(budgetTone({ ratio: 1, enforcement: "hard", blocked: true })).toBe("blocked");
    expect(budgetTone({ ratio: 0.8, enforcement: "hard", blocked: false })).toBe("warning");
    expect(budgetTone({ ratio: 1.2, enforcement: "soft", blocked: false })).toBe("soft");
    expect(budgetTone({ ratio: 1.2, enforcement: "audit", blocked: false })).toBe("audit");
  });

  it("四类预算超限文案互不相同", () => {
    const texts = [
      describeBudgetExceeded({ budget_kind: "request", limit_kind: "cost" }),
      describeBudgetExceeded({ budget_kind: "user", limit_kind: "daily" }),
      describeBudgetExceeded({ budget_kind: "tenant", limit_kind: "monthly" }),
      describeBudgetExceeded({ budget_kind: "pricing", limit_kind: "missing_price" }),
    ];
    expect(new Set(texts).size).toBe(4);
    expect(texts.join(" ")).not.toContain("无限");
  });
});

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  filterByTimeRange,
  formatRelative,
  formatCost,
  formatTime,
  truncate,
  durationColor,
  durationBg,
  statusBadge,
  statusDot,
  stepColor,
  severityStyle,
} from "./trace";

afterEach(() => {
  vi.useRealTimers();
});

describe("filterByTimeRange", () => {
  const now = new Date("2026-07-16T05:00:00Z");

  it.each([
    { range: "15m", agoMin: 5, expected: true },
    { range: "15m", agoMin: 14, expected: true },
    { range: "15m", agoMin: 16, expected: false },
    { range: "1h", agoMin: 30, expected: true },
    { range: "1h", agoMin: 59, expected: true },
    { range: "1h", agoMin: 61, expected: false },
    { range: "6h", agoMin: 300, expected: true },
    { range: "6h", agoMin: 400, expected: false },
    { range: "24h", agoMin: 1400, expected: true },
    { range: "24h", agoMin: 1500, expected: false },
  ])("$range 范围内 $agoMin 分钟前 → expected=$expected", ({ range, agoMin, expected }) => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const ts = new Date(now.getTime() - agoMin * 60_000).toISOString();
    const result = filterByTimeRange([{ id: "t", timestamp: ts }], range as any);
    expect(result).toHaveLength(expected ? 1 : 0);
  });

  it("'custom' 范围不过滤，返回全部", () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const traces = [
      { id: "1", timestamp: new Date(now.getTime() - 10 * 365 * 24 * 3600_000).toISOString() }, // 10 年前
      { id: "2", timestamp: new Date(now.getTime() - 1_000).toISOString() },
    ];
    expect(filterByTimeRange(traces, "custom")).toHaveLength(2);
  });

  it("未来时间戳应保留（不爆炸）", () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const future = [{ id: "f", timestamp: new Date(now.getTime() + 600_000).toISOString() }];
    expect(filterByTimeRange(future, "15m")).toHaveLength(1);
  });

  it("空数组返回空", () => {
    expect(filterByTimeRange([], "24h")).toEqual([]);
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-07-16T05:00:00Z");

  it("0 秒前", () => {
    expect(formatRelative(now.toISOString(), now)).toBe("0 秒前");
  });

  it("30 秒前", () => {
    const t = new Date(now.getTime() - 30_000);
    expect(formatRelative(t.toISOString(), now)).toBe("30 秒前");
  });

  it("59 秒 → 59 秒前", () => {
    const t = new Date(now.getTime() - 59_000);
    expect(formatRelative(t.toISOString(), now)).toBe("59 秒前");
  });

  it("60 秒 → 1 分钟前（边界）", () => {
    const t = new Date(now.getTime() - 60_000);
    expect(formatRelative(t.toISOString(), now)).toBe("1 分钟前");
  });

  it("59 分钟 → 59 分钟前", () => {
    const t = new Date(now.getTime() - 59 * 60_000);
    expect(formatRelative(t.toISOString(), now)).toBe("59 分钟前");
  });

  it("60 分钟 → 1 小时前", () => {
    const t = new Date(now.getTime() - 60 * 60_000);
    expect(formatRelative(t.toISOString(), now)).toBe("1 小时前");
  });

  it("23 小时 → 23 小时前", () => {
    const t = new Date(now.getTime() - 23 * 3600_000);
    expect(formatRelative(t.toISOString(), now)).toBe("23 小时前");
  });

  it("24 小时 → 1 天前", () => {
    const t = new Date(now.getTime() - 24 * 3600_000);
    expect(formatRelative(t.toISOString(), now)).toBe("1 天前");
  });

  it("空字符串返回 --", () => {
    expect(formatRelative("", now)).toBe("--");
  });
});

describe("formatCost", () => {
  it("undefined → --", () => {
    expect(formatCost(undefined)).toBe("--");
  });

  it("null → --", () => {
    expect(formatCost(null)).toBe("--");
  });

  it("0 → $0.00", () => {
    expect(formatCost(0)).toBe("$0.00");
  });

  it("< 0.0001 → 微单位 ($Xµ)", () => {
    expect(formatCost(0.00005)).toBe("$50.00µ");
  });

  it("0.0001 → $0.0001", () => {
    expect(formatCost(0.0001)).toBe("$0.0001");
  });

  it("< 1 → 4 位小数", () => {
    expect(formatCost(0.0005)).toBe("$0.0005");
    expect(formatCost(0.5)).toBe("$0.5000");
  });

  it(">= 1 → 2 位小数", () => {
    expect(formatCost(1)).toBe("$1.00");
    expect(formatCost(123.456)).toBe("$123.46");
  });
});

describe("formatTime", () => {
  it("空字符串 → --", () => {
    expect(formatTime("")).toBe("--");
  });

  it("合法 ISO → 时:分:秒.毫秒", () => {
    const result = formatTime("2026-07-16T05:23:45.678Z");
    // 时区相关，只校验结构 HH:MM:SS.mmm
    expect(result).toMatch(/^\d{2}:\d{2}:\d{2}\.\d{3}$/);
  });
});

describe("truncate", () => {
  it("短于 n → 原样", () => {
    expect(truncate("abc", 10)).toBe("abc");
  });

  it("等于 n → 原样", () => {
    expect(truncate("abcde", 5)).toBe("abcde");
  });

  it("长于 n → 截断 + ...", () => {
    expect(truncate("abcdefghij", 5)).toBe("abcde...");
  });

  it("空 → --", () => {
    expect(truncate("", 5)).toBe("--");
  });
});

describe("durationColor", () => {
  it("<= 2000ms → 绿色", () => {
    expect(durationColor(0)).toBe("text-emerald-500");
    expect(durationColor(2000)).toBe("text-emerald-500");
  });

  it("2000 < ms <= 5000 → 琥珀", () => {
    expect(durationColor(2001)).toBe("text-amber-500");
    expect(durationColor(5000)).toBe("text-amber-500");
  });

  it("> 5000ms → 红色", () => {
    expect(durationColor(5001)).toBe("text-red-500");
  });
});

describe("durationBg", () => {
  it("<= 2000ms → 空字符串", () => {
    expect(durationBg(1000)).toBe("");
  });

  it("2000-5000ms → amber bg", () => {
    expect(durationBg(3000)).toBe("bg-amber-50 border-amber-200");
  });

  it("> 5000ms → red bg", () => {
    expect(durationBg(6000)).toBe("bg-red-50 border-red-200");
  });
});

describe("statusBadge", () => {
  it.each([
    ["success", "SUCCESS"],
    ["error", "ERROR"],
    ["timeout", "TIMEOUT"],
    ["cancelled", "CANCELLED"],
    ["skipped", "SKIPPED"],
    ["unknown", "UNKNOWN"],
  ] as const)("状态 %s → 标签 %s", (status, expected) => {
    expect(statusBadge(status).label).toBe(expected);
  });
});

describe("statusDot", () => {
  it("success → emerald", () => {
    expect(statusDot("success")).toContain("emerald");
  });

  it("error → red", () => {
    expect(statusDot("error")).toContain("red");
  });

  it("未知 → slate-300", () => {
    expect(statusDot("unknown")).toContain("slate-300");
  });
});

describe("stepColor", () => {
  it("skipped → 虚线灰", () => {
    expect(stepColor("skipped", 100)).toContain("dashed");
  });

  it("error → red-400", () => {
    expect(stepColor("error", 100)).toBe("bg-red-400");
  });

  it("> 1000ms → amber", () => {
    expect(stepColor("success", 1500)).toBe("bg-amber-500");
  });

  it("正常 ≤ 1000ms → violet", () => {
    expect(stepColor("success", 500)).toBe("bg-violet-500");
  });
});

describe("severityStyle", () => {
  it("critical → CRITICAL 红", () => {
    expect(severityStyle("critical").label).toBe("CRITICAL");
    expect(severityStyle("critical").bg).toContain("red");
  });

  it("error → ERROR 橙", () => {
    expect(severityStyle("error").label).toBe("ERROR");
  });

  it("warning → WARNING 琥珀", () => {
    expect(severityStyle("warning").label).toBe("WARNING");
  });
});
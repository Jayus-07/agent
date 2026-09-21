import { describe, expect, it } from "vitest";

/** Mirror of UploadDialog.fmtMs — 不可 import component 故复刻一份 */
function fmtMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "0μs";
  if (ms < 0.001) return `${Math.round(ms * 1_000_000)}ns`;
  if (ms < 1) return `${(ms * 1000).toFixed(1)}μs`;
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

describe("fmtMs 耗时格式化", () => {
  it("负数和无效值显示 0μs", () => {
    expect(fmtMs(-1)).toBe("0μs");
    expect(fmtMs(NaN)).toBe("0μs");
    expect(fmtMs(Infinity)).toBe("0μs");
  });

  it("< 1 ns 显示整数 ns", () => {
    expect(fmtMs(0)).toBe("0ns");
    expect(fmtMs(0.0000005)).toBe("1ns");
    expect(fmtMs(0.0009999)).toBe("1000ns");
  });

  it("[1μs, 1ms) 显示一位小数 μs", () => {
    expect(fmtMs(0.001)).toBe("1.0μs");
    expect(fmtMs(0.1)).toBe("100.0μs");
    expect(fmtMs(0.5)).toBe("500.0μs");
    expect(fmtMs(0.999)).toBe("999.0μs");
  });

  it("[1ms, 1s) 显示整数 ms", () => {
    expect(fmtMs(1)).toBe("1ms");
    expect(fmtMs(13)).toBe("13ms");
    expect(fmtMs(999.4)).toBe("999ms");
    expect(fmtMs(999.6)).toBe("1000ms");
  });

  it("≥ 1s 显示一位小数 s", () => {
    expect(fmtMs(1000)).toBe("1.0s");
    expect(fmtMs(1500)).toBe("1.5s");
    expect(fmtMs(10_300)).toBe("10.3s");
    expect(fmtMs(60_000)).toBe("60.0s");
  });
});
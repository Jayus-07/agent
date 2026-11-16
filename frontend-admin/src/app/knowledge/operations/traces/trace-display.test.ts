import { describe, expect, it } from "vitest";
import type { Span } from "@/types/trace";
import {
  getTraceMetricSummary,
  getTraceSpanNameLabel,
  getTraceSpanTypeLabel,
} from "./trace-display";

function span(type: string, metrics: Span["metrics"] = {}, name = ""): Span {
  return { id: "span-1", type, name, metrics } as Span;
}

describe("Trace 详情中文展示", () => {
  it("将入库指标名称和规则枚举值翻译为中文，布尔值不显示 true/false", () => {
    const summary = getTraceMetricSummary([
      span("parse", {
        loader: "pipeline",
        page_count: 0,
        page_count_applicable: false,
        internal_structure_analysis_ms: 237,
      }),
      span("stage", {
        strategy: "deterministic_fallback",
        provider_call: true,
        cache_status: "miss",
        prompt_tokens: 967,
      }),
    ]);

    expect(summary).toContain("解析流程：统一处理流程");
    expect(summary).toContain("页数：不适用");
    expect(summary).toContain("页数是否适用：否");
    expect(summary).toContain("结构识别耗时：237 毫秒");
    expect(summary).toContain("处理策略：规则兜底");
    expect(summary).toContain("是否调用模型服务：是");
    expect(summary).toContain("缓存状态：未命中");
    expect(summary).toContain("输入词元数：967");
    expect(summary).not.toMatch(/\b(true|false|pipeline|deterministic_fallback|cache_status)\b/);
  });

  it("隐藏容器内部质检路径，只显示记录是否已生成", () => {
    const summary = getTraceMetricSummary([
      span("audit", { record_path: "/app/data/quality_records/sample.json", coverage_issue: "" }),
    ]);
    expect(summary).toBe("质量记录：已生成");
  });

  it("为质量审计和模型调用显示中文阶段名称", () => {
    expect(getTraceSpanTypeLabel("audit")).toBe("质量检查");
    expect(getTraceSpanNameLabel({
      type: "llm_call",
      name: "Question generation Provider call",
    } as Span)).toBe("模拟问题生成模型服务调用");
  });

  it("未知运行指标用中文兜底标签，不直接暴露字段名", () => {
    const summary = getTraceMetricSummary([span("custom", { internal_unknown_probe: 1 })]);
    expect(summary).toBe("其他运行指标 1：1");
  });
});

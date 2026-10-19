import { describe, expect, it } from "vitest";
import type { Span } from "@/types/trace";
import {
  getLogicalPhases,
  getMetadataSlowWarning,
  getQualityScore,
  getQualityWarning,
} from "./ragTraceMetrics";

function span(overrides: Partial<Span>): Span {
  return {
    id: "span",
    type: "llm",
    name: "Span",
    parent_id: "index_upload",
    status: "success",
    start_time: "2026-10-19T00:00:00.000Z",
    end_time: "2026-10-19T00:00:01.000Z",
    duration_ms: 1000,
    duration_ratio: 0,
    attributes: {},
    metrics: {},
    ...overrides,
  };
}

describe("RAG Trace 统计", () => {
  it("缺少评分时不产生低分告警", () => {
    expect(getQualityScore([
      span({ id: "index_quality", type: "audit", metrics: { anomalies_total: 0 } }),
    ])).toBeNull();
    expect(getQualityWarning(null)).toBeNull();
  });

  it("真实低分告警，真实高分不告警", () => {
    expect(getQualityWarning(getQualityScore([
      span({ id: "index_quality", type: "audit", metrics: { quality_score: 42 } }),
    ]))).toBe("质量评分偏低 (42/100)");
    expect(getQualityWarning(getQualityScore([
      span({ id: "index_quality", type: "audit", metrics: { quality_score: 88 } }),
    ]))).toBeNull();
  });

  it("Pipeline 只统计直接逻辑阶段，嵌套及多个模型调用不重复计时", () => {
    const phases = getLogicalPhases([
      span({ id: "index_metadata", type: "metadata_stage", duration_ms: 12_000,
        start_time: "2026-10-19T00:00:00.000Z", end_time: "2026-10-19T00:00:12.000Z" }),
      span({ id: "metadata_llm_provider", type: "llm", parent_id: "index_metadata", duration_ms: 8_000 }),
      span({ id: "question_gen_provider", type: "llm", parent_id: "question_gen_stage", duration_ms: 5_000 }),
      span({ id: "embed_a", type: "embedding", duration_ms: 4_000,
        start_time: "2026-10-19T00:00:00.000Z", end_time: "2026-10-19T00:00:04.000Z" }),
      span({ id: "embed_b", type: "embedding", duration_ms: 4_000,
        start_time: "2026-10-19T00:00:02.000Z", end_time: "2026-10-19T00:00:06.000Z" }),
    ]);

    expect(phases.find((phase) => phase.type === "metadata_stage")?.duration_ms).toBe(12_000);
    expect(phases.find((phase) => phase.type === "llm")).toBeUndefined();
    expect(phases.find((phase) => phase.type === "embedding")?.duration_ms).toBe(6000);
  });

  it("低性能提示按 index_metadata 逻辑阶段计算，不受第一个模型 Span 影响", () => {
    const spans = [
      span({ id: "slow_other_model", type: "llm", duration_ms: 30_000 }),
      span({ id: "index_metadata", type: "metadata_stage", duration_ms: 12_000,
        start_time: "2026-10-19T00:00:00.000Z", end_time: "2026-10-19T00:00:12.000Z" }),
    ];
    expect(getMetadataSlowWarning(spans, 20_000)).toBe(
      "元数据生成耗时 12.0s，占总耗时 60%",
    );
    expect(getMetadataSlowWarning(spans, 40_000)).toBeNull();
  });
});

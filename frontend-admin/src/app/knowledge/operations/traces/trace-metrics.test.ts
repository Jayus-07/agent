import { describe, expect, it } from "vitest";
import type { Span } from "@/types/trace";
import {
  getLogicalStageSpans, getMetadataPerformanceWarning, getMetadataStageDuration,
  getQualityScore, getQualityScoreBreakdown, getQualityWarning, groupSpansByType,
} from "./trace-metrics";

function span(
  id: string,
  type: string,
  parent_id: string | null,
  duration_ms: number,
  metrics: Span["metrics"] = {},
): Span {
  return { id, type, parent_id, duration_ms, metrics } as Span;
}

describe("Trace duration and quality metrics", () => {
  it("uses only root logical spans and aggregates same-type stages without nested calls", () => {
    const spans = [
      span("root", "workflow", null, 47_160),
      span("index_metadata", "llm", "root", 30_499),
      span("metadata_llm_extract", "llm_call", "index_metadata", 7_302, {
        provider_duration_ms: 7_184,
      }),
      span("parse_a", "parse", "root", 100),
      span("parse_b", "parse", "root", 200),
      span("nested_parse", "parse", "index_metadata", 900),
    ];

    const logical = getLogicalStageSpans(spans, "root");
    const groups = groupSpansByType(logical);
    expect(groups.find((group) => group.type === "parse")?.totalMs).toBe(300);
    expect(getMetadataStageDuration(spans, "root")).toBe(30_499);
    expect(getMetadataPerformanceWarning(30_499, 47_160)).toContain("30.5s");
    expect(getMetadataPerformanceWarning(30_499, 47_160)).toContain("65%");
    expect(groups.find((group) => group.type === "llm")?.totalMs).toBe(30_499);
  });

  it("aggregates every root metadata stage for the warning", () => {
    const spans = [
      span("root", "workflow", null, 50_000),
      span("index_metadata", "llm", "root", 30_000),
      span("index_metadata#1", "llm", "root", 8_000),
      span("nested_metadata", "stage", "index_metadata", 4_000),
    ];

    expect(getMetadataStageDuration(spans, "root")).toBe(38_000);
    expect(getMetadataPerformanceWarning(getMetadataStageDuration(spans, "root"), 50_000))
      .toContain("38.0s");
  });

  it("does not warn when quality score is missing, but uses a real low score", () => {
    const spans = [span("index_quality", "audit", "root", 10, {
      anomalies_total: 0,
      anomalies_hard: 0,
      anomalies_warn: 0,
    })];
    expect(getQualityScore(spans)).toBeNull();
    expect(getQualityWarning(getQualityScore(spans))).toBeNull();
    expect(getQualityWarning(0)).toBe("质量评分偏低 (0/100)");
    expect(getQualityWarning(59)).toBe("质量评分偏低 (59/100)");
  });

  it("does not warn for a real high score and preserves a valid zero score", () => {
    expect(getQualityScore([
      span("index_quality", "audit", "root", 10, { quality_score: 100 }),
    ])).toBe(100);
    expect(getQualityWarning(100)).toBeNull();
    expect(getQualityWarning(60)).toBeNull();
  });

  it("exposes the backend score rule and this trace's anomaly counts", () => {
    const breakdown = getQualityScoreBreakdown([
      span("index_quality", "audit", "root", 10, {
        quality_score_base: 100,
        quality_score_hard_deduction: 40,
        quality_score_warn_deduction: 10,
        anomalies_total: 3,
        anomalies_hard: 1,
        anomalies_warn: 2,
      }),
    ]);

    expect(breakdown).toEqual({
      base: 100,
      hardDeduction: 40,
      warningDeduction: 10,
      hardCount: 1,
      warningCount: 2,
      totalCount: 3,
    });
  });

  it("uses the current score rule for older traces without policy metrics", () => {
    expect(getQualityScoreBreakdown([])).toEqual({
      base: 100,
      hardDeduction: 40,
      warningDeduction: 10,
      hardCount: null,
      warningCount: null,
      totalCount: null,
    });
  });
});

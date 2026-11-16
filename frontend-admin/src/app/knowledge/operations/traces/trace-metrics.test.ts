import { describe, expect, it } from "vitest";
import type { Span } from "@/types/trace";
import {
  formatTracePageCount, getEmbeddingCounts, getEmbeddingCountsFromSpans,
  getLogicalStageSpans, getMetadataPerformanceWarning,
  getTraceDocumentTitle, getProcessingRunDetailError, getTraceDocumentId,
  getTraceDocumentIdentityError, getRegistryDocumentIdentityError,
  getMetadataStageDuration,
  getQualityScore, getQualityScoreBreakdown, getQualityWarning, getTraceCostDisplay,
  groupSpansByType,
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
  it("Trace 未记录嵌入计数时保持未知，真实的零计数仍显示为零", () => {
    expect(getEmbeddingCounts()).toEqual({ attempted: null, succeeded: null, failed: null });
    expect(getEmbeddingCounts({ attempted: 0, succeeded: 0, failed: 0 })).toEqual({
      attempted: 0,
      succeeded: 0,
      failed: 0,
    });
  });

  it("从多个 embedding Span 中汇总 chunk 计数，不把文档向量计为分块", () => {
    expect(getEmbeddingCountsFromSpans([
      span("index_doc_embed", "embedding", "root", 8, { doc_vector_count: 1 }),
      span("index_embed", "embedding", "root", 80, {
        chunk_count: 8, attempted: 8, succeeded: 8, failed: 0,
      }),
    ])).toEqual({ attempted: 8, succeeded: 8, failed: 0 });
    expect(getEmbeddingCountsFromSpans([
      span("index_embed#1", "embedding", "root", 30, {
        attempted: 3, succeeded: 2, failed: 1,
      }),
      span("index_embed#2", "embedding", "root", 50, {
        attempted: 5, succeeded: 5, failed: 0,
      }),
    ])).toEqual({ attempted: 8, succeeded: 7, failed: 1 });
  });

  it("文件列表打开 Trace 时优先显示实际文件名", () => {
    expect(getTraceDocumentTitle("upload-id.generated.md", "厦门-鼓浪屿建筑风格.md"))
      .toBe("厦门-鼓浪屿建筑风格.md");
    expect(getTraceDocumentTitle("原始 Trace 标题", "  ")).toBe("原始 Trace 标题");
  });

  it("处理运行兜底必须匹配当前文件和运行 ID", () => {
    expect(getProcessingRunDetailError(
      { doc_id: "doc-1", run_id: "run-1" }, "doc-1", "run-1",
    )).toBeNull();
    expect(getProcessingRunDetailError(
      { doc_id: "doc-other", run_id: "run-1" }, "doc-1", "run-1",
    )).toBe("处理运行对应的文档 ID 与当前文件不匹配");
    expect(getProcessingRunDetailError(
      { doc_id: "doc-1", run_id: "run-other" }, "doc-1", "run-1",
    )).toBe("处理运行 ID 与请求记录不匹配");
    expect(getProcessingRunDetailError(
      { doc_id: "doc-1", run_id: "run-1", error: "处理运行不存在" }, "doc-1", "run-1",
    )).toBe("处理运行不存在");
    expect(getProcessingRunDetailError(
      { doc_id: "doc-1", run_id: "run-1", file_hash: "old-hash" },
      "doc-1", "run-1", "current-hash",
    )).toBe("处理运行文件 SHA-256 与当前文件不匹配");
    expect(getProcessingRunDetailError(
      { doc_id: "doc-1", run_id: "run-1", file_hash: "current-hash" },
      "doc-1", "run-1", "current-hash",
    )).toBeNull();
    expect(getProcessingRunDetailError(
      { doc_id: "doc-1", run_id: "run-1", file_hash: "current-hash", trace_id: "trace-1" },
      "doc-1", "run-1", "current-hash", "trace-1",
    )).toBeNull();
    expect(getProcessingRunDetailError(
      { doc_id: "doc-1", run_id: "run-1", file_hash: "current-hash", trace_id: "trace-other" },
      "doc-1", "run-1", "current-hash", "trace-1",
    )).toBe("处理运行关联的 Trace ID 与当前详情不匹配");
    expect(getProcessingRunDetailError(
      { doc_id: "doc-1", run_id: "run-1", file_hash: "current-hash" },
      "doc-1", "run-1", "current-hash", "trace-1",
    )).toBe("处理运行未记录 Trace ID，无法与当前详情核对");
  });

  it("文件名和文档 ID 必须来自匹配的 Registry 记录", () => {
    expect(getRegistryDocumentIdentityError(
      { doc_id: "doc-1", file_name: "source.md" }, "doc-1", "source.md",
    )).toBeNull();
    expect(getRegistryDocumentIdentityError(
      { id: "doc-1", name: "source.md" }, "doc-1", "source.md",
    )).toBeNull();
    expect(getRegistryDocumentIdentityError(
      { doc_id: "doc-other", file_name: "source.md" }, "doc-1", "source.md",
    )).toBe("文档登记 ID 与当前文件不匹配");
    expect(getRegistryDocumentIdentityError(
      { id: "doc-1", name: "renamed.md" }, "doc-1", "source.md",
    )).toBe("文档登记文件名与入口记录不匹配");
    expect(getRegistryDocumentIdentityError(
      { doc_id: "doc-1", file_name: "renamed.md" }, "doc-1", "source.md",
    )).toBe("文档登记文件名与入口记录不匹配");
  });

  it("Registry 文件 SHA 必须与入口记录一致", () => {
    expect(getRegistryDocumentIdentityError(
      { id: "doc-1", name: "source.md", hash: "AbC123" },
      "doc-1", "source.md", "abc123",
    )).toBeNull();
    expect(getRegistryDocumentIdentityError(
      { id: "doc-1", name: "source.md" }, "doc-1", "source.md", "abc123",
    )).toBe("文档登记未记录文件 SHA-256");
    expect(getRegistryDocumentIdentityError(
      { doc_id: "doc-1", file_name: "source.md", file_hash: "old-hash" },
      "doc-1", "source.md", "current-hash",
    )).toBe("文档登记文件 SHA-256 与当前文件不匹配");
  });

  it("Trace 有文档身份时必须与文件列表记录一致", () => {
    expect(getTraceDocumentId({ tags: { doc_id: "doc-1" } })).toBe("doc-1");
    expect(getTraceDocumentId({ spans: [{ metrics: { doc_id: "doc-2" } }] })).toBe("doc-2");
    expect(getTraceDocumentId({ tags: {}, spans: [] })).toBeNull();
    expect(getTraceDocumentIdentityError("doc-1", "doc-1")).toBeNull();
    expect(getTraceDocumentIdentityError("doc-other", "doc-1"))
      .toBe("Trace 记录的文档 ID 与当前文件不匹配");
    expect(getTraceDocumentIdentityError(null, "doc-1"))
      .toBe("Trace 未记录文档 ID，无法确认属于当前文件");
  });

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

  it("有模型用量但费用金额未登记时显示未核算", () => {
    expect(getTraceCostDisplay({ costCny: 0, costUsd: 0, totalTokens: 157_987, calls: 131 }))
      .toEqual({
        value: "未核算",
        detail: "已有 131 次调用 / 157,987 Tokens，费用台账未登记金额",
      });
  });

  it("有费用台账金额时显示费用，没有用量时提示暂无记录", () => {
    expect(getTraceCostDisplay({ costCny: 0.25, costUsd: 0, totalTokens: 100, calls: 2 }))
      .toEqual({ value: "¥0.2500", detail: "费用台账 · 2 次调用" });
    expect(getTraceCostDisplay({ costCny: 0, costUsd: 0, totalTokens: 0, calls: 0 }))
      .toEqual({ value: "暂无记录", detail: "Trace 没有模型用量记录" });
  });

  it("真实页数缺失时不把0页显示成实际0页", () => {
    expect(formatTracePageCount(0, true)).toBe("历史记录未留存");
    expect(formatTracePageCount(501, true)).toBe("501");
    expect(formatTracePageCount(0, false)).toBe("不适用");
  });
});

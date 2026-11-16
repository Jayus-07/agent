import type { Span } from "@/types/trace";

export interface SpanTypeGroup {
  type: string;
  spans: Span[];
  totalMs: number;
}

export function getRootSpanId(spans: Span[], rootSpanId?: string): string {
  return rootSpanId || spans.find((span) => span.parent_id === null)?.id || "";
}

/** Pipeline 只聚合根 Span 的直接子阶段，嵌套调用保留在明细中。 */
export function getLogicalStageSpans(spans: Span[], rootSpanId?: string): Span[] {
  const rootId = getRootSpanId(spans, rootSpanId);
  if (!rootId) return [];
  return spans.filter((span) => span.parent_id === rootId);
}

export function groupSpansByType(spans: Span[]): SpanTypeGroup[] {
  const groups = new Map<string, Span[]>();
  for (const span of spans) {
    const group = groups.get(span.type) || [];
    group.push(span);
    groups.set(span.type, group);
  }
  return [...groups.entries()].map(([type, groupedSpans]) => ({
    type,
    spans: groupedSpans,
    totalMs: groupedSpans.reduce((total, span) => total + (span.duration_ms || 0), 0),
  }));
}

export interface EmbeddingCounts {
  attempted: number | null;
  succeeded: number | null;
  failed: number | null;
}

/** Trace 未记录的计数保持为空，不能显示成真实的 0。 */
export function getEmbeddingCounts(metrics?: Span["metrics"]): EmbeddingCounts {
  const readCount = (key: keyof NonNullable<Span["metrics"]>): number | null => {
    const value = metrics?.[key];
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  };

  return {
    attempted: readCount("attempted"),
    succeeded: readCount("succeeded"),
    failed: readCount("failed"),
  };
}

/** 汇总 chunk embedding 阶段，跳过仅写文档级向量的 embedding Span。 */
export function getEmbeddingCountsFromSpans(spans: Span[]): EmbeddingCounts {
  const chunkSpans = spans.filter((span) => (
    span.id === "index_embed" || span.id.startsWith("index_embed#")
  ));
  const countedSpans = chunkSpans.length > 0
    ? chunkSpans
    : spans.filter((span) => (
      (["attempted", "succeeded", "failed"] as const).some((key) => (
        typeof span.metrics?.[key] === "number"
      ))
    ));
  if (countedSpans.length === 0) return getEmbeddingCounts();

  const sumCount = (key: keyof EmbeddingCounts): number | null => {
    const values = countedSpans.map((span) => getEmbeddingCounts(span.metrics)[key]);
    if (values.some((value) => value === null)) return null;
    return values.reduce<number>((total, value) => total + (value ?? 0), 0);
  };

  return {
    attempted: sumCount("attempted"),
    succeeded: sumCount("succeeded"),
    failed: sumCount("failed"),
  };
}

/** 文件列表打开 Trace 时使用 Registry 文件名覆盖上传过程中的临时文件名。 */
export function getTraceDocumentTitle(traceQuestion: string, documentName?: unknown): string {
  const name = typeof documentName === "string" ? documentName.trim() : "";
  return name || traceQuestion;
}

export function getTraceDocumentId(trace: {
  tags?: Record<string, unknown>;
  spans?: Array<{ metrics?: Record<string, unknown> }>;
}): string | null {
  const tagDocId = trace.tags?.doc_id;
  if (typeof tagDocId === "string" && tagDocId) return tagDocId;
  for (const span of trace.spans ?? []) {
    const spanDocId = span.metrics?.doc_id;
    if (typeof spanDocId === "string" && spanDocId) return spanDocId;
  }
  return null;
}

export function getTraceDocumentIdentityError(
  actualDocId: string | null,
  expectedDocId: string,
): string | null {
  if (!actualDocId) return "Trace 未记录文档 ID，无法确认属于当前文件";
  return actualDocId === expectedDocId ? null : "Trace 记录的文档 ID 与当前文件不匹配";
}

export function getRegistryDocumentIdentityError(
  document: {
    doc_id?: unknown;
    id?: unknown;
    file_name?: unknown;
    name?: unknown;
    file_hash?: unknown;
    hash?: unknown;
    sha256?: unknown;
  } | null | undefined,
  expectedDocId: string,
  expectedFileName?: string,
  expectedFileHash?: string,
): string | null {
  if (!document) return "文档登记详情为空";
  const registryDocId = document.doc_id ?? document.id;
  const registryFileName = document.file_name ?? document.name;
  const registryFileHash = document.file_hash ?? document.hash ?? document.sha256;
  if (registryDocId !== expectedDocId) return "文档登记 ID 与当前文件不匹配";
  if (typeof registryFileName !== "string" || !registryFileName.trim()) {
    return "文档登记未记录文件名";
  }
  if (expectedFileName && registryFileName !== expectedFileName) {
    return "文档登记文件名与入口记录不匹配";
  }
  if (expectedFileHash) {
    if (typeof registryFileHash !== "string" || !registryFileHash.trim()) {
      return "文档登记未记录文件 SHA-256";
    }
    if (registryFileHash.trim().toLowerCase() !== expectedFileHash.trim().toLowerCase()) {
      return "文档登记文件 SHA-256 与当前文件不匹配";
    }
  }
  return null;
}

export function getProcessingRunDetailError(
  detail: {
    doc_id?: unknown;
    run_id?: unknown;
    file_hash?: unknown;
    trace_id?: unknown;
    error?: unknown;
  } | null | undefined,
  expectedDocId: string,
  expectedRunId: string,
  expectedFileHash?: string,
  expectedTraceId?: string,
): string | null {
  if (!detail) return "处理运行详情为空";
  if (detail.doc_id !== expectedDocId) return "处理运行对应的文档 ID 与当前文件不匹配";
  if (detail.run_id !== expectedRunId) return "处理运行 ID 与请求记录不匹配";
  if (typeof detail.error === "string" && detail.error.trim()) return detail.error;
  if (
    expectedFileHash
    && String(detail.file_hash || "").toLowerCase() !== expectedFileHash.toLowerCase()
  ) return "处理运行文件 SHA-256 与当前文件不匹配";
  if (expectedTraceId && detail.trace_id !== expectedTraceId) {
    return detail.trace_id
      ? "处理运行关联的 Trace ID 与当前详情不匹配"
      : "处理运行未记录 Trace ID，无法与当前详情核对";
  }
  return null;
}

function getQualitySpan(spans: Span[]): Span | undefined {
  return spans.find((span) => span.id === "index_quality"
      && typeof span.metrics?.quality_score === "number")
    || spans.find((span) => typeof span.metrics?.quality_score === "number")
    || spans.find((span) => span.id === "index_quality");
}

/** 仅读取索引质量记录分数；缺失值保持 null，零分仍是有效分数。 */
export function getQualityScore(spans: Span[]): number | null {
  const qualitySpan = getQualitySpan(spans);
  if (!qualitySpan) return null;
  const value = qualitySpan.metrics?.quality_score ?? qualitySpan.metrics?.score;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export interface QualityScoreBreakdown {
  base: number;
  hardDeduction: number;
  warningDeduction: number;
  hardCount: number | null;
  warningCount: number | null;
  totalCount: number | null;
}

/** 从质量 Trace 读取计分规则与异常数量，旧 Trace 使用现行规则兜底。 */
export function getQualityScoreBreakdown(spans: Span[]): QualityScoreBreakdown {
  const metrics = getQualitySpan(spans)?.metrics;
  const readNumber = (key: string): number | null => {
    const value = metrics?.[key];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };

  return {
    base: readNumber("quality_score_base") ?? 100,
    hardDeduction: readNumber("quality_score_hard_deduction") ?? 40,
    warningDeduction: readNumber("quality_score_warn_deduction") ?? 10,
    hardCount: readNumber("anomalies_hard"),
    warningCount: readNumber("anomalies_warn"),
    totalCount: readNumber("anomalies_total"),
  };
}

export function getMetadataStageDuration(spans: Span[], rootSpanId?: string): number | null {
  const stages = getLogicalStageSpans(spans, rootSpanId).filter((span) =>
    span.id === "index_metadata" || span.id.startsWith("index_metadata#"),
  );
  if (!stages.length || stages.some((span) =>
    typeof span.duration_ms !== "number" || !Number.isFinite(span.duration_ms),
  )) return null;
  return stages.reduce((total, span) => total + span.duration_ms, 0);
}

export function getQualityWarning(score: number | null): string | null {
  if (score === null) return null;
  return score < 60 ? `质量评分偏低 (${score}/100)` : null;
}

export interface TraceCostDisplayInput {
  costCny: number;
  costUsd: number;
  totalTokens: number;
  calls: number;
}

/** 费用为零且存在用量时明确提示未核算，避免把缺失台账显示成免费。 */
export function getTraceCostDisplay(input: TraceCostDisplayInput): {
  value: string;
  detail: string;
} {
  if (input.costCny > 0) {
    return { value: `¥${input.costCny.toFixed(4)}`, detail: `费用台账 · ${input.calls} 次调用` };
  }
  if (input.costUsd > 0) {
    return { value: `$${input.costUsd.toFixed(4)}`, detail: `费用台账 · ${input.calls} 次调用` };
  }
  if (input.totalTokens > 0 || input.calls > 0) {
    return {
      value: "未核算",
      detail: `已有 ${input.calls} 次调用 / ${input.totalTokens.toLocaleString()} Tokens，费用台账未登记金额`,
    };
  }
  return { value: "暂无记录", detail: "Trace 没有模型用量记录" };
}

/** 历史 PDF Trace 没留真实页数时不能把 0 当成实际页数。 */
export function formatTracePageCount(pageCount: unknown, applicable: unknown): string {
  if (applicable !== true) return "不适用";
  if (typeof pageCount !== "number" || !Number.isFinite(pageCount) || pageCount <= 0) {
    return "历史记录未留存";
  }
  return pageCount.toLocaleString();
}

export function getMetadataPerformanceWarning(
  durationMs: number | null,
  wallDurationMs: number,
): string | null {
  if (durationMs === null || wallDurationMs <= 0) return null;
  if (durationMs <= 10_000 || durationMs / wallDurationMs <= 0.5) return null;
  const share = Math.round((durationMs / wallDurationMs) * 100);
  return `元数据生成耗时 ${(durationMs / 1000).toFixed(1)}s，占总耗时 ${share}%`;
}

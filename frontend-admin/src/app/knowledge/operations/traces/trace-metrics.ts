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

/** 仅读取索引质量记录分数；缺失值保持 null，零分仍是有效分数。 */
export function getQualityScore(spans: Span[]): number | null {
  const qualitySpan = spans.find((span) => span.id === "index_quality")
    || spans.find((span) => typeof span.metrics?.quality_score === "number");
  if (!qualitySpan) return null;
  const value = qualitySpan.metrics?.quality_score ?? qualitySpan.metrics?.score;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
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

export function getMetadataPerformanceWarning(
  durationMs: number | null,
  wallDurationMs: number,
): string | null {
  if (durationMs === null || wallDurationMs <= 0) return null;
  if (durationMs <= 10_000 || durationMs / wallDurationMs <= 0.5) return null;
  const share = Math.round((durationMs / wallDurationMs) * 100);
  return `元数据生成耗时 ${(durationMs / 1000).toFixed(1)}s，占总耗时 ${share}%`;
}

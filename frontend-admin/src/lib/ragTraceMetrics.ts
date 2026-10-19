import type { Span } from "@/types/trace";

export interface LogicalPhase {
  type: string;
  duration_ms: number;
  spans: Span[];
}

function intervalUnionDuration(spans: Span[]): number | null {
  const intervals = spans
    .map((span) => ({
      start: Date.parse(span.start_time),
      end: Date.parse(span.end_time || ""),
    }))
    .filter(({ start, end }) => Number.isFinite(start) && Number.isFinite(end) && end >= start)
    .sort((a, b) => a.start - b.start);

  if (intervals.length !== spans.length || intervals.length === 0) return null;
  let total = 0;
  let start = intervals[0].start;
  let end = intervals[0].end;
  for (const interval of intervals.slice(1)) {
    if (interval.start <= end) {
      end = Math.max(end, interval.end);
    } else {
      total += end - start;
      start = interval.start;
      end = interval.end;
    }
  }
  return total + end - start;
}

/** 仅统计索引工作流的直接子 Span，避免把嵌套模型调用重复计入阶段耗时。 */
export function getLogicalPhases(
  spans: Span[],
  rootSpanId = "index_upload",
): LogicalPhase[] {
  const groups = new Map<string, Span[]>();
  for (const span of spans) {
    if (span.parent_id !== rootSpanId) continue;
    const group = groups.get(span.type) || [];
    group.push(span);
    groups.set(span.type, group);
  }

  return Array.from(groups, ([type, groupedSpans]) => ({
    type,
    spans: groupedSpans,
    duration_ms: intervalUnionDuration(groupedSpans)
      ?? groupedSpans.reduce((sum, span) => sum + Math.max(0, span.duration_ms || 0), 0),
  }));
}

/** 评分只从质量记录 Span 读取；缺失值保持 null，不伪装成 0 分。 */
export function getQualityScore(spans: Span[]): number | null {
  const qualitySpan = spans.find((span) => span.id === "index_quality");
  const value = qualitySpan?.metrics?.quality_score ?? qualitySpan?.metrics?.score;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function getQualityWarning(score: number | null): string | null {
  return score !== null && score < 60
    ? `质量评分偏低 (${score}/100)`
    : null;
}

/** 低性能提示只读取 index_metadata 逻辑阶段，不取任意一个 llm Span。 */
export function getMetadataSlowWarning(
  spans: Span[],
  wallDurationMs: number,
  rootSpanId = "index_upload",
): string | null {
  if (wallDurationMs <= 0) return null;
  const metadataSpans = spans.filter(
    (span) => span.parent_id === rootSpanId && span.id === "index_metadata",
  );
  if (!metadataSpans.length) return null;
  const durationMs = intervalUnionDuration(metadataSpans)
    ?? metadataSpans.reduce((sum, span) => sum + Math.max(0, span.duration_ms || 0), 0);
  if (durationMs <= 10_000 || durationMs / wallDurationMs <= 0.5) return null;
  return `元数据生成耗时 ${(durationMs / 1000).toFixed(1)}s，占总耗时 ${(
    (durationMs / wallDurationMs) * 100
  ).toFixed(0)}%`;
}

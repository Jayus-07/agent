import type { Span, TraceRecord } from "@/types/trace";

/** 把跨服务子 Trace 的 Span 映射进父 Trace 时间线，保留可追溯来源与层级。 */
export function mergeChildTraceSpans(
  parentSpans: Span[],
  childTraces: TraceRecord[],
): Span[] {
  const merged = [...parentSpans];
  const seenTraceIds = new Set<string>();
  const rootId = parentSpans.find((span) => span.parent_id == null)?.id;

  for (const childTrace of childTraces) {
    if (!childTrace.id || seenTraceIds.has(childTrace.id)) continue;
    seenTraceIds.add(childTrace.id);
    const sourceSpans = childTrace.spans || [];
    if (sourceSpans.length === 0) continue;

    const linkedOwner = parentSpans.find((span) => {
      const traceIds = span.metrics?.child_trace_ids;
      return Array.isArray(traceIds) && traceIds.includes(childTrace.id);
    });
    const workflow = `${childTrace.workflow_name || ""} ${JSON.stringify(childTrace.tags || {})}`
      .toLowerCase();
    const signals = workflow.includes("rag") || workflow.includes("knowledge")
      ? ["rag", "knowledge"]
      : workflow.includes("sql") || workflow.includes("database")
        ? ["sql", "database"]
        : [];
    const inferredOwner = signals.length
      ? parentSpans.find((span) => {
        if (span.type !== "tool_call") return false;
        const identity = `${span.id} ${span.name} ${JSON.stringify(span.input || {})}`.toLowerCase();
        return signals.some((signal) => identity.includes(signal));
      })
      : undefined;
    const ownerId = (linkedOwner || inferredOwner)?.id || rootId;
    const idMap = new Map(
      sourceSpans.map((span) => [span.id, `${childTrace.id}--${span.id}`]),
    );

    for (const childSpan of sourceSpans) {
      const id = idMap.get(childSpan.id)!;
      const isChildRoot = !childSpan.parent_id || !idMap.has(childSpan.parent_id);
      merged.push({
        ...childSpan,
        id,
        parent_id: isChildRoot
          ? ownerId || null
          : idMap.get(childSpan.parent_id!) || ownerId || null,
        duration_ratio: childSpan.duration_ms / Math.max(childTrace.duration_ms || 1, 1),
        attributes: {
          ...(childSpan.attributes || {}),
          source_trace_id: childTrace.id,
          source_workflow_name: childTrace.workflow_name || "",
        },
        metrics: {
          ...(childSpan.metrics || {}),
          origin_trace_id: childTrace.id,
        },
      });
    }
  }

  return merged;
}

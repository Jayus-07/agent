"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { getTraceById } from "@/lib/observability/source";
import type { TraceRecord, Span, SpanEvent } from "@/types/trace";

function formatScore(s: number | undefined | null): string {
  if (s == null) return "-";
  return s.toFixed(4);
}

/** 从 spans 中收集所有 retrieval 相关的 events */
function collectRetrievalEvents(spans: Span[]): { spanName: string; events: SpanEvent[] }[] {
  const results: { spanName: string; events: SpanEvent[] }[] = [];
  for (const span of spans) {
    if (!span.events || span.events.length === 0) continue;
    // 筛选 retrieval 相关 events
    const retrievalEvents = span.events.filter((e) =>
      ["doc_filter", "rrf_fusion", "rerank", "final_context", "query_analyzer"].includes(e.name)
    );
    if (retrievalEvents.length > 0) {
      results.push({ spanName: span.name || span.id, events: retrievalEvents });
    }
  }
  return results;
}

function EventTable({ event }: { event: SpanEvent }) {
  // 兼容后端 attributes 和前端别名 data
  const raw = (event.attributes || event.data || {}) as Record<string, unknown>;

  return (
    <div className="border border-border-subtle rounded-lg overflow-hidden">
      <div className="px-4 py-2.5 bg-surface-elevated border-b border-border-subtle flex items-center justify-between">
        <span className="text-xs font-medium text-text-primary">{event.name}</span>
        <span className="text-[10px] text-text-muted">{event.message}</span>
      </div>

      <div className="divide-y divide-border-subtle">
        {/* ── doc_filter ── */}
        {event.name === "doc_filter" && (
          <div className="p-4 text-xs space-y-1.5">
            <div className="flex gap-2">
              <span className="text-text-muted">Metadata Filter:</span>
              <code className="text-text-primary bg-slate-50 px-1.5 py-0.5 rounded text-[11px]">
                {JSON.stringify(raw.metadata_filter || {})}
              </code>
            </div>
            {(raw.person_names as string[])?.length > 0 && (
              <div className="flex gap-2">
                <span className="text-text-muted">Person Names:</span>
                <span className="text-text-primary">{String(raw.person_names)}</span>
              </div>
            )}
            <div className="flex gap-2">
              <span className="text-text-muted">Output:</span>
              <span className="font-mono text-accent">{String(raw.output_doc_count)} docs</span>
            </div>
          </div>
        )}

        {/* ── rrf_fusion ── */}
        {event.name === "rrf_fusion" && (
          <div className="p-4 space-y-3">
            {(["vector_top3", "bm25_top3", "fused_top5"] as const).map((key) => {
              const items = (raw[key] as Array<Record<string, unknown>>) || [];
              if (!items.length) return null;
              return (
                <div key={key}>
                  <span className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">{key.replace("_", " ")}</span>
                  <div className="space-y-1">
                    {items.map((item, i) => (
                      <div key={i} className="flex items-start gap-2 text-[11px] bg-slate-50 rounded px-2 py-1">
                        <span className="text-slate-300 shrink-0 w-4">#{i + 1}</span>
                        <code className="text-slate-500 text-[10px] shrink-0 w-20 truncate">{String(item.chunk_id || "-").slice(0, 12)}</code>
                        {"rrf_score" in item && <span className="font-mono text-accent shrink-0">{formatScore(item.rrf_score as number)}</span>}
                        {"score" in item && <span className="font-mono text-accent shrink-0">{formatScore(item.score as number)}</span>}
                        <span className="text-text-secondary truncate">{String(item.snippet || "").slice(0, 80)}</span>
                        {!!item.doc_type && <span className="text-[10px] bg-blue-50 text-blue-600 px-1 rounded shrink-0">{String(item.doc_type)}</span>}
                        {!!item.source && <span className="text-[10px] text-slate-400 truncate shrink-0 max-w-[120px]">{String(item.source)}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* ── rerank ── */}
        {event.name === "rerank" && (
          <div className="p-3 space-y-1">
            {(raw.scored as Array<Record<string, unknown>>)?.map((item, i) => (
              <div key={i} className="flex items-center gap-2 text-[11px] bg-slate-50 rounded px-2 py-1">
                <span className="text-slate-300 shrink-0 w-4">#{i + 1}</span>
                <span className={`font-mono text-xs shrink-0 ${(item.score as number) < 0.4 ? "text-red-400" : "text-emerald-600"}`}>
                  {formatScore(item.score as number)}
                </span>
                <span className="text-text-secondary truncate">{String(item.snippet || "").slice(0, 80)}</span>
                {!!item.doc_type && <span className="text-[10px] bg-blue-50 text-blue-600 px-1 rounded shrink-0">{String(item.doc_type)}</span>}
                {!!item.source && <span className="text-[10px] text-slate-400 truncate shrink-0 max-w-[120px]">{String(item.source)}</span>}
              </div>
            ))}
          </div>
        )}

        {/* ── final_context ── */}
        {event.name === "final_context" && (
          <div className="p-3 space-y-1">
            {(raw.chunks as Array<Record<string, unknown>>)?.map((item, i) => (
              <div key={i} className="flex items-start gap-2 text-[11px] bg-slate-50 rounded px-2 py-1">
                <span className="text-slate-300 shrink-0 w-4">#{i + 1}</span>
                <span className="text-text-secondary truncate flex-1">{String(item.snippet || "").slice(0, 80)}</span>
                {!!item.keywords && <span className="text-[10px] text-violet-500 shrink-0 max-w-[150px] truncate">{String(item.keywords)}</span>}
                {!!item.doc_type && <span className="text-[10px] bg-blue-50 text-blue-600 px-1 rounded shrink-0">{String(item.doc_type)}</span>}
              </div>
            ))}
          </div>
        )}

        {/* ── query_analyzer ── */}
        {event.name === "query_analyzer" && (
          <div className="p-4 text-xs space-y-1.5">
            <div className="flex gap-2">
              <span className="text-text-muted">Intent:</span>
              <span className="text-text-primary font-mono">{String(raw.intent || "-")}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-text-muted">Doc Types:</span>
              <span className="text-text-primary">{String(raw.doc_types || "[]")}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-text-muted">Filter:</span>
              <code className="text-text-primary bg-slate-50 px-1.5 py-0.5 rounded text-[11px]">
                {JSON.stringify(raw.metadata_filter || {})}
              </code>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function RetrievalDebugPage() {
  const { traceId } = useParams<{ traceId: string }>();
  const router = useRouter();
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const t = await getTraceById(traceId);
      if (!cancelled) { setTrace(t); setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [traceId]);

  if (loading) {
    return <div className="min-h-screen bg-slate-50 flex items-center justify-center"><p className="text-sm text-slate-400">加载中…</p></div>;
  }
  if (!trace) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-400">Trace {traceId} 不存在</p>
          <button onClick={() => router.back()} className="mt-3 text-sm text-violet-600">← 返回</button>
        </div>
      </div>
    );
  }

  const spans = trace.spans || [];
  const retrievalGroups = collectRetrievalEvents(spans);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-4xl mx-auto px-6 py-6 space-y-5">
        <div className="flex items-center gap-3">
          <button onClick={() => router.back()} className="text-xs text-slate-400 hover:text-slate-600">← 返回</button>
          <h1 className="text-sm font-semibold text-slate-700">Retrieval Debug</h1>
          <span className="font-mono text-xs text-slate-400">{trace.id?.slice(0, 12)}</span>
        </div>

        {/* Query */}
        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <span className="text-[10px] text-slate-400 uppercase tracking-wider block mb-2">Query</span>
          <p className="text-sm text-slate-700">{trace.question}</p>
        </div>

        {/* Events */}
        {retrievalGroups.length === 0 && (
          <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-sm text-slate-400">
            该 Trace 无检索调试数据（可能为知识库索引操作）
          </div>
        )}
        {retrievalGroups.map((group, gi) => (
          <div key={gi} className="space-y-3">
            <span className="text-[10px] text-slate-400 uppercase tracking-wider block">{group.spanName}</span>
            {group.events.map((evt, ei) => (
              <EventTable key={ei} event={evt} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

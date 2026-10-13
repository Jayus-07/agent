"use client";

import type { Span } from "@/types/trace";
import { safeRuntimeLabel } from "@/lib/observability/runtimeStatus";

/**
 * LangGraph 图拓扑 DAG 渲染
 * 从 trace.graph 字段取节点和边，渲染为 CSS DAG
 * 节点颜色 = 状态（绿=success / 红=error / 灰=skipped）
 */

interface GraphNode {
  id: string;
  label: string;
}

interface GraphEdge {
  source: string;
  target: string;
  label?: string;
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  spans: Span[];
  loopCount: number;
  maxLoops: number;
  degradationTriggered: boolean;
  onNodeClick?: (spanId: string) => void;
}

/** 图节点只在存在实际 Span 证据时展示。 */
export function findGraphNodeSpan(nodeId: string, spans: Span[]): Span | undefined {
  const exact = spans.find((span) => span.id === nodeId || (span as any).span_id === nodeId);
  if (exact) return exact;

  if (nodeId === "supervisor") {
    return spans.find((span) => span.id.startsWith("supervisor-round-"));
  }
  if (nodeId.startsWith("skill-")) {
    const stepId = nodeId.slice("skill-".length);
    return spans.find((span) => span.id === stepId || span.id === `skill-${stepId}` || span.id.endsWith(`:${stepId}`));
  }
  return spans.find((span) => span.id.startsWith(`${nodeId}:`));
}

export function hasGraphNodeExecution(nodeId: string, spans: Span[]): boolean {
  return Boolean(findGraphNodeSpan(nodeId, spans));
}

/** 从实际 Span 中取得 graph node 的状态；缺少证据时保持未知。 */
function nodeStatus(nodeId: string, spans: Span[]): string {
  const span = findGraphNodeSpan(nodeId, spans);
  return span?.status || "unknown";
}

function statusColor(status: string): string {
  switch (status) {
    case "success": return "border-emerald-400 bg-emerald-50 text-emerald-700";
    case "error":
    case "failed":
    case "timeout": return "border-red-400 bg-red-50 text-red-700";
    case "running": return "border-blue-400 bg-blue-50 text-blue-700";
    case "partial":
    case "degraded": return "border-amber-300 bg-amber-50 text-amber-800";
    case "rejected": return "border-orange-300 bg-orange-50 text-orange-800";
    case "skipped":
    case "cancelled": return "border-slate-300 bg-slate-50 text-slate-500";
    default: return "border-slate-300 bg-white text-slate-600";
  }
}

export default function GraphTopology({ nodes, edges, spans, loopCount, maxLoops, degradationTriggered, onNodeClick }: Props) {
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <p className="text-[10px] uppercase tracking-widest text-slate-400">LangGraph DAG</p>
        <div className="flex items-center gap-3 text-[10px] text-slate-500">
          <span>🔄 {loopCount}/{maxLoops} rounds</span>
          {degradationTriggered && (
            <span className="text-amber-600 bg-amber-50 rounded px-2 py-0.5">⚠ 降级触发</span>
          )}
        </div>
      </div>

      {/* DAG */}
      <div className="space-y-2">
        {nodes.map((node) => {
          const status = nodeStatus(node.id, spans);
          return (
            <button
              key={node.id}
              onClick={() => onNodeClick?.(node.id)}
              aria-label={`执行节点 ${safeRuntimeLabel(node.label, "未命名节点")}，状态 ${safeRuntimeLabel(status, "状态未知")}`}
              className={`w-full min-h-10 text-left px-3 py-2 rounded-lg border text-xs font-medium transition-colors hover:ring-2 hover:ring-violet-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${statusColor(status)}`}
            >
              <div className="flex items-center justify-between">
                <span>{safeRuntimeLabel(node.label, "未命名节点")}</span>
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                  status === "success" ? "bg-emerald-500"
                    : ["error", "failed", "timeout"].includes(status) ? "bg-red-500"
                      : status === "running" ? "bg-blue-500"
                        : ["partial", "degraded", "rejected"].includes(status) ? "bg-amber-500" : "bg-slate-300"
                }`} />
              </div>
            </button>
          );
        })}
      </div>

      {/* Edges 简化为箭头列表 */}
      {edges.length > 0 && (
        <div className="mt-4 pt-3 border-t border-slate-100">
          <p className="text-[9px] text-slate-400 mb-2 uppercase">编排拓扑连接 · 未提供执行边状态</p>
          <div className="space-y-1">
            {edges.map((e, i) => (
              <div key={i} className="flex items-center gap-2 text-[10px]">
                <span className="font-mono text-slate-500">{safeRuntimeLabel(e.source, "执行节点")}</span>
                <span className="text-slate-300">→</span>
                <span className="font-mono text-slate-500">{safeRuntimeLabel(e.target, "执行节点")}</span>
                {e.label && (
                  <span className="text-[9px] text-violet-600 bg-violet-50 rounded px-1.5 py-0.5 font-mono">条件文本已隐藏</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

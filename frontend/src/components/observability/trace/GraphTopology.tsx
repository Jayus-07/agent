"use client";

import type { Span } from "@/types/trace";

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

/** 从 spans 中找到某个 graph node 的 status */
function nodeStatus(nodeId: string, spans: Span[]): string {
  const span = spans.find((s) => s.id === nodeId || (s as any).span_id === nodeId);
  return span?.status || "success";
}

function statusColor(status: string): string {
  switch (status) {
    case "error":   return "border-red-400 bg-red-50 text-red-700";
    case "running": return "border-blue-400 bg-blue-50 text-blue-700";
    case "skipped": return "border-slate-300 bg-slate-50 text-slate-400";
    default:        return "border-emerald-400 bg-emerald-50 text-emerald-700";
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
              className={`w-full text-left px-3 py-2 rounded-lg border text-xs font-medium transition-colors hover:ring-2 hover:ring-violet-300 ${statusColor(status)}`}
            >
              <div className="flex items-center justify-between">
                <span>{node.label}</span>
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                  status === "success" ? "bg-emerald-500" : status === "error" ? "bg-red-500" : "bg-slate-300"
                }`} />
              </div>
            </button>
          );
        })}
      </div>

      {/* Edges 简化为箭头列表 */}
      {edges.length > 0 && (
        <div className="mt-4 pt-3 border-t border-slate-100">
          <p className="text-[9px] text-slate-400 mb-2 uppercase">路由决策</p>
          <div className="space-y-1">
            {edges.map((e, i) => (
              <div key={i} className="flex items-center gap-2 text-[10px]">
                <span className="font-mono text-slate-500">{e.source}</span>
                <span className="text-slate-300">→</span>
                <span className="font-mono text-slate-500">{e.target}</span>
                {e.label && (
                  <span className="text-[9px] text-violet-600 bg-violet-50 rounded px-1.5 py-0.5 font-mono">{e.label}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

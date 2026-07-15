"use client";

import { useEffect, useState, useCallback } from "react";

interface TraceStep {
  id: string; label: string; duration_ms: number; duration_ratio: number;
  status: "success" | "skipped" | "error";
  metrics: Record<string, number>;
}

interface Trace {
  id: string; request_id: string; timestamp: string; session_id: string;
  model: { name: string; provider: string };
  question: string; answer_preview: string; answer_len: number;
  duration_ms: number;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  steps: TraceStep[];
}

// 步骤到图标的映射
const ICONS: Record<string, string> = {
  mq_check: "\u{1F504}", query_rewrite: "\u{270F}\u{FE0F}",
  hybrid_retrieval: "\u{1F50D}", retrieval: "\u{1F4E5}",
  rerank: "\u{1F4CA}", llm_generate: "\u{1F4AC}", citation: "\u{2705}",
};

// metrics 字段的中文名
const METRIC_LABELS: Record<string, string> = {
  vector_hits: "向量命中", bm25_hits: "BM25命中", merged_hits: "融合",
  retrieved_chunks: "Chunk数",
  input_docs: "输入", output_docs: "输出", threshold: "阈值",
  prompt_tokens: "Prompt", completion_tokens: "Completion", total_tokens: "Total",
  variants: "变体数", triggered: "触发", filtered: "过滤", mode: "模式",
  verified_citations: "通过", total_citations: "总数",
};

function formatMs(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function statusColor(s: string) {
  if (s === "success") return "text-green-600 bg-green-50";
  if (s === "error") return "text-red-600 bg-red-50";
  return "text-gray-400 bg-gray-50";
}

function statusLabel(s: string) {
  if (s === "success") return "";
  if (s === "error") return "失败";
  return "跳过";
}

function modelBadge(m: Trace["model"]) {
  const n = m?.name || "";
  if (n.startsWith("deepseek")) return "bg-blue-100 text-blue-700";
  if (n.startsWith("MiniMax")) return "bg-purple-100 text-purple-700";
  return "bg-green-100 text-green-700";
}

function formatTokens(u: Trace["usage"]) {
  if (!u?.total_tokens) return null;
  return `P${u.prompt_tokens} | C${u.completion_tokens} | T${u.total_tokens}`;
}

export default function AgentTracePage() {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [selected, setSelected] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchTraces = useCallback(async () => {
    try {
      const res = await fetch("/api/observability/rag-traces?limit=50");
      const data = await res.json();
      setTraces(data.traces || []);
      if (!selected && data.traces?.length > 0) setSelected(data.traces[0]);
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchTraces();
    const es = new EventSource("/api/observability/rag-traces/stream");
    es.onmessage = (e) => {
      try { setTraces((prev) => [JSON.parse(e.data), ...prev.slice(0, 49)]); } catch {}
    };
    return () => es.close();
  }, [fetchTraces]);

  return (
    <div className="h-full flex flex-col">
      <div className="px-6 py-4 border-b border-border-subtle flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">Agent Trace</h1>
          <p className="text-xs text-text-muted mt-1">RAG 全链路耗时 — 每一步的详细指标</p>
        </div>
        <button onClick={fetchTraces} className="text-xs text-accent hover:underline">刷新</button>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* 左侧列表 */}
        <div className="w-80 border-r border-border-subtle overflow-y-auto">
          {loading ? (
            <div className="p-4 text-xs text-text-muted">加载中...</div>
          ) : traces.length === 0 ? (
            <div className="p-4 text-xs text-text-muted">暂无记录，去 Agent 对话页提问试试</div>
          ) : (
            traces.map((t) => (
              <div
                key={t.id}
                onClick={() => setSelected(t)}
                className={`px-4 py-3 border-b border-border-subtle cursor-pointer hover:bg-surface-hover ${
                  selected?.id === t.id ? "bg-accent/5 border-l-2 border-l-accent" : ""
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${modelBadge(t.model)}`}>
                    {t.model?.name || "?"}
                  </span>
                  <span className="text-[10px] text-text-muted">{formatMs(t.duration_ms)}</span>
                </div>
                <div className="text-xs text-text-primary font-medium truncate">{t.question}</div>
                <div className="text-[10px] text-text-muted mt-1 flex justify-between">
                  <span>{t.session_id}</span>
                  <span>{t.timestamp?.slice(11, 19)}</span>
                </div>
              </div>
            ))
          )}
        </div>

        {/* 右侧详情 */}
        <div className="flex-1 overflow-y-auto p-6">
          {!selected ? (
            <div className="text-xs text-text-muted">选择左侧一条记录查看详情</div>
          ) : (
            <div>
              {/* 查询信息 */}
              <div className="mb-4">
                <div className="bg-surface-base rounded-lg border border-border-subtle p-4 space-y-2 text-xs">
                  <div className="flex justify-between"><span className="text-text-muted">时间</span><span className="text-text-primary">{selected.timestamp}</span></div>
                  <div className="flex justify-between"><span className="text-text-muted">用户</span><span className="text-text-primary">{selected.session_id}</span></div>
                  <div className="flex justify-between">
                    <span className="text-text-muted">模型</span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] ${modelBadge(selected.model)}`}>
                      {selected.model?.name} ({selected.model?.provider})
                    </span>
                  </div>
                  <div className="flex justify-between"><span className="text-text-muted">问题</span><span className="text-text-primary text-right max-w-[60%]">{selected.question}</span></div>
                  {formatTokens(selected.usage) && (
                    <div className="flex justify-between">
                      <span className="text-text-muted">Token</span>
                      <span className="text-primary font-mono text-[11px]">{formatTokens(selected.usage)}</span>
                    </div>
                  )}
                  <div className="flex justify-between font-semibold pt-1 border-t border-border-subtle">
                    <span className="text-text-muted">总耗时</span>
                    <span className="text-accent">{formatMs(selected.duration_ms)}</span>
                  </div>
                </div>
              </div>

              {/* 步骤 */}
              <h2 className="text-sm font-semibold text-text-primary mb-2">
                执行步骤 ({selected.steps.length})
              </h2>
              <div className="space-y-1.5">
                {selected.steps.map((step) => (
                  <div key={step.id} className="bg-surface-base rounded-lg border border-border-subtle p-3">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span>{ICONS[step.id] || "⏱️"}</span>
                      <span className="text-xs font-medium text-text-primary">{step.label}</span>
                      <span className={`text-[9px] px-1 rounded ${statusColor(step.status)}`}>{statusLabel(step.status)}</span>
                      <span className="flex-1" />
                      <span className="text-[10px] text-text-muted">{formatMs(step.duration_ms)}</span>
                      <span className="text-[9px] text-text-muted w-8 text-right">{Math.round(step.duration_ratio * 100)}%</span>
                    </div>

                    {/* 进度条 */}
                    <div className="h-1 bg-surface-elevated rounded-full mb-1.5">
                      <div className="h-full bg-accent rounded-full" style={{ width: `${Math.min(step.duration_ratio * 100, 100)}%` }} />
                    </div>

                    {/* Metrics */}
                    {Object.keys(step.metrics).length > 0 && (
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                        {Object.entries(step.metrics).map(([k, v]) => (
                          <span key={k} className="text-[10px] text-text-muted">
                            <span>{METRIC_LABELS[k] || k}: </span>
                            <span className="text-text-primary font-mono">{typeof v === "number" && k.includes("ratio") ? v.toFixed(2) : v}</span>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

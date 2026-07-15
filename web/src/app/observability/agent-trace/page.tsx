"use client";

import { useEffect, useState, useCallback } from "react";

interface TraceStep {
  name: string;
  detail: string;
  hits: string;
  elapsed_ms: number;
}

interface Trace {
  id: string;
  timestamp: string;
  session_id: string;
  model: string;
  question: string;
  answer_preview: string;
  answer_len: number;
  total_ms: number;
  steps: TraceStep[];
}

const STEP_ICON: Record<string, string> = {
  "MultiQuery": "🔄",
  "检索+Rerank+LLM": "🔍",
  "Citation": "✅",
};

export default function AgentTracePage() {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [selected, setSelected] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchTraces = useCallback(async () => {
    try {
      const res = await fetch("/api/observability/rag-traces?limit=50");
      const data = await res.json();
      setTraces(data.traces || []);
      if (!selected && data.traces?.length > 0) {
        setSelected(data.traces[0]);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTraces();
  }, [fetchTraces]);

  const formatMs = (ms: number) => {
    if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
    return `${ms}ms`;
  };

  const modelLabel = (m: string) => {
    if (m.startsWith("deepseek")) return "bg-blue-100 text-blue-700";
    if (m.startsWith("MiniMax")) return "bg-purple-100 text-purple-700";
    return "bg-green-100 text-green-700";
  };

  return (
    <div className="h-full flex flex-col">
      <div className="px-6 py-4 border-b border-border-subtle">
        <h1 className="text-lg font-semibold text-text-primary">Agent Trace</h1>
        <p className="text-xs text-text-muted mt-1">
          RAG 全链路耗时记录 — 每次查询的每一步
        </p>
        <button
          onClick={() => { fetchTraces(); }}
          className="mt-2 text-xs text-accent hover:underline"
        >
          刷新
        </button>
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
                className={`px-4 py-3 border-b border-border-subtle cursor-pointer hover:bg-surface-hover transition-colors ${
                  selected?.id === t.id ? "bg-accent/5 border-l-2 border-l-accent" : ""
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${modelLabel(t.model)}`}>
                    {t.model}
                  </span>
                  <span className="text-[10px] text-text-muted">
                    {formatMs(t.total_ms)}
                  </span>
                </div>
                <div className="text-xs text-text-primary font-medium truncate">
                  {t.question}
                </div>
                <div className="text-[10px] text-text-muted mt-1 flex items-center justify-between">
                  <span>{t.session_id}</span>
                  <span>{t.timestamp.slice(11, 19)}</span>
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
              {/* 基本信息 */}
              <div className="mb-6">
                <h2 className="text-sm font-semibold text-text-primary mb-2">查询信息</h2>
                <div className="bg-surface-base rounded-lg border border-border-subtle p-4 space-y-2 text-xs">
                  <div className="flex justify-between">
                    <span className="text-text-muted">时间</span>
                    <span className="text-text-primary">{selected.timestamp}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-muted">用户/Session</span>
                    <span className="text-text-primary">{selected.session_id}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-muted">模型</span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] ${modelLabel(selected.model)}`}>
                      {selected.model}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-muted">问题</span>
                    <span className="text-text-primary text-right max-w-[60%]">{selected.question}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-muted">答案长度</span>
                    <span className="text-text-primary">{selected.answer_len} 字符</span>
                  </div>
                  <div className="flex justify-between font-semibold">
                    <span className="text-text-muted">总耗时</span>
                    <span className="text-accent">{formatMs(selected.total_ms)}</span>
                  </div>
                </div>
              </div>

              {/* 步骤详情 */}
              <div>
                <h2 className="text-sm font-semibold text-text-primary mb-2">
                  执行步骤
                </h2>
                <div className="space-y-1">
                  {selected.steps.map((step, i) => {
                    const pct = selected.total_ms > 0
                      ? Math.round((step.elapsed_ms / selected.total_ms) * 100)
                      : 0;
                    return (
                      <div
                        key={i}
                        className="bg-surface-base rounded-lg border border-border-subtle p-3 flex items-center gap-3"
                      >
                        <span className="text-sm">{STEP_ICON[step.name] || "⏱️"}</span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-xs font-medium text-text-primary">
                              {step.name}
                            </span>
                            <span className="text-[10px] text-text-muted">
                              {formatMs(step.elapsed_ms)}
                            </span>
                          </div>
                          <div className="text-[11px] text-text-muted truncate">
                            {step.detail}
                          </div>
                          <div className="flex items-center gap-2 mt-1">
                            <div className="flex-1 h-1 bg-surface-elevated rounded-full overflow-hidden">
                              <div
                                className="h-full bg-accent rounded-full transition-all"
                                style={{ width: `${Math.min(pct, 100)}%` }}
                              />
                            </div>
                            <span className="text-[9px] text-text-muted w-8 text-right">
                              {pct}%
                            </span>
                          </div>
                          {step.hits && (
                            <div className="text-[10px] text-accent mt-0.5">
                              {step.hits}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

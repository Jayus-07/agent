"use client";

import { useState, useEffect, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import { getTraceById } from "@/lib/observability/source";
import type { TraceRecord, Span } from "@/types/trace";
import { statusBadge, formatTime, formatRelative } from "@/types/trace";
import { knowledgeService } from "@/services/knowledge";
import { X, Loader2 } from "lucide-react";

/** 文档索引 span type → 中文标签 + 排序 */
const SPAN_LABELS: Record<string, { label: string; order: number }> = {
  load:       { label: "文件加载", order: 1 },
  parse:      { label: "解析文档", order: 2 },
  clean:      { label: "数据清洗", order: 3 },
  dedup:      { label: "去重检查", order: 4 },
  chunk:      { label: "文本分块", order: 5 },
  llm:        { label: "元数据生成", order: 6 },
  embedding:  { label: "向量嵌入", order: 7 },
  vector_db:  { label: "写入向量库", order: 8 },
  workflow:   { label: "索引编排", order: 0 },
};

function spanOrder(s: Span): number {
  return SPAN_LABELS[s.type]?.order ?? 99;
}

/** 兼容新旧关键词格式：[{word,source}] 或 ["string"] */
function normalizeKeywords(arr: unknown): {word: string; source: string}[] {
  if (!Array.isArray(arr) || arr.length === 0) return [];
  if (typeof arr[0] === "string") return (arr as string[]).map(w => ({word: w, source: ""}));
  return arr as {word: string; source: string}[];
}

/** LLM 策略映射 */
const STRATEGY_LABELS: Record<string, string> = {
  rule_first:  "规则优先",
  llm_force:   "LLM 强制",
  dual_merge:  "双线合并",
};

interface ChunkDetail {
  chunk_index: number;
  content: string;
  token_count: number;
  keywords: string;
}

export default function DocTracePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());

  // ③ Chunk 详情面板
  const [chunkPanelOpen, setChunkPanelOpen] = useState(false);
  const [chunkDetailData, setChunkDetailData] = useState<ChunkDetail[]>([]);
  const [chunkDetailLoading, setChunkDetailLoading] = useState(false);
  const [chunkDetailError, setChunkDetailError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const t = await getTraceById(id);
      if (!cancelled) {
        setTrace(t);
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [id]);

  // ⑦ ESC 关闭侧边面板
  useEffect(() => {
    if (!chunkPanelOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setChunkPanelOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chunkPanelOpen]);

  const childSpans = useMemo(() => {
    if (!trace) return [];
    return (trace.spans || []).filter((s) => s.parent_id !== null).sort((a, b) => spanOrder(a) - spanOrder(b));
  }, [trace]);

  const typeGroups = useMemo(() => {
    const map = new Map<string, Span[]>();
    for (const s of childSpans) {
      if (!map.has(s.type)) map.set(s.type, []);
      map.get(s.type)!.push(s);
    }
    return Array.from(map.entries()).sort((a, b) => spanOrder(a[1][0]) - spanOrder(b[1][0]));
  }, [childSpans]);

  // ③ 获取 doc_id：优先从 tags，fallback 从 span metrics 提取
  const docIdFromTags = trace?.tags?.doc_id || "";
  // fallback: 从 chunk/embed span metrics 中找 doc_id
  const docIdFromSpans = useMemo(() => {
    if (docIdFromTags) return "";
    for (const s of trace?.spans || []) {
      const m = s.metrics as Record<string, unknown> | undefined;
      if (m?.doc_id) return String(m.doc_id);
    }
    return "";
  }, [trace, docIdFromTags]);
  const docId = docIdFromTags || docIdFromSpans;
  const handleOpenChunkDetail = async () => {
    if (!docId) return;
    setChunkPanelOpen(true);
    setChunkDetailLoading(true);
    setChunkDetailError("");
    try {
      const res = await knowledgeService.getChunkDetail(docId);
      const data = (res as any)?.chunks ?? (res as any)?.data?.chunks ?? [];
      setChunkDetailData(data);
    } catch (e) {
      setChunkDetailError((e as Error).message);
    } finally {
      setChunkDetailLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-slate-400">加载中…</p>
      </div>
    );
  }

  if (!trace) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-400">Trace {id} 不存在或已过期</p>
          <button onClick={() => router.back()} className="mt-3 text-sm text-violet-600 hover:text-violet-500">
            ← 返回
          </button>
        </div>
      </div>
    );
  }

  const badge = statusBadge(trace.status ?? "success");

  const toggleTypeExpand = (type: string) => {
    const next = new Set(expandedTypes);
    next.has(type) ? next.delete(type) : next.add(type);
    setExpandedTypes(next);
  };

  /** 获取摘要指标：合并同组所有 span 的 metrics（跳过 chunk_id / attempt） */
  function summaryMetrics(spans: Span[]): string {
    const merged: Record<string, unknown> = {};
    for (const s of spans) {
      if (!s.metrics) continue;
      for (const [k, v] of Object.entries(s.metrics)) {
        if (k === "chunk_id" || k === "attempt") continue;
        if (merged[k] === undefined) merged[k] = v;
      }
    }
    const entries = Object.entries(merged);
    if (!entries.length) return "";
    return entries.map(([k, v]) => `${k}: ${typeof v === "number" ? v : String(v)}`).join(" · ");
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-5">
        {/* 返回 */}
        <button onClick={() => router.push("/knowledge/operations")} className="text-xs text-slate-400 hover:text-slate-600">
          ← 返回操作中心
        </button>

        {/* 文档信息卡片 */}
        <div className="bg-white border border-slate-200 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <h1 className="text-base font-semibold text-slate-800">{trace.question}</h1>
              <span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${badge.bg}`}>
                {badge.label}
              </span>
            </div>
            <span className="text-xs text-slate-400" title={formatTime(trace.timestamp)}>
              {formatRelative(trace.timestamp)}
            </span>
          </div>
          <div className="flex items-center gap-6 text-xs text-slate-500">
            <span>总耗时 <span className="font-mono font-medium text-slate-700">{trace.duration_ms}ms</span></span>
            <span>Span <span className="font-mono font-medium text-slate-700">{childSpans.length}</span></span>
            {docId && <span>Doc <span className="font-mono text-[10px] text-slate-400">{docId}</span></span>}
            <span>Trace <span className="font-mono text-[10px] text-slate-400">{trace.id}</span></span>
          </div>
        </div>

        {/* 处理流水线 */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-slate-100">
            <h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider">📦 处理流水线</h2>
          </div>
          <div className="divide-y divide-slate-50">
            {typeGroups.map(([type, spans]) => {
              const info = SPAN_LABELS[type] ?? { label: spans[0]?.name || type, order: 99 };
              const totalMs = spans.reduce((sum, s) => sum + (s.duration_ms || 0), 0);
              const hasError = spans.some((s) => s.status === "error");
              const hasSkipped = spans.some((s) => s.status === "skipped");
              const statusIcon = hasError ? "❌" : hasSkipped ? "⚠️" : "✅";
              const isMulti = spans.length > 1;
              const expanded = expandedTypes.has(type);
              const summary = summaryMetrics(spans);

              // ── ② clean: 清洗卡片 ──
              if (type === "clean") {
                const s = spans[0];
                const metrics = s.metrics || {};
                const charsBefore = metrics.chars_before as number || 0;
                const charsAfter = metrics.chars_after as number || 0;
                const operations = metrics.operations as string || "";
                const errMsg = metrics.error as string || "";
                const reduction = charsBefore > 0 ? (1 - charsAfter / charsBefore) * 100 : 0;
                const isNoop = charsBefore > 0 && charsBefore === charsAfter;
                return (
                  <div key={type} className={`px-5 py-4 ${hasError ? "bg-red-50/30" : ""}`}>
                    <div className="flex items-center gap-4 mb-2">
                      <span className="text-xs w-5">{statusIcon}</span>
                      <span className="text-sm font-medium text-slate-700">{info.label}</span>
                      <span className="font-mono text-xs text-slate-500">{totalMs}ms</span>
                      {operations && operations !== "none" && (
                        <span className="text-xs text-slate-400 truncate max-w-[300px]">{operations}</span>
                      )}
                      {isNoop && !hasError && (
                        <span className="text-[10px] text-slate-300">（文本已规范，无需清洗）</span>
                      )}
                    </div>
                    <div className="ml-9 space-y-1 text-xs">
                      {hasError ? (
                        <div className="text-red-600 bg-red-50 rounded px-2 py-1 font-mono text-[11px]">
                          ❌ 清洗失败：{errMsg || "未知错误"}（已降级使用原始文本继续）
                        </div>
                      ) : (
                        <>
                          {charsBefore > 0 && (
                            <div className="text-slate-500">
                              清洗前 <span className="font-mono text-slate-600">{charsBefore.toLocaleString()}</span> 字符
                              → 清洗后 <span className="font-mono text-slate-600">{charsAfter.toLocaleString()}</span> 字符
                              <span className={`ml-1 ${isNoop ? "text-slate-300" : "text-slate-400"}`}>
                                （{reduction.toFixed(1)}% 缩减）
                              </span>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                );
              }

              // ── chunk: 预览卡片 + ③ 完整内容按钮 ──
              if (type === "chunk") {
                const firstSpan = spans[0];
                const output: Record<string, unknown> = (firstSpan?.output || {}) as Record<string, unknown>;
                const preview: string[] = (output["preview"] as string[]) || [];
                const total: number = (output["total"] as number) || (firstSpan?.metrics?.kept_chunks as number) || 0;
                const rawChunks = firstSpan?.metrics?.raw_chunks as number || 0;
                const filteredOut = firstSpan?.metrics?.filtered_out as number || 0;
                return (
                  <div key={type} className="px-5 py-4">
                    <div className="flex items-center gap-4 mb-2">
                      <span className="text-xs w-5">{statusIcon}</span>
                      <span className="text-sm font-medium text-slate-700">{info.label}</span>
                      <span className="font-mono text-xs text-slate-500">{totalMs}ms</span>
                      <span className="text-xs text-slate-400">
                        切分 <span className="font-mono text-slate-600">{total}</span> 块
                        {rawChunks > 0 && (
                          <span className="text-slate-400 ml-1">
                            （原始 {rawChunks}{filteredOut > 0 ? `，过滤 ${filteredOut}` : ""}）
                          </span>
                        )}
                      </span>
                      {/* ③ 查看完整 chunk 按钮 */}
                      {docId && total > 0 && (
                        <button
                          onClick={handleOpenChunkDetail}
                          className="ml-auto text-xs text-accent hover:text-accent-hover hover:underline"
                        >
                          📋 查看完整内容 →
                        </button>
                      )}
                    </div>
                    {preview.length > 0 && (
                      <div className="ml-9 space-y-1 mb-2">
                        {preview.map((text, i) => (
                          <div key={i} className="text-xs text-slate-500 bg-slate-50 rounded px-2 py-1 truncate">
                            <span className="text-slate-300 mr-1">预览块 {i + 1}</span>
                            {text.slice(0, 120)}{text.length > 120 ? "…" : ""}
                          </div>
                        ))}
                      </div>
                    )}
                    {total > 3 && (
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleTypeExpand(type); }}
                        className="ml-9 text-xs text-accent hover:text-accent-hover"
                      >
                        {expanded ? "收起" : `查看全部 ${total} 个 Chunks 预览`} →
                      </button>
                    )}
                    {expanded && (
                      <div className="ml-9 mt-2 space-y-1 max-h-60 overflow-y-auto">
                        {spans.map((s, i) => {
                          const so = (s.output || {}) as Record<string, unknown>;
                          const pa = (so["preview"] as string[]) || [];
                          const chunkText = pa[0] || "";
                          return (
                            <div key={s.id || i} className="flex items-start gap-2 text-xs text-slate-500 bg-slate-50 rounded px-2 py-1">
                              <span className="text-slate-300 shrink-0 mt-0.5">#{i + 1}</span>
                              <span className="truncate">{chunkText || (s.metrics ? `attempt: ${s.metrics.attempt}` : "")}</span>
                              <span className="font-mono text-slate-400 shrink-0">{s.duration_ms}ms</span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              }

              // ── ④ llm / metadata: 默认折叠，点击展开 ──
              if (type === "llm") {
                const out: Record<string, unknown> = (spans[0]?.output || {}) as Record<string, unknown>;
                const ruleMeta = (out["rule_metadata"] || out) as Record<string, unknown>;
                const llmMeta = (out["llm_metadata"] || out) as Record<string, unknown>;
                const kwsRule = normalizeKeywords(ruleMeta["keywords_rule"]);
                const kwsLlmRaw = normalizeKeywords(llmMeta["keywords_llm"]);
                // ⑥ 去重：LLM 关键词中剔除已在规则中出现的（大小写不敏感），统一首字母大写
                const ruleWordLower = new Set(kwsRule.map(k => k.word.toLowerCase()));
                const kwsLlm = kwsLlmRaw
                  .map(k => ({ word: k.word.charAt(0).toUpperCase() + k.word.slice(1).toLowerCase(), source: k.source }))
                  .filter(k => !ruleWordLower.has(k.word.toLowerCase()));
                const llmUsed = (llmMeta["llm_used"] as boolean) || false;
                const llmTokens = (llmMeta["llm_tokens"] as Record<string, number>) || {};
                const llmStrategy = (llmMeta["llm_strategy"] as string) || "";
                const llmDecision = (llmMeta["llm_decision"] as Record<string, unknown>) || {};
                const llmScore = llmDecision["llm_score"] as number || 0;
                const llmReason = (llmDecision["llm_reason"] as string) || "";
                const docType = (ruleMeta["doc_type"] as string) || (spans[0]?.metrics?.doc_type as string) || "";
                const domain = (ruleMeta["business_domain"] as string) || (out["business_domain"] as string) || "";
                const persons = (ruleMeta["person_names"] as string) || (out["person_names"] as string) || "";
                const confidence = (ruleMeta["confidence"] as number) || 0;
                const complexity = (ruleMeta["complexity"] as Record<string, unknown>) || {};
                const timeRefs = (ruleMeta["time_refs"] as string[]) || [];

                return (
                  <div key={type} className="px-5 py-3 cursor-pointer hover:bg-violet-50/50 transition-colors"
                    onClick={() => toggleTypeExpand(type)}>
                    {/* ── 标题行（始终可见）── */}
                    <div className="flex items-center gap-4 flex-wrap">
                      <span className="text-xs w-5">{statusIcon}</span>
                      <span className="text-sm font-medium text-slate-700">{info.label}</span>
                      <span className="font-mono text-xs text-slate-500">{totalMs}ms</span>
                      {llmUsed && (
                        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-200 text-violet-700">
                          🔮 LLM
                        </span>
                      )}
                      {llmStrategy && (
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
                          llmStrategy === "llm_force" ? "bg-purple-100 text-purple-700" :
                          llmStrategy === "dual_merge" ? "bg-indigo-100 text-indigo-700" :
                          "bg-slate-100 text-slate-600"
                        }`}>
                          {STRATEGY_LABELS[llmStrategy] || llmStrategy}
                        </span>
                      )}
                      {confidence > 0 && (
                        <span className="text-[10px] text-slate-400">
                          规则置信度 <span className="font-mono text-slate-500">{(confidence * 100).toFixed(0)}%</span>
                        </span>
                      )}
                      <span className="ml-auto text-[10px] text-slate-300">{expanded ? "▲ 收起" : "▼ 展开"}</span>
                    </div>
                    {/* ── 详情区（仅展开时渲染）── */}
                    {expanded && (
                      <div className="mt-3 ml-9 space-y-2" onClick={(e) => e.stopPropagation()}>
                        {llmReason && (
                          <div className="text-[11px] text-slate-500 bg-violet-50/50 rounded px-2 py-1 leading-relaxed">
                            💡 {llmReason}
                            {llmScore > 0 && <span className="text-slate-400 ml-1">（LLM 价值评分 {llmScore}）</span>}
                          </div>
                        )}
                        {llmUsed && typeof llmTokens["prompt_tokens"] === "number" && (llmTokens["prompt_tokens"] > 0 || (llmTokens["completion_tokens"] ?? 0) > 0) && (
                          <div className="flex items-center gap-3 text-[10px] text-slate-400">
                            <span>Token: </span>
                            <span className="font-mono">入 {llmTokens["prompt_tokens"]}</span>
                            <span className="font-mono">出 {llmTokens["completion_tokens"] ?? 0}</span>
                          </div>
                        )}
                        <div className="flex flex-wrap gap-1.5">
                          {docType && (
                            <span className="inline-block px-2 py-0.5 rounded text-[10px] font-medium bg-blue-100 text-blue-700">
                              📄 {docType}
                            </span>
                          )}
                          {domain && (
                            <span className="inline-block px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-100 text-emerald-700">
                              🏭 {domain}
                            </span>
                          )}
                          {persons && persons !== "" && persons.split(",").filter(Boolean).map((p) => (
                            <span key={p.trim()} className="inline-block px-2 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700">
                              🏷️ {p.trim()}
                            </span>
                          ))}
                        </div>
                        {complexity && Object.keys(complexity).length > 0 && (
                          <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                            <span className="text-slate-400 mr-0.5">📊</span>
                            {(complexity["keyword_coverage"] as string) && (
                              <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 font-medium">
                                覆盖度 {complexity["keyword_coverage"] as string}
                              </span>
                            )}
                            {complexity["structure_score"] !== undefined && (
                              <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">
                                🏗️ 结构 {(complexity["structure_score"] as number)}
                              </span>
                            )}
                            {complexity["risk_keyword_hits"] !== undefined && (
                              <span className={`px-1.5 py-0.5 rounded font-medium ${
                                (complexity["risk_keyword_hits"] as number) > 0 ? "bg-red-50 text-red-600" : "bg-slate-100 text-slate-500"
                              }`}>
                                ⚠️ 风险命中 {(complexity["risk_keyword_hits"] as number)}
                              </span>
                            )}
                            {complexity["token_estimate"] !== undefined && (
                              <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-400 font-mono">
                                ~{(complexity["token_estimate"] as number)} tokens
                              </span>
                            )}
                          </div>
                        )}
                        {timeRefs && timeRefs.length > 0 && (
                          <div className="flex flex-wrap gap-1 items-center">
                            <span className="text-[10px] text-slate-400">🕐</span>
                            {timeRefs.map((t, i) => (
                              <span key={i} className="inline-block px-1.5 py-0.5 rounded text-[10px] bg-slate-100 text-slate-500 font-mono">{t}</span>
                            ))}
                          </div>
                        )}
                        {kwsLlm.length > 0 && (
                          <div>
                            <span className="text-[10px] text-violet-400 mr-1">🔮 LLM 提取</span>
                            <div className="flex flex-wrap gap-1 mt-1">
                              {kwsLlm.map((kw) => (
                                <span key={kw.word} className="inline-block px-1.5 py-0.5 rounded text-[10px] bg-violet-100 text-violet-700 ring-1 ring-violet-200">{kw.word}</span>
                              ))}
                            </div>
                          </div>
                        )}
                        {kwsRule.length > 0 && (
                          <div>
                            <span className="text-[10px] text-slate-400 mr-1">📋 规则提取</span>
                            <div className="flex flex-wrap gap-1 mt-1">
                              {kwsRule.map((kw, i) => (
                                <span key={i} className="inline-block px-1.5 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600">{kw.word}</span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              }

              // ── ⑤ embedding: 汇总卡片 ──
              if (type === "embedding") {
                const s = spans[0];
                const attempted = s.metrics?.attempted as number || 0;
                const succeeded = s.metrics?.succeeded as number || 0;
                const failed = s.metrics?.failed as number || 0;
                return (
                  <div key={type} className={`px-5 py-4 ${hasError ? "bg-red-50/30" : ""}`}>
                    <div className="flex items-center gap-4">
                      <span className="text-xs w-5">{statusIcon}</span>
                      <span className="text-sm font-medium text-slate-700">{info.label}</span>
                      <span className="font-mono text-xs text-slate-500">{totalMs}ms</span>
                      <span className="text-xs text-slate-400">
                        成功 <span className="font-mono text-green-600">{succeeded}</span>
                        {failed > 0 && (
                          <span className="ml-1">失败 <span className="font-mono text-red-500">{failed}</span></span>
                        )}
                        <span className="ml-1">/ 共 <span className="font-mono text-slate-500">{attempted}</span></span>
                      </span>
                    </div>
                  </div>
                );
              }

              // ── dedup: 去重检查（非折叠，展示命中状态）──
              if (type === "dedup") {
                const s = spans[0];
                const cached = s.metrics?.cached as boolean | undefined;
                const existingDocId = s.metrics?.existing_doc_id as string || "";
                return (
                  <div key={type} className="px-5 py-4">
                    <div className="flex items-center gap-4">
                      <span className="text-xs w-5">{statusIcon}</span>
                      <span className="text-sm font-medium text-slate-700">{info.label}</span>
                      <span className="font-mono text-xs text-slate-500">{totalMs}ms</span>
                      {cached ? (
                        <span className="text-xs text-amber-600">
                          ⚡ 命中缓存（SHA256 一致，跳过索引）→ doc {existingDocId}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-400">未命中缓存 → 进入完整索引流程</span>
                      )}
                    </div>
                  </div>
                );
              }
              return (
                <div key={type}
                  className={`flex items-center gap-4 px-5 py-2.5 text-sm ${isMulti ? "cursor-pointer hover:bg-slate-50" : ""}`}
                  onClick={() => isMulti && toggleTypeExpand(type)}
                >
                  <span className="text-xs w-5">{statusIcon}</span>
                  <span className="w-24 text-slate-500">{info.label}</span>
                  <span className={`font-mono text-xs w-16 text-right ${hasError ? "text-red-500" : "text-slate-400"}`}>
                    {totalMs}ms
                  </span>
                  <span className="text-xs text-slate-300 flex-1 truncate">
                    {isMulti && <span className="mr-1">×{spans.length}</span>}
                    {summary}
                  </span>
                  {isMulti && <span className="text-[10px] text-slate-300">{expanded ? "▲" : "▼"}</span>}
                </div>
              );
            })}
            {childSpans.length === 0 && (
              <div className="px-5 py-8 text-center text-xs text-slate-400">无 Span 数据</div>
            )}
          </div>
        </div>

        {/* ③ Chunk 详情侧边面板 */}
        {chunkPanelOpen && (
          <div className="fixed inset-0 z-50 flex justify-end">
            <div className="absolute inset-0 bg-black/20" onClick={() => setChunkPanelOpen(false)} />
            <div className="relative w-[520px] bg-white shadow-xl border-l border-slate-200 flex flex-col h-full">
              {/* 面板头 */}
              <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 shrink-0">
                <div>
                  <h3 className="text-sm font-semibold text-slate-800">📋 Chunk 完整内容</h3>
                  <p className="text-[10px] text-slate-400 mt-0.5">文档 {docId}</p>
                </div>
                <button onClick={() => setChunkPanelOpen(false)}
                  className="p-1 rounded-md hover:bg-slate-100 text-slate-400">
                  <X size={16} />
                </button>
              </div>
              {/* 面板体 */}
              <div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">
                {chunkDetailLoading && (
                  <div className="flex items-center justify-center py-12">
                    <Loader2 size={20} className="animate-spin text-accent" />
                    <span className="ml-2 text-sm text-slate-400">加载中...</span>
                  </div>
                )}
                {chunkDetailError && (
                  <div className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">{chunkDetailError}</div>
                )}
                {!chunkDetailLoading && !chunkDetailError && chunkDetailData.length === 0 && (
                  <p className="text-sm text-slate-400 text-center py-8">暂无 Chunk 数据（可能尚未索引或 chunk_store 未写入）</p>
                )}
                {!chunkDetailLoading && chunkDetailData.map((ch, i) => (
                  <div key={i} className="bg-slate-50 rounded-lg p-3 border border-slate-100">
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[10px] font-medium text-slate-400">Chunk #{ch.chunk_index}</span>
                      <div className="flex items-center gap-2 text-[10px] text-slate-400">
                        <span>{ch.token_count} tokens</span>
                        {ch.keywords && (
                          <span className="text-violet-400 truncate max-w-[200px]">🏷 {ch.keywords}</span>
                        )}
                      </div>
                    </div>
                    <p className="text-xs text-slate-700 leading-relaxed whitespace-pre-wrap break-words">
                      {ch.content}
                    </p>
                  </div>
                ))}
              </div>
              <div className="px-5 py-2.5 border-t border-slate-100 text-[10px] text-slate-400 shrink-0">
                共 {chunkDetailData.length} 个 Chunk
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

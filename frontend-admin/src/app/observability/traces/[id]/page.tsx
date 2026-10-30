"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Sparkles } from "lucide-react";
import { getTraceDetail } from "@/api/observability";
import { evaluationService } from "@/api/evaluation";
import TraceBreadcrumb from "@/components/observability/trace/TraceBreadcrumb";
import InputOutputPanel from "@/components/observability/trace/InputOutputPanel";
import SpanGantt from "@/components/observability/trace/SpanGantt";
import type { Span, TraceRecord } from "@/types/trace";
import { formatCost, formatTime, safeNum } from "@/types/trace";
import { getRunStatus, getRunStatusLabel, getRunStatusTone, safeRuntimeLabel } from "@/lib/observability/runtimeStatus";
import { deriveDomainLabel, deriveRunKind, runKindBadgeClass } from "@/lib/observability/runKind";

/**
 * Trace 详情页（2026-10-30 按模拟页「四问」布局重写，不复用旧 StepTimeline/FlameGraph/
 * GraphTopology 等区块组件）：
 *   ① 错在哪——问题与结果 + 错误诊断卡（含「错误题加入候选评测集」入口）
 *   ② 慢在哪——Span 甘特时间线（SpanGantt，含未埋点黑洞）
 *   ③ 答了什么——LLM 调用明细（含 Rerank/多查询等全部 llm_call 与跳过阶段）
 *   ④ 花了多少——概要侧卡（成本/Token/模型/Prompt 版本/SLA 语义修正）
 * 内容读取审计由后端 get_trace 写入；SLA 对失败运行显示「未参与判定」。
 */

function traceReadFailure(error: unknown): { title: string; message: string } {
  const status = (error as { status?: number } | null)?.status;
  if (status === 401) return { title: "登录状态已失效", message: "请重新登录后再读取 Trace。" };
  if (status === 403) return { title: "当前身份无权读取", message: "该 Trace 受资源级权限保护。" };
  if (status === 429) return { title: "请求过于频繁", message: "请稍后重试读取 Trace。" };
  if (status && status >= 500) return { title: "Trace 服务暂不可用", message: "服务端读取失败，请稍后重试。" };
  return { title: "Trace 服务暂不可用", message: "读取详情失败，请检查连接后重试。" };
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

/**
 * Prompt 版本快照两形态兼容：runner 写入 {key: version} map 快照，部分链路写 list。
 * map 按 key 数计数、list 按元素数计数；两者皆空/缺省如实显示「未采集」。
 */
function promptVersionsLabel(metadata: Record<string, unknown> | undefined): string {
  const pv = metadata?.prompt_versions;
  if (Array.isArray(pv)) return pv.length > 0 ? `${pv.length} 个` : "未采集";
  if (pv && typeof pv === "object") {
    const count = Object.keys(pv as Record<string, unknown>).length;
    return count > 0 ? `${count} 个` : "未采集";
  }
  return "未采集";
}

export default function TraceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [parent, setParent] = useState<TraceRecord | null>(null);
  const [children, setChildren] = useState<TraceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ title: string; message: string } | null>(null);
  const [relationshipLoadError, setRelationshipLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [candidateState, setCandidateState] = useState<"idle" | "creating" | "done" | "error">("idle");
  const [candidateId, setCandidateId] = useState<string>("");

  const requestedReturnTo = searchParams.get("returnTo") || "";
  const returnTo = requestedReturnTo === "/observability/monitoring"
    || requestedReturnTo.startsWith("/observability/monitoring?")
    ? requestedReturnTo
    : "/observability/monitoring?tab=traces";
  const traceHref = (traceId: string) => `/observability/traces/${encodeURIComponent(traceId)}?${new URLSearchParams({ returnTo }).toString()}`;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setTrace(null);
    setLoadError(null);
    setRelationshipLoadError(false);
    setCandidateState("idle");
    setCandidateId("");
    (async () => {
      try {
        const t = await getTraceDetail(id);
        if (cancelled) return;
        setTrace(t);
        if (!t) {
          setParent(null);
          setChildren([]);
          setLoading(false);
          return;
        }
        const relationError = () => { if (!cancelled) setRelationshipLoadError(true); };
        const pTask = t.parent_id
          ? getTraceDetail(t.parent_id).catch(() => { relationError(); return null; })
          : Promise.resolve(null);
        const descendants: TraceRecord[] = [];
        const seen = new Set<string>([t.id]);
        let pending = [...(t.children_ids || [])];
        while (pending.length > 0 && descendants.length < 100) {
          const batch = [...new Set(pending.filter((childId) => !seen.has(childId)))].slice(0, 25);
          if (batch.length === 0) break;
          const batchIds = new Set(batch);
          pending = pending.filter((childId) => !batchIds.has(childId));
          batch.forEach((childId) => seen.add(childId));
          const fetched = await Promise.all(batch.map((childId) => getTraceDetail(childId).catch(() => { relationError(); return null; })));
          for (const child of fetched) {
            if (!child) continue;
            descendants.push(child);
            pending.push(...(child.children_ids || []));
          }
        }
        const p = await pTask;
        if (cancelled) return;
        setParent(p);
        setChildren(descendants);
        setLoading(false);
      } catch (error) {
        if (cancelled) return;
        setTrace(null);
        setLoadError(traceReadFailure(error));
        setParent(null);
        setChildren([]);
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [id, loadAttempt]);

  const spans = useMemo(() => (trace?.spans || []) as Span[], [trace]);
  const runStatus = trace ? getRunStatus(trace) : "unknown";
  const hasError = Boolean(trace?.error && Object.keys(trace.error).length > 0);
  const err = (trace?.error ?? {}) as Record<string, unknown>;
  const errorCode = typeof err.code === "string" ? err.code.trim() : "";
  const errorMessage = typeof err.message === "string" ? err.message.trim() : "";
  const errorClass = typeof err.class === "string" ? err.class.trim() : "";

  const rootSpan = useMemo(() => spans.find((s) => s.parent_id === null), [spans]);
  const uncoveredMs = rootSpan ? Number(rootSpan.metrics?.uncovered_ms ?? 0) : 0;
  const llmSpans = useMemo(() => spans.filter((s) => s.type === "llm_call"), [spans]);
  const skippedStages = useMemo(() => {
    const raw = rootSpan?.metrics?.skipped_stages;
    return Array.isArray(raw) ? raw : [];
  }, [rootSpan]);

  const addErrorToEvalSet = async () => {
    if (!trace || candidateState === "creating") return;
    setCandidateState("creating");
    try {
      const candidate = await evaluationService.intakeV2TraceCandidate(trace.id, `trace-${trace.id}`);
      setCandidateId(candidate?.candidate_id || "");
      setCandidateState("done");
    } catch {
      setCandidateState("error");
    }
  };

  if (loading && !trace) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="text-center text-sm text-slate-500" role="status">正在读取 Trace 详情…</div>
      </div>
    );
  }

  if (!trace) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
        <div className="max-w-md rounded-lg border border-slate-200 bg-white p-6 text-center">
          <h1 className="font-semibold text-slate-900">{loadError?.title || "无法查看此 Trace"}</h1>
          <p className="mt-2 text-sm text-slate-600">{loadError?.message || "记录不存在，或当前身份无权查看该受保护资源。"}</p>
          <div className="mt-4 flex justify-center gap-3">
            {loadError && <button type="button" onClick={() => setLoadAttempt((a) => a + 1)} className="min-h-10 rounded-md border border-slate-300 px-3 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">重试</button>}
            <button type="button" onClick={() => router.push(returnTo)} className="min-h-10 rounded-md bg-blue-700 px-3 text-sm font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2">返回执行记录</button>
          </div>
        </div>
      </div>
    );
  }

  const kind = deriveRunKind(trace);
  const domain = deriveDomainLabel(trace);
  const isFailure = runStatus === "error" || runStatus === "timeout" || runStatus === "failed";

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-[1440px] space-y-5 px-4 py-5 sm:px-6 sm:py-6">
        <TraceBreadcrumb
          crumbs={[
            { label: "运行控制中心", href: returnTo },
            { label: "执行记录", href: returnTo },
            ...(parent ? [{ label: `父 · ${parent.id.slice(0, 8)}`, href: traceHref(parent.id) }] : []),
            { label: trace.id.slice(0, 12) },
          ]}
        />

        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => router.push(returnTo)} className="min-h-10 rounded px-2 text-sm text-slate-600 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">← 返回列表</button>
          <span className="font-mono font-semibold text-slate-700">{trace.id.slice(0, 12)}</span>
          <span className={`inline-flex rounded border px-2 py-1 text-xs font-semibold ${getRunStatusTone(runStatus)}`}>{getRunStatusLabel(runStatus)}</span>
          <span className={`inline-flex rounded border px-2 py-0.5 text-xs font-medium ${runKindBadgeClass(kind.key)}`}>{kind.label}</span>
          <span className="inline-flex rounded border border-dashed border-slate-300 px-2 py-0.5 text-xs text-slate-500">{domain}</span>
          {trace.sla?.breached && <span className="inline-flex items-center rounded bg-red-100 px-2 py-0.5 text-[10px] font-semibold text-red-700">⚠️ SLA 违反 ({trace.sla.threshold_ms}ms)</span>}
          <button type="button" onClick={() => void navigator.clipboard.writeText(trace.id)} className="min-h-10 rounded border border-slate-200 px-2 text-xs text-slate-600 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">复制 ID</button>
          <span className="text-[10px] text-slate-400" title={formatTime(trace.timestamp)}>{formatTime(trace.timestamp)}</span>
        </div>

        {(parent || children.length > 0) && (
          <div className="rounded-xl border border-violet-200 bg-violet-50/50 p-4">
            <p className="mb-2 text-[10px] uppercase tracking-widest text-violet-700">🔗 Trace 关联</p>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              {parent && (
                <Link href={traceHref(parent.id)} className="inline-flex items-center gap-1 rounded border border-violet-200 bg-white px-2 py-0.5 font-mono text-violet-700 hover:border-violet-400">
                  ↳ 父 {parent.id.slice(0, 8)}
                </Link>
              )}
              {children.map((c) => (
                <Link key={c.id} href={traceHref(c.id)} className="inline-flex items-center gap-1 rounded border border-violet-200 bg-white px-2 py-0.5 font-mono text-violet-700 hover:border-violet-400">
                  {c.id.slice(0, 8)}
                  <span className={`rounded border px-1 py-0.5 text-[10px] ${getRunStatusTone(getRunStatus(c))}`}>{getRunStatusLabel(getRunStatus(c))}</span>
                </Link>
              ))}
            </div>
          </div>
        )}

        {relationshipLoadError && <p className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">Trace 本身已读取；部分父子关联记录暂不可用。</p>}

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-4">
          <div className="space-y-5 lg:col-span-3">
            <section>
              <h2 className="mb-3 text-sm font-semibold text-slate-700">① 错在哪 · 业务结果与错误诊断</h2>
              <div className="space-y-3">
                <InputOutputPanel question={trace.question} answer={trace.answer_preview} answerLen={trace.answer_len} error={trace.error} />
                {hasError && (
                  <div className="rounded-xl border border-red-200 bg-red-50 p-5">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-semibold text-red-700">{errorCode ? `错误码：${errorCode}` : "错误码：未采集"}</p>
                        {errorMessage ? (
                          <p className="mt-1 whitespace-pre-wrap break-words text-sm text-red-600">{errorMessage}</p>
                        ) : (
                          <p className="mt-1 text-sm text-red-600">错误消息未采集；请结合下方失败步骤与受控服务端日志排查。</p>
                        )}
                        {errorClass && <p className="mt-1 text-xs text-red-500">错误类别：{errorClass}</p>}
                        <p className="mt-1 text-xs text-red-500">
                          失败步骤：{safeRuntimeLabel(String(err.error_node ?? ""), "未记录")} · 重试 {typeof err.retry_count === "number" ? safeNum(err.retry_count) : "未采集"} 次
                        </p>
                      </div>
                      <span className="font-mono text-xs text-red-400">{formatTime(trace.timestamp)}</span>
                    </div>
                    <div className="mt-3 border-t border-red-100 pt-3">
                      {candidateState === "done" ? (
                        <p className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700" role="status">
                          ✓ 已创建评测候选{candidateId ? ` ${candidateId}` : ""}（来源 trace {trace.id}）· 待评测中心「候选审核」批准后进入不可变数据集版本
                        </p>
                      ) : (
                        <>
                          <button
                            type="button"
                            onClick={() => void addErrorToEvalSet()}
                            disabled={candidateState === "creating"}
                            className="min-h-10 rounded-md border border-violet-400 bg-white px-3 text-sm font-medium text-violet-700 hover:bg-violet-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 disabled:opacity-60"
                          >
                            {candidateState === "creating" ? "正在创建候选…" : "将本题加入候选评测集"}
                          </button>
                          {candidateState === "error" && <span className="ml-2 text-xs text-red-600" role="alert">创建失败，请稍后重试或到评测中心手工录入。</span>}
                          <p className="mt-1.5 text-[11px] text-red-400">提交后进入评测中心「候选审核」（批准/驳回），批准进入不可变数据集版本用于回归。</p>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </section>

            <section>
              <h2 className="mb-3 text-sm font-semibold text-slate-700">② 慢在哪 · 耗时分析（Span 甘特时间线）</h2>
              <div className="rounded-xl border border-slate-200 bg-white p-5">
                {spans.length > 0 ? (
                  <SpanGantt spans={spans} totalMs={trace.duration_ms} uncoveredMs={uncoveredMs} />
                ) : (
                  <p className="py-6 text-center text-sm text-slate-400">本 Trace 无 Span 记录（失败过早或采集链未覆盖）。</p>
                )}
              </div>
            </section>

            <section>
              <h2 className="mb-3 text-sm font-semibold text-slate-700">③ 答了什么 · LLM 调用（含多查询 / Provider 尝试）</h2>
              <div className="space-y-3">
                {skippedStages.length > 0 && (
                  <div className="rounded-md border border-dashed border-slate-300 px-3 py-2 text-xs text-slate-600" role="note">
                    ⏭ 跳过阶段：{skippedStages.map((s) => (typeof s === "object" && s && "name" in s ? String((s as Record<string, unknown>).name) : String(s))).join("、")}
                  </div>
                )}
                {llmSpans.length === 0 && (
                  <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-500">本次 Trace 未记录 LLM 调用。</div>
                )}
                {llmSpans.map((s) => (
                  <div key={s.id} className="rounded-lg border border-slate-200 bg-white p-4">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                      <Sparkles size={14} className="text-slate-400" aria-hidden="true" />
                      <strong className="text-slate-800">{s.name || "LLM 调用"}</strong>
                      <span>{s.llm_call?.model || "未记录模型"}</span>
                      {s.llm_call && <span>Token {safeNum(s.llm_call.prompt_tokens)} / {safeNum(s.llm_call.completion_tokens)}</span>}
                      {s.llm_call?.cost_cny != null && <span>{formatCost(s.llm_call.cost_cny)}</span>}
                      <span className="text-slate-400">{fmtMs(s.duration_ms)}</span>
                    </div>
                    {s.llm_call?.prompt_text && <p className="mt-2 whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs text-slate-600"><b className="font-medium">Prompt</b>{"\n"}{s.llm_call.prompt_text}</p>}
                    {s.llm_call?.response_text && <p className="mt-2 whitespace-pre-wrap break-words rounded bg-slate-50 p-2 text-xs text-slate-600"><b className="font-medium">Response</b>{"\n"}{s.llm_call.response_text}</p>}
                  </div>
                ))}
              </div>
            </section>
          </div>

          <aside className="space-y-5 lg:col-span-1">
            <div className="rounded-xl border border-slate-200 bg-white p-5">
              <p className="mb-2 text-[10px] uppercase tracking-widest text-slate-400">④ 花了多少 · 概要</p>
              <div className="space-y-1.5 text-sm">
                <div className="flex justify-between"><span className="text-slate-500">总 Token</span><span className="font-mono">{trace.usage?.total_tokens ? trace.usage.total_tokens.toLocaleString() : "未采集"}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">费用（CNY）</span><span className="font-mono">{typeof trace.cost_cny === "number" ? formatCost(trace.cost_cny) : "未采集"}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">模型（main）</span><span className="font-mono text-xs">{trace.model?.name || "未采集"}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Provider</span><span className="font-mono text-xs">{trace.model?.provider || "未采集"}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">总耗时</span><span className="font-mono">{trace.duration_ms > 0 ? fmtMs(trace.duration_ms) : "未采集"}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">Prompt 版本</span><span className="font-mono text-xs">{promptVersionsLabel(trace.metadata)}</span></div>
              </div>
              {trace.sla && (
                <div className={`mt-3 rounded-lg px-3 py-2 text-xs font-semibold ${isFailure ? "bg-slate-100 text-slate-500" : trace.sla.breached ? "bg-red-50 text-red-600" : "bg-emerald-50 text-emerald-600"}`}>
                  {isFailure ? "SLA 未参与判定 · 失败运行不计入合规" : trace.sla.breached ? `❌ 已违反 SLA（阈值 ${trace.sla.threshold_ms}ms）` : `✅ SLA 合规（阈值 ${trace.sla.threshold_ms}ms）`}
                </div>
              )}
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4 text-[11px] leading-5 text-slate-500">
              本次详情读取已由服务端写入审计日志（trace_content_view）；输入输出按 TRACE_DETAIL_LEVEL 服务端脱敏后下发。
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

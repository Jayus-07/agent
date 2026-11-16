"use client";

import { useState, useEffect, useMemo } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { getTraceById } from "@/lib/observability/source";
import type { TraceRecord, Span } from "@/types/trace";
import { statusBadge, formatTime, formatRelative } from "@/types/trace";
import { knowledgeService } from "@/api/knowledge";
import type { KnowledgeDoc, ProcessingRunDetail } from "@/api/knowledge";
import { X, Loader2 } from "lucide-react";
import {
  getLogicalStageSpans, getMetadataPerformanceWarning, getMetadataStageDuration,
  getQualityScore, getQualityScoreBreakdown, getQualityWarning,
  getTraceCostDisplay, groupSpansByType,
  getEmbeddingCountsFromSpans,
  getTraceDocumentTitle,
  getTraceDocumentId,
  findProcessingRunForTrace,
  getSuccessfulIndexOutcomeLabel,
  getTraceDocumentIdentityError,
  getRegistryDocumentIdentityError,
  getProcessingRunDetailError,
} from "../trace-metrics";
import ProcessingRunDetailView from "../processing-run-detail";
import {
  getAdminErrorLabel,
  getBusinessDomainLabel,
  getChunkStrategyLabel,
  getDocumentTypeLabel,
  getModelRoleLabel,
  getOperationLabel,
  getProcessingStatusLabel,
} from "@/lib/knowledge-display";
import {
  getTraceMetricSummary,
  getTraceSpanNameLabel,
  getTraceSpanTypeLabel,
} from "../trace-display";
import {
  getChunkHeading,
  SIMULATED_QUESTIONS_LABEL,
} from "../chunk-detail-labels";

const SPAN_LABELS: Record<string, { label: string; order: number }> = {
  load: { label: "文件加载", order: 1 }, parse: { label: "解析文档", order: 2 },
  clean: { label: "数据清洗", order: 3 }, dedup: { label: "去重检查", order: 4 },
  chunk: { label: "文本分块", order: 5 }, llm: { label: "大模型元数据生成", order: 6 },
  embedding: { label: "向量嵌入", order: 7 }, vector_db: { label: "写入向量库", order: 8 },
  workflow: { label: "索引编排", order: 0 },
  llm_call: { label: "大模型调用", order: 6.5 },
  stage: { label: "内部阶段", order: 99 },
  rule: { label: "规则处理", order: 9 },
  audit: { label: "质量检查", order: 10 },
};
function spanOrder(s: Span): number { return SPAN_LABELS[s.type]?.order ?? 99; }

function SummaryExpand({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return <span>{open ? text : text.slice(0, 150) + '…'}<button onClick={() => setOpen(!open)} className="text-blue-500 hover:text-blue-600 ml-1 text-[10px]">{open ? '收起' : '展开全文'}</button></span>;
}

function normalizeKeywords(arr: unknown): {word: string; source: string}[] {
  if (!Array.isArray(arr) || arr.length === 0) return [];
  if (typeof arr[0] === "string") return (arr as string[]).map(w => ({word: w, source: ""}));
  return arr as {word: string; source: string}[];
}

const STRATEGY_LABEL = (s: string, llm: boolean, rule: boolean) => {
  if (!llm && rule) return "规则"; if (llm && rule) return "大模型 / 规则"; if (llm && !rule) return "大模型"; return "";
};

interface ChunkDetail { chunk_index: number; content: string; char_count: number; keywords: string; llm_keywords?: string; llm_model?: string; granularity?: string; parent_scope?: string; document_title?: string; section_title?: string; section_titles?: string[]; chunk_type?: string; doc_type?: string; simulated_questions?: string[]; }

export default function DocTracePage() {
  const { id } = useParams<{ id: string }>(); const router = useRouter(); const searchParams = useSearchParams();
  const documentName = searchParams.get("documentName") || "";
  const documentIdParam = searchParams.get("docId") || "";
  const returnTab = searchParams.get("returnTab") === "documents" || (!searchParams.has("returnTab") && Boolean(documentIdParam))
    ? "documents"
    : "operations";
  const returnPageParam = Number(searchParams.get("returnPage") || "1");
  const returnParams = new URLSearchParams({ tab: returnTab });
  if (returnTab === "documents" && Number.isSafeInteger(returnPageParam) && returnPageParam > 1) {
    returnParams.set("doc_page", String(returnPageParam));
  }
  const returnHref = `/knowledge/workbench?${returnParams.toString()}`;
  const fileHashParam = searchParams.get("fileHash") || "";
  const runIdParam = searchParams.get("runId") || "";
  const viewParam = searchParams.get("view") || "";
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [registryDocument, setRegistryDocument] = useState<KnowledgeDoc | null>(null);
  const [documentIdentityError, setDocumentIdentityError] = useState("");
  const [processingRun, setProcessingRun] = useState<ProcessingRunDetail | null>(null);
  const [processingRunHistoryFallback, setProcessingRunHistoryFallback] = useState(false);
  const [processingRunError, setProcessingRunError] = useState("");
  const [loading, setLoading] = useState(true);
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());
  const [expandedMeta, setExpandedMeta] = useState<Set<string>>(new Set());
  const [chunkPanelOpen, setChunkPanelOpen] = useState(false);
  const [chunkDetailData, setChunkDetailData] = useState<ChunkDetail[]>([]);
  const [chunkDetailLoading, setChunkDetailLoading] = useState(false);
  const [chunkDetailError, setChunkDetailError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setTrace(null);
    setRegistryDocument(null);
    setDocumentIdentityError("");
    setProcessingRun(null);
    setProcessingRunHistoryFallback(false);
    setProcessingRunError("");

    const loadDocumentIdentity = async (): Promise<KnowledgeDoc | null> => {
      if (!documentIdParam) {
        if (!cancelled) setDocumentIdentityError("入口未提供文件 ID，无法核对文档登记");
        return null;
      }
      try {
        const response = await knowledgeService.getDocument(documentIdParam);
        const detail = response?.doc as KnowledgeDoc | undefined;
        const identityError = response?.ok
          ? getRegistryDocumentIdentityError(
            detail, documentIdParam, documentName || undefined, fileHashParam || undefined,
          )
          : getAdminErrorLabel(String(response?.error || "文档登记详情读取失败"));
        if (cancelled) return null;
        if (!detail) {
          setRegistryDocument(null);
          setDocumentIdentityError(identityError || "文档登记详情未返回");
          return null;
        }
        setRegistryDocument(detail);
        setDocumentIdentityError(identityError || "");
        return detail;
      } catch {
        if (!cancelled) setDocumentIdentityError("文档登记详情读取失败");
        return null;
      }
    };

    const loadRunDetail = async (
      options: { expectedTraceId?: string; allowHistoryFallback?: boolean } = {},
      documentIdentity: Promise<KnowledgeDoc | null> = Promise.resolve(null),
    ) => {
      if (!documentIdParam) {
        if (!cancelled) setProcessingRunError("缺少文件 ID，无法核对历史处理记录");
        return;
      }
      const allowHistoryFallback = options.allowHistoryFallback ?? true;
      const documentDetail = await documentIdentity;
      const registryFileHash = String(
        documentDetail?.file_hash || documentDetail?.hash || documentDetail?.sha256 || "",
      ).trim();
      const expectedFileHash = fileHashParam || registryFileHash;
      let firstError = "";
      if (runIdParam) {
        try {
          const detail = await knowledgeService.getProcessingRun(documentIdParam, runIdParam);
          const identityError = getProcessingRunDetailError(
            detail, documentIdParam, runIdParam, expectedFileHash, options.expectedTraceId,
          );
          const hasSteps = Array.isArray(detail?.steps) && detail.steps.length > 0;
          if (!identityError && hasSteps) {
            if (!cancelled) {
              setProcessingRun(detail);
              setProcessingRunHistoryFallback(false);
            }
            return;
          }
          firstError = identityError || "该运行没有保存阶段明细";
        } catch {
          firstError = "最近一次处理运行读取失败";
        }
      } else if (options.expectedTraceId) {
        try {
          const history = await knowledgeService.getProcessingRuns(
            documentIdParam, { page: 1, page_size: 100 },
          );
          const candidate = findProcessingRunForTrace(
            Array.isArray(history?.items) ? history.items : [],
            options.expectedTraceId,
            expectedFileHash,
          );
          if (!candidate?.run_id) {
            firstError = "没有找到与当前 Trace ID 和文件 SHA 一致的处理运行";
          } else {
            const detail = await knowledgeService.getProcessingRun(documentIdParam, candidate.run_id);
            const identityError = getProcessingRunDetailError(
              detail, documentIdParam, candidate.run_id, expectedFileHash, options.expectedTraceId,
            );
            if (!identityError && Array.isArray(detail?.steps) && detail.steps.length > 0) {
              if (!cancelled) {
                setProcessingRun(detail);
                setProcessingRunHistoryFallback(false);
                setProcessingRunError("");
              }
              return;
            }
            firstError = identityError || "该运行没有保存阶段明细";
          }
        } catch {
          firstError = "按 Trace ID 查找处理运行失败";
        }
      } else {
        firstError = "入口未提供处理运行 ID，无法核对 Trace 与运行记录";
      }

      if (!allowHistoryFallback) {
        if (!cancelled) setProcessingRunError(firstError ? getAdminErrorLabel(firstError) : "处理运行与 Trace 未通过身份核验");
        return;
      }
      if (!expectedFileHash) {
        if (!cancelled) {
          setProcessingRunError(firstError ? getAdminErrorLabel(firstError) : "入口与文档登记均未提供 SHA-256，无法核对历史处理版本");
        }
        return;
      }

      try {
        const history = await knowledgeService.getProcessingRuns(
          documentIdParam, { page: 1, page_size: 100 },
        );
        const candidates = (Array.isArray(history?.items) ? history.items : [])
          .filter((run: ProcessingRunDetail) => (
            run?.run_id
            && run.run_id !== runIdParam
            && String(run.file_hash || "").toLowerCase() === expectedFileHash.toLowerCase()
          ));
        for (const candidate of candidates) {
          try {
            const detail = await knowledgeService.getProcessingRun(
              documentIdParam, candidate.run_id,
            );
            const identityError = getProcessingRunDetailError(
              detail, documentIdParam, candidate.run_id, expectedFileHash,
            );
            if (identityError || !Array.isArray(detail?.steps) || detail.steps.length === 0) {
              continue;
            }
            if (!cancelled) {
              setProcessingRun(detail);
              setProcessingRunHistoryFallback(true);
              setProcessingRunError("");
            }
            return;
          } catch {
            // 单条历史记录不可读时，继续核对下一条同 SHA 记录。
          }
        }
        if (!cancelled) {
          setProcessingRunError(
            firstError
              ? `${getAdminErrorLabel(firstError)}；未找到 SHA 一致且保存了阶段的历史运行`
              : "未找到与当前文件 SHA 一致且保存了阶段明细的处理运行",
          );
        }
      } catch {
        if (!cancelled) {
          setProcessingRunError(
            firstError
              ? `${getAdminErrorLabel(firstError)}；历史处理记录读取失败`
              : "历史处理记录读取失败",
          );
        }
      }
    };

    const loadIdentityAndRunDetail = async (
      options: { expectedTraceId?: string; allowHistoryFallback?: boolean } = {},
    ) => {
      const documentIdentity = loadDocumentIdentity();
      await Promise.all([documentIdentity, loadRunDetail(options, documentIdentity)]);
    };

    void (async () => {
      if (viewParam === "unavailable") {
        await loadIdentityAndRunDetail({ allowHistoryFallback: true });
        if (!cancelled) setLoading(false);
        return;
      }
      if (viewParam === "processing-run") {
        await loadIdentityAndRunDetail({ allowHistoryFallback: true });
        if (!cancelled) setLoading(false);
        return;
      }

      const foundTrace = await getTraceById(id);
      if (cancelled) return;
      if (foundTrace) {
        const traceIdError = foundTrace.id === id
          ? null
          : "Trace API 返回的 ID 与页面请求不匹配";
        const documentIdError = documentIdParam
          ? getTraceDocumentIdentityError(getTraceDocumentId(foundTrace), documentIdParam)
          : null;
        const identityError = traceIdError || documentIdError;
        if (!identityError) {
          setTrace(foundTrace);
          await loadIdentityAndRunDetail({ expectedTraceId: foundTrace.id, allowHistoryFallback: false });
          if (!cancelled) setLoading(false);
          return;
        }
        setProcessingRunError(identityError);
      }
      if (documentIdParam) {
        await loadIdentityAndRunDetail({ allowHistoryFallback: true });
        if (!cancelled && foundTrace && documentIdParam
          && getTraceDocumentIdentityError(getTraceDocumentId(foundTrace), documentIdParam)) {
          setProcessingRunError((current) => current ? `Trace 已拒绝展示（文档 ID 不匹配）；运行记录读取失败：${current}` : "Trace 文档 ID 不匹配");
        }
      } else if (!foundTrace) {
        setProcessingRunError("Trace 详情不存在或已过期，且没有关联的处理运行 ID");
      }
      if (!cancelled) setLoading(false);
    })();

    return () => { cancelled = true; };
  }, [id, documentIdParam, documentName, fileHashParam, runIdParam, viewParam]);
  useEffect(() => { if (!chunkPanelOpen) return; const onKey=(e:KeyboardEvent)=>{if(e.key==="Escape")setChunkPanelOpen(false)}; window.addEventListener("keydown",onKey); return ()=>window.removeEventListener("keydown",onKey); }, [chunkPanelOpen]);

  const childSpans = useMemo(() => { if(!trace)return[]; return (trace.spans||[]).filter(s=>s.parent_id!==null).sort((a,b)=>spanOrder(a)-spanOrder(b)); }, [trace]);
  const pipelineGroups = useMemo(() => {
    if (!trace) return [];
    const direct = getLogicalStageSpans(trace.spans || [], trace.root_span_id);
    return groupSpansByType(direct).sort((a,b)=>spanOrder(a.spans[0])-spanOrder(b.spans[0]));
  }, [trace]);
  const typeGroups = useMemo(() => { const m=new Map<string,Span[]>(); for(const s of childSpans){if(!m.has(s.type))m.set(s.type,[]); m.get(s.type)!.push(s);} return Array.from(m.entries()).sort((a,b)=>spanOrder(a[1][0])-spanOrder(b[1][0])); }, [childSpans]);

  const overview = useMemo(() => {
    if(!trace) return {fileSize:0,chunkCount:0,totalTokens:0,costCny:0,costUsd:0,llmCalls:0,models:[] as Array<{component:string;model:string;calls:number;total_tokens:number;cost_cny:number}>,docType:'',businessDomain:'',model:'',confidence:0,qualityScore:null as number|null};
    const ls=childSpans.find(s=>s.type==='load'); const cs=childSpans.find(s=>s.type==='chunk');
    const llmS=childSpans.find(s=>s.type==='llm'); const es=childSpans.find(s=>s.type==='embedding');
    const out=(llmS?.output||{}) as Record<string,unknown>;
    const rm=(out.rule_metadata||{}) as Record<string,unknown>;
    const lm=(out.llm_metadata||{}) as Record<string,unknown>;
    const lt=(lm.llm_tokens||{}) as Record<string,unknown>;
    const pt=(lt.prompt_tokens as number)||0; const ct=(lt.completion_tokens as number)||0;
    // llm_usage 台账读时回填（后端 _backfill_usage）：usage.by_component 按
    // 组件聚合 tokens（索引 trace 常为 embedding-only），usage.models 为
    // 组件×模型清单（一次索引可能 embedding+LLM+OCR 多模型并发），cost_cny
    // 为本位币精确值；span 内嵌 llm_tokens 仅作旧数据回退
    const u=(trace.usage||{}) as Record<string,any>;
    const byComp=(u.by_component||{}) as Record<string,Record<string,number>>;
    const compTokens=Object.values(byComp).reduce((s,c)=>s+(c.total_tokens||0),0);
    const compCalls=Object.values(byComp).reduce((s,c)=>s+(c.calls||0),0);
    const models=(u.models||[]) as Array<{component:string;model:string;calls:number;total_tokens:number;cost_cny:number}>;
    const modelName=models.map(m=>m.model).join(' / ');
    return {fileSize:(ls?.metrics?.file_size as number)||0, chunkCount:(cs?.metrics?.kept_chunks as number)||(cs?.output as any)?.total||0,
      totalTokens:compTokens>0? compTokens : pt+ct,
      costCny:(u.cost_cny as number)||0, costUsd:(u.cost_usd as number)||((lt.cost_usd as number)||0),
      llmCalls:compCalls, models,
      docType:(rm.doc_type as string)||(out.doc_type as string)||'',
      businessDomain:(rm.business_domain as string)||(out.business_domain as string)||'',
      model:modelName||(typeof trace?.model==='string'? trace.model as string :'')||(lt.model as string)||(es?.metrics?.model_name as string)||'',
      confidence:(rm.confidence as number)??0, qualityScore:getQualityScore(trace.spans||[])};
  }, [childSpans, trace]);

  const warnings = useMemo(() => {
    const items:string[]=[];
    const qualityWarning=getQualityWarning(overview.qualityScore); if(qualityWarning) items.push(qualityWarning);
    if(trace){const performanceWarning=getMetadataPerformanceWarning(
      getMetadataStageDuration(trace.spans||[],trace.root_span_id),trace.duration_ms,
    ); if(performanceWarning) items.push(performanceWarning);}
    return items;
  }, [overview, trace]);

  const docIdFromTags = trace?.tags?.doc_id||"";
  const docIdFromSpans = useMemo(() => { if(docIdFromTags)return""; for(const s of trace?.spans||[]){const m=s.metrics as Record<string,unknown>|undefined; if(m?.doc_id)return String(m.doc_id);} return""; }, [trace, docIdFromTags]);
  const docId = docIdFromTags||docIdFromSpans;
  const traceDocumentId = trace ? getTraceDocumentId(trace) : null;
  const traceKnowledgeBaseId = trace?.tags?.kb_id || "";
  const traceSla = trace?.sla;
  const registryKnowledgeBaseId = registryDocument?.kb_id || "";
  const knowledgeBaseIdentityError = !traceKnowledgeBaseId && !registryKnowledgeBaseId
    ? "Trace 与文档登记均未记录知识库 ID，无法确认所属知识库"
    : !traceKnowledgeBaseId
      ? "Trace 未记录知识库 ID，无法与文档登记核对"
      : !registryKnowledgeBaseId
        ? "文档登记未记录知识库 ID，无法与 Trace 核对"
        : traceKnowledgeBaseId !== registryKnowledgeBaseId
          ? "Trace 知识库 ID 与文档登记不一致"
          : "";
  const registryFileHash = String(
    registryDocument?.file_hash || registryDocument?.hash || registryDocument?.sha256 || "",
  );
  const identityVerified = Boolean(
    registryDocument
    && !documentIdentityError
    && processingRun
    && (!runIdParam || processingRun.run_id === runIdParam)
    && !processingRunError
    && (fileHashParam || registryFileHash)
    && registryFileHash
    && registryFileHash.toLowerCase() === (fileHashParam || registryFileHash).toLowerCase()
    && processingRun.file_hash
    && String(processingRun.file_hash).toLowerCase() === (fileHashParam || registryFileHash).toLowerCase()
    && documentIdParam
    && traceDocumentId === documentIdParam
    && trace?.id === id
    && !knowledgeBaseIdentityError,
  );
  const registryDocumentName = registryDocument?.file_name || registryDocument?.name || "";
  const traceDocumentTitle = registryDocumentName
    || documentName
    || getTraceDocumentTitle(trace?.question || "");
  const identityRows = [
    { label: "文件名", value: traceDocumentTitle, source: registryDocumentName ? "项目文档登记" : documentName ? "入口参数，未核验" : "未记录" },
    { label: "文档编号", value: traceDocumentId || "追踪记录未记录" },
    { label: "文档登记编号", value: registryDocument?.doc_id || registryDocument?.id || "未核验" },
    { label: "知识库编号", value: traceKnowledgeBaseId || "追踪记录未记录" },
    { label: "文档登记知识库编号", value: registryKnowledgeBaseId || "未核验" },
    { label: "处理追踪编号", value: trace?.id || "追踪记录未记录" },
    { label: "处理运行编号", value: processingRun?.run_id || (runIdParam ? "未核验" : "未记录") },
    { label: "文件 SHA-256", value: processingRun?.file_hash || fileHashParam || registryFileHash || "未记录" },
    { label: "文档登记 SHA-256", value: registryFileHash || "未核验" },
    { label: "处理操作", value: getOperationLabel(processingRun?.operation) },
    { label: "处理状态", value: processingRun ? getProcessingStatusLabel(processingRun.status) : (runIdParam ? "未核验" : "未记录") },
    { label: "处理开始", value: processingRun?.started_at ? formatTime(processingRun.started_at) : "未记录" },
    { label: "处理结束", value: processingRun?.finished_at ? formatTime(processingRun.finished_at) : "未记录" },
    { label: "服务时限来源", value: traceSla?.source || "未配置" },
    { label: "服务时限阈值", value: traceSla ? `${traceSla.threshold_ms} 毫秒` : "未配置" },
    { label: "服务时限判定", value: !traceSla ? "不判定" : traceSla.breached === null ? "尚未评估" : traceSla.breached ? "已超限" : "未超限" },
  ];
  const identityWarnings = [
    documentIdentityError,
    processingRunError,
    knowledgeBaseIdentityError,
    !fileHashParam && !registryFileHash ? "入口与文档登记均未提供文件 SHA-256，无法核对文件版本" : "",
  ]
    .filter(Boolean);
  const handleOpenChunkDetail = async () => { if(!docId)return; setChunkPanelOpen(true);setChunkDetailLoading(true);setChunkDetailError(""); try{const res=await knowledgeService.getChunkDetail(docId); setChunkDetailData((res as any)?.chunks??(res as any)?.data?.chunks??[]);}catch(e){setChunkDetailError(getAdminErrorLabel((e as Error).message));}finally{setChunkDetailLoading(false);} };

  if(loading) return <div className="flex-1 flex items-center justify-center"><p className="text-sm text-slate-400">加载中…</p></div>;
  if (!trace && processingRun) {
    return <ProcessingRunDetailView
      detail={processingRun}
      documentName={documentName}
      expectedDocId={documentIdParam}
      expectedFileHash={fileHashParam}
      registryDocument={registryDocument}
      registryIdentityError={documentIdentityError}
      runIdentityError={processingRunError}
      historyFallback={processingRunHistoryFallback}
      onBack={() => router.push(returnHref)}
      backLabel={returnTab === "documents" ? "文档" : "操作日志"}
    />;
  }
  if (!trace) return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-10">
        <button onClick={() => router.push(returnHref)} className="text-xs text-slate-400 hover:text-slate-600">← 返回{ returnTab === "documents" ? "文档" : "操作日志" }</button>
        <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-5">
          <div className="text-sm font-semibold text-amber-900">处理详情未留存</div>
          <p className="mt-2 text-sm text-slate-700">{documentName || "未记录文件名"}</p>
          <p className="mt-2 text-xs leading-relaxed text-amber-800">{processingRunError || `Trace ${id} 不存在或已过期`}</p>
          {searchParams.get("operation") && (
            <p className="mt-3 text-xs text-slate-600">最后操作：{searchParams.get("operation")}；时间：{searchParams.get("operationAt") || "未记录"}</p>
          )}
          <p className="mt-3 text-xs leading-relaxed text-slate-500">当前记录不足以还原处理阶段。请保留该文件现状；下一次受控重建成功后，可从新运行记录追溯，不会补造历史详情。</p>
        </div>
      </div>
    </div>
  );

  const badge = statusBadge(trace.status??"success");
  const successfulIndexOutcome = getSuccessfulIndexOutcomeLabel(trace.status || "success", processingRun);
  const traceCost = getTraceCostDisplay({
    costCny: overview.costCny,
    costUsd: overview.costUsd,
    totalTokens: overview.totalTokens,
    calls: overview.llmCalls,
  });
  const toggleTypeExpand = (type:string) => { const next=new Set(expandedTypes); next.has(type)?next.delete(type):next.add(type); setExpandedTypes(next); };
  const toggleMeta = (key:string) => { const next=new Set(expandedMeta); next.has(key)?next.delete(key):next.add(key); setExpandedMeta(next); };
  return (
    <div className="flex-1 overflow-y-auto"><div className="max-w-3xl mx-auto px-6 py-8 space-y-5">
      <button onClick={()=>router.push(returnHref)} className="text-xs text-slate-400 hover:text-slate-600">← 返回{ returnTab === "documents" ? "文档" : "操作日志" }</button>

      {/* Overview */}
      <div>
        <div className="flex items-center justify-between mb-3"><div className="flex items-center gap-3"><h1 className="text-base font-semibold text-slate-800">{traceDocumentTitle}</h1><span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${successfulIndexOutcome ? "bg-amber-100 text-amber-800" : badge.bg}`}>{successfulIndexOutcome || badge.label}</span></div><span className="text-xs text-slate-400" title={formatTime(trace.timestamp)}>{formatRelative(trace.timestamp)}</span></div>
        {successfulIndexOutcome && trace.status === "error" && (
          <p className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            处理运行终态为成功；Trace 保留可选模型阶段的失败/回退明细，请查看下方具体阶段。
          </p>
        )}
        <section aria-labelledby="trace-identity-heading" className="mb-3 rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 id="trace-identity-heading" className="text-xs font-medium text-slate-600">文件与处理身份</h2>
            <span role="status" className={`rounded px-2 py-1 text-[10px] font-semibold ${identityVerified ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>
              {identityVerified ? "身份核验通过" : "身份未核验"}
            </span>
          </div>
          <dl className="grid grid-cols-1 gap-x-5 gap-y-3 sm:grid-cols-2">
            {identityRows.map((item) => (
              <div key={item.label} className="min-w-0">
                <dt className="text-[10px] text-slate-400">{item.label}</dt>
                <dd className="mt-0.5 break-all font-mono text-xs text-slate-700">{item.value}</dd>
                {"source" in item && <dd className="mt-0.5 text-[9px] text-slate-400">来源：{item.source}</dd>}
              </div>
            ))}
          </dl>
          {identityWarnings.length > 0 && (
            <p role="alert" className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
              未核验原因：{identityWarnings.join("；")}
            </p>
          )}
        </section>
        <div className="grid grid-cols-4 gap-2 mb-2">
          {[{label:'文件大小',value:overview.fileSize>0?`${(overview.fileSize/1024).toFixed(1)} KB`:'-',sub:`${overview.fileSize} 字节`},{label:'文本分块数',value:overview.chunkCount>0?String(overview.chunkCount):'-',sub:'文本分块数'},{label:'词元数',value:overview.totalTokens>0?overview.totalTokens.toLocaleString():'-',sub:'输入与输出词元数'},{label:'成本',value:traceCost.value,sub:traceCost.detail}].map(c=>(<div key={c.label} className="bg-white border border-slate-200 rounded-lg px-3 py-2.5"><div className="text-[10px] text-slate-400 mb-0.5">{c.label}</div><div className="text-sm font-semibold text-slate-800 font-mono">{c.value}</div><div className="text-[9px] text-slate-400">{c.sub}</div></div>))}
        </div>
        <div className="grid grid-cols-4 gap-2">
          {[{label:'文档类型',value:getDocumentTypeLabel(overview.docType)},{label:'业务领域',value:getBusinessDomainLabel(overview.businessDomain)},{label:'模型',value:overview.model||trace.model?.name||'-',sub:overview.models.length>0?`${overview.models.length} 个模型 · ${overview.llmCalls} 次调用`:undefined},{label:'总耗时',value:trace.duration_ms>0?`${(trace.duration_ms/1000).toFixed(1)} 秒`:'-',sub:`${childSpans.length} 个处理阶段 · ${trace.id?.slice(0,8)}`}].map(c=>(<div key={c.label} className="bg-white border border-slate-200 rounded-lg px-3 py-2.5"><div className="text-[10px] text-slate-400 mb-0.5">{c.label}</div><div className="text-sm font-semibold text-slate-800 truncate">{c.value}</div>{c.sub&&<div className="text-[9px] text-slate-400">{c.sub}</div>}{c.label==='模型'&&overview.models.length>1&&<div className="mt-1 pt-1 border-t border-slate-100 space-y-0.5">{overview.models.map(m=>(<div key={m.component+m.model} className="flex items-center justify-between gap-2 text-[9px]"><span className="text-slate-500 truncate">{getModelRoleLabel(m.component)}: {m.model}</span><span className="font-mono text-slate-400 shrink-0">{m.total_tokens.toLocaleString()} 个词元{m.cost_cny>0?` · ¥${m.cost_cny.toFixed(4)}`:''}</span></div>))}</div>}</div>))}
        </div>
      </div>

      {/* 处理阶段耗时分布 */}
      {pipelineGroups.length>0&&trace.duration_ms>0&&(<div className="bg-white border border-slate-200 rounded-xl p-5"><h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-1">⏱ 处理阶段耗时分布</h2><p className="text-[9px] text-slate-400 mb-3">口径：总流程阶段的逻辑耗时 / 本次处理总耗时；嵌套调用不重复累加。</p><div className="space-y-1.5">{pipelineGroups.map(({type,totalMs})=>{const info=SPAN_LABELS[type]??{label:getTraceSpanTypeLabel(type),order:99};const pct=(totalMs/trace.duration_ms)*100;const isHot=pct>30;const barColor=isHot?'bg-amber-400':pct>10?'bg-blue-400':'bg-slate-300';return(<div key={type} className="flex items-center gap-2 text-xs"><span className="w-20 text-slate-500 text-right shrink-0">{info.label}</span><div className="flex-1 bg-slate-100 rounded-full h-4 overflow-hidden"><div className={`h-full rounded-full transition-all ${barColor}`} style={{width:`${Math.min(Math.max(pct,0.5),100)}%`,minWidth:pct>0?'4px':'0'}} /></div><span className="w-20 text-right font-mono text-slate-600 shrink-0">{totalMs} 毫秒</span><span className="w-10 text-right text-slate-400 shrink-0">{pct.toFixed(0)}%</span></div>);})}</div></div>)}

      {/* 处理流水线 */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-100"><h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider">📦 处理流水线</h2></div>
        <div className="divide-y divide-slate-50">
          {typeGroups.map(([type,spans])=>{
            const info=SPAN_LABELS[type]??{label:getTraceSpanTypeLabel(type),order:99};
            const logicalStageMs=pipelineGroups.find(group=>group.type===type)?.totalMs;
            const totalMs=logicalStageMs??spans.reduce((sum,s)=>sum+(s.duration_ms||0),0);
            const hasError=spans.some(s=>s.status==="error"); const hasSkipped=spans.some(s=>s.status==="skipped");
            const statusIcon=hasError?"❌":hasSkipped?"⚠️":"✅"; const isMulti=spans.length>1;
            const expanded=expandedTypes.has(type); const summary=getTraceMetricSummary(spans);

            // clean
            if(type==="clean"){const s=spans[0];const metrics=s.metrics||{};const cb=metrics.chars_before as number||0;const ca=metrics.chars_after as number||0;const ops=metrics.operations as string||"";const errMsg=metrics.error as string||"";const reduction=cb>0?(1-ca/cb)*100:0;const isNoop=cb>0&&cb===ca;return(<div key={type} className={`px-5 py-4 ${hasError?"bg-red-50/30":""}`}><div className="flex items-center gap-4 mb-2"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs} 毫秒</span>{ops&&ops!=="none"&&<span className="text-xs text-slate-400 truncate">{ops}</span>}{isNoop&&!hasError&&<span className="text-[10px] text-slate-300 shrink-0">（文本已规范，无需清洗）</span>}</div><div className="ml-9 space-y-1 text-xs">{hasError?<div className="text-red-600 bg-red-50 rounded px-2 py-1 font-mono text-[11px]">❌ 清洗失败：{errMsg||"未知错误"}（已降级使用原始文本继续）</div>:<>{cb>0&&<div className="text-slate-500">清洗前 <span className="font-mono text-slate-600">{cb.toLocaleString()}</span> 字符 → 清洗后 <span className="font-mono text-slate-600">{ca.toLocaleString()}</span> 字符<span className={`ml-1 ${isNoop?"text-slate-300":"text-slate-400"}`}>（{reduction.toFixed(1)}% 缩减）</span></div>}</>}</div></div>);}

            // chunk
            if(type==="chunk"){const fs=spans[0];const output=(fs?.output||{}) as Record<string,unknown>;const preview=(output["preview"] as string[])||[];const total=(output["total"] as number)||(fs?.metrics?.kept_chunks as number)||0;const rawChunks=fs?.metrics?.raw_chunks as number||0;const filteredOut=fs?.metrics?.filtered_out as number||0;const strategy=(output["strategy"] as string)||"";const strategyCN=getChunkStrategyLabel(strategy);return(<div key={type} className="px-5 py-4"><div className="flex items-center gap-4 mb-2"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs} 毫秒</span><span className="text-xs text-slate-400">切分 <span className="font-mono text-slate-600">{total}</span> 块{rawChunks>0&&<span className="text-slate-400 ml-1">（原始 {rawChunks}{filteredOut>0?`，过滤 ${filteredOut}`:""}）</span>}</span>{docId&&total>0&&<button onClick={handleOpenChunkDetail} className="ml-auto text-xs text-accent hover:text-accent-hover hover:underline">📋 查看完整内容 →</button>}</div>{strategy&&<div className="ml-9 mb-2 text-[10px] text-slate-400"><span>✂️ 切分方案: </span><span className="text-slate-600 font-medium">{strategyCN||'—'}</span></div>}{preview.length>0&&<div className="ml-9 space-y-1 mb-2">{preview.map((text,i)=>(<div key={i} className="text-xs text-slate-500 bg-slate-50 rounded px-2 py-1 truncate"><span className="text-slate-300 mr-1">预览块 {i+1}</span>{text.slice(0,120)}{text.length>120?"…":""}</div>))}</div>}</div>);}

            // llm / metadata
            if(type==="llm"){
              const out=(spans[0]?.output||{}) as Record<string,unknown>;
              const ruleMeta=(out["rule_metadata"]||out) as Record<string,unknown>;
              const llmMeta=(out["llm_metadata"]||out) as Record<string,unknown>;
              const kwsRule=normalizeKeywords(ruleMeta["keywords_rule"]);
              const kwsLlmRaw=normalizeKeywords(llmMeta["keywords_llm"]);
              const rwl=new Set(kwsRule.map(k=>k.word.toLowerCase()));
              const kwsLlm=kwsLlmRaw.map(k=>({word:k.word.charAt(0).toUpperCase()+k.word.slice(1).toLowerCase(),source:k.source})).filter(k=>!rwl.has(k.word.toLowerCase()));
              const llmUsed=(llmMeta["llm_used"] as boolean)||false;
              const llmTokens=(llmMeta["llm_tokens"] as Record<string,number>)||{};
              const llmStrategy=(llmMeta["llm_strategy"] as string)||"";
              const llmDecision=(llmMeta["llm_decision"] as Record<string,unknown>)||{};
              const llmScore=llmDecision["llm_score"] as number||0;
              const llmReason=String(llmDecision["llm_reason"]||"").split(";").filter(reason=>!reason.includes("complex_struct")).join(";");
              const docType=(ruleMeta["doc_type"] as string)||(spans[0]?.metrics?.doc_type as string)||"";
              const confidence=(ruleMeta["confidence"] as number)||0;
              const hasRule=kwsRule.length>0;
              const strategyLabel=STRATEGY_LABEL(llmStrategy,llmUsed,hasRule);
              const tokenOk=llmUsed&&typeof llmTokens["prompt_tokens"]==="number"&&(llmTokens["prompt_tokens"]>0||(llmTokens["completion_tokens"]??0)>0);
              const costUsd=llmTokens["cost_usd"] as number|undefined;
              const costOk=typeof costUsd==="number"&&costUsd>0;
              const clsSpan=childSpans.find(s=>s.id==='classify');
              const clsOut=(clsSpan?.output||{}) as Record<string,any>;
              const clsKeywordHits=(clsOut.keyword_hits||[]) as Array<{type:string;keyword:string;weight:number;source?:string}>;
              const topTypes=(Object.entries(clsOut.scores||{}) as [string,number][]).sort((a,b)=>b[1]-a[1]).slice(0,5);
              const domainSpan=childSpans.find(s=>s.id==='domain_classify');
              const domainOut=((domainSpan?.output||{})as any);
              const domainTopTypes=(Object.entries(domainOut.scores||{}) as [string,number][]).sort((a,b)=>b[1]-a[1]).slice(0,5);
              const domainHits=domainOut.hits||[];
              const qualityBreakdown=getQualityScoreBreakdown(childSpans);
              const qualityCalculatedScore=qualityBreakdown.hardCount===null||qualityBreakdown.warningCount===null?null:Math.max(0,qualityBreakdown.base-qualityBreakdown.hardDeduction*qualityBreakdown.hardCount-qualityBreakdown.warningDeduction*qualityBreakdown.warningCount);
              const summaryText=docType.toLowerCase()==="faq"?"":((ruleMeta["summary"] as string)||"");
              const sections=ruleMeta["sections"] as string[]||[];

              return (<div key={type} className="px-5 py-3 cursor-pointer hover:bg-violet-50/50 transition-colors" onClick={()=>toggleTypeExpand(type)}>
                <div className="flex items-center gap-4 flex-wrap">
                  <span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs} 毫秒</span>
                  {strategyLabel&&<span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${llmUsed?"bg-violet-200 text-violet-700":"bg-slate-100 text-slate-600"}`}>{strategyLabel}</span>}
                  {docType&&<span className="text-[10px] text-slate-400">{getDocumentTypeLabel(docType)}{confidence>0&&<span className="text-slate-400 ml-0.5">· 置信度 {(confidence*100).toFixed(0)}%</span>}</span>}
                  <span className="ml-auto text-[10px] text-slate-300">{expanded?"▲ 收起":"▼ 展开"}</span>
                </div>

                {expanded&&(<div className="mt-2 ml-9 space-y-0.5" onClick={e=>e.stopPropagation()}>
                  {[
                    {k:'classify',icon:'📄',label:'文档分类',value:`${getDocumentTypeLabel(docType)} · ${(confidence*100).toFixed(0)}%`,hint:'规则关键词加权计分',show:true,
                      detail:()=>(<div className="space-y-1">
                        {topTypes.length>0&&(<div>{topTypes.map(([t,s])=>(<div key={t} className="flex items-center gap-2" style={{fontSize:'9px'}}><span className="w-16 text-slate-500 text-right">{getDocumentTypeLabel(t)}</span><div className="flex-1 bg-slate-200 rounded-full h-2.5 overflow-hidden"><div className="h-full rounded-full bg-blue-400" style={{width:Math.max(s/20*100,2)+'%'}}/></div><span className="w-8 text-right font-mono text-slate-500">{s}</span></div>))}<div className="text-[9px] text-slate-400">置信度 = 最高分/(最高分+次高分)</div></div>)}
                        {clsKeywordHits.length>0&&(<div className="border-t border-slate-100 pt-1 mt-1">{(()=>{const g:Record<string,any[]>={};for(const h of clsKeywordHits){if(!g[h.type])g[h.type]=[];g[h.type].push(h);}for(const[t,hits]of Object.entries(g)){const mg:Record<string,any>={};for(const h of hits){const key=h.keyword+(h.source||'');if(mg[key]){mg[key].w+=h.weight;mg[key].c++}else mg[key]={k:h.keyword,w:h.weight,s:h.source,c:1}}g[t]=Object.values(mg)}return Object.entries(g).sort((a,b)=>b[1].reduce((s:number,h:any)=>s+h.w,0)-a[1].reduce((s:number,h:any)=>s+h.w,0)).slice(0,5).map(([t,hits])=>(<div key={t}><span className="text-[9px] text-slate-400">{getDocumentTypeLabel(t)}: </span>{hits.map((h:any)=>(<span key={h.k+h.s} className="text-[9px] text-slate-500 mr-2">{h.s==='title'?'📌':h.s==='filename'?'📎':''}{h.k}<span className="font-mono text-slate-300 ml-0.5">+{h.w}{h.c>1?`×${h.c}`:''}</span></span>))}</div>));})()}</div>)}
                      </div>)},
                    {k:'domain',icon:'🌐',label:'业务领域',value:overview.businessDomain||'—',hint:'关键词命中最高',show:!!(domainTopTypes.length||domainHits.length),
                      detail:()=>(<div className="space-y-1">{domainTopTypes.length>0&&(<div>{domainTopTypes.map(([t,s])=>(<div key={t} className="flex items-center gap-2" style={{fontSize:'9px'}}><span className="w-16 text-slate-500 text-right">{t}</span><div className="flex-1 bg-slate-200 rounded-full h-2.5 overflow-hidden"><div className="h-full rounded-full bg-cyan-400" style={{width:Math.max(s/10*100,2)+'%'}}/></div><span className="w-8 text-right font-mono text-slate-500">{s}</span></div>))}</div>)}{domainHits.length>0&&<div className="flex flex-wrap gap-x-2 gap-y-0.5">{domainHits.map((h:string,i:number)=>{const m=h.match(/^(.+)\+(\d+)$/);return <span key={'dh'+i} className="text-[9px] text-slate-500">▸ {m?m[1]:h}{m&&<span className="font-mono text-cyan-500 ml-0.5">+{m[2]}</span>}</span>;})}</div>}</div>)},
                    {k:'quality',icon:'📊',label:'质量评分',value:overview.qualityScore===null?'未提供':`${overview.qualityScore}/100 ${overview.qualityScore>=60?'✅':overview.qualityScore>=40?'⚠':'❌'}`,hint:`${qualityBreakdown.base} − ${qualityBreakdown.hardDeduction}×硬异常 − ${qualityBreakdown.warningDeduction}×告警`,show:true,
                      detail:()=>(<div className="space-y-1 text-[9px]">
                        <div className="text-slate-600">公式：max(0，{qualityBreakdown.base} − {qualityBreakdown.hardDeduction}×硬异常数 − {qualityBreakdown.warningDeduction}×告警数)</div>
                        {qualityCalculatedScore!==null?<div className="text-slate-500">本次计分：max(0，{qualityBreakdown.base} − {qualityBreakdown.hardDeduction}×{qualityBreakdown.hardCount} − {qualityBreakdown.warningDeduction}×{qualityBreakdown.warningCount}) = {qualityCalculatedScore}/100（异常 {qualityBreakdown.totalCount??(qualityBreakdown.hardCount!+qualityBreakdown.warningCount!)} 项）{overview.qualityScore!==null&&overview.qualityScore!==qualityCalculatedScore&&<span className="text-amber-600">；Trace 记录 {overview.qualityScore}/100，与现行公式不一致</span>}</div>:<div className="text-slate-500">此 Trace 未记录完整异常计数。</div>}
                        <div className="text-slate-400">硬异常会阻止文档发布；此分数反映解析与索引校验异常，不代表答案语义正确或检索召回效果。</div>
                      </div>)},
                    {k:'llm_decision',icon:'🔮',label:'大模型决策',value:llmUsed?`评分${llmScore} · ${llmStrategy==='llm_force'?'强制触发':llmScore>=50?'达标触发':'未触发'}`:'未使用大模型',hint:llmUsed?'评分累加':'—',show:llmUsed,
                      detail:()=>(<div className="space-y-1">{llmReason&&llmReason.split(";").map((s:string)=>s.trim()).filter(Boolean).map((p:string)=>{let label="其他触发条件";let score=0;let forced=false;const sm=p.match(/\((\+?\d+)\)/);const parsed=sm?parseInt(sm[1]):0;const cv=(v:string)=>v.replace(/\(.*\)/,'').trim();if(p.includes("high_value:")){label=`${getDocumentTypeLabel(cv(p.split(":")[1]))} 高价值`;score=parsed}else if(p.includes("risk_hits:")){label=`命中 ${cv(p.split(":")[1])} 个风险词`;score=parsed}else if(p.includes("low_conf:")){label=`分类置信度不足（${(parseFloat(cv(p.split(":")[1]))*100).toFixed(0)}%）`;score=parsed}else if(p.includes("forced:high_risk")){label="高风险类强制触发";forced=true}return(<div key={p} className="flex items-center gap-2 text-[9px]"><span className="text-slate-500 flex-1">· {label}</span>{forced?<span className="font-mono text-slate-300">—</span>:<span className={`font-mono ${score>0?'text-violet-500':'text-slate-300'}`}>+{score}</span>}</div>)})}{tokenOk&&<div className="text-[9px] text-slate-400 mt-1 pt-1 border-t border-slate-100">模型: {String(llmTokens["model"]||"")} · 入{llmTokens["prompt_tokens"]}/出{llmTokens["completion_tokens"]??0}{costOk&&<span> · ${costUsd!.toFixed(6)}</span>}</div>}</div>)},
                    {k:'summary',icon:'📝',label:'摘要',value:summaryText?summaryText.slice(0,50)+'…':'—',hint:llmUsed?'大模型生成':'抽取式',show:!!summaryText,
                      detail:()=>(<div className="text-[10px] text-slate-700 leading-relaxed">{summaryText.length>200?<SummaryExpand text={summaryText}/>:summaryText}</div>)},
                    {k:'sections',icon:'📑',label:'章节',value:`${sections.length} 个`,hint:'正则提取',show:sections.length>0,
                      detail:()=>(<div className="flex flex-wrap gap-1">{sections.map((s:string,i:number)=>(<span key={i} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-slate-100 text-slate-600">{s}</span>))}</div>)},
                    {k:'rule_kw',icon:'🏷️',label:'规则关键词',value:`${kwsRule.length} 个`,hint:'模式匹配',show:hasRule,
                      detail:()=>(<div className="flex flex-wrap gap-1">{kwsRule.map((kw,i)=>(<span key={i} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-slate-100 text-slate-600 ring-1 ring-slate-200">{kw.word}</span>))}</div>)},
                    {k:'llm_kw',icon:'💬',label:'大模型关键词',value:`${kwsLlm.length} 个`,hint:String(llmTokens["model"]||"大模型"),show:llmUsed&&kwsLlm.length>0,
                      detail:()=>(<div className="flex flex-wrap gap-1">{kwsLlm.map(kw=>(<span key={kw.word} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-violet-100 text-violet-700 ring-1 ring-violet-200">{kw.word}</span>))}</div>)},
                  ].filter(r=>r.show).map(r=>{const open=expandedMeta.has(r.k);return(<div key={r.k}><div className="flex items-center gap-2 py-1 px-1 rounded hover:bg-slate-50 cursor-pointer" onClick={()=>toggleMeta(r.k)}><span className="text-[10px] w-5 text-center">{r.icon}</span><span className="text-[10px] text-slate-500 w-16 shrink-0">{r.label}</span><span className="text-[10px] text-slate-700 font-medium flex-1 truncate">{r.value}</span><span className="text-[9px] text-slate-300 shrink-0">{r.hint} {open?'▾':'▸'}</span></div>{open&&<div className="ml-11 mr-2 mb-1.5 p-2 bg-slate-50 rounded border border-slate-100" onClick={e=>e.stopPropagation()}>{r.detail()}</div>}</div>);})}
                </div>)}
              </div>);
            }

            if(type==="llm_call"){
              return(<div key={type} className="px-5 py-4"><div className="flex items-center gap-4 mb-2"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="text-xs text-slate-400">{spans.length} 次模型服务调用</span></div><div className="ml-9 space-y-1">{spans.map((span,index)=>{const metrics=span.metrics||{};const providerMs=typeof metrics.provider_duration_ms==="number"?metrics.provider_duration_ms:span.duration_ms;const promptTokens=typeof metrics.prompt_tokens==="number"?metrics.prompt_tokens:0;const completionTokens=typeof metrics.completion_tokens==="number"?metrics.completion_tokens:0;return(<div key={span.id||index} className="flex items-center gap-3 text-[10px] text-slate-500"><span className="truncate flex-1">{getTraceSpanNameLabel(span)}</span><span className="font-mono shrink-0">模型服务调用 {providerMs} 毫秒</span><span className="font-mono shrink-0">{promptTokens.toLocaleString()} 入 / {completionTokens.toLocaleString()} 出</span><span className="shrink-0">{String(metrics.model||"模型未回报")}</span></div>);})}</div></div>);
            }

            // embedding
            if(type==="embedding"){const {attempted,succeeded,failed}=getEmbeddingCountsFromSpans(spans);const embModel=(overview.models||[]).find(m=>m.component==='embedding')?.model||trace?.tags?.embedding_model||"";return(<div key={type} className={`px-5 py-4 ${hasError?"bg-red-50/30":""}`}><div className="flex items-center gap-4"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs} 毫秒</span><span className="text-xs text-slate-400">{attempted===null&&succeeded===null?"Trace 未记录计数":<><span>成功 <span className="font-mono text-green-600">{succeeded===null?"—":succeeded.toLocaleString()}</span></span>{failed!==null&&failed>0&&<span className="ml-1">失败 <span className="font-mono text-red-500">{failed.toLocaleString()}</span></span>}<span className="ml-1">/ 共 <span className="font-mono text-slate-500">{attempted===null?"—":attempted.toLocaleString()}</span></span></>}</span>{embModel&&<span className="ml-auto text-[10px] text-slate-300 shrink-0">模型: {embModel}</span>}</div></div>);}

            // dedup
            if(type==="dedup"){const s=spans[0];const cached=s.metrics?.cached as boolean|undefined;const existingDocId=s.metrics?.existing_doc_id as string||"";return(<div key={type} className="px-5 py-4"><div className="flex items-center gap-4"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs} 毫秒</span>{cached?<span className="text-xs text-amber-600">⚡ 命中缓存 → doc {existingDocId}</span>:<span className="text-xs text-slate-400">未命中缓存 → 进入完整索引流程</span>}</div></div>);}

            const groupId = `pipeline-span-group-${encodeURIComponent(type)}`;
            return (
              <div key={type} className="px-5 py-2.5">
                <button
                  type="button"
                  disabled={!isMulti}
                  aria-expanded={isMulti ? expanded : undefined}
                  aria-controls={isMulti ? groupId : undefined}
                  onClick={() => isMulti && toggleTypeExpand(type)}
                  className={`flex w-full items-center gap-4 text-left text-sm ${isMulti ? "cursor-pointer rounded-md hover:bg-slate-50" : "cursor-default"}`}
                >
                  <span className="text-xs w-5 shrink-0">{statusIcon}</span>
                  <span className="w-24 text-slate-500 shrink-0">{info.label}</span>
                  <span className={`font-mono text-xs w-16 text-right shrink-0 ${hasError ? "text-red-500" : "text-slate-400"}`}>{totalMs} 毫秒</span>
                  <span className="text-xs text-slate-300 flex-1 truncate">{isMulti && <span className="mr-1">×{spans.length}</span>}{summary}</span>
                  {isMulti && <span className="text-[10px] text-slate-300">{expanded ? "▲ 收起" : "▼ 展开"}</span>}
                </button>
                {isMulti && expanded && (
                  <div id={groupId} className="ml-9 mt-2 space-y-2" role="region" aria-label={`${info.label}阶段记录详情`}>
                    {spans.map((span, index) => {
                      const spanSummary = getTraceMetricSummary([span]);
                      return (
                        <div key={span.id || `${type}-${index}`} className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2">
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            <span className="text-xs font-medium text-slate-700">{getTraceSpanNameLabel(span) || info.label} <span className="text-slate-400">#{index + 1}</span></span>
                            <span className="font-mono text-[10px] text-slate-500">{span.duration_ms} 毫秒</span>
                            <span className="font-mono text-[9px] text-slate-400">{span.id}</span>
                          </div>
                          {spanSummary && <p className="mt-1 break-words text-[10px] text-slate-500">{spanSummary}</p>}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
          {childSpans.length===0&&<div className="px-5 py-8 text-center text-xs text-slate-400">无 Span 数据</div>}
        </div>
      </div>

      {/* Warnings */}
      {warnings.length>0&&(<div className="bg-amber-50 border border-amber-200 rounded-xl p-5"><h2 className="text-xs font-medium text-amber-700 uppercase tracking-wider mb-2">⚠ 警告</h2><ul className="space-y-1">{warnings.map((w,i)=>(<li key={i} className="text-xs text-amber-800 flex items-start gap-2"><span className="shrink-0 mt-0.5">⚠</span><span>{w}</span></li>))}</ul></div>)}

      {/* Chunk 详情侧边面板 */}
      {chunkPanelOpen&&(<div className="fixed inset-0 z-50 flex justify-end"><div className="absolute inset-0 bg-black/20" onClick={()=>setChunkPanelOpen(false)}/><div className="relative h-full w-[92vw] max-w-[760px] bg-white shadow-xl border-l border-slate-200 flex flex-col h-full"><div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 shrink-0"><div><h3 className="text-sm font-semibold text-slate-800">📋 分块完整内容</h3><p className="text-[10px] text-slate-400 mt-0.5">文档 {docId}</p></div><button onClick={()=>setChunkPanelOpen(false)} className="p-1 rounded-md hover:bg-slate-100 text-slate-400"><X size={16}/></button></div><div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">{chunkDetailLoading&&<div className="flex items-center justify-center py-12"><Loader2 size={20} className="animate-spin text-accent"/><span className="ml-2 text-sm text-slate-400">加载中...</span></div>}{chunkDetailError&&<div className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">{chunkDetailError}</div>}{!chunkDetailLoading&&!chunkDetailError&&chunkDetailData.length===0&&<p className="text-sm text-slate-400 text-center py-8">暂无分块数据</p>}{!chunkDetailLoading&&chunkDetailData.map((ch,i)=>{const ruleKws=ch.keywords?ch.keywords.split(',').map(k=>k.trim()).filter(Boolean):[];const llmKws=ch.llm_keywords?ch.llm_keywords.split(',').map(k=>k.trim()).filter(Boolean):[];const heading=getChunkHeading(ch);return(<div key={i} className="bg-slate-50 rounded-lg p-3 border border-slate-100"><div className="flex items-center justify-between mb-2"><span className="text-[10px] font-semibold text-slate-500">分块 #{ch.chunk_index}</span><div className="flex items-center gap-2 text-[10px] text-slate-400"><span>{ch.char_count.toLocaleString()} 个字符</span></div></div><div className="space-y-1 mb-2">{heading&&<div className="flex items-center gap-1.5 text-[10px]"><span className="text-slate-400 shrink-0">{heading.label}</span>{heading.title&&<span className="text-slate-600">{heading.title}</span>}</div>}{ruleKws.length>0&&<div className="flex items-start gap-1.5 text-[10px]"><span className="text-slate-400 shrink-0 mt-0.5">📋 规则</span><div className="flex flex-wrap gap-0.5">{ruleKws.map((kw,j)=><span key={j} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-slate-100 text-slate-600 ring-1 ring-slate-200">{kw}</span>)}</div></div>}{llmKws.length>0&&<div className="flex items-start gap-1.5 text-[10px]"><span className="text-violet-400 shrink-0 mt-0.5">🔮 大模型</span><div className="flex flex-wrap gap-0.5">{llmKws.map((kw,j)=><span key={j} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-violet-50 text-violet-600 ring-1 ring-violet-200">{kw}</span>)}</div>{ch.llm_model&&<span className="text-[9px] text-slate-300 ml-auto shrink-0">{ch.llm_model}</span>}</div>}
{ch.simulated_questions&&ch.simulated_questions.length>0&&<div className="flex items-start gap-1.5 text-[10px]"><span className="text-amber-500 shrink-0 mt-0.5">{SIMULATED_QUESTIONS_LABEL}</span><div className="flex flex-col gap-0.5">{ch.simulated_questions.map((q,j)=><span key={j} className="text-slate-600 leading-relaxed">• {q}</span>)}</div></div>}</div><p className="text-xs text-slate-700 leading-relaxed whitespace-pre-wrap break-words border-t border-slate-200 pt-2">{ch.content}</p></div>);})}</div><div className="px-5 py-2.5 border-t border-slate-100 text-[10px] text-slate-400 shrink-0">共 {chunkDetailData.length} 个分块</div></div></div>)}
    </div></div>
  );
}

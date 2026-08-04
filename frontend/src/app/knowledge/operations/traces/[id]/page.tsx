"use client";

import { useState, useEffect, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import { getTraceById } from "@/lib/observability/source";
import type { TraceRecord, Span } from "@/types/trace";
import { statusBadge, formatTime, formatRelative } from "@/types/trace";
import { knowledgeService } from "@/services/knowledge";
import { X, Loader2 } from "lucide-react";

const SPAN_LABELS: Record<string, { label: string; order: number }> = {
  load: { label: "文件加载", order: 1 }, parse: { label: "解析文档", order: 2 },
  clean: { label: "数据清洗", order: 3 }, dedup: { label: "去重检查", order: 4 },
  chunk: { label: "文本分块", order: 5 }, llm: { label: "元数据生成", order: 6 },
  embedding: { label: "向量嵌入", order: 7 }, vector_db: { label: "写入向量库", order: 8 },
  workflow: { label: "索引编排", order: 0 },
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
  if (!llm && rule) return "规则"; if (llm && rule) return "LLM/规则"; if (llm && !rule) return "LLM"; return "";
};

const DOC_TYPE_CN: Record<string, string> = {
  compliance: "合规文档", policy: "制度文档", legal: "法律文档", financial: "财务文档",
  faq: "常见问题", product_spec: "商品规格", listing: "商品上架", sop: "操作流程",
  training: "培训文档", ad_policy: "广告政策", security: "安全文档",
  customer_data: "客户数据", contract_template: "合同模板", general: "通用文档",
};

const CHUNK_STRATEGY_CN: Record<string, string> = {
  "ManualPolicyChunkStrategy": "制度/规范文档", "ProjectReportChunkStrategy": "项目/报告",
  "QAChunkStrategy": "FAQ 问答", "GeneralChunkStrategy": "通用",
  "ManualChunkStrategy": "操作手册", "ContractChunkStrategy": "合同条款",
};
const CHUNK_METHOD_CN: Record<string, string> = {
  "ManualPolicyChunkStrategy": "按章节标题切分（#、第X章、一、），超长段(>2000字)递归子分块",
  "ProjectReportChunkStrategy": "按 Markdown 标题切分（#/##/###），保留 Header 层级元数据，超长段(>1500字)子分块",
  "QAChunkStrategy": "按 Q&A 边界切分（Q:/问:/【问】），一问一答一条 chunk",
  "GeneralChunkStrategy": "递归分割（\\n\\n→\\n→。→.→空格），固定 chunk_size=1000, overlap=100",
  "ManualChunkStrategy": "按步骤编号切分（步骤1、Step 1、①、1.），保持步骤完整，超长步骤(>3000字)才子分块",
  "ContractChunkStrategy": "按条款边界切分（第X条、Article X、§X），禁止跨条款切割，短条款(<2000字)合并",
};

interface ChunkDetail { chunk_index: number; content: string; char_count: number; keywords: string; llm_keywords?: string; llm_model?: string; section_title?: string; simulated_questions?: string[]; }

export default function DocTracePage() {
  const { id } = useParams<{ id: string }>(); const router = useRouter();
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());
  const [expandedMeta, setExpandedMeta] = useState<Set<string>>(new Set());
  const [chunkPanelOpen, setChunkPanelOpen] = useState(false);
  const [chunkDetailData, setChunkDetailData] = useState<ChunkDetail[]>([]);
  const [chunkDetailLoading, setChunkDetailLoading] = useState(false);
  const [chunkDetailError, setChunkDetailError] = useState("");

  useEffect(() => { let c=false; (async()=>{const t=await getTraceById(id); if(!c){setTrace(t);setLoading(false);}})(); return ()=>{c=true;}; }, [id]);
  useEffect(() => { if (!chunkPanelOpen) return; const onKey=(e:KeyboardEvent)=>{if(e.key==="Escape")setChunkPanelOpen(false)}; window.addEventListener("keydown",onKey); return ()=>window.removeEventListener("keydown",onKey); }, [chunkPanelOpen]);

  const childSpans = useMemo(() => { if(!trace)return[]; return (trace.spans||[]).filter(s=>s.parent_id!==null).sort((a,b)=>spanOrder(a)-spanOrder(b)); }, [trace]);
  const typeGroups = useMemo(() => { const m=new Map<string,Span[]>(); for(const s of childSpans){if(!m.has(s.type))m.set(s.type,[]); m.get(s.type)!.push(s);} return Array.from(m.entries()).sort((a,b)=>spanOrder(a[1][0])-spanOrder(b[1][0])); }, [childSpans]);

  const overview = useMemo(() => {
    if(!trace) return {fileSize:0,chunkCount:0,totalTokens:0,costUsd:0,docType:'',businessDomain:'',model:'',confidence:0,qualityScore:0};
    const ls=childSpans.find(s=>s.type==='load'); const cs=childSpans.find(s=>s.type==='chunk');
    const llmS=childSpans.find(s=>s.type==='llm'); const es=childSpans.find(s=>s.type==='embedding');
    const out=(llmS?.output||{}) as Record<string,unknown>;
    const rm=(out.rule_metadata||{}) as Record<string,unknown>;
    const lm=(out.llm_metadata||{}) as Record<string,unknown>;
    const lt=(lm.llm_tokens||{}) as Record<string,unknown>;
    const pt=(lt.prompt_tokens as number)||0; const ct=(lt.completion_tokens as number)||0;
    const qs=childSpans.find(s=>s.id==='quality');
    return {fileSize:(ls?.metrics?.file_size as number)||0, chunkCount:(cs?.metrics?.kept_chunks as number)||(cs?.output as any)?.total||0,
      totalTokens:pt+ct, costUsd:(lt.cost_usd as number)||0,
      docType:(rm.doc_type as string)||(out.doc_type as string)||'',
      businessDomain:(rm.business_domain as string)||(out.business_domain as string)||'',
      model:(trace?.model as any)?.name||(lt.model as string)||(es?.metrics?.model_name as string)||'',
      confidence:(rm.confidence as number)??0, qualityScore:(qs?.metrics?.score as number)??0};
  }, [childSpans, trace]);

  const warnings = useMemo(() => {
    const items:string[]=[]; if(overview.qualityScore<60) items.push(`质量评分偏低 (${overview.qualityScore}/100)`);
    const llmS=childSpans.find(s=>s.type==='llm');
    if(llmS&&trace&&(llmS.duration_ms||0)>10000&&trace.duration_ms>0&&(llmS.duration_ms||0)/trace.duration_ms>0.5)
      items.push(`元数据生成耗时 ${((llmS.duration_ms||0)/1000).toFixed(1)}s，占总耗时 ${(((llmS.duration_ms||0)/trace.duration_ms)*100).toFixed(0)}%`);
    if(overview.fileSize>0&&overview.fileSize<500) items.push(`文档过短 (${overview.fileSize} bytes)`);
    return items;
  }, [overview, childSpans, trace]);

  const docIdFromTags = trace?.tags?.doc_id||"";
  const docIdFromSpans = useMemo(() => { if(docIdFromTags)return""; for(const s of trace?.spans||[]){const m=s.metrics as Record<string,unknown>|undefined; if(m?.doc_id)return String(m.doc_id);} return""; }, [trace, docIdFromTags]);
  const docId = docIdFromTags||docIdFromSpans;
  const handleOpenChunkDetail = async () => { if(!docId)return; setChunkPanelOpen(true);setChunkDetailLoading(true);setChunkDetailError(""); try{const res=await knowledgeService.getChunkDetail(docId); setChunkDetailData((res as any)?.chunks??(res as any)?.data?.chunks??[]);}catch(e){setChunkDetailError((e as Error).message);}finally{setChunkDetailLoading(false);} };

  if(loading) return <div className="flex-1 flex items-center justify-center"><p className="text-sm text-slate-400">加载中…</p></div>;
  if(!trace) return <div className="flex-1 flex items-center justify-center"><div className="text-center"><p className="text-slate-400">Trace {id} 不存在或已过期</p><button onClick={()=>router.back()} className="mt-3 text-sm text-violet-600 hover:text-violet-500">← 返回</button></div></div>;

  const badge = statusBadge(trace.status??"success");
  const toggleTypeExpand = (type:string) => { const next=new Set(expandedTypes); next.has(type)?next.delete(type):next.add(type); setExpandedTypes(next); };
  const toggleMeta = (key:string) => { const next=new Set(expandedMeta); next.has(key)?next.delete(key):next.add(key); setExpandedMeta(next); };
  function summaryMetrics(spans: Span[]): string { const merged:Record<string,unknown>={}; for(const s of spans){if(!s.metrics)continue; for(const [k,v] of Object.entries(s.metrics)){if(k==="chunk_id"||k==="attempt")continue; if(merged[k]===undefined)merged[k]=v;}} const e=Object.entries(merged); if(!e.length)return""; return e.map(([k,v])=>`${k}: ${typeof v==="number"?v:String(v)}`).join(" · "); }

  return (
    <div className="flex-1 overflow-y-auto"><div className="max-w-3xl mx-auto px-6 py-8 space-y-5">
      <button onClick={()=>router.push("/knowledge/operations")} className="text-xs text-slate-400 hover:text-slate-600">← 返回操作中心</button>

      {/* Overview */}
      <div>
        <div className="flex items-center justify-between mb-3"><div className="flex items-center gap-3"><h1 className="text-base font-semibold text-slate-800">{trace.question}</h1><span className={`inline-block px-2 py-0.5 rounded text-[10px] font-semibold ${badge.bg}`}>{badge.label}</span></div><span className="text-xs text-slate-400" title={formatTime(trace.timestamp)}>{formatRelative(trace.timestamp)}</span></div>
        <div className="grid grid-cols-4 gap-2 mb-2">
          {[{label:'文件大小',value:overview.fileSize>0?`${(overview.fileSize/1024).toFixed(1)}KB`:'-',sub:`${overview.fileSize} bytes`},{label:'Chunks',value:overview.chunkCount>0?String(overview.chunkCount):'-',sub:'文本分块数'},{label:'Tokens',value:overview.totalTokens>0?overview.totalTokens.toLocaleString():'-',sub:'prompt+completion'},{label:'成本',value:overview.costUsd>0?`$${overview.costUsd.toFixed(4)}`:'-',sub:'LLM API 调用'}].map(c=>(<div key={c.label} className="bg-white border border-slate-200 rounded-lg px-3 py-2.5"><div className="text-[10px] text-slate-400 mb-0.5">{c.label}</div><div className="text-sm font-semibold text-slate-800 font-mono">{c.value}</div><div className="text-[9px] text-slate-400">{c.sub}</div></div>))}
        </div>
        <div className="grid grid-cols-4 gap-2">
          {[{label:'文档类型',value:DOC_TYPE_CN[overview.docType]||overview.docType||'-'},{label:'业务领域',value:overview.businessDomain||'-'},{label:'模型',value:overview.model||trace.model?.name||'-'},{label:'总耗时',value:trace.duration_ms>0?`${(trace.duration_ms/1000).toFixed(1)}s`:'-',sub:`${childSpans.length} spans · ${trace.id?.slice(0,8)}`}].map(c=>(<div key={c.label} className="bg-white border border-slate-200 rounded-lg px-3 py-2.5"><div className="text-[10px] text-slate-400 mb-0.5">{c.label}</div><div className="text-sm font-semibold text-slate-800 truncate">{c.value}</div>{c.sub&&<div className="text-[9px] text-slate-400">{c.sub}</div>}</div>))}
        </div>
      </div>

      {/* Pipeline 耗时分布 */}
      {typeGroups.length>0&&trace.duration_ms>0&&(<div className="bg-white border border-slate-200 rounded-xl p-5"><h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">⏱ Pipeline 耗时分布</h2><div className="space-y-1.5">{typeGroups.map(([type,spans])=>{const info=SPAN_LABELS[type]??{label:type,order:99};const totalMs=spans.reduce((sum,s)=>sum+(s.duration_ms||0),0);const pct=trace.duration_ms>0?(totalMs/trace.duration_ms)*100:0;const isHot=pct>30;const barColor=isHot?'bg-amber-400':pct>10?'bg-blue-400':'bg-slate-300';return(<div key={type} className="flex items-center gap-2 text-xs"><span className="w-20 text-slate-500 text-right shrink-0">{info.label}</span><div className="flex-1 bg-slate-100 rounded-full h-4 overflow-hidden"><div className={`h-full rounded-full transition-all ${barColor}`} style={{width:`${Math.max(pct,0.5)}%`,minWidth:pct>0?'4px':'0'}} /></div><span className="w-20 text-right font-mono text-slate-600 shrink-0">{totalMs}ms</span><span className="w-10 text-right text-slate-400 shrink-0">{pct.toFixed(0)}%</span></div>);})}</div></div>)}

      {/* 处理流水线 */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-slate-100"><h2 className="text-xs font-medium text-slate-500 uppercase tracking-wider">📦 处理流水线</h2></div>
        <div className="divide-y divide-slate-50">
          {typeGroups.map(([type,spans])=>{
            const info=SPAN_LABELS[type]??{label:spans[0]?.name||type,order:99};
            const totalMs=spans.reduce((sum,s)=>sum+(s.duration_ms||0),0);
            const hasError=spans.some(s=>s.status==="error"); const hasSkipped=spans.some(s=>s.status==="skipped");
            const statusIcon=hasError?"❌":hasSkipped?"⚠️":"✅"; const isMulti=spans.length>1;
            const expanded=expandedTypes.has(type); const summary=summaryMetrics(spans);

            // clean
            if(type==="clean"){const s=spans[0];const metrics=s.metrics||{};const cb=metrics.chars_before as number||0;const ca=metrics.chars_after as number||0;const ops=metrics.operations as string||"";const errMsg=metrics.error as string||"";const reduction=cb>0?(1-ca/cb)*100:0;const isNoop=cb>0&&cb===ca;return(<div key={type} className={`px-5 py-4 ${hasError?"bg-red-50/30":""}`}><div className="flex items-center gap-4 mb-2"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs}ms</span>{ops&&ops!=="none"&&<span className="text-xs text-slate-400 truncate">{ops}</span>}{isNoop&&!hasError&&<span className="text-[10px] text-slate-300 shrink-0">（文本已规范，无需清洗）</span>}</div><div className="ml-9 space-y-1 text-xs">{hasError?<div className="text-red-600 bg-red-50 rounded px-2 py-1 font-mono text-[11px]">❌ 清洗失败：{errMsg||"未知错误"}（已降级使用原始文本继续）</div>:<>{cb>0&&<div className="text-slate-500">清洗前 <span className="font-mono text-slate-600">{cb.toLocaleString()}</span> 字符 → 清洗后 <span className="font-mono text-slate-600">{ca.toLocaleString()}</span> 字符<span className={`ml-1 ${isNoop?"text-slate-300":"text-slate-400"}`}>（{reduction.toFixed(1)}% 缩减）</span></div>}</>}</div></div>);}

            // chunk
            if(type==="chunk"){const fs=spans[0];const output=(fs?.output||{}) as Record<string,unknown>;const preview=(output["preview"] as string[])||[];const total=(output["total"] as number)||(fs?.metrics?.kept_chunks as number)||0;const rawChunks=fs?.metrics?.raw_chunks as number||0;const filteredOut=fs?.metrics?.filtered_out as number||0;const strategy=(output["strategy"] as string)||"";const strategyCN=CHUNK_STRATEGY_CN[strategy]||strategy;return(<div key={type} className="px-5 py-4"><div className="flex items-center gap-4 mb-2"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs}ms</span><span className="text-xs text-slate-400">切分 <span className="font-mono text-slate-600">{total}</span> 块{rawChunks>0&&<span className="text-slate-400 ml-1">（原始 {rawChunks}{filteredOut>0?`，过滤 ${filteredOut}`:""}）</span>}</span>{docId&&total>0&&<button onClick={handleOpenChunkDetail} className="ml-auto text-xs text-accent hover:text-accent-hover hover:underline">📋 查看完整内容 →</button>}</div>{strategy&&<div className="ml-9 mb-2 text-[10px] text-slate-400"><span>✂️ 切分方案: </span><span className="text-slate-600 font-medium">{strategyCN||'—'}</span>{strategy&&CHUNK_METHOD_CN[strategy]&&<div className="text-[9px] text-slate-400 mt-0.5">切法: {CHUNK_METHOD_CN[strategy]}</div>}</div>}{preview.length>0&&<div className="ml-9 space-y-1 mb-2">{preview.map((text,i)=>(<div key={i} className="text-xs text-slate-500 bg-slate-50 rounded px-2 py-1 truncate"><span className="text-slate-300 mr-1">预览块 {i+1}</span>{text.slice(0,120)}{text.length>120?"…":""}</div>))}</div>}</div>);}

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
              const llmReason=(llmDecision["llm_reason"] as string)||"";
              const docType=(ruleMeta["doc_type"] as string)||(spans[0]?.metrics?.doc_type as string)||"";
              const confidence=(ruleMeta["confidence"] as number)||0;
              const complexity=(ruleMeta["complexity"] as Record<string,unknown>)||{};
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
              const qualitySpan=childSpans.find(s=>s.id==='quality');
              const qualityDims=((qualitySpan?.output||{})as Record<string,number>);
              const summaryText=ruleMeta["summary"] as string||"";
              const sections=ruleMeta["sections"] as string[]||[];

              return (<div key={type} className="px-5 py-3 cursor-pointer hover:bg-violet-50/50 transition-colors" onClick={()=>toggleTypeExpand(type)}>
                <div className="flex items-center gap-4 flex-wrap">
                  <span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs}ms</span>
                  {strategyLabel&&<span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${llmUsed?"bg-violet-200 text-violet-700":"bg-slate-100 text-slate-600"}`}>{strategyLabel}</span>}
                  {docType&&<span className="text-[10px] text-slate-400">{DOC_TYPE_CN[docType]||docType}<span className="text-slate-300">({docType})</span>{confidence>0&&<span className="text-slate-400 ml-0.5">· 置信度 {(confidence*100).toFixed(0)}%</span>}</span>}
                  <span className="ml-auto text-[10px] text-slate-300">{expanded?"▲ 收起":"▼ 展开"}</span>
                </div>

                {expanded&&(<div className="mt-2 ml-9 space-y-0.5" onClick={e=>e.stopPropagation()}>
                  {[
                    {k:'classify',icon:'📄',label:'文档分类',value:`${DOC_TYPE_CN[docType]||docType} · ${(confidence*100).toFixed(0)}%`,hint:'规则关键词加权计分',show:true,
                      detail:()=>(<div className="space-y-1">
                        {topTypes.length>0&&(<div>{topTypes.map(([t,s])=>(<div key={t} className="flex items-center gap-2" style={{fontSize:'9px'}}><span className="w-16 text-slate-500 text-right">{DOC_TYPE_CN[t]||t}</span><div className="flex-1 bg-slate-200 rounded-full h-2.5 overflow-hidden"><div className="h-full rounded-full bg-blue-400" style={{width:Math.max(s/20*100,2)+'%'}}/></div><span className="w-8 text-right font-mono text-slate-500">{s}</span></div>))}<div className="text-[9px] text-slate-400">置信度 = 最高分/(最高分+次高分)</div></div>)}
                        {clsKeywordHits.length>0&&(<div className="border-t border-slate-100 pt-1 mt-1">{(()=>{const g:Record<string,any[]>={};for(const h of clsKeywordHits){if(!g[h.type])g[h.type]=[];g[h.type].push(h);}for(const[t,hits]of Object.entries(g)){const mg:Record<string,any>={};for(const h of hits){const key=h.keyword+(h.source||'');if(mg[key]){mg[key].w+=h.weight;mg[key].c++}else mg[key]={k:h.keyword,w:h.weight,s:h.source,c:1}}g[t]=Object.values(mg)}return Object.entries(g).sort((a,b)=>b[1].reduce((s:number,h:any)=>s+h.w,0)-a[1].reduce((s:number,h:any)=>s+h.w,0)).slice(0,5).map(([t,hits])=>(<div key={t}><span className="text-[9px] text-slate-400">{DOC_TYPE_CN[t]||t}: </span>{hits.map((h:any)=>(<span key={h.k+h.s} className="text-[9px] text-slate-500 mr-2">{h.s==='title'?'📌':h.s==='filename'?'📎':''}{h.k}<span className="font-mono text-slate-300 ml-0.5">+{h.w}{h.c>1?`×${h.c}`:''}</span></span>))}</div>));})()}</div>)}
                      </div>)},
                    {k:'domain',icon:'🌐',label:'业务领域',value:overview.businessDomain||'—',hint:'关键词命中最高',show:!!(domainTopTypes.length||domainHits.length),
                      detail:()=>(<div className="space-y-1">{domainTopTypes.length>0&&(<div>{domainTopTypes.map(([t,s])=>(<div key={t} className="flex items-center gap-2" style={{fontSize:'9px'}}><span className="w-16 text-slate-500 text-right">{t}</span><div className="flex-1 bg-slate-200 rounded-full h-2.5 overflow-hidden"><div className="h-full rounded-full bg-cyan-400" style={{width:Math.max(s/10*100,2)+'%'}}/></div><span className="w-8 text-right font-mono text-slate-500">{s}</span></div>))}</div>)}{domainHits.length>0&&<div className="flex flex-wrap gap-x-2 gap-y-0.5">{domainHits.map((h:string,i:number)=>{const m=h.match(/^(.+)\+(\d+)$/);return <span key={'dh'+i} className="text-[9px] text-slate-500">▸ {m?m[1]:h}{m&&<span className="font-mono text-cyan-500 ml-0.5">+{m[2]}</span>}</span>;})}</div>}</div>)},
                    {k:'quality',icon:'📊',label:'质量评分',value:`${overview.qualityScore}/100 ${overview.qualityScore>=60?'✅':overview.qualityScore>=40?'⚠':'❌'}`,hint:'4 维度综合',show:overview.qualityScore>0,
                      detail:()=>(<div className="grid grid-cols-2 gap-1 text-[9px]">{Object.entries(qualityDims).filter(([k])=>k!=='total').map(([k,v])=>(<div key={k} className="flex justify-between"><span className="text-slate-500">{{completeness:'文本完整度',structure:'结构完整度',noise:'噪声检测',uniqueness:'去重检测'}[k]||k}</span><span className="font-mono text-slate-600">{v}</span></div>))}</div>)},
                    {k:'complexity',icon:'🧩',label:'复杂度',value:`${String(complexity['complexity_score']??'—')}/100`,hint:'6 维度综合',show:true,
                      detail:()=>{const dims=complexity['dimensions'] as Record<string,number>|undefined;if(dims)return(<div className="grid grid-cols-3 gap-1 text-[9px]">{[{l:'章节',max:10,k:'headings'},{l:'表格',max:5,k:'tables'},{l:'法规',max:15,k:'legal'},{l:'风险',max:30,k:'risk'},{l:'长度',max:10,k:'length'},{l:'置信度',max:10,k:'confidence'}].map(d=>(<div key={d.k} className="flex justify-between"><span className="text-slate-500">{d.l}</span><span className="font-mono text-slate-600">{dims[d.k]??0}<span className="text-slate-300">/{d.max}</span></span></div>))}</div>);return <div className="text-[9px] text-slate-400">无维度数据</div>}},
                    {k:'llm_decision',icon:'🔮',label:'LLM 决策',value:llmUsed?`评分${llmScore} · ${llmStrategy==='llm_force'?'强制触发':llmScore>=50?'达标触发':'未触发'}`:'未使用 LLM',hint:llmUsed?'评分累加':'—',show:llmUsed,
                      detail:()=>(<div className="space-y-1">{llmReason&&llmReason.split(";").map((s:string)=>s.trim()).filter(Boolean).map((p:string)=>{let label=p;let score=0;let forced=false;const sm=p.match(/\((\+?\d+)\)/);const parsed=sm?parseInt(sm[1]):0;const cv=(v:string)=>v.replace(/\(.*\)/,'').trim();if(p.includes("high_value:")){label=`${DOC_TYPE_CN[cv(p.split(":")[1])]||cv(p.split(":")[1])} 高价值`;score=parsed}else if(p.includes("risk_hits:")){label=`命中 ${cv(p.split(":")[1])} 个风险词`;score=parsed}else if(p.includes("low_conf:")){label=`分类置信度不足（${(parseFloat(cv(p.split(":")[1]))*100).toFixed(0)}%）`;score=parsed}else if(p.includes("complex_struct:")){label=`文档结构复杂（${cv(p.split(":")[1])} 分）`;score=parsed}else if(p.includes("forced:high_risk")){label="高风险类强制触发";forced=true}return(<div key={p} className="flex items-center gap-2 text-[9px]"><span className="text-slate-500 flex-1">· {label}</span>{forced?<span className="font-mono text-slate-300">—</span>:<span className={`font-mono ${score>0?'text-violet-500':'text-slate-300'}`}>+{score}</span>}</div>)})}{tokenOk&&<div className="text-[9px] text-slate-400 mt-1 pt-1 border-t border-slate-100">模型: {String(llmTokens["model"]||"")} · 入{llmTokens["prompt_tokens"]}/出{llmTokens["completion_tokens"]??0}{costOk&&<span> · ${costUsd!.toFixed(6)}</span>}</div>}</div>)},
                    {k:'summary',icon:'📝',label:'摘要',value:summaryText?summaryText.slice(0,50)+'…':'—',hint:llmUsed?'LLM 生成':'抽取式',show:!!summaryText,
                      detail:()=>(<div className="text-[10px] text-slate-700 leading-relaxed">{summaryText.length>200?<SummaryExpand text={summaryText}/>:summaryText}</div>)},
                    {k:'sections',icon:'📑',label:'章节',value:`${sections.length} 个`,hint:'正则提取',show:sections.length>0,
                      detail:()=>(<div className="flex flex-wrap gap-1">{sections.map((s:string,i:number)=>(<span key={i} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-slate-100 text-slate-600">{s}</span>))}</div>)},
                    {k:'rule_kw',icon:'🏷️',label:'规则关键词',value:`${kwsRule.length} 个`,hint:'模式匹配',show:hasRule,
                      detail:()=>(<div className="flex flex-wrap gap-1">{kwsRule.map((kw,i)=>(<span key={i} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-slate-100 text-slate-600 ring-1 ring-slate-200">{kw.word}</span>))}</div>)},
                    {k:'llm_kw',icon:'💬',label:'LLM 关键词',value:`${kwsLlm.length} 个`,hint:String(llmTokens["model"]||"LLM"),show:llmUsed&&kwsLlm.length>0,
                      detail:()=>(<div className="flex flex-wrap gap-1">{kwsLlm.map(kw=>(<span key={kw.word} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-violet-100 text-violet-700 ring-1 ring-violet-200">{kw.word}</span>))}</div>)},
                  ].filter(r=>r.show).map(r=>{const open=expandedMeta.has(r.k);return(<div key={r.k}><div className="flex items-center gap-2 py-1 px-1 rounded hover:bg-slate-50 cursor-pointer" onClick={()=>toggleMeta(r.k)}><span className="text-[10px] w-5 text-center">{r.icon}</span><span className="text-[10px] text-slate-500 w-16 shrink-0">{r.label}</span><span className="text-[10px] text-slate-700 font-medium flex-1 truncate">{r.value}</span><span className="text-[9px] text-slate-300 shrink-0">{r.hint} {open?'▾':'▸'}</span></div>{open&&<div className="ml-11 mr-2 mb-1.5 p-2 bg-slate-50 rounded border border-slate-100" onClick={e=>e.stopPropagation()}>{r.detail()}</div>}</div>);})}
                </div>)}
              </div>);
            }

            // embedding
            if(type==="embedding"){const s=spans[0];const attempted=s.metrics?.attempted as number||0;const succeeded=s.metrics?.succeeded as number||0;const failed=s.metrics?.failed as number||0;const embModel=trace?.tags?.embedding_model||"";return(<div key={type} className={`px-5 py-4 ${hasError?"bg-red-50/30":""}`}><div className="flex items-center gap-4"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs}ms</span><span className="text-xs text-slate-400">成功 <span className="font-mono text-green-600">{succeeded}</span>{failed>0&&<span className="ml-1">失败 <span className="font-mono text-red-500">{failed}</span></span>}<span className="ml-1">/ 共 <span className="font-mono text-slate-500">{attempted}</span></span></span>{embModel&&<span className="ml-auto text-[10px] text-slate-300 shrink-0">模型: {embModel}</span>}</div></div>);}

            // dedup
            if(type==="dedup"){const s=spans[0];const cached=s.metrics?.cached as boolean|undefined;const existingDocId=s.metrics?.existing_doc_id as string||"";return(<div key={type} className="px-5 py-4"><div className="flex items-center gap-4"><span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-sm font-medium text-slate-700 shrink-0">{info.label}</span><span className="font-mono text-xs text-slate-500 w-16 text-right shrink-0">{totalMs}ms</span>{cached?<span className="text-xs text-amber-600">⚡ 命中缓存 → doc {existingDocId}</span>:<span className="text-xs text-slate-400">未命中缓存 → 进入完整索引流程</span>}</div></div>);}

            return (<div key={type} className={`flex items-center gap-4 px-5 py-2.5 text-sm ${isMulti?"cursor-pointer hover:bg-slate-50":""}`} onClick={()=>isMulti&&toggleTypeExpand(type)}>
              <span className="text-xs w-5 shrink-0">{statusIcon}</span><span className="w-24 text-slate-500 shrink-0">{info.label}</span><span className={`font-mono text-xs w-16 text-right shrink-0 ${hasError?"text-red-500":"text-slate-400"}`}>{totalMs}ms</span>
              <span className="text-xs text-slate-300 flex-1 truncate">{isMulti&&<span className="mr-1">×{spans.length}</span>}{summary}</span>
              {isMulti&&<span className="text-[10px] text-slate-300">{expanded?"▲":"▼"}</span>}
            </div>);
          })}
          {childSpans.length===0&&<div className="px-5 py-8 text-center text-xs text-slate-400">无 Span 数据</div>}
        </div>
      </div>

      {/* Warnings */}
      {warnings.length>0&&(<div className="bg-amber-50 border border-amber-200 rounded-xl p-5"><h2 className="text-xs font-medium text-amber-700 uppercase tracking-wider mb-2">⚠ 警告</h2><ul className="space-y-1">{warnings.map((w,i)=>(<li key={i} className="text-xs text-amber-800 flex items-start gap-2"><span className="shrink-0 mt-0.5">⚠</span><span>{w}</span></li>))}</ul></div>)}

      {/* Chunk 详情侧边面板 */}
      {chunkPanelOpen&&(<div className="fixed inset-0 z-50 flex justify-end"><div className="absolute inset-0 bg-black/20" onClick={()=>setChunkPanelOpen(false)}/><div className="relative w-[520px] bg-white shadow-xl border-l border-slate-200 flex flex-col h-full"><div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 shrink-0"><div><h3 className="text-sm font-semibold text-slate-800">📋 Chunk 完整内容</h3><p className="text-[10px] text-slate-400 mt-0.5">文档 {docId}</p></div><button onClick={()=>setChunkPanelOpen(false)} className="p-1 rounded-md hover:bg-slate-100 text-slate-400"><X size={16}/></button></div><div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">{chunkDetailLoading&&<div className="flex items-center justify-center py-12"><Loader2 size={20} className="animate-spin text-accent"/><span className="ml-2 text-sm text-slate-400">加载中...</span></div>}{chunkDetailError&&<div className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">{chunkDetailError}</div>}{!chunkDetailLoading&&!chunkDetailError&&chunkDetailData.length===0&&<p className="text-sm text-slate-400 text-center py-8">暂无 Chunk 数据</p>}{!chunkDetailLoading&&chunkDetailData.map((ch,i)=>{const ruleKws=ch.keywords?ch.keywords.split(',').map(k=>k.trim()).filter(Boolean):[];const llmKws=ch.llm_keywords?ch.llm_keywords.split(',').map(k=>k.trim()).filter(Boolean):[];return(<div key={i} className="bg-slate-50 rounded-lg p-3 border border-slate-100"><div className="flex items-center justify-between mb-2"><span className="text-[10px] font-semibold text-slate-500">Chunk #{ch.chunk_index}</span><div className="flex items-center gap-2 text-[10px] text-slate-400"><span>{ch.char_count.toLocaleString()} tokens</span></div></div><div className="space-y-1 mb-2">{ch.section_title&&<div className="flex items-center gap-1.5 text-[10px]"><span className="text-slate-400 shrink-0">📑 章节</span><span className="text-slate-600">{ch.section_title}</span></div>}{ruleKws.length>0&&<div className="flex items-start gap-1.5 text-[10px]"><span className="text-slate-400 shrink-0 mt-0.5">📋 规则</span><div className="flex flex-wrap gap-0.5">{ruleKws.map((kw,j)=><span key={j} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-slate-100 text-slate-600 ring-1 ring-slate-200">{kw}</span>)}</div></div>}{llmKws.length>0&&<div className="flex items-start gap-1.5 text-[10px]"><span className="text-violet-400 shrink-0 mt-0.5">🔮 LLM</span><div className="flex flex-wrap gap-0.5">{llmKws.map((kw,j)=><span key={j} className="inline-block px-1.5 py-0.5 rounded text-[9px] bg-violet-50 text-violet-600 ring-1 ring-violet-200">{kw}</span>)}</div>{ch.llm_model&&<span className="text-[9px] text-slate-300 ml-auto shrink-0">{ch.llm_model}</span>}</div>}
{ch.simulated_questions&&ch.simulated_questions.length>0&&<div className="flex items-start gap-1.5 text-[10px]"><span className="text-amber-500 shrink-0 mt-0.5">❓ FAQ</span><div className="flex flex-col gap-0.5">{ch.simulated_questions.map((q,j)=><span key={j} className="text-slate-600 leading-relaxed">• {q}</span>)}</div></div>}</div><p className="text-xs text-slate-700 leading-relaxed whitespace-pre-wrap break-words border-t border-slate-200 pt-2">{ch.content}</p></div>);})}</div><div className="px-5 py-2.5 border-t border-slate-100 text-[10px] text-slate-400 shrink-0">共 {chunkDetailData.length} 个 Chunk</div></div></div>)}
    </div></div>
  );
}

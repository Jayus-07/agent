import { TRACE_SOURCE_LABELS, type TraceRecord } from "@/types/trace";

/**
 * 运行对象类型 / 域展示口径（2026-10-30 管理端重构）。
 *
 * 列表 DTO 尚未下发 workflow_kind（TraceRecord 已有该字段、接口未透出，
 * 由 pushdown 任务补齐），此处先按 workflow_name 字面量做**唯一**前端映射；
 * DTO 补字段后此文件只保留域文案，不再做 kind 推断。禁止在其他文件复制映射。
 */
export type RunKindKey = "agent" | "rag" | "index" | "wf" | "probe";

export interface RunKindInfo {
  key: RunKindKey;
  label: string;
}

const KIND_RULES: Array<{ match: RegExp; key: RunKindKey; label: string }> = [
  { match: /^admin-probe/, key: "probe", label: "健康探测" },
  { match: /^knowledge_index/, key: "index", label: "索引流水线" },
  { match: /^agent_roundtable/, key: "wf", label: "Agent 圆桌" },
  { match: /^daily_report/, key: "wf", label: "定时任务" },
  { match: /^sql_agent/, key: "wf", label: "SQL Agent" },
  { match: /^rag_agent/, key: "rag", label: "RAG 链" },
  { match: /^agent$/, key: "agent", label: "Agent 图" },
];

export function deriveRunKind(t: Pick<TraceRecord, "workflow_name">): RunKindInfo {
  const wf = (t.workflow_name || "").trim();
  for (const rule of KIND_RULES) {
    if (rule.match.test(wf)) return { key: rule.key, label: rule.label };
  }
  return { key: "wf", label: wf ? "工作流" : "未记录" };
}

/** 域口径单一事实源仍是后端 classify_trace_source（DTO source）；此处只做展示文案。 */
export function deriveDomainLabel(t: Pick<TraceRecord, "source" | "workflow_name">): string {
  if (/^admin-probe/.test((t.workflow_name || "").trim())) return "探测流量";
  const source = t.source || "";
  if (source && TRACE_SOURCE_LABELS[source]) return TRACE_SOURCE_LABELS[source];
  const kind = deriveRunKind(t).key;
  if (kind === "rag" || kind === "index" || kind === "wf") return "平台工作流";
  return "其他";
}

export function runKindBadgeClass(key: RunKindKey): string {
  switch (key) {
    case "agent": return "bg-blue-50 text-blue-700 border-blue-200";
    case "rag": return "bg-violet-50 text-violet-700 border-violet-200";
    case "index": return "bg-emerald-50 text-emerald-700 border-emerald-200";
    case "probe": return "bg-amber-50 text-amber-700 border-amber-200";
    default: return "bg-slate-50 text-slate-600 border-slate-200";
  }
}

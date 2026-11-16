import type { Span } from "@/types/trace";
import {
  getBusinessDomainLabel,
  getCacheStatusLabel,
  getChunkStrategyLabel,
  getDocumentTypeLabel,
  getProcessingCodeLabel,
  getProcessingStatusLabel,
  getQualityIssueLabel,
} from "@/lib/knowledge-display";
import { formatTracePageCount } from "./trace-metrics";

const SPAN_TYPE_LABELS: Record<string, string> = {
  load: "文件加载",
  parse: "文档解析",
  clean: "文本清洗",
  dedup: "重复检查",
  chunk: "文本切分",
  llm: "大模型处理",
  llm_call: "模型服务调用",
  embedding: "向量生成",
  vector_db: "写入向量库",
  workflow: "处理流程",
  stage: "内部处理阶段",
  rule: "规则处理",
  audit: "质量检查",
};

const SPAN_NAME_LABELS: Record<string, string> = {
  "Question generation Provider call": "模拟问题生成模型服务调用",
  "Question generation": "模拟问题生成",
  "Rule metadata assembly": "规则元数据整理",
  "Rule classify": "规则分类",
  "Rule domain classify": "业务领域规则分类",
  "Quality check": "质量检查",
  "MinHash dedup": "相似内容去重",
  "Rule extract": "规则抽取",
  "Section extract": "章节抽取",
  "Quality record": "质量记录",
  "Metadata enrichment skipped": "元数据增强已跳过",
};

const METRIC_LABELS: Record<string, string> = {
  file_size: "文件大小",
  ext: "文件格式",
  doc_count: "解析文档数",
  page_count: "页数",
  page_count_applicable: "页数是否适用",
  chunk_count: "分块数",
  raw_chunks: "原始分块数",
  kept_chunks: "保留分块数",
  filtered_out: "过滤分块数",
  loader: "解析流程",
  internal_parse_file_ms: "文件解析耗时",
  internal_clean_ms: "内部清洗耗时",
  internal_structure_analysis_ms: "结构识别耗时",
  internal_classification_routing_ms: "分类路由耗时",
  internal_split_ms: "内部切分耗时",
  internal_chunk_postprocess_ms: "切分后处理耗时",
  internal_total_ms: "内部处理总耗时",
  written: "写入数量",
  context_only_chunks: "仅作上下文的分块数",
  table: "表格信息",
  doc_type: "文档类型",
  confidence: "分类置信度",
  domain: "业务领域",
  near_dup_id: "相似文档",
  time_refs_count: "时间引用数",
  strategy: "处理策略",
  doc_size: "文档字数",
  need_llm_keywords: "是否需要大模型关键词",
  parallel_execution: "是否并行执行",
  simulated_questions_chunks: "生成模拟问题涉及分块数",
  generation_status: "生成状态",
  provider_call: "是否调用模型服务",
  provider_duration_ms: "模型服务耗时",
  cache_status: "缓存状态",
  model: "模型",
  prompt_tokens: "输入词元数",
  completion_tokens: "输出词元数",
  reasoning_tokens: "推理词元数",
  total_tokens: "总词元数",
  output_questions: "生成问题数",
  score: "质量分数",
  status: "检查状态",
  issues: "问题数",
  sections_count: "章节数",
  quality_score: "质量评分",
  quality_score_base: "质量分基准",
  quality_score_hard_deduction: "严重问题扣分值",
  quality_score_warn_deduction: "提醒项扣分值",
  anomalies_total: "异常总数",
  anomalies_hard: "严重异常数",
  anomalies_warn: "提醒项异常数",
  record_path: "质量记录",
  coverage_sampled: "是否抽样检查",
  coverage_leaf_total: "检索分块总数",
  coverage_audited_leaf_count: "已检查检索分块数",
  coverage_uncovered_count: "未覆盖分块数",
  coverage_audit_error: "覆盖检查是否失败",
  coverage_issue: "覆盖检查问题",
  leaf_chunk_count: "检索分块数",
  valid_leaf_chunk_count: "有效检索分块数",
  parent_chunk_count: "父分块数",
  empty_chunk_count: "空分块数",
  missing_chunk_id_count: "缺少分块 ID 数",
  duplicate_chunk_id_count: "重复分块 ID 数",
  parent_reference_count: "父分块引用数",
  invalid_parent_reference_count: "无效父分块引用数",
  attempted: "尝试数量",
  succeeded: "成功数量",
  failed: "失败数量",
  skipped: "跳过数量",
  cached: "是否命中缓存",
  existing_doc_id: "已存在文档 ID",
};

const VALUE_LABELS: Record<string, Record<string, string>> = {
  loader: { pipeline: "统一处理流程" },
  strategy: { deterministic_fallback: "规则兜底" },
  generation_status: { success: "成功", failed: "失败", error: "失败", skipped: "已跳过" },
  status: { pass: "通过", success: "成功", failed: "失败", error: "失败", warning: "有提醒" },
  near_dup_id: { "(see below)": "见下方记录" },
  domain: { "(computed below)": "由后续规则计算" },
};

export function getTraceSpanTypeLabel(type: string): string {
  return SPAN_TYPE_LABELS[type] || "其他处理阶段";
}

export function getTraceSpanNameLabel(span: Pick<Span, "name" | "type">): string {
  const name = span.name?.trim() || "";
  const exact = SPAN_NAME_LABELS[name];
  if (exact) return exact;

  const filenamePrefixes: Array<[string, string]> = [
    ["Load ", "读取文件"],
    ["Parse ", "解析文档"],
    ["Clean ", "清洗文本"],
    ["Check ", "重复检查"],
    ["Chunk ", "切分文档"],
    ["Build metadata", "构建元数据"],
    ["Embed document summary", "生成文档摘要向量"],
    ["Embed ", "生成分块向量"],
    ["Write to vector DB", "写入向量库"],
  ];
  for (const [prefix, label] of filenamePrefixes) {
    if (name.startsWith(prefix)) {
      const detail = name.slice(prefix.length).trim();
      return detail ? `${label} · ${detail}` : label;
    }
  }
  if (/[\u3400-\u9fff]/.test(name)) return name;
  return `${getTraceSpanTypeLabel(span.type)}详情`;
}

function formatMetricValue(key: string, value: unknown, metrics: Record<string, unknown>): string {
  if (key === "page_count") {
    return formatTracePageCount(value, metrics.page_count_applicable);
  }
  if (key === "record_path") return value ? "已生成" : "未生成";
  if (key === "coverage_issue") return value ? getQualityIssueLabel(String(value)) : "无";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "未记录";
    const formatted = value.toLocaleString();
    if (key === "file_size") return `${formatted} 字节`;
    if (key === "doc_size") return `${formatted} 字`;
    if (key.endsWith("_ms")) return `${formatted} 毫秒`;
    return formatted;
  }
  if (Array.isArray(value)) return `${value.length.toLocaleString()} 项`;
  if (value === null || value === undefined || value === "") return "未记录";

  const text = String(value);
  const valueLabel = VALUE_LABELS[key]?.[text];
  if (valueLabel) return valueLabel;
  if (key === "doc_type") return getDocumentTypeLabel(text);
  if (key === "domain") return getBusinessDomainLabel(text);
  if (key === "strategy") return getChunkStrategyLabel(text);
  if (key === "cache_status") return getCacheStatusLabel(text);
  if (key === "generation_status") return getProcessingStatusLabel(text);
  if (key === "loader") return getProcessingCodeLabel(text);
  if (text === "(see below)") return "见下方记录";
  if (text === "(computed below)") return "由后续规则计算";
  return text;
}

export function getTraceMetricSummary(spans: Span[]): string {
  const merged: Record<string, unknown> = {};
  for (const span of spans) {
    for (const [key, value] of Object.entries(span.metrics || {})) {
      if (key === "chunk_id" || key === "attempt" || value === "" || value === null || value === undefined) continue;
      if (merged[key] === undefined) merged[key] = value;
    }
  }
  return Object.entries(merged)
    .map(([key, value], index) => `${METRIC_LABELS[key] || `其他运行指标 ${index + 1}`}：${formatMetricValue(key, value, merged)}`)
    .join(" · ");
}

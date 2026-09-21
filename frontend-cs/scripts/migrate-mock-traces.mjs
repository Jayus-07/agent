/**
 * 旧 mock/traces.json → 新 mock/traces/ 目录结构
 *
 * 生成：
 *   summaries/{workflow_name}.json   — 列表页 Summary
 *   details/{trace_id}.json          — 详情页 Detail
 *   index.ts                         — 聚合入口（import + DETAILS 映射）
 *
 * 使用：node scripts/migrate-mock-traces.mjs
 */
import fs from "fs";
import path from "path";

const ROOT = path.resolve(import.meta.dirname, "..");
const OLD = path.join(ROOT, "src/mock/traces.json");
const NEW = path.join(ROOT, "src/mock/traces/fixtures");

const old = JSON.parse(fs.readFileSync(OLD, "utf-8"));
console.log(`读取旧数据: ${old.length} 条 trace`);

// ── 只清理 fixtures/ 目录（不动 schemas/ / api.ts / index.ts） ──
if (fs.existsSync(NEW)) fs.rmSync(NEW, { recursive: true });
fs.mkdirSync(path.join(NEW, "summaries"), { recursive: true });
fs.mkdirSync(path.join(NEW, "details"), { recursive: true });

// ═══════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════

/** 分离 span 的 attributes（字符串）和 metrics（数值） */
function splitAttrsAndMetrics(rawMetrics, span) {
  const attrs = {};
  const metrics = {};

  // 先把旧 metrics 字段分类
  for (const [k, v] of Object.entries(rawMetrics || {})) {
    if (typeof v === "number") {
      metrics[k] = v;
    } else if (typeof v === "boolean") {
      metrics[k] = v ? 1 : 0;
    } else if (typeof v === "string" && !isNaN(Number(v))) {
      metrics[k] = Number(v);
    } else {
      attrs[k] = String(v ?? "");
    }
  }

  // 从 llm_call 提取 model/temperature → attributes
  if (span.llm_call) {
    if (span.llm_call.model) attrs["llm.model"] = span.llm_call.model;
    if (span.llm_call.temperature !== undefined) attrs["llm.temperature"] = String(span.llm_call.temperature);
  }

  // 从 http_breakdown 提取 → metrics
  if (span.http_breakdown) {
    for (const [k, v] of Object.entries(span.http_breakdown)) {
      if (typeof v === "number") metrics[k] = v;
    }
    attrs["http.method"] = span.http_breakdown.dns_ms !== undefined ? "POST" : (attrs["http.method"] || "GET");
  }

  // attributes 里的旧 attributes（从之前脚本生成的）
  if (span.attributes) {
    for (const [k, v] of Object.entries(span.attributes)) {
      if (typeof v === "number") {
        metrics[k] = v;
      } else if (typeof v === "boolean") {
        metrics[k] = v ? 1 : 0;
      } else {
        attrs[k] = String(v ?? "");
      }
    }
  }

  return { attrs, metrics };
}

/** 推断 start_time（没有就用 trace 时间，有 duration 就反推 end_time） */
function fixTimes(span, traceStartTime) {
  const start = span.start_time || traceStartTime;
  const dur = span.duration_ms || 0;
  let end = start;
  if (dur > 0) {
    const d = new Date(start);
    d.setMilliseconds(d.getMilliseconds() + dur);
    end = d.toISOString().replace(/\.\d{3}Z$/, "Z");
  }
  return { start_time: start, end_time: end };
}

/** 迁移一个 span */
function migrateSpan(span, traceStartTime, seq) {
  const { attrs, metrics } = splitAttrsAndMetrics(span.metrics || {}, span);
  const { start_time, end_time } = fixTimes(span, traceStartTime);

  // input/output：优先用 span 自身的，其次从 llm_call 提取
  let input = span.input || null;
  let output = span.output || null;
  if (span.llm_call) {
    if (!input) input = { prompt: span.llm_call.prompt_text };
    if (!output) output = { response: span.llm_call.response_text };
  }

  return {
    span_id: span.id,
    parent_id: span.parent_id ?? null,
    name: span.name,
    type: span.type,
    status: span.status,
    start_time,
    end_time,
    duration_ms: span.duration_ms,
    sequence: seq,
    attributes: attrs,
    metrics,
    input,
    output,
    events: span.events || [],
    warnings: span.warnings || [],
    errors: span.errors || [],
  };
}

// ═══════════════════════════════════════════
// 主迁移逻辑
// ═══════════════════════════════════════════

const summariesByWorkflow = {};  // { workflow_name: [summary, ...] }
const allTraceIds = [];

for (const trace of old) {
  const spans = (trace.spans || []).map((s, i) => migrateSpan(s, trace.timestamp, i));

  // ── Summary ──
  const hasErr = trace.error && Object.keys(trace.error).length > 0;
  const summary = {
    trace_id: trace.id,
    workflow_name: trace.workflow_name || "rag",
    status: trace.status || (hasErr ? "error" : "success"),
    start_time: trace.timestamp,
    duration_ms: trace.duration_ms,
    session_id: trace.session_id,
    user_id: String(trace.metadata?.user_id || trace.session?.user_id || "anonymous"),
    user_name: String(trace.metadata?.user_name || trace.session?.user_name || "User"),
    question: (trace.question || "").slice(0, 200),
    answer_preview: (trace.answer_preview || "").slice(0, 200),
    token_total: trace.usage?.total_tokens || 0,
    cost_usd: trace.cost_usd || 0,
    span_count: spans.length,
    model_name: trace.model?.name || "",
    kb_id: String(trace.metadata?.kb_id || ""),
    sla_threshold_ms: trace.sla?.threshold_ms || 10000,
    sla_breached: trace.sla?.breached || false,
    error_code: hasErr ? String((trace.error).code || "ERROR") : null,
    error_node: hasErr ? String((trace.error).error_node || null) : null,
    parent_id: trace.parent_id || null,
    children_ids: trace.children_ids || [],
    bookmarked: trace.bookmarked || false,
  };

  const wf = summary.workflow_name;
  if (!summariesByWorkflow[wf]) summariesByWorkflow[wf] = [];
  summariesByWorkflow[wf].push(summary);
  allTraceIds.push(trace.id);

  // ── Detail ──
  const detail = {
    trace_id: trace.id,
    request: {
      question: trace.question || "",
      kb_id: trace.metadata?.kb_id,
      temperature: trace.metadata?.temperature,
      max_tokens: trace.metadata?.max_tokens,
    },
    response: {
      answer: trace.answer_preview || "",
      answer_len: trace.answer_len || 0,
    },
    usage: {
      prompt_tokens: trace.usage?.prompt_tokens || 0,
      completion_tokens: trace.usage?.completion_tokens || 0,
      total_tokens: trace.usage?.total_tokens || 0,
      cost_usd: trace.cost_usd || 0,
      model_name: trace.model?.name || "",
      model_provider: trace.model?.provider || "",
    },
    error: hasErr ? {
      code: String(trace.error.code || "ERROR"),
      message: String(trace.error.message || ""),
      node: String(trace.error.error_node || null),
      retry_count: Number(trace.error.retry_count || 0),
    } : null,
    root_span_id: trace.root_span_id || `${trace.id}-root`,
    spans,
  };

  const detailPath = path.join(NEW, "details", `${trace.id}.json`);
  fs.writeFileSync(detailPath, JSON.stringify(detail, null, 2), "utf-8");
}

// 写 summary 文件
for (const [wf, list] of Object.entries(summariesByWorkflow)) {
  const summaryPath = path.join(NEW, "summaries", `${wf}.json`);
  fs.writeFileSync(summaryPath, JSON.stringify(list, null, 2), "utf-8");
}

console.log(`Summary 文件: ${Object.keys(summariesByWorkflow).length} 个 workflow`);
console.log(`Detail 文件: ${allTraceIds.length} 个 trace`);

// ── 统计 ──
const allFiles = fs.readdirSync(path.join(NEW, "details"));
const totalSize = allFiles.reduce((s, f) => s + fs.statSync(path.join(NEW, "details", f)).size, 0);

console.log(`\n✅ 迁移完成`);
console.log(`   Summary: ${Object.keys(summariesByWorkflow).length} 个文件`);
console.log(`   Detail:  ${allFiles.length} 个文件`);
console.log(`   Detail 总大小: ${(totalSize / 1024).toFixed(0)} KB`);
console.log(`   (index.ts + api.ts 已存在，跳过生成)`);

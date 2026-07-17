// fixtures/agent/details.ts — LangGraph Multi-Agent Trace
// 含 graph_node / graph_route / graph_loop / graph_fallback
import type { TraceDetail } from "../../schemas/trace";

const T = "2026-07-16T04:25:00Z";
function t(ms = 0) { const d = new Date(T); d.setMilliseconds(d.getMilliseconds() + ms); return d.toISOString().replace(/\.\d{3}Z$/, "Z"); }

export default {
  "ma-002": {
    trace_id: "ma-002",
    workflow_name: "agent",
    request: { question: "对比 Amazon/Shopify/Walmart 三个平台的入驻成本和佣金结构" },
    response: { answer: "【三平台对比报告】\n| 平台 | 月费 | 佣金 |\n|------|------|------|\n| Amazon | $39.99 | 6-15% |\n| Shopify | $29 | 2.9%+$0.30 |\n| Walmart | $0 | 6-15% |\n\n综合建议：月销<$5000 选 Shopify，>$10000 选 Amazon FBA。", answer_len: 560 },
    usage: { total_tokens: 2200, total_cost_usd: 0.00045, llm_calls: 5 },
    statistics: { total_spans: 14, llm_latency_ms: 10500, retrieval_latency_ms: 850, http_latency_ms: 0, db_latency_ms: 0 },
    error: null,
    graph: {
      nodes: [
        { id: "planner", label: "Planner 拆解" },
        { id: "supervisor", label: "Supervisor 调度" },
        { id: "rag_worker", label: "RAG Worker" },
        { id: "report_worker", label: "Report Worker" },
        { id: "reporter", label: "Reporter 汇总" },
      ],
      edges: [
        { source: "planner", target: "supervisor" },
        { source: "supervisor", target: "rag_worker", label: "dispatch" },
        { source: "supervisor", target: "report_worker", label: "dispatch" },
        { source: "rag_worker", target: "supervisor", label: "完成" },
        { source: "report_worker", target: "supervisor", label: "完成" },
        { source: "supervisor", target: "reporter", label: "all_done" },
      ],
      max_loops: 10,
      loop_count: 2,
      degradation_triggered: false,
    },
    root_span_id: "ma-002-root",
    spans: [
      // ── Root ──
      { trace_id: "ma-002", span_id: "ma-002-root", parent_id: null, name: "Multi-Agent Pipeline", type: "workflow", kind: "graph_node", status: "success", start_time: t(0), end_time: t(22500), duration_ms: 22500, sequence: 0, attributes: { "workflow.type": "multi_agent" }, metrics: { span_count: 14, sub_agents: 3 }, input: { question: "对比 Amazon/Shopify/Walmart 三个平台的入驻成本和佣金结构" }, output: {}, events: [], errors: [] },

      // ── Planner ──
      { trace_id: "ma-002", span_id: "planner", parent_id: "ma-002-root", name: "Planner 拆解", type: "agent", kind: "graph_node", status: "success", start_time: t(0), end_time: t(1500), duration_ms: 1500, sequence: 1, attributes: { "agent.name": "planner" }, metrics: { subtasks: 3, prompt_tokens: 300, completion_tokens: 150 }, input: { question: "对比 Amazon/Shopify/Walmart" }, output: { plan: { nodes: { step1: { capability: "rag", description: "Amazon 费用" }, step2: { capability: "rag", description: "Shopify 费用" }, step3: { capability: "report", description: "综合对比" } } } }, events: [], errors: [] },

      // ── Route: planner → supervisor ──
      { trace_id: "ma-002", span_id: "route-planner-supervisor", parent_id: "ma-002-root", name: "Planner → Supervisor", type: "workflow", kind: "graph_route", status: "success", start_time: t(1500), end_time: t(1502), duration_ms: 2, sequence: 2, attributes: { edge: "planner→supervisor", condition: "has_tasks", result: "continue" }, metrics: {}, input: {}, output: {}, events: [], errors: [] },

      // ── Supervisor Round 1 ──
      { trace_id: "ma-002", span_id: "supervisor-round-1", parent_id: "ma-002-root", name: "Supervisor Round 1", type: "workflow", kind: "graph_loop", status: "success", start_time: t(1502), end_time: t(10102), duration_ms: 8600, sequence: 3, attributes: { round: 1, dispatch_count: 2, dispatched_skills: ["rag_worker", "report_worker"], degraded_steps: [] }, metrics: {}, input: {}, output: {}, events: [], errors: [] },

      // Parallel: rag_worker
      { trace_id: "ma-002", span_id: "rag-worker", parent_id: "supervisor-round-1", name: "rag_worker", type: "agent", kind: "internal", status: "success", start_time: t(1502), end_time: t(10002), duration_ms: 8500, sequence: 4, attributes: { "agent.skill": "rag" }, metrics: { llm_calls: 2 }, input: {}, output: {}, events: [], errors: [] },
      { trace_id: "ma-002", span_id: "rag-retrieve", parent_id: "rag-worker", name: "Amazon 文档检索", type: "retrieval", kind: "internal", status: "success", start_time: t(1502), end_time: t(1852), duration_ms: 350, sequence: 5, attributes: { "retrieval.method": "hybrid", "retrieval.kb": "AMAZON_SOP" }, metrics: { vector_hits: 3, bm25_hits: 7, merged_hits: 5 }, input: {}, output: {}, events: [], errors: [] },
      { trace_id: "ma-002", span_id: "rag-llm", parent_id: "rag-worker", name: "Amazon 费用分析", type: "llm_call", kind: "internal", status: "success", start_time: t(1852), end_time: t(10002), duration_ms: 8150, sequence: 6, attributes: { "llm.model": "deepseek-v4-flash", "llm.temperature": "0.2" }, metrics: { prompt_tokens: 350, completion_tokens: 200, cost_usd: 0.00011 }, input: { prompt: "分析 Amazon 平台费用..." }, output: { response: "Amazon: 月费$39.99, 佣金6-15%..." }, events: [], errors: [] },

      // Parallel: report_worker
      { trace_id: "ma-002", span_id: "report-worker", parent_id: "supervisor-round-1", name: "report_worker", type: "agent", kind: "internal", status: "success", start_time: t(1502), end_time: t(7402), duration_ms: 5900, sequence: 7, attributes: { "agent.skill": "report" }, metrics: { llm_calls: 1 }, input: {}, output: {}, events: [], errors: [] },
      { trace_id: "ma-002", span_id: "report-sql", parent_id: "report-worker", name: "查询对比数据", type: "sql", kind: "internal", status: "success", start_time: t(1502), end_time: t(3702), duration_ms: 2200, sequence: 8, attributes: { "sql.engine": "postgresql" }, metrics: { rows_returned: 28, query_ms: 2150 }, input: {}, output: {}, events: [], errors: [] },
      { trace_id: "ma-002", span_id: "report-llm", parent_id: "report-worker", name: "生成对比报告", type: "llm_call", kind: "internal", status: "success", start_time: t(3702), end_time: t(7402), duration_ms: 3700, sequence: 9, attributes: { "llm.model": "deepseek-v4-flash", "llm.temperature": "0.1" }, metrics: { prompt_tokens: 500, completion_tokens: 330, cost_usd: 0.000166 }, input: { prompt: "综合三平台数据生成报告..." }, output: { response: "【三平台对比报告】..." }, events: [], errors: [] },

      // ── Route: supervisor → supervisor (还有剩余任务) ──
      { trace_id: "ma-002", span_id: "route-supervisor-supervisor", parent_id: "ma-002-root", name: "Supervisor → Supervisor", type: "workflow", kind: "graph_route", status: "success", start_time: t(10102), end_time: t(10103), duration_ms: 1, sequence: 10, attributes: { edge: "supervisor→supervisor", condition: "pending_skills", remaining: 0, round: 1 }, metrics: {}, input: {}, output: {}, events: [], errors: [] },

      // ── Supervisor Round 2 (所有 skill 完成，准备进入 reporter) ──
      { trace_id: "ma-002", span_id: "supervisor-round-2", parent_id: "ma-002-root", name: "Supervisor Round 2", type: "workflow", kind: "graph_loop", status: "success", start_time: t(10103), end_time: t(10133), duration_ms: 30, sequence: 11, attributes: { round: 2, dispatch_count: 0, all_done: true, success_count: 2, total_count: 2 }, metrics: {}, input: {}, output: {}, events: [], errors: [] },

      // ── Route: supervisor → reporter ──
      { trace_id: "ma-002", span_id: "route-supervisor-reporter", parent_id: "ma-002-root", name: "Supervisor → Reporter", type: "workflow", kind: "graph_route", status: "success", start_time: t(10133), end_time: t(10134), duration_ms: 1, sequence: 12, attributes: { edge: "supervisor→reporter", condition: "all_done", round: 2 }, metrics: {}, input: {}, output: {}, events: [], errors: [] },

      // ── Reporter ──
      { trace_id: "ma-002", span_id: "reporter", parent_id: "ma-002-root", name: "Reporter 汇总", type: "agent", kind: "graph_node", status: "success", start_time: t(10134), end_time: t(22500), duration_ms: 12366, sequence: 13, attributes: { "agent.name": "reporter" }, metrics: { merged_sources: 2, prompt_tokens: 400, completion_tokens: 200 }, input: {}, output: { answer: "综合建议..." }, events: [], errors: [] },
    ],
  },

  // ── 降级场景 trace ──
  "agent-degraded-001": {
    trace_id: "agent-degraded-001",
    workflow_name: "agent",
    request: { question: "生成 2026年7月 运营周报并发送邮件" },
    response: { answer: "【运营周报 - 部分完成】\nSQL 数据查询成功，但 report_worker 失败（LLM 超时）。已降级为 report_worker_lite，报告内容可能不够详细。", answer_len: 380 },
    usage: { total_tokens: 1200, total_cost_usd: 0.00025, llm_calls: 3 },
    statistics: { total_spans: 8, llm_latency_ms: 12000, retrieval_latency_ms: 0, http_latency_ms: 0, db_latency_ms: 2200 },
    error: null,
    graph: {
      nodes: [
        { id: "planner", label: "Planner 拆解" },
        { id: "supervisor", label: "Supervisor 调度" },
        { id: "sql_worker", label: "SQL Worker" },
        { id: "report_worker", label: "Report Worker" },
        { id: "report_worker_lite", label: "Report Worker Lite (降级)" },
        { id: "reporter", label: "Reporter 汇总" },
      ],
      edges: [
        { source: "planner", target: "supervisor" },
        { source: "supervisor", target: "sql_worker" },
        { source: "supervisor", target: "report_worker", label: "原始" },
        { source: "report_worker", target: "supervisor", label: "失败 → 降级" },
        { source: "supervisor", target: "report_worker_lite", label: "降级" },
        { source: "sql_worker", target: "supervisor", label: "完成" },
        { source: "report_worker_lite", target: "supervisor", label: "完成" },
        { source: "supervisor", target: "reporter", label: "all_done" },
      ],
      max_loops: 10,
      loop_count: 3,
      degradation_triggered: true,
    },
    root_span_id: "agent-degraded-001-root",
    spans: [
      { trace_id: "agent-degraded-001", span_id: "agent-degraded-001-root", parent_id: null, name: "Report Agent Pipeline", type: "workflow", kind: "graph_node", status: "success", start_time: t(0), end_time: t(35000), duration_ms: 35000, sequence: 0, attributes: { "workflow.type": "report_agent" }, metrics: { span_count: 8 }, input: { question: "生成 2026年7月 运营周报并发送邮件" }, output: {}, events: [], errors: [] },
      { trace_id: "agent-degraded-001", span_id: "d-planner", parent_id: "agent-degraded-001-root", name: "Planner 拆解", type: "agent", kind: "graph_node", status: "success", start_time: t(0), end_time: t(1200), duration_ms: 1200, sequence: 1, attributes: {}, metrics: { subtasks: 2 }, input: {}, output: {}, events: [], errors: [] },
      { trace_id: "agent-degraded-001", span_id: "d-supervisor-r1", parent_id: "agent-degraded-001-root", name: "Supervisor Round 1", type: "workflow", kind: "graph_loop", status: "success", start_time: t(1200), end_time: t(10200), duration_ms: 9000, sequence: 2, attributes: { round: 1, dispatched_skills: ["sql_worker", "report_worker"] }, metrics: {}, input: {}, output: {}, events: [], errors: [] },
      { trace_id: "agent-degraded-001", span_id: "d-sql-worker", parent_id: "d-supervisor-r1", name: "sql_worker", type: "agent", kind: "internal", status: "success", start_time: t(1200), end_time: t(3400), duration_ms: 2200, sequence: 3, attributes: {}, metrics: { rows_returned: 28 }, input: {}, output: {}, events: [], errors: [] },
      { trace_id: "agent-degraded-001", span_id: "d-report-worker", parent_id: "d-supervisor-r1", name: "report_worker (失败)", type: "agent", kind: "internal", status: "error", start_time: t(1200), end_time: t(10200), duration_ms: 9000, sequence: 4, attributes: {}, metrics: { retry_count: 0 }, input: {}, output: {}, events: [], errors: ["LLM 调用超时 (30000ms)，report_worker 执行失败"] },
      { trace_id: "agent-degraded-001", span_id: "d-supervisor-r2", parent_id: "agent-degraded-001-root", name: "Supervisor Round 2 (降级)", type: "workflow", kind: "graph_fallback", status: "success", start_time: t(10200), end_time: t(20200), duration_ms: 10000, sequence: 5, attributes: { round: 2, degraded_steps: ["report_worker"], degradation_reason: "report_worker 失败，降级为 report_worker_lite", alert: "SUPERVISOR_DEGRADATION" }, metrics: {}, input: {}, output: {}, events: [], errors: [] },
      { trace_id: "agent-degraded-001", span_id: "d-report-worker-lite", parent_id: "d-supervisor-r2", name: "report_worker_lite (降级)", type: "agent", kind: "graph_fallback", status: "success", start_time: t(10200), end_time: t(20200), duration_ms: 10000, sequence: 6, attributes: { "degradation.type": "lite", "degradation.reason": "跳过详细分析，仅生成摘要" }, metrics: { prompt_tokens: 400, completion_tokens: 150 }, input: {}, output: { report: "（摘要版）" }, events: [], errors: [], warnings: ["⚠ 降级模式，报告内容可能不够详细"] },
      { trace_id: "agent-degraded-001", span_id: "d-reporter", parent_id: "agent-degraded-001-root", name: "Reporter 汇总", type: "agent", kind: "graph_node", status: "success", start_time: t(20200), end_time: t(35000), duration_ms: 14800, sequence: 7, attributes: { "degradation_occurred": true }, metrics: { merged_sources: 2, partial: true }, input: {}, output: { answer: "【运营周报 - 部分完成】..." }, events: [], errors: [], warnings: ["降级后报告可能不完整"] },
    ],
  },
} as Record<string, TraceDetail>;

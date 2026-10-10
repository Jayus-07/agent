import { describe, expect, it } from "vitest";
import type { Span, TraceRecord } from "@/types/trace";
import { mergeChildTraceSpans } from "./traceSpanTree";

const span = (overrides: Partial<Span>): Span => ({
  id: "root",
  type: "workflow",
  name: "根",
  parent_id: null,
  status: "success",
  start_time: "2026-10-09T00:00:00.000Z",
  end_time: "2026-10-09T00:00:00.100Z",
  duration_ms: 100,
  duration_ratio: 1,
  attributes: {},
  metrics: {},
  children: [],
  events: [],
  warnings: [],
  errors: [],
  ...overrides,
});

const trace = (overrides: Partial<TraceRecord>): TraceRecord => ({
  id: "rag-child",
  request_id: "synthetic-request",
  timestamp: "2026-10-09T00:00:00.000Z",
  session_id: "synthetic-session",
  question: "退款怎么处理？",
  answer_preview: "",
  answer_len: 0,
  duration_ms: 80,
  model: { name: "", provider: "" },
  usage: {},
  cost: {},
  cost_usd: 0,
  error: {},
  metadata: {},
  status: "success",
  workflow_name: "rag_agent",
  root_span_id: "root",
  spans: [
    span({ id: "root", name: "RAG 问答", duration_ms: 80 }),
    span({ id: "hybrid_retrieval", parent_id: "root", name: "混合检索", type: "retrieval", duration_ms: 42 }),
  ],
  sla: { threshold_ms: 30000, breached: false },
  parent_id: "agent-parent",
  children_ids: [],
  tags: {},
  source: "ai_assistant",
  summary: { llm_calls: 0, tool_calls: 0, retrieval_calls: 1, span_count: 2 },
  ...overrides,
});

describe("mergeChildTraceSpans", () => {
  it("把远端 RAG 子 Trace 的 Span 挂到对应工具调用下，并保持父子层级", () => {
    const parentSpans = [
      span({ id: "root", name: "Agent" }),
      span({
        id: "rag-tool",
        parent_id: "root",
        name: "rag:rag.search",
        type: "tool_call",
        metrics: { child_trace_ids: ["rag-child"] },
      }),
    ];

    const merged = mergeChildTraceSpans(parentSpans, [trace({})]);
    const childRoot = merged.find((item) => item.id === "rag-child--root");
    const retrieval = merged.find((item) => item.id === "rag-child--hybrid_retrieval");

    expect(childRoot?.parent_id).toBe("rag-tool");
    expect(retrieval?.parent_id).toBe("rag-child--root");
    expect(retrieval?.metrics?.origin_trace_id).toBe("rag-child");
  });

  it("父 Trace 没收到子 Trace ID 时，仍把已落库的 RAG 子 Trace 显示在时间线", () => {
    const parentSpans = [
      span({ id: "root", name: "Agent" }),
      span({ id: "rag-tool", parent_id: "root", name: "rag:rag.search", type: "tool_call" }),
    ];

    const merged = mergeChildTraceSpans(parentSpans, [trace({})]);

    expect(merged.some((item) => item.id === "rag-child--hybrid_retrieval")).toBe(true);
  });
});

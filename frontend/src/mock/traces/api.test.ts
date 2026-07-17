// mock/traces/api.test.ts — mock 层 mergeTrace 回归测试
//
// 重点拦截：
// 1. session_id 写死 ""（P0 #5）
// 2. SLA 阈值写死 10000 忽略 fixture（P0 #6）
// 3. error_node 永远 undefined → 错误面板跳转失效（P0 #4）
// 4. type=llm_call span 没有 llm_call{} 嵌套 → 显示层"无 LLM 调用"（P0 #3）

import { describe, expect, it } from "vitest";
import { mergeTrace } from "./api";
import ragSummaries from "./fixtures/summaries/rag_agent.json";
import f3Detail from "./fixtures/details/f3e2d1c0b9a8.json";
import dbDetail from "./fixtures/details/db3748af5cb4.json";

describe("mergeTrace", () => {
  // 找一条有 session_id 的 summary
  const summaryWithSession = ragSummaries.find((s) => !!s.session_id);
  const summaryWithSla = ragSummaries.find(
    (s) => typeof s.sla_threshold_ms === "number" && typeof s.sla_breached === "boolean"
  );

  it("propagates session_id from summary (catches P0 #5)", () => {
    expect(summaryWithSession).toBeDefined();
    const fakeDetail = { ...f3Detail, trace_id: summaryWithSession!.trace_id, request: { question: "x" }, response: { answer: "", answer_len: 0 }, usage: { total_tokens: 0, total_cost_usd: 0, llm_calls: 0 }, error: null, root_span_id: "root", spans: [] };
    const merged = mergeTrace(summaryWithSession!, fakeDetail as any);
    expect(merged.session_id).toBe(summaryWithSession!.session_id);
    expect(merged.session_id).not.toBe(""); // 不是硬编码 ""
  });

  it("uses fixture sla_threshold_ms and sla_breached (catches P0 #6)", () => {
    expect(summaryWithSla).toBeDefined();
    const fakeDetail = { ...dbDetail, trace_id: summaryWithSla!.trace_id, request: { question: "x" }, response: { answer: "", answer_len: 0 }, usage: { total_tokens: 0, total_cost_usd: 0, llm_calls: 0 }, error: null, root_span_id: "root", spans: [] };
    const merged = mergeTrace(summaryWithSla!, fakeDetail as any);
    expect(merged.sla).toBeDefined();
    expect(merged.sla!.threshold_ms).toBe(summaryWithSla!.sla_threshold_ms);
    expect(merged.sla!.breached).toBe(summaryWithSla!.sla_breached);
    // 不应是兜底 10000
    expect(merged.sla!.threshold_ms).not.toBe(10000);
  });

  it("derives llm_call for type=llm_call spans and routes error_node from summary (catches P0 #3, #4)", () => {
    // 用 f3e2d1c0b9a8（error.node='rerank'）+ summary error_node='rerank'
    const summary = { ...summaryWithSession!, trace_id: f3Detail.trace_id, sla_threshold_ms: 10000, sla_breached: false } as any;
    summary.error_node = "rerank";

    const merged = mergeTrace(summary, f3Detail as any);
    // P0 #4: trace.error.error_node 应可读
    expect(merged.error).toBeTruthy();
    expect((merged.error as any).error_node).toBe("rerank");
    // P0 #3: type=llm_call span 应有 llm_call 派生
    const llmSpans = (merged.spans || []).filter((s) => s.type === "llm_call");
    expect(llmSpans.length).toBeGreaterThan(0);
    for (const sp of llmSpans) {
      expect(sp.llm_call).toBeDefined();
      // model 字段可能为空（fixture 不完整），但派生块的字段名/类型必须正确
      expect(typeof sp.llm_call!.model).toBe("string");
      expect(typeof sp.llm_call!.prompt_tokens).toBe("number");
      expect(typeof sp.llm_call!.completion_tokens).toBe("number");
      expect(typeof sp.llm_call!.cost_usd).toBe("number");
      expect(typeof sp.llm_call!.temperature).toBe("number");
      expect(typeof sp.llm_call!.prompt_text).toBe("string");
      expect(typeof sp.llm_call!.response_text).toBe("string");
    }
    // 至少有一个 llm span 携带真实 model（f3e2d1c0b9a8.llm_generate 的 "deepseek-v4-flash"）
    const hasModel = llmSpans.some((s) => s.llm_call!.model === "deepseek-v4-flash");
    expect(hasModel).toBe(true);
  });
});

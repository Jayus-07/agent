import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import type { Span } from "@/types/trace";
import StepTimeline from "./StepTimeline";

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
});

const mounted: { container: HTMLElement; root: Root }[] = [];

function makeSpan(overrides: Partial<Span> = {}): Span {
  return {
    id: "tool-lookup",
    type: "tool_call",
    name: "inventory.lookup",
    parent_id: null,
    status: "success",
    start_time: "2026-10-09T00:00:00.000Z",
    end_time: "2026-10-09T00:00:00.042Z",
    duration_ms: 42,
    duration_ratio: 1,
    attributes: { capability: "inventory.lookup" },
    metrics: {},
    input: { sku: "SKU-001", tool: "catalog_search" },
    output: { available: true, quantity: 3 },
    children: [],
    events: [],
    warnings: [],
    errors: [],
    ...overrides,
  };
}

function mountTimeline(span: Span | Span[]): HTMLElement {
  const steps = Array.isArray(span) ? span : [span];
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  function Harness() {
    const [expanded, setExpanded] = useState(new Set<string>());
    return (
      <StepTimeline
        steps={steps}
        totalMs={steps[0].duration_ms}
        expanded={expanded}
        onToggle={(id) => setExpanded((current) => {
          const next = new Set(current);
          next.has(id) ? next.delete(id) : next.add(id);
          return next;
        })}
      />
    );
  }
  act(() => root.render(<Harness />));
  mounted.push({ container, root });
  return container;
}

function expand(container: HTMLElement, spanId: string) {
  const row = container.querySelector(`#step-${spanId} > div`);
  if (!row) throw new Error(`找不到 Span 行 ${spanId}`);
  act(() => row.dispatchEvent(new MouseEvent("click", { bubbles: true })));
}

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
});

describe("StepTimeline Span 明细", () => {
  it("任意 Tool 都按通用结构展示名称、输入参数与结果", () => {
    const container = mountTimeline(makeSpan());
    expand(container, "tool-lookup");

    expect(container.textContent).toContain("inventory.lookup");
    expect(container.textContent).toContain("调用能力");
    expect(container.textContent).toContain("实际 Tool");
    expect(container.textContent).toContain("catalog_search");
    expect(container.textContent).toContain("输入参数");
    expect(container.textContent).toContain("SKU-001");
    expect(container.textContent).toContain("输出结果");
    expect(container.textContent).toContain("quantity");
  });

  it("多查询 Span 展示每条已记录的改写查询", () => {
    const container = mountTimeline(makeSpan({
      id: "query-rewrite",
      type: "llm_call",
      name: "LLM 改写",
      attributes: {},
      input: { question: "退款怎么处理？" },
      output: { variants: ["退款申请流程", "退款多久到账"] },
      metrics: { variants: 2 },
    }));
    expand(container, "query-rewrite");

    expect(container.textContent).toContain("多查询改写");
    expect(container.textContent).toContain("退款申请流程");
    expect(container.textContent).toContain("退款多久到账");
  });

  it("多查询检索 Span 按查询显示命中数及失败/超时状态", () => {
    const container = mountTimeline(makeSpan({
      id: "multi-query-fanout",
      type: "retrieval",
      name: "多查询并发检索",
      status: "partial",
      attributes: {},
      input: { queries: ["退款申请流程", "退款多久到账"] },
      output: {
        query_results: [
          { query: "退款申请流程", status: "success", retrieved_docs: 3, retained_docs: 2 },
          { query: "退款多久到账", status: "timeout", retrieved_docs: 0, retained_docs: 0 },
        ],
      },
      metrics: { query_count: 2, timeout_count: 1, unique_docs: 2 },
    }));
    expand(container, "multi-query-fanout");

    expect(container.textContent).toContain("部分成功");
    expect(container.textContent).toContain("各查询检索结果");
    expect(container.textContent).toContain("退款申请流程");
    expect(container.textContent).toContain("召回 3");
    expect(container.textContent).toContain("退款多久到账");
    expect(container.textContent).toContain("超时");
  });

  it("检索结果事件显示证据来源查询与片段摘要", () => {
    const container = mountTimeline(makeSpan({
      id: "rag-retrieval",
      type: "retrieval",
      name: "检索",
      attributes: {},
      input: { question: "退款怎么处理？" },
      output: { total_docs: 1 },
      events: [{
        name: "final_context",
        level: "info",
        message: "1 chunks → LLM",
        attributes: {
          chunks: [{
            chunk_id: "refund-policy-1",
            source: "退款制度",
            source_query: "退款申请流程",
            snippet: "退款需提交申请",
          }],
        },
      }],
    }));
    expand(container, "rag-retrieval");

    expect(container.textContent).toContain("执行事件");
    expect(container.textContent).toContain("退款申请流程");
    expect(container.textContent).toContain("退款需提交申请");
  });

  it("错误 Span 区分超时并显示已记录的原因", () => {
    const container = mountTimeline(makeSpan({
      id: "rag-fanout",
      name: "多查询并发检索",
      status: "error",
      attributes: { error_type: "timeout" },
      errors: ["检索 fan-out 超时，2 条查询未完成"],
    }));
    expect(container.querySelector("#step-rag-fanout")?.textContent).toContain("超时");
    expect(container.querySelector("#step-rag-fanout .h-full")?.className).toContain("bg-amber-500");
    expand(container, "rag-fanout");

    expect(container.textContent).toContain("超时");
    expect(container.textContent).toContain("检索 fan-out 超时，2 条查询未完成");
  });

  it("普通异常 Span 展示异常状态与错误详情", () => {
    const container = mountTimeline(makeSpan({
      id: "sql-query",
      name: "数据库查询",
      type: "sql",
      status: "error",
      metrics: { error: "PermissionDenied: 当前账号无权访问" },
    }));
    expand(container, "sql-query");

    expect(container.textContent).toContain("异常");
    expect(container.textContent).toContain("PermissionDenied: 当前账号无权访问");
  });


  it("根步骤默认展开，其嵌套子步骤默认收起", () => {
    // 根（depth 0）保持展开，否则整棵树会被压成 1 行，看不到顶层流程
    const parent = makeSpan({ id: "router", name: "路由阶段", duration_ms: 744 });
    const mid = makeSpan({
      id: "routing.engine", name: "主路由引擎", parent_id: "router", duration_ms: 687,
    });
    const deep = makeSpan({
      id: "routing.route_decision", name: "路由决策", parent_id: "routing.engine", duration_ms: 1,
    });
    const container = mountTimeline([parent, mid, deep]);

    // depth 0/1 可见，depth 2 收起
    expect(container.querySelector("#step-router")).not.toBeNull();
    expect(container.querySelector("#step-routing\\.engine")).not.toBeNull();
    expect(container.querySelector("#step-routing\\.route_decision")).toBeNull();
    expect(container.textContent).toContain("已收起 1 个嵌套子步骤");

    // 点击 depth1 行的折叠三角 → 展开其子步骤
    const chevron = container.querySelector('#step-routing\\.engine button[title="展开子步骤"]');
    expect(chevron).not.toBeNull();
    act(() => chevron!.dispatchEvent(new MouseEvent("click", { bubbles: true })));

    expect(container.querySelector("#step-routing\\.route_decision")).not.toBeNull();
  });

  it("展开/收起全部按钮控制所有嵌套步骤", () => {
    const parent = makeSpan({ id: "router", name: "路由阶段" });
    const child = makeSpan({ id: "router.child", name: "子步骤", parent_id: "router" });
    const grand = makeSpan({ id: "router.grand", name: "孙步骤", parent_id: "router.child" });
    const container = mountTimeline([parent, child, grand]);

    // 默认：根与 depth1 可见，depth2 收起
    expect(container.querySelector("#step-router\\.child")).not.toBeNull();
    expect(container.querySelector("#step-router\\.grand")).toBeNull();

    const expandAll = [...container.querySelectorAll("button")]
      .find((b) => b.textContent === "展开");
    expect(expandAll).toBeTruthy();
    act(() => expandAll!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(container.querySelector("#step-router\\.child")).not.toBeNull();
    expect(container.querySelector("#step-router\\.grand")).not.toBeNull();

    const collapseAll = [...container.querySelectorAll("button")]
      .find((b) => b.textContent === "收起");
    act(() => collapseAll!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(container.querySelector("#step-router\\.child")).toBeNull();
    expect(container.querySelector("#step-router\\.grand")).toBeNull();
  });

  it("大耗时步骤默认折叠，点击后展开明细", () => {
    const container = mountTimeline(makeSpan({ duration_ms: 1_250 }));

    expect(container.textContent).not.toContain("输入参数");
    expect(container.textContent).not.toContain("输出结果");

    expand(container, "tool-lookup");

    expect(container.textContent).toContain("输入参数");
    expect(container.textContent).toContain("输出结果");
  });
});

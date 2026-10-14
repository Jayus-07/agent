import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import type { Span } from "@/types/trace";
import LLMCallDetail from "./LLMCallDetail";

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
});

const mounted: { container: HTMLElement; root: Root }[] = [];

function makeSpan(overrides: Partial<Span> = {}): Span {
  return {
    id: "llm-1",
    type: "llm_call",
    name: "LLM 生成",
    parent_id: null,
    status: "success",
    start_time: "2026-10-09T00:00:00.000Z",
    end_time: "2026-10-09T00:00:00.042Z",
    duration_ms: 42,
    duration_ratio: 1,
    attributes: {},
    metrics: {},
    children: [],
    events: [],
    warnings: [],
    errors: [],
    llm_call: {
      model: "test-model",
      temperature: 0,
      prompt_text: "private prompt",
      response_text: "private response",
      prompt_tokens: 11,
      completion_tokens: 7,
      cost_usd: 0,
      cost_cny: 0.01,
    },
    ...overrides,
  };
}

function mountDetail(span: Span): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(<LLMCallDetail steps={[span]} />));
  mounted.push({ container, root });
  return container;
}

function expand(container: HTMLElement) {
  const button = container.querySelector("button");
  if (!button) throw new Error("找不到 LLM 调用明细按钮");
  act(() => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
}

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
});

describe("LLMCallDetail Token 采集语义", () => {
  it("来源不可用时隐藏 DTO 填充的零并显示未采集", () => {
    const container = mountDetail(makeSpan({
      metrics: {
        token_source: "unavailable",
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
      },
      llm_call: {
        model: "test-model",
        temperature: 0,
        prompt_text: "private prompt",
        response_text: "private response",
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0,
      },
    }));

    expect(container.textContent).toContain("Token 未采集");
    expect(container.textContent).not.toContain("Token 0");
    expand(container);
    expect(container.textContent).toContain("Prompt Tokens未采集");
    expect(container.textContent).toContain("Completion Tokens未采集");
    expect(container.textContent).not.toContain("Prompt Tokens0");
    expect(container.textContent).not.toContain("Completion Tokens0");
  });

  it("已采集的真实零仍显示为零", () => {
    const container = mountDetail(makeSpan({
      metrics: { token_source: "response_metadata", prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
      llm_call: {
        model: "test-model",
        temperature: 0,
        prompt_text: "private prompt",
        response_text: "private response",
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0,
      },
    }));

    expect(container.textContent).toContain("Token 0");
    expand(container);
    expect(container.textContent).toContain("Prompt Tokens0");
    expect(container.textContent).toContain("Completion Tokens0");
  });

  it("展示正常采集的 Token 用量", () => {
    const container = mountDetail(makeSpan({
      metrics: { token_source: "response_metadata", prompt_tokens: 11, completion_tokens: 7, total_tokens: 18 },
    }));

    expect(container.textContent).toContain("Token 18");
    expand(container);
    expect(container.textContent).toContain("Prompt Tokens11");
    expect(container.textContent).toContain("Completion Tokens7");
  });

  it("缺失的 Token、模型和费用保持未采集且不暴露文本", () => {
    const span = makeSpan({
      attributes: {},
      metrics: {},
      llm_call: {
        model: "",
        temperature: 0,
        prompt_text: "private prompt",
        response_text: "private response",
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0,
        cost_cny: undefined,
      } as Span["llm_call"],
    });
    const container = mountDetail(span);

    expect(container.textContent).toContain("模型未采集");
    expect(container.textContent).toContain("Token 未采集");
    expect(container.textContent).toContain("费用 未采集");
    expand(container);
    expect(container.textContent).toContain("Prompt Tokens未采集");
    expect(container.textContent).toContain("Completion Tokens未采集");
    expect(container.textContent).toContain("费用（CNY）未采集");
    expect(container.textContent).not.toContain("private prompt");
    expect(container.textContent).not.toContain("private response");
  });

  it("只展示有采集证据的字段，不用占位零补齐缺失 Token", () => {
    const container = mountDetail(makeSpan({
      metrics: { prompt_tokens: 0 },
      llm_call: {
        model: "test-model",
        temperature: 0,
        prompt_text: "private prompt",
        response_text: "private response",
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0,
      },
    }));

    expect(container.textContent).toContain("Token 未采集");
    expand(container);
    expect(container.textContent).toContain("Prompt Tokens0");
    expect(container.textContent).toContain("Completion Tokens未采集");
  });
});

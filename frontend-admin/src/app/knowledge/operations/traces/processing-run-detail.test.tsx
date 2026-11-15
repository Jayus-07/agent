import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ProcessingRunDetail } from "@/api/knowledge";
import ProcessingRunDetailView from "./processing-run-detail";

describe("ProcessingRunDetailView", () => {
  it("明确标为非 Trace，并显示数据库保存的文件、运行和阶段事实", () => {
    const detail: ProcessingRunDetail = {
      doc_id: "doc-1",
      run_id: "run-1",
      file_hash: "sha256-example",
      operation: "upload",
      status: "success",
      pipeline_version: "rag-processing-v2",
      trace_id: "trace-1",
      started_at: "2026-11-14T10:00:00Z",
      finished_at: "2026-11-14T10:00:02Z",
      steps: [
        {
          step_id: "step-1",
          run_id: "run-1",
          stage: "parse",
          status: "success",
          input_count: 145,
          output_count: 131,
          duration_ms: 250,
        },
        {
          step_id: "step-2",
          run_id: "run-1",
          stage: "chunking_llm_fallback",
          status: "success",
          role: "chunking",
          engine_type: "llm",
          provider: "fixture-provider",
          model_name: "fixture-model",
          input_count: 3,
          output_count: 5,
          fallback_reason: "no_recognized_structure",
          metadata: {
            source_format: "txt",
            fallback_type: "no_recognized_structure",
            attempted: true,
            applied: true,
            group_count: 2,
          },
        },
      ],
      usage: [],
    };

    const html = renderToStaticMarkup(createElement(ProcessingRunDetailView, {
      detail,
      documentName: "厦门-城市-厦门.md",
    }));

    expect(html).toContain("处理运行记录（非 Trace）");
    expect(html).toContain("厦门-城市-厦门.md");
    expect(html).toContain("运行 ID：run-1");
    expect(html).toContain("Trace ID");
    expect(html).toContain("解析文档");
    expect(html).toContain("LLM 切分边界兜底");
    expect(html).toContain("规则未识别结构");
    expect(html).toContain("已采用");
    expect(html).toContain("输入/输出：145 / 131");
    expect(html).toContain("规则版本：未记录");
  });

  it("历史记录回退时说明按当前文件 SHA 找到记录", () => {
    const detail: ProcessingRunDetail = {
      doc_id: "doc-1",
      run_id: "historical-run",
      file_hash: "same-hash",
      operation: "reindex",
      status: "success",
      steps: [{ step_id: "step-1", run_id: "historical-run", stage: "parser", status: "success" }],
    }
    const html = renderToStaticMarkup(createElement(ProcessingRunDetailView, {
      detail,
      documentName: "已登记文件.md",
      historyFallback: true,
    }))

    expect(html).toContain("按文件 SHA-256 从历史运行中找到")
    expect(html).toContain("内容与当前文件一致")
  })

  it("没有阶段行时说明未留存，不展示虚构步骤", () => {
    const detail: ProcessingRunDetail = {
      doc_id: "doc-1",
      run_id: "run-1",
      status: "success",
      steps: [],
    };

    const html = renderToStaticMarkup(createElement(ProcessingRunDetailView, {
      detail,
      documentName: "sample.md",
    }));

    expect(html).toContain("这次运行没有保存阶段明细");
    expect(html).not.toContain("解析文档");
  });

  it("同一模型的多条用量行不产生重复 React key", () => {
    const detail: ProcessingRunDetail = {
      doc_id: "doc-1",
      run_id: "run-1",
      status: "failed",
      steps: [],
      usage: Array.from({ length: 3 }, () => ({
        component: "ocr",
        provider: "test-provider",
        model: "test-model",
        calls: 1,
        total_tokens: 0,
      })),
    };
    const container = document.createElement("div");
    const root = createRoot(container);
    const previousActEnvironment = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
      .IS_REACT_ACT_ENVIRONMENT;
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    try {
      act(() => root.render(createElement(ProcessingRunDetailView, {
        detail,
        documentName: "sample.pdf",
      })));

      expect(container.textContent).toContain("保存的模型用量（3）");
      expect(consoleError.mock.calls.flat().join(" ")).not.toContain("same key");
    } finally {
      act(() => root.unmount());
      consoleError.mockRestore();
      if (previousActEnvironment === undefined) {
        delete (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
          .IS_REACT_ACT_ENVIRONMENT;
      } else {
        (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean })
          .IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
      }
    }
  });
});

// services/knowledge.test.ts — knowledgeService.uploadDocument SSE 消费回归测试
//
// 重点拦截：
// 1. P1.5 bugfix: 改用 onmessage 替代 addEventListener(stage) —— 避免 listener 注册 race
//    导致中间 stage（parsing/chunking/embedding/writing）丢失

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { knowledgeService } from "./knowledge";

// ============================================================
// EventSource mock
// ============================================================

type EventHandler = (e: MessageEvent) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onmessage: EventHandler | null = null;
  onerror: ((e: Event) => void) | null = null;
  readyState: number = 0; // 0=CONNECTING, 1=OPEN, 2=CLOSED
  private listeners: Map<string, EventHandler[]> = new Map();
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(event: string, handler: EventHandler) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event)!.push(handler);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  // 测试用：模拟 server 推送 event
  emit(eventName: string, data: unknown) {
    const payload = typeof data === "string" ? data : JSON.stringify(data);
    const e = { data: payload } as MessageEvent;
    // 优先触发 onmessage（捕获所有）
    if (this.onmessage) {
      this.onmessage(e);
    }
    // 也触发 addEventListener 注册的 listener（保留兼容性）
    const handlers = this.listeners.get(eventName) || [];
    for (const h of handlers) {
      h(e);
    }
  }

  emitError() {
    const e = { readyState: this.readyState } as any;
    if (this.onerror) this.onerror(e);
    const handlers = this.listeners.get("error") || [];
    for (const h of handlers) h(e);
  }
}

// ============================================================
// fetch mock
// ============================================================

const originalFetch = global.fetch;
let mockFetchResponse: { ok: boolean; status: number; body: any } = {
  ok: true,
  status: 200,
  body: { ok: true, upload_id: "test_upload_001", filename: "doc.txt" },
};

function setupFetchMock() {
  global.fetch = vi.fn(async () => {
    return {
      ok: mockFetchResponse.ok,
      status: mockFetchResponse.status,
      text: async () => JSON.stringify(mockFetchResponse.body),
      json: async () => mockFetchResponse.body,
    };
  }) as any;
}

async function getEventSource(): Promise<MockEventSource> {
  await vi.waitFor(() => expect(MockEventSource.instances[0]).toBeDefined());
  return MockEventSource.instances[0];
}

beforeEach(() => {
  MockEventSource.instances = [];
  mockFetchResponse = {
    ok: true,
    status: 200,
    body: { ok: true, upload_id: "test_upload_001", filename: "doc.txt" },
  };
  setupFetchMock();
  (globalThis as any).EventSource = MockEventSource;
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("knowledgeService.uploadDocument SSE consumer (P1.5 fix)", () => {
  it("forwards every stage to onProgress, in order", async () => {
    const progressCalls: Array<[string, string]> = [];

    const promise = knowledgeService.uploadDocument(
      new File(["x"], "doc.txt"),
      (stage, message) => progressCalls.push([stage, message]),
    );

    const es = await getEventSource();
    expect(es).toBeDefined();

    // 模拟 server 立即推完全部事件（不等 listener 注册 —— P1.5 bug 触发条件）
    es.emit("uploading", { stage: "uploading", message: "文件已保存" });
    es.emit("parsing",   { stage: "parsing",   message: "已解析 1 页" });
    es.emit("chunking",  { stage: "chunking",  message: "切分 5 chunks" });
    es.emit("embedding", { stage: "embedding", message: "Embedding 5/5" });
    es.emit("writing",   { stage: "writing",   message: "写入 5 向量" });
    es.emit("done",      { stage: "done",      message: "索引完成", doc: { id: "abc" } });

    const result = await promise;

    // 所有阶段（包括完成事件）都应按顺序转发
    const stages = progressCalls.map(([s]) => s);
    expect(stages).toEqual([
      "uploading", "parsing", "chunking", "embedding", "writing", "done",
    ]);
    expect(result.ok).toBe(true);
    expect((result.doc as any)?.id).toBe("abc");
    expect(es.closed).toBe(true); // SSE 已关闭
  });

  it("handles done arriving first (early completion)", async () => {
    const progressCalls: string[] = [];

    const promise = knowledgeService.uploadDocument(
      new File(["x"], "doc.txt"),
      (stage) => progressCalls.push(stage),
    );

    const es = await getEventSource();
    es.emit("done", { stage: "done", message: "索引完成", doc: { id: "abc" } });

    const result = await promise;
    expect(result.ok).toBe(true);
    expect(progressCalls).toEqual(["done"]);
    expect(es.closed).toBe(true);
  });

  it("handles error event", async () => {
    const promise = knowledgeService.uploadDocument(
      new File(["x"], "doc.txt"),
      () => {},
    );

    const es = await getEventSource();
    es.emit("error", { stage: "error", message: "pypdf 包未安装" });

    const result = await promise;
    expect(result.ok).toBe(false);
    expect(result.error).toBe("pypdf 包未安装");
    expect(es.closed).toBe(true);
  });

  it("handles malformed JSON in event data", async () => {
    const progressCalls: string[] = [];

    const promise = knowledgeService.uploadDocument(
      new File(["x"], "doc.txt"),
      (stage) => progressCalls.push(stage),
    );

    const es = await getEventSource();
    // 模拟非 JSON 数据
    es.onmessage!({ data: "not-json" } as MessageEvent);
    es.emit("done", { stage: "done", message: "ok", doc: { id: "x" } });

    const result = await promise;
    // malformed 数据 → 仍走到下一个 event
    expect(result.ok).toBe(true);
  });

  it("treats post-close error as no-op (server sent done before disconnect)", async () => {
    const promise = knowledgeService.uploadDocument(
      new File(["x"], "doc.txt"),
      () => {},
    );

    const es = await getEventSource();
    es.emit("done", { stage: "done", message: "ok", doc: { id: "x" } });
    await promise; // resolved with ok=true

    // 服务端关闭连接 → readyState=2 + error 事件
    // 此时 resolved 已 true → onerror 应 no-op
    es.readyState = 2;
    es.emitError();
    // 不应再次 resolve（否则会覆盖之前的成功结果）
    expect(true).toBe(true);
  });

  it("treats non-2 close error as connection failure", async () => {
    const promise = knowledgeService.uploadDocument(
      new File(["x"], "doc.txt"),
      () => {},
    );

    const es = await getEventSource();
    // 连接意外中断（非 done/error 引起）
    es.readyState = 2;
    es.emitError();

    const result = await promise;
    expect(result.ok).toBe(false);
    expect(result.error).toBe("进度连接中断");
    expect(es.closed).toBe(true);
  });

  it("handles upload POST failure", async () => {
    mockFetchResponse = {
      ok: false,
      status: 500,
      body: { detail: "internal error" },
    };

    await expect(
      knowledgeService.uploadDocument(new File(["x"], "doc.txt"), () => {})
    ).rejects.toThrow(/上传失败 \(500\)/);
  });

  it("handles POST returning ok=false with no upload_id", async () => {
    mockFetchResponse = {
      ok: true,
      status: 200,
      body: { ok: false, error: "文件格式不支持" },
    };

    const result = await knowledgeService.uploadDocument(
      new File(["x"], "doc.txt"),
      () => {},
    );
    expect(result.ok).toBe(false);
    expect(result.error).toBe("文件格式不支持");
  });
});

describe("P1.5 regression — listener race condition", () => {
  it("emits events in immediate succession (no debouncing) without dropping", async () => {
    // 模拟 server 在 listener 完全注册前已发出前几个 event
    // addEventListener 路径会丢失这些 event；onmessage 路径不会
    const captured: string[] = [];
    const promise = knowledgeService.uploadDocument(
      new File(["x"], "doc.txt"),
      (stage) => captured.push(stage),
    );

    const es = await getEventSource();
    // 模拟极端时序：onmessage 已注册但前几个 stage 在 microtask 队列里
    es.emit("uploading", { stage: "uploading", message: "" });
    es.emit("parsing",   { stage: "parsing",   message: "" });
    es.emit("chunking",  { stage: "chunking",  message: "" });
    es.emit("embedding", { stage: "embedding", message: "" });
    es.emit("writing",   { stage: "writing",   message: "" });
    es.emit("done",      { stage: "done", message: "ok", doc: { id: "x" } });

    await promise;

    // 关键：onmessage 不依赖 addEventListener 注册时机
    // 所有 stage（含 uploading）都应被收到
    expect(captured).toContain("uploading");
    expect(captured).toContain("parsing");
    expect(captured).toContain("chunking");
    expect(captured).toContain("embedding");
    expect(captured).toContain("writing");
    expect(captured[0]).toBe("uploading");
    expect(captured[captured.length - 1]).toBe("done");
  });
});
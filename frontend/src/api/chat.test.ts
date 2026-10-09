import { afterEach, describe, expect, it, vi } from "vitest";
import { streamChat } from "@/api/chat";

vi.mock("@/lib/fetcher", () => ({
  request: vi.fn(),
  requestSilent: vi.fn(),
}));
vi.mock("@/api/client", () => ({
  apiErrorFromEnvelope: (payload: { message?: string }) => new Error(payload.message ?? "API error"),
}));
vi.mock("@/lib/auth", () => ({
  bearerHeaders: () => ({}),
  handleAuthFailure: vi.fn(),
  tryRefreshOnce: vi.fn(async () => false),
}));

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("streamChat SSE 恢复", () => {
  it("只发送一次提问、按 seq 去重，并保留 done 权威答案", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(sseResponse([
        'event: meta\ndata: {"seq":1,"request_id":"req-1"}\n\n',
        'event: delta\ndata: {"seq":2,"content":"预览片段"}\n\n',
      ]))
      .mockResolvedValueOnce(sseResponse([
        'event: meta\ndata: {"seq":1,"request_id":"req-1"}\n\n',
        'event: delta\ndata: {"seq":2,"content":"预览片段"}\n\n',
        'event: done\ndata: {"seq":3,"answer":"最终权威答案","answer_source":"reporter"}\n\n',
      ]));
    vi.stubGlobal("fetch", fetchMock);

    const received: Array<{ event: string; data: Record<string, unknown> }> = [];
    for await (const event of streamChat({
      question: "合成续传验收",
      session_id: "synthetic-session",
      request_id: "req-1",
      idempotency_key: "idem-1",
    })) {
      received.push(event as unknown as { event: string; data: Record<string, unknown> });
    }

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [initialUrl, initialInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [resumeUrl, resumeInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(initialUrl).toBe("/api/chat/stream");
    expect(resumeUrl).toBe("/api/chat/stream/resume");
    expect(initialInit.body).toContain('"question":"合成续传验收"');
    expect(JSON.parse(String(resumeInit.body))).toEqual({ request_id: "req-1", after_seq: 2 });
    expect(new Headers(initialInit.headers).get("Idempotency-Key")).toBe("idem-1");
    expect(new Headers(resumeInit.headers).get("Idempotency-Key")).toBe("idem-1");
    expect(received.map((event) => event.event)).toEqual(["meta", "delta", "done"]);
    expect(received.filter((event) => event.event === "delta")).toHaveLength(1);
    expect(received.at(-1)?.data.answer).toBe("最终权威答案");
  });
});

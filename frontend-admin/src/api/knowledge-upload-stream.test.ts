import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  fetchRaw: vi.fn(),
  mutationFetchRaw: vi.fn(),
  createIdempotencyKey: vi.fn(() => "upload-key-1"),
  request: vi.fn(),
}));

vi.mock("@/api/client", () => apiMock);

import { knowledgeService } from "./knowledge";

const ACCEPTED = { ok: true, upload_id: "upload-42", filename: "a.pdf" };
const DONE_FRAME = `data: ${JSON.stringify({ stage: "done", message: "索引完成" })}\n\n`;

function acceptedOnce() {
  apiMock.mutationFetchRaw.mockReset().mockResolvedValue(
    new Response(JSON.stringify(ACCEPTED), { status: 200 }),
  );
}

/** batch3 复审 P2-1/P2-2 + #8（2026-11-11）：
 * 上传 SSE 流的连接层失败先重订阅一次（后端支持 Redis 镜像重订阅），
 * 看门狗从绝对超时改为活跃性驱动，终态事件不走重连。 */
describe("knowledgeService 上传 SSE 流可靠性（batch4a）", () => {
  beforeEach(() => {
    apiMock.fetchRaw.mockReset();
    apiMock.request.mockReset();
    apiMock.createIdempotencyKey.mockClear();
    acceptedOnce();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("首次 SSE 连接失败（502）→ 自动重订阅一次并拿到终态", async () => {
    apiMock.fetchRaw
      .mockResolvedValueOnce(new Response(null, { status: 502 }))
      .mockResolvedValueOnce(new Response(DONE_FRAME, { status: 200 }));

    const result = await knowledgeService.uploadDocument(new File(["x"], "a.pdf"));

    expect(result.ok).toBe(true);
    expect(apiMock.fetchRaw).toHaveBeenCalledTimes(2);
    expect(apiMock.fetchRaw.mock.calls[1][0]).toContain("/upload/upload-42/stream");
  });

  it("SSE 流提前关闭（done 无终态事件）→ 重订阅一次拿到终态", async () => {
    apiMock.fetchRaw
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response(DONE_FRAME, { status: 200 }));

    const result = await knowledgeService.uploadDocument(new File(["x"], "a.pdf"));

    expect(result.ok).toBe(true);
    expect(apiMock.fetchRaw).toHaveBeenCalledTimes(2);
  });

  it("重连后再次失败 → 终判「进度连接中断」，不无限重连", async () => {
    apiMock.fetchRaw.mockResolvedValue(new Response(null, { status: 502 }));

    const result = await knowledgeService.uploadDocument(new File(["x"], "a.pdf"));

    expect(result).toEqual({ ok: false, error: "进度连接中断" });
    expect(apiMock.fetchRaw).toHaveBeenCalledTimes(2);
  });

  it("收到 error 终态事件是任务失败语义，不触发重连", async () => {
    apiMock.fetchRaw.mockResolvedValueOnce(
      new Response(
        `data: ${JSON.stringify({ stage: "error", message: "解析失败" })}\n\n`,
        { status: 200 },
      ),
    );

    const result = await knowledgeService.uploadDocument(new File(["x"], "a.pdf"));

    expect(result).toEqual({ ok: false, error: "解析失败" });
    expect(apiMock.fetchRaw).toHaveBeenCalledTimes(1);
  });

  it("长时间无更新（超 idle 上限）→ 触发重连，新流终态成功", async () => {
    vi.useFakeTimers();
    // 第一条流：连接建立后无任何字节（read 永久挂起）
    const hangingStream = new ReadableStream<Uint8Array>({ start() { /* 挂起 */ } });
    apiMock.fetchRaw
      .mockResolvedValueOnce(new Response(hangingStream, { status: 200 }))
      .mockResolvedValueOnce(new Response(DONE_FRAME, { status: 200 }));

    const pending = knowledgeService.uploadDocument(new File(["x"], "a.pdf"));

    // 推进超过 idle 上限（120s）+ 重连退避（1s）
    await vi.advanceTimersByTimeAsync(126 * 1000);
    const result = await pending;

    expect(result.ok).toBe(true);
    expect(apiMock.fetchRaw).toHaveBeenCalledTimes(2);
  });
});

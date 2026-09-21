import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  fetchRaw: vi.fn(),
  mutationFetchRaw: vi.fn(),
  createIdempotencyKey: vi.fn(() => "upload-key-1"),
}));

vi.mock("@/api/client", () => apiMock);

import { knowledgeService } from "./knowledge";

describe("knowledgeService 上传幂等接线", () => {
  beforeEach(() => {
    apiMock.fetchRaw.mockReset();
    apiMock.mutationFetchRaw.mockReset().mockResolvedValue(
      new Response(JSON.stringify({ ok: false, error: "校验失败" }), { status: 200 }),
    );
    apiMock.createIdempotencyKey.mockClear();
  });

  it("上传请求使用稳定幂等键，并在服务端拒绝时不建立第二条 SSE 请求", async () => {
    const result = await knowledgeService.uploadDocument(
      new File(["content"], "policy.pdf", { type: "application/pdf" }),
    );

    expect(result).toEqual({ ok: false, error: "校验失败" });
    expect(apiMock.createIdempotencyKey).toHaveBeenCalledTimes(1);
    expect(apiMock.mutationFetchRaw).toHaveBeenCalledTimes(1);
    const [, options] = apiMock.mutationFetchRaw.mock.calls[0];
    expect(options.operation).toBe("rag.upload");
    expect(options.idempotencyKey).toBe("upload-key-1");
    expect(options.dedupeKey).toContain("policy.pdf");
    expect(apiMock.fetchRaw).not.toHaveBeenCalled();
  });
});

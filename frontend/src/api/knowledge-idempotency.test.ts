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

describe("knowledgeService 上传 4xx 错误解析（2026-11-10 评审）", () => {
  beforeEach(() => {
    apiMock.fetchRaw.mockReset();
    apiMock.mutationFetchRaw.mockReset();
    apiMock.createIdempotencyKey.mockClear();
  });

  it("403 业务错误取 error 字段人话，不再把原始 JSON 塞进错误串", async () => {
    apiMock.mutationFetchRaw.mockResolvedValue(
      new Response(
        JSON.stringify({ ok: false, error: "没有知识库 'policy_general' 的访问权限" }),
        { status: 403 },
      ),
    );
    await expect(
      knowledgeService.uploadDocument(new File(["c"], "a.md")),
    ).rejects.toThrow("没有知识库 'policy_general' 的访问权限");
  });

  it("413 限长优先取 detail 字段（error 是错误码 PayloadTooLarge）", async () => {
    apiMock.mutationFetchRaw.mockResolvedValue(
      new Response(
        JSON.stringify({ error: "PayloadTooLarge", detail: "请求体超过上限：… > … bytes" }),
        { status: 413 },
      ),
    );
    await expect(
      knowledgeService.uploadDocument(new File(["c"], "a.md")),
    ).rejects.toThrow("请求体超过上限");
  });

  it("非 JSON body 退回原文截断并带状态码", async () => {
    apiMock.mutationFetchRaw.mockResolvedValue(
      new Response("<html>Bad Gateway</html>", { status: 502 }),
    );
    await expect(
      knowledgeService.uploadDocument(new File(["c"], "a.md")),
    ).rejects.toThrow("上传失败（502）");
  });
});

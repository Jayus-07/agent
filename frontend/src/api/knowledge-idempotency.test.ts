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

describe("knowledgeService 删除/重索引错误可见性（2026-11-10 评审 #4）", () => {
  beforeEach(() => {
    apiMock.fetchRaw.mockReset();
    apiMock.mutationFetchRaw.mockReset();
  });

  it("403 授权拒绝取 detail 人话，不再返回无原因的 {ok:false}", async () => {
    apiMock.fetchRaw.mockResolvedValue(
      new Response(JSON.stringify({ detail: "没有该文档的管理权限" }), { status: 403 }),
    );
    const result = await knowledgeService.deleteDocument("doc-1");
    expect(result).toEqual({ ok: false, error: "没有该文档的管理权限" });
  });

  it("网关 HTML 错误页退回状态码兜底文案（重索引）", async () => {
    apiMock.fetchRaw.mockResolvedValue(
      new Response("<html>502</html>", { status: 502 }),
    );
    const result = await knowledgeService.reindexDocument("doc-1");
    expect(result).toEqual({ ok: false, error: "操作失败（HTTP 502）" });
  });

  it("2xx 业务成功原样透传", async () => {
    apiMock.fetchRaw.mockResolvedValue(
      new Response(JSON.stringify({ ok: true, warnings: ["w1"] }), { status: 200 }),
    );
    const result = await knowledgeService.deleteDocument("doc-1");
    expect(result).toEqual({ ok: true, warnings: ["w1"] });
  });
});

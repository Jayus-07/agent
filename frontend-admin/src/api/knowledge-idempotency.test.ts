import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  fetchRaw: vi.fn(),
  mutationFetchRaw: vi.fn(),
  createIdempotencyKey: vi.fn(() => "upload-key-1"),
  request: vi.fn(),
}));

vi.mock("@/api/client", () => apiMock);

import { knowledgeService } from "./knowledge";

describe("knowledgeService 上传幂等接线", () => {
  beforeEach(() => {
    apiMock.fetchRaw.mockReset();
    apiMock.mutationFetchRaw.mockReset().mockResolvedValue(
      new Response(JSON.stringify({ ok: false, error: "校验失败" }), { status: 200 }),
    );
    apiMock.request.mockReset();
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

  it("服务端受理后、索引 SSE 结束前发送上传回执", async () => {
    const file = new File(["content"], "faq_only.docx");
    const onAccepted = vi.fn();
    apiMock.mutationFetchRaw.mockResolvedValue(
      new Response(JSON.stringify({
        ok: true,
        upload_id: "upload-42",
        filename: "faq_only.docx",
      }), { status: 200 }),
    );
    apiMock.fetchRaw.mockImplementation(async () => {
      expect(onAccepted).toHaveBeenCalledWith({
        upload_id: "upload-42",
        filename: "faq_only.docx",
      });
      return new Response(
        `data: ${JSON.stringify({ stage: "done", message: "索引完成" })}\n\n`,
        { status: 200 },
      );
    });

    const result = await knowledgeService.uploadDocument(
      file, undefined, undefined, undefined, undefined, onAccepted,
    );

    expect(result.ok).toBe(true);
    expect(apiMock.fetchRaw).toHaveBeenCalledTimes(1);
  });

  it("操作日志请求错误继续向调用方抛出", async () => {
    apiMock.request.mockRejectedValue(new Error("HTTP 503"));

    await expect(knowledgeService.getOperations({ page: 2 })).rejects.toThrow("HTTP 503");
    expect(apiMock.request).toHaveBeenCalledWith("/api/rag/operations?page=2");
  });

  it("拒绝把错误或格式不完整的操作日志响应当作空列表", async () => {
    apiMock.request.mockResolvedValueOnce({
      items: [], total: 0, active_uploads: [], error: "数据库暂不可用",
    }).mockResolvedValueOnce({});

    await expect(knowledgeService.getOperations()).rejects.toThrow("数据库暂不可用");
    await expect(knowledgeService.getOperations()).rejects.toThrow("操作日志响应格式无效");
  });
});

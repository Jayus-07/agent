import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  mutationRequest: vi.fn(),
  request: vi.fn(),
}));

vi.mock("@/api/client", () => apiMock);

import { competitorService } from "./competitor";

describe("competitorService 写操作幂等接线", () => {
  beforeEach(() => {
    apiMock.mutationRequest.mockReset().mockResolvedValue({});
    apiMock.request.mockReset().mockResolvedValue({});
  });

  it("所有竞品写入口都经过 mutationRequest", async () => {
    await competitorService.addWatch({ url: "https://example.com/item" });
    await competitorService.removeWatch("https://example.com/item");
    await competitorService.toggleWatch("https://example.com/item", false);
    await competitorService.analyze("https://example.com/item");
    await competitorService.scanAll();
    await competitorService.saveCookies("cookie", "taobao");
    await competitorService.clearCookies("taobao");
    await competitorService.testCookies("https://example.com/item");
    await competitorService.startQrLogin("taobao");
    await competitorService.pollQrLogin("taobao", "token-1", "session-cookie");
    await competitorService.retryBlocked();

    expect(apiMock.request).not.toHaveBeenCalled();
    expect(apiMock.mutationRequest).toHaveBeenCalledTimes(11);
    expect(apiMock.mutationRequest.mock.calls.map(([, options]) => options.operation)).toEqual([
      "competitor.watchlist.add",
      "competitor.watchlist.remove",
      "competitor.watchlist.toggle",
      "competitor.analyze",
      "competitor.scan",
      "competitor.cookies.save",
      "competitor.cookies.clear",
      "competitor.cookies.test",
      "competitor.qr_login.start",
      "competitor.qr_login.poll",
      "competitor.retry_blocked",
    ]);
  });
});

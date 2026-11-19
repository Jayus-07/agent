import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { completeSsoLogin, startSsoLogin } from "@/lib/auth";

function response(data: unknown) {
  return { json: async () => ({ code: 200, message: "success", data }) };
}

describe("扫码登录浏览器绑定", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("把一次性浏览器随机值带到发起和回调请求，并在回调后消费", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce(response({ authorizationUrl: "https://idp.example/authorize" }) as Response)
      .mockResolvedValueOnce(response({ token: "local-jwt", userInfo: { username: "demo" } }) as Response);

    const authorizationUrl = await startSsoLogin("feishu", "/agent");
    const startBody = JSON.parse(String(fetchMock.mock.calls[0][1]?.body)) as {
      browser_nonce: string;
    };
    expect(authorizationUrl).toBe("https://idp.example/authorize");
    expect(startBody.browser_nonce).toMatch(/^[0-9a-f]{64}$/);

    await completeSsoLogin("feishu", "code", "state");
    const callbackBody = JSON.parse(String(fetchMock.mock.calls[1][1]?.body)) as {
      browser_nonce: string;
    };
    expect(callbackBody.browser_nonce).toBe(startBody.browser_nonce);
    await expect(completeSsoLogin("feishu", "code", "state")).rejects.toThrow("当前浏览器");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("不接受其他浏览器或其他平台留下的待处理随机值", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce(
      response({ authorizationUrl: "https://idp.example/authorize" }) as Response,
    );
    await startSsoLogin("feishu", "/agent");

    await expect(completeSsoLogin("wecom", "code", "state")).rejects.toThrow("当前浏览器");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

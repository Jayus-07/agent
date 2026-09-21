/**
 * securityOps 响应形态契约测试（2026-09-19，纯新增）
 *
 * 背景：本模块同时消费**两种**后端响应形态 ——
 *   /sys/security/*     带 Result 壳 `{code, message, data}`
 *   /sys/config/{key}   裸 dict `{key, old, new, changedBy}`
 *
 * 2026-09-19 之前 `updateGuardMode` 误按壳取 `.data`，而裸 dict 没有 `data` 字段，
 * 返回值恒为 `undefined`；消费方 `security/page.tsx` 解构即抛
 * `TypeError: Cannot destructure property 'old' of 'undefined'` —— 表现为
 * **「界面报切换失败，但后端已写库并落审计」的假失败**（且因抛出而跳过列表刷新）。
 *
 * 本测试用**真实响应形状**（裸 dict）驱动，锁住「不得再套 Result 壳」这条契约：
 * 一旦有人改回 `res.data`，返回值立刻变 `undefined`，断言失败。
 * 反向也锁住请求形态（PUT + `{value}` body），避免「修好了读、写坏了」。
 *
 * mock 边界取被测模块的**直接依赖** `@/lib/fetcher`（兼容层），不穿透到 `@/api/client`。
 */
import { describe, expect, it, vi } from "vitest";
import { request } from "@/lib/fetcher";
import { updateGuardMode } from "./securityOps";

vi.mock("@/lib/fetcher", () => ({
  request: vi.fn(),
}));

// 后端 sys_config_admin.py:51 的真实形状：顶层 key/old/new/changedBy（无 data）
function bareDict(old: string | null, next: string) {
  return {
    key: "JWT_SESSION_GUARD_MODE",
    old,
    new: next,
    changedBy: "user:1",
  };
}

describe("updateGuardMode 响应形态契约（裸 dict，不带 Result 壳）", () => {
  it("直接返回裸 dict 的 old/new/changedBy，不经过 .data", async () => {
    vi.mocked(request).mockResolvedValueOnce(bareDict("audit", "enforce"));

    const result = await updateGuardMode("JWT_SESSION_GUARD_MODE", "enforce");

    expect(result.old).toBe("audit");
    expect(result.new).toBe("enforce");
    expect(result.changedBy).toBe("user:1");
  });

  it("首次覆盖时 old 为 null（此前生效的是 env 值，回滚基准为空）", async () => {
    vi.mocked(request).mockResolvedValueOnce(bareDict(null, "audit"));

    await expect(
      updateGuardMode("JWT_SESSION_GUARD_MODE", "audit"),
    ).resolves.toMatchObject({ old: null, new: "audit" });
  });

  it("返回值必须是可解构的对象（回归防护：曾返回 undefined 导致消费方抛 TypeError）", async () => {
    vi.mocked(request).mockResolvedValueOnce(bareDict(null, "off"));

    const result = await updateGuardMode("JWT_SESSION_GUARD_MODE", "off");

    expect(result).toBeDefined();
    expect(typeof result).toBe("object");
    // 消费方 security/page.tsx 的写法：const { old, new: newVal } = ...
    const { old, new: newVal } = result;
    expect(old).toBeNull();
    expect(newVal).toBe("off");
  });

  it("请求形态：PUT + JSON body {value}（读修好了不能把写改坏）", async () => {
    vi.mocked(request).mockResolvedValueOnce(bareDict(null, "off"));

    await updateGuardMode("JWT_SESSION_GUARD_MODE", "off");

    expect(request).toHaveBeenCalledWith(
      "/api/sys/config/JWT_SESSION_GUARD_MODE",
      { method: "PUT", body: JSON.stringify({ value: "off" }) },
    );
  });
});

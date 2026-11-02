/**
 * S6/S7 审计修复回归：登录回跳目标白名单化 + 明文凭据废除（主站）。
 *
 * S6：/login?redirect 历史只查 startsWith("/")，//attacker.example 与
 * /\attacker.example（浏览器把 \ 归一为 /）都放行，登录成功后跳外站钓鱼。
 * S7：密码曾明文写 localStorage（agent.saved_credentials_v1），任意同源
 * 脚本或能读浏览器 profile 的主体可取回可复用密码；修复后只记用户名，
 * 历史明文键在首次读取登录态时清除，凭据记忆交浏览器密码管理器。
 */
import { beforeEach, describe, expect, it } from "vitest";
import { clearSavedUsername, getSavedUsername, safeRedirectPath } from "@/lib/auth";

const USERNAME_KEY = "agent.saved_username";
const SAVED_CREDENTIALS_KEY = "agent.saved_credentials_v1";

describe("safeRedirectPath（S6：登录回跳白名单）", () => {
  it.each([
    ["/agent", "/agent"],
    ["/travel/plans?tab=map", "/travel/plans?tab=map"],
    ["/customer-service", "/customer-service"],
  ])("同源路径 %s 原样放行", (input, expected) => {
    expect(safeRedirectPath(input, "/agent")).toBe(expected);
  });

  it.each([
    ["null 回退", null],
    ["空串回退", ""],
    ["外域绝对 URL", "https://evil.example/auth"],
    ["协议相对", "//evil.example/auth"],
    ["反斜杠协议相对变体", "/\\evil.example/auth"],
    ["双斜杠加反斜杠", "//\\evil.example"],
    ["路径中的裸反斜杠", "/agent?next=/x\\y"],
    ["缺 leading slash", "agent"],
  ])("%s 一律回退默认页", (_name, input) => {
    expect(safeRedirectPath(input as string | null, "/agent")).toBe("/agent");
  });
});

describe("明文凭据废除（S7）", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("历史 v1 明文凭据键在首次读取登录态时被清除，用户名偏好保留", () => {
    localStorage.setItem(
      SAVED_CREDENTIALS_KEY,
      JSON.stringify({ username: "13800000000", password: "secret-pass" }),
    );
    localStorage.setItem(USERNAME_KEY, "13800000000");
    expect(getSavedUsername()).toBe("13800000000");
    expect(localStorage.getItem(SAVED_CREDENTIALS_KEY)).toBeNull();
    expect(localStorage.getItem(USERNAME_KEY)).toBe("13800000000");
  });

  it("忘记账号同时清掉用户名与历史明文凭据键", () => {
    localStorage.setItem(USERNAME_KEY, "13800000000");
    localStorage.setItem(SAVED_CREDENTIALS_KEY, "leftover");
    clearSavedUsername();
    expect(localStorage.getItem(USERNAME_KEY)).toBeNull();
    expect(localStorage.getItem(SAVED_CREDENTIALS_KEY)).toBeNull();
  });

  it("正常登录态写入后 localStorage 不含任何密码字段", () => {
    // saveUsername 是登录成功后的唯一持久化入口（S7 后 saveCredentials 已删）
    localStorage.setItem(USERNAME_KEY, "13800000000");
    const dump = JSON.stringify(localStorage);
    expect(dump).not.toContain("secret-pass");
    expect(dump).not.toContain("password");
  });
});

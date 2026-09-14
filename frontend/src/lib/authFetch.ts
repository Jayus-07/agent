/**
 * 带认证的 fetch 包装 — 后端 API_KEY 认证中间件（backend/app/api/middleware/auth.py）
 * 对所有非白名单路径强制要求 X-API-Key。前端各 service / hook 统一经此包装发请求，
 * 避免裸 fetch 被 401 后又被 catch 静默吞掉（页面显示空数据、不报错）。
 *
 * 认证（对接网关 JWT 体系）：
 * - 有登录态时自动附带 Authorization: Bearer
 * - 401 → 静默 refresh 一次并重试；refresh 也失败 → 清态跳 /login
 * - 返回值仍是 Promise<Response>，既有调用方（await / .then）无需改动
 *
 * 注意：EventSource 原生不支持自定义 header，SSE 场景请改用 authFetch 流式读取
 * （参考 services/knowledge.ts uploadDocument 内的实现）。
 */
import { bearerHeaders, handleAuthFailure, tryRefreshOnce } from "./auth";

const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

function isAuthPath(input: string): boolean {
  // 相对路径与跨网关绝对路径都要覆盖
  return input.startsWith("/api/auth/") || /:\/\/[^/]+\/api\/auth\//.test(input);
}

export const authFetch = async (
  input: string,
  init: RequestInit = {},
): Promise<Response> => {
  const doFetch = () =>
    fetch(input, {
      ...init,
      headers: {
        ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
        ...bearerHeaders(),
        ...((init.headers as Record<string, string>) || {}),
      },
    });

  let res = await doFetch();

  // 401：静默刷新一次并重试；刷新失败则清态跳登录页
  if (res.status === 401 && !isAuthPath(input)) {
    if (await tryRefreshOnce()) {
      res = await doFetch();
    } else {
      handleAuthFailure();
    }
  }

  return res;
};

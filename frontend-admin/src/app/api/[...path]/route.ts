/**
 * app/api/[...path]/route.ts — BFF 服务端代理（凭据收口，2026-09-16 方案 B）
 *
 * 背景：`NEXT_PUBLIC_API_KEY` 会编译进浏览器 bundle（等价公开值），此前仅靠
 * 后端敏感端点守卫兜底。本路由把 X-API-Key 收到服务端 env（`API_KEY`，
 * 非 NEXT_PUBLIC_ 前缀），浏览器零密钥，只带 Authorization Bearer。
 *
 * 职责：
 *   1. 透传 `/api/*` 到网关（`${API_URL}/api/...`），保留 query；
 *   2. 服务端注入 `X-API-Key`；
 *   3. 转发 Authorization / Content-Type / Accept / User-Agent / Cookie /
 *      Idempotency-Key / X-Trace-Id，注入 X-Client-IP（会话台账），回传 Content-Type /
 *      Content-Disposition / X-Trace-Id 及逐条 Set-Cookie（refresh 令牌
 *      HttpOnly Cookie 的生命周期依赖它，缺失 = 静默刷新必失败）；
 *   4. 响应体流式透传（SSE / 上传 / 下载均不受影响）。
 *
 * 注意：
 *   - `dynamic = "force-dynamic"`：请求必须实时代理，禁止静态化；
 *   - 与 next.config.js 的 rewrite 并存：App Router 路由文件优先于
 *     afterFiles rewrite，本文件存在时 rewrite 不生效（保留作回退）；
 *   - 401 → 静默 refresh → 重试 的逻辑仍在浏览器侧 client.ts，本层不介入。
 */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const GATEWAY = (process.env.API_URL || "http://127.0.0.1:9080").replace(
  /\/+$/,
  "",
);
const CLIENT_APP = "admin";

const FORWARD_REQ_HEADERS = [
  "authorization",
  "content-type",
  "accept",
  "user-agent",
  "cookie",
  "idempotency-key",
  "x-trace-id",
] as const;
const FORWARD_RES_HEADERS = [
  "content-type",
  "content-disposition",
  "x-trace-id",
] as const;

function hasRefreshCookie(req: NextRequest): boolean {
  return /(?:^|;\s*)refresh_token(?:_(?:web|admin|cs))?=/.test(
    req.headers.get("cookie") || "",
  );
}

function isPublicApi(path: string[], method: string, req: NextRequest): boolean {
  if (method === "POST" && path.length === 2 && path[0] === "auth" && path[1] === "login") return true;
  if (
    method === "POST" && path.length === 2 && path[0] === "auth" &&
    (path[1] === "refresh" || path[1] === "logout") && hasRefreshCookie(req)
  ) return true;
  if (method === "GET" && path.join("/") === "sys/users/captcha") return true;
  if (method === "POST" && path.join("/") === "sys/users/register") return true;
  return false;
}

async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await ctx.params;
  if (req.method === "OPTIONS") return new Response(null, { status: 204 });
  const authorization = req.headers.get("authorization") || "";
  if (!/^Bearer\s+\S+$/i.test(authorization) && !isPublicApi(path, req.method, req)) {
    return Response.json(
      { error: "Unauthorized", message: "需要有效的用户访问令牌" },
      { status: 401 },
    );
  }
  const target = `${GATEWAY}/api/${path
    .map(encodeURIComponent)
    .join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  for (const h of FORWARD_REQ_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  headers.set("X-Client-App", CLIENT_APP);
  const apiKey = process.env.API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);

  // 客户端 IP 仅用于会话台账展示，不参与鉴权；Next 15 已移除 req.ip，
  // 登录限流和会话台账使用此地址；前置入口必须覆盖客户端伪造的代理头。
  // 优先使用受信代理链传入的 XFF 首段，缺失时回退 X-Real-IP。
  const clientIp =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("x-real-ip")?.trim() ||
    "";
  if (clientIp) headers.set("X-Client-IP", clientIp);

  const method = req.method;
  const body =
    method === "GET" || method === "HEAD"
      ? undefined
      : await req.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, { method, headers, body, redirect: "manual" });
  } catch (e) {
    return Response.json(
      { error: "Bad Gateway", message: `网关不可达: ${String(e)}` },
      { status: 502 },
    );
  }

  const resHeaders = new Headers();
  for (const h of FORWARD_RES_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) resHeaders.set(h, v);
  }
  // set-cookie 逐条透传（refresh_token HttpOnly Cookie 的生命周期依赖它；
  // Headers 复制会合并多条 set-cookie，必须用 getSetCookie 逐条回填）
  for (const sc of upstream.headers.getSetCookie()) {
    resHeaders.append("set-cookie", sc);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: resHeaders,
  });
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;

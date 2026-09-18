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
 *      X-Trace-Id，注入 X-Client-IP（会话台账），回传 Content-Type /
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

const FORWARD_REQ_HEADERS = [
  "authorization",
  "content-type",
  "accept",
  "user-agent",
  "cookie",
  "x-trace-id",
] as const;
const FORWARD_RES_HEADERS = [
  "content-type",
  "content-disposition",
  "x-trace-id",
] as const;

async function proxy(
  req: NextRequest,
  ctx: { params: { path: string[] } },
): Promise<Response> {
  const target = `${GATEWAY}/api/${ctx.params.path
    .map(encodeURIComponent)
    .join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  for (const h of FORWARD_REQ_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const apiKey = process.env.API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);

  // 客户端真实 IP 透传（2026-09-19 会话台账）：不透传时后端只能看到本服务
  // 的出口连接（APISIX 侧 X-Real-IP = 容器网桥地址）。优先 Next 按连接算出
  // 的 req.ip，回退 XFF 首段（上游代理链场景）。仅作台账展示，不参与鉴权。
  const clientIp =
    req.ip ||
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
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

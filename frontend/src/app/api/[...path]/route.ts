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
 *   3. 转发 Authorization / Content-Type / Accept / X-Trace-Id，
 *      回传 Content-Type / Content-Disposition / X-Trace-Id；
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

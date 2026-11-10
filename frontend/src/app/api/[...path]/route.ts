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
 *      Idempotency-Key / X-Batch-Id / X-Trace-Id，注入 X-Client-IP（会话台账），
 *      回传 Content-Type / Content-Disposition / X-Trace-Id 及逐条
 *      Set-Cookie（refresh 令牌 HttpOnly Cookie 的生命周期依赖它，缺失 =
 *      静默刷新必失败）；
 *   4. 请求/响应体流式透传（上传不再整包读入 Node 内存；SSE / 下载均不受影响）。
 *
 * 注意：
 *   - `dynamic = "force-dynamic"`：请求必须实时代理，禁止静态化；
 *   - 与 next.config.js 的 rewrite 并存：App Router 路由文件优先于
 *     afterFiles rewrite，本文件存在时 rewrite 不生效（保留作回退）；
 *   - 401 → 静默 refresh → 重试 的逻辑仍在浏览器侧 client.ts，本层不介入。
 */
import type { NextRequest } from "next/server";
import { randomUUID } from "crypto";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const GATEWAY = (process.env.API_URL || "http://127.0.0.1:9080").replace(
  /\/+$/,
  "",
);
const CLIENT_APP = "web";

/**
 * 登录限流/会话台账的客户端键（2026-11-09 审计修复）。
 * 默认不信任客户端可伪造的 X-Forwarded-For / X-Real-IP：改用服务端签发的
 * HttpOnly rl_client Cookie 作为分桶键；只有显式声明位于可信代理之后
 * （TRUST_PROXY_HEADERS=true）才采信转发头。
 */
function resolveClientKey(req: NextRequest): { key: string; issued?: string } {
  if (process.env.TRUST_PROXY_HEADERS === "true") {
    const forwarded = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
    const real = req.headers.get("x-real-ip")?.trim();
    return { key: forwarded || real || "" };
  }
  const existing = req.cookies.get("rl_client")?.value || "";
  if (/^[0-9a-f-]{36}$/i.test(existing)) return { key: `rl-${existing}` };
  const issued = randomUUID();
  return { key: `rl-${issued}`, issued };
}

const FORWARD_REQ_HEADERS = [
  "authorization",
  "content-type",
  // 幂等键（2026-09-23 缺陷4）：写操作（转人工等）的 Idempotency-Key 由前端
  // 统一网络层注入，后端强制校验（缺失 400）。BFF 白名单漏掉它等于剥头，
  // 真实 UI 转人工必 400，而脚本测试直连网关掩盖了这条链路。
  "idempotency-key",
  // 批次关联（2026-11-10 评审修复）：RAG 上传/批量删除/批量重索引由前端
  // 注入 X-Batch-Id，后端落操作日志供操作中心按批次折叠。此前不在白名单，
  // 浏览器路径下被剥头，批次聚合静默失效（后端从未收到过该头）。
  "x-batch-id",
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
const SHARE_RES_HEADERS = [
  "cache-control",
  "referrer-policy",
  "x-robots-tag",
  "vary",
] as const;

const SHARE_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isAnonymousShareApi(path: string[], method: string): boolean {
  if (
    method !== "GET" ||
    path[0] !== "travel" ||
    path[1] !== "v2" ||
    path[2] !== "shares" ||
    !SHARE_UUID.test(path[3] || "")
  ) {
    return false;
  }
  // 仅允许两个匿名 GET 路由接收分享令牌，避免扩展到私有 Trip API。
  return path.length === 4 || (path.length === 5 && path[4] === "export.ics");
}

function isShareCreateApi(path: string[], method: string): boolean {
  return (
    method === "POST" &&
    path.length === 5 &&
    path[0] === "travel" &&
    path[1] === "v2" &&
    path[2] === "trips" &&
    SHARE_UUID.test(path[3] || "") &&
    path[4] === "shares"
  );
}

function hasRefreshCookie(req: NextRequest): boolean {
  return /(?:^|;\s*)refresh_token(?:_(?:web|admin|cs))?=/.test(
    req.headers.get("cookie") || "",
  );
}

function isPublicApi(path: string[], method: string, req: NextRequest): boolean {
  if (isAnonymousShareApi(path, method)) return true;
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
  const hasBearer = /^Bearer\s+\S+$/i.test(authorization);
  if (!hasBearer && !isPublicApi(path, req.method, req)) {
    return Response.json(
      { error: "Unauthorized", message: "需要有效的用户访问令牌" },
      { status: 401 },
    );
  }

  const target = `${GATEWAY}/api/${path
    .map(encodeURIComponent)
    .join("/")}${req.nextUrl.search}`;
  const isAnonymousShareRead = isAnonymousShareApi(path, req.method);
  const forwardShareSecurityHeaders =
    isAnonymousShareRead || isShareCreateApi(path, req.method);

  const headers = new Headers();
  for (const h of FORWARD_REQ_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  if (isAnonymousShareRead) {
    // 分享令牌只通过请求头发送；不要拼入上游 URL、日志或代理错误响应。
    const shareToken = req.headers.get("x-travel-share-token");
    if (shareToken) headers.set("X-Travel-Share-Token", shareToken);
  }
  headers.set("X-Client-App", CLIENT_APP);
  const apiKey = process.env.API_KEY;
  if (apiKey) headers.set("X-API-Key", apiKey);

  // 限流/台账键：默认用服务端签发的 rl_client Cookie，不信客户端转发头
  const rl = resolveClientKey(req);
  if (rl.key) headers.set("X-Client-IP", rl.key);

  const method = req.method;
  // T6（2026-11-08 安全审计）：带 Content-Length 的请求在读取 body 前先做
  // 上限检查。分路径规则（2026-11-10 冒烟修复）：
  //   - /rag/upload → RAG_MAX_FILE_SIZE + multipart 余量（与后端 body_limit
  //     和路由层 F10 预检三方同源）。超限在 BFF 直接人话拒绝：流式透传场景
  //     下游会读完整个 body 才拒（白白传 50MB+），且上游快速 413 断连会触发
  //     undici「terminated」把错误变成连接重置（客户端收到 000 而非 413）。
  //   - 其余 → BFF_MAX_BODY_BYTES 默认 64MB。
  // chunked 请求无 Content-Length 无法预检，由 APISIX 边缘上限按实际字节兜底。
  const declaredLength = Number(req.headers.get("content-length") || "0");
  if (
    method !== "GET" &&
    method !== "HEAD" &&
    Number.isFinite(declaredLength) &&
    declaredLength > 0
  ) {
    // #6 env 联动（2026-11-11 batch4b）：部署侧设 BFF_RAG_UPLOAD_LIMIT_BYTES
    // 与后端 RAG_MAX_FILE_SIZE 同步调整；未设时回退默认 50MiB+16KiB（对齐
    // backend/config/rag.py 的 RAG_MAX_FILE_SIZE=50 + RAG_UPLOAD_MULTIPART_OVERHEAD）。
    // 管理端 UI 的预检上限走 /rag/health 接口透传（deps.get_rag_status）。
    const ragUploadLimit =
      Number(process.env.BFF_RAG_UPLOAD_LIMIT_BYTES || 50 * 1024 * 1024 + 16 * 1024);
    const maxBodyBytes =
      path[0] === "rag" && path[1] === "upload"
        ? ragUploadLimit
        : Number(process.env.BFF_MAX_BODY_BYTES || 64 * 1024 * 1024);
    if (declaredLength > maxBodyBytes) {
      return Response.json(
        {
          // detail 优先对齐后端 body_limit 413 的字段结构——前端 4xx 解析
          // 链（detail → error → 原文）取 detail 人话，不显示错误码
          error: "PayloadTooLarge",
          detail: `请求体超过上限（${maxBodyBytes} bytes）`,
        },
        { status: 413 },
      );
    }
  }
  // 请求体流式透传（2026-11-10 评审）：旧实现 arrayBuffer() 把整个上传体
  // （最大 50MB）读入 Node 内存后再转发，并发上传内存峰值线性叠加。改为
  // ReadableStream 直通，Node fetch（undici）对流 body 要求 duplex: "half"。
  // 代价：下游收到 chunked 请求（无 Content-Length），body_limit 的源内
  // 预检对 chunked 不生效——该边界本就存在，由 APISIX 边缘上限兜底。
  const body =
    method === "GET" || method === "HEAD" || req.body === null
      ? undefined
      : req.body;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body,
      // undici：body 为 ReadableStream 时必需
      duplex: "half",
      redirect: "manual",
    } as RequestInit & { duplex: "half" });
  } catch {
    return Response.json(
      { error: "Bad Gateway", message: "网关暂不可达" },
      { status: 502 },
    );
  }

  const resHeaders = new Headers();
  const responseHeaderAllowlist = forwardShareSecurityHeaders
    ? [...FORWARD_RES_HEADERS, ...SHARE_RES_HEADERS]
    : FORWARD_RES_HEADERS;
  for (const h of responseHeaderAllowlist) {
    const v = upstream.headers.get(h);
    if (v) resHeaders.set(h, v);
  }
  // set-cookie 逐条透传（refresh_token HttpOnly Cookie 的生命周期依赖它；
  // Headers 复制会合并多条 set-cookie，必须用 getSetCookie 逐条回填）
  for (const sc of upstream.headers.getSetCookie()) {
    resHeaders.append("set-cookie", sc);
  }
  if (rl.issued) {
    resHeaders.append(
      "set-cookie",
      `rl_client=${rl.issued}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000`,
    );
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

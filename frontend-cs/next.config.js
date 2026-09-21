/** @type {import('next').NextConfig} */
const nextConfig = {
  // 生产模式使用 standalone 输出（Docker 部署）
  output: 'standalone',

  // 构建输出目录：默认 .next；可用 NEXT_DIST_DIR 指定（并行构建 / 沙箱内
  // .next 清理被安全策略拦截时，换一个干净目录即可正常启停）。
  distDir: process.env.NEXT_DIST_DIR || '.next',

  // 关闭 Next 内置 gzip 压缩：压缩中间件会把 /chat/stream 的 SSE 响应
  // 缓冲成单个 gzip 块一次性下发，浏览器端打字机逐 token 效果全失
  // （2026-09-14 实测：7390B 整块到达；关闭后恢复逐 chunk）。静态资源
  // 压缩如需要应由前置 nginx 层承担。
  compress: false,

  // API 代理：已由 BFF 路由 `src/app/api/[...path]/route.ts` 接管（凭据收口，
  // 2026-09-16 方案 B）——服务端注入 X-API-Key，浏览器不再持有密钥。
  // ⚠️ 此处**不能**再配 `/api/:path*` 的 rewrite：Next 的 afterFiles rewrite
  // 优先级高于动态路由（catch-all route handler），rewrite 一旦存在代理路由
  // 就永远不会被命中（2026-09-16 实测：请求全部走 rewrite、Key 未注入、后端 401）。
  // 临时绕过网关直连后端调试时：API_URL=http://localhost:8000（后端路由无
  // /api 前缀，需同时给代理路由的 target 去掉 /api，或临时恢复本 rewrite）。
}

module.exports = nextConfig

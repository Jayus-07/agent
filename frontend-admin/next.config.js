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

  // API 代理：将 /api/* 转发到 APISIX 网关（与生产拓扑一致）
  // 网关 enforce 模式验签 JWT 后向下游注入 X-User-Id 等身份头——后端
  // IDENTITY_SOURCE=header 只认身份头，dev 直连 :8000 会拿不到身份
  // （chat 一律 guest）。需要临时绕过网关直连后端时：
  //   API_URL=http://localhost:8000 npx next dev
  // Docker 部署 → http://api:8000（容器网络内仍应经 apisix）
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.API_URL || 'http://127.0.0.1:9080'}/:path*`,
      },
    ]
  },
}

module.exports = nextConfig

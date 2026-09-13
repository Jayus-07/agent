/** @type {import('next').NextConfig} */
const nextConfig = {
  // 生产模式使用 standalone 输出（Docker 部署）
  output: 'standalone',

  // 关闭 Next 内置 gzip 压缩：压缩中间件会把 /chat/stream 的 SSE 响应
  // 缓冲成单个 gzip 块一次性下发，浏览器端打字机逐 token 效果全失
  // （2026-09-14 实测：7390B 整块到达；关闭后恢复逐 chunk）。静态资源
  // 压缩如需要应由前置 nginx 层承担。
  compress: false,

  // API 代理：将 /api/* 转发到 FastAPI 后端
  // 本地 dev → http://localhost:8000，Docker → http://api:8000
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.API_URL || 'http://localhost:8000'}/:path*`,
      },
    ]
  },
}

module.exports = nextConfig

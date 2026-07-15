/** @type {import('next').NextConfig} */
const nextConfig = {
  // 生产模式使用 standalone 输出（Docker 部署）
  output: 'standalone',

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

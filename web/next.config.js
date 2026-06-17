/** @type {import('next').NextConfig} */
const nextConfig = {
  // API 代理：开发时将 /api 请求转发到 FastAPI 后端
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/:path*',
      },
    ]
  },
}

module.exports = nextConfig

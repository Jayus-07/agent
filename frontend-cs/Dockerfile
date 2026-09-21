# Agent Platform — Next.js 前端
# 多阶段构建：build → runtime（standalone 模式，最终 ~150MB）

# =============================================
# Stage 1: Build
# =============================================
FROM node:18-alpine AS builder
WORKDIR /app

# 安装依赖（利用 Docker 缓存层）
COPY package.json package-lock.json* ./
RUN npm ci

# 复制源码
COPY . .

# API 代理目标：统一经过 APISIX，确保 JWT 验签及坐席身份头注入。
ARG API_URL=http://apisix:9080
ENV API_URL=${API_URL}

# 生产构建（standalone 模式）
RUN npm run build

# =============================================
# Stage 2: Runtime
# =============================================
FROM node:18-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# 复制 standalone 输出
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]

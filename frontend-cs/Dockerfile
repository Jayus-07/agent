# Agent Platform — Next.js 前端
# 多阶段构建：build → runtime（standalone 模式，最终 ~150MB）

# =============================================
# Stage 1: Build
# =============================================
FROM node:22-alpine AS builder
WORKDIR /app

# 安装依赖（利用 Docker 缓存层）
COPY package.json package-lock.json* ./
RUN --mount=type=cache,id=agent-platform-npm,target=/root/.npm npm ci

# 复制源码
COPY . .

# API 代理目标：统一经过 APISIX，确保 JWT 验签及坐席身份头注入。
ARG API_URL=http://apisix:9080
ENV API_URL=${API_URL}

# W3：坐席端 WS 网关 origin（构建期内联进客户端产物）。
# Next standalone 容器无 /ws/* 路由，页面同源握手必败；经此注入网关
# origin（本机 ws://127.0.0.1:9080，ECS 按部署网关注入）。空值 = 回退
# location.host（旧行为，零破坏）。
ARG NEXT_PUBLIC_WS_ORIGIN=
ENV NEXT_PUBLIC_WS_ORIGIN=${NEXT_PUBLIC_WS_ORIGIN}

# 生产构建（standalone 模式）
RUN npm run build

# =============================================
# Stage 2: Runtime
# =============================================
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# 复制 standalone 输出
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]

# Agent Platform — Next.js 前端
# 多阶段构建：build → runtime（standalone 模式，最终 ~150MB）

# =============================================
# Stage 1: Build
# =============================================
# Vite 8 / Tailwind 4 的锁定依赖要求 Node 20.19+ 或 22.12+。
FROM node:22-alpine AS builder
WORKDIR /app

# 安装依赖（利用 Docker 缓存层）
COPY package.json package-lock.json* ./
# Tailwind 4 的平台原生/WASM 后端使用 optionalDependencies；构建需保留可选包。
RUN --mount=type=cache,id=agent-platform-npm,target=/root/.npm npm ci --include=optional

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
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# 复制 standalone 输出
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]

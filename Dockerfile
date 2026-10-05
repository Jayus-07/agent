# Agent Platform — FastAPI + LangGraph Multi-Agent
# P1-12 生产化：多阶段构建（builder 编译依赖 / runtime 仅运行时库）、非 root 用户。
#
# 构建:  docker build -t agent-platform .
# 运行:  见 docker-compose.yml（业务库走 agent_readonly 只读账号）
# 健康检查: 统一在各服务的 compose healthcheck 定义（2026-09-16 从此处移除——
# 本镜像被 app/rag/mcp/worker 四个不同端口的服务共用，镜像级探针写死端口
# 会导致其他服务永久 unhealthy，实测踩坑：mcp-service 8091 失败 1918 次）

# ════════════════════════════════════════════════
# Stage 1 — builder：安装依赖到独立 venv
# ════════════════════════════════════════════════
FROM python:3.10-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

# 编译期依赖（仅存在于 builder 层，不进最终镜像）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 依赖单独成层：pyproject.toml 未变时命中缓存
COPY pyproject.toml ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -e ".[postgres]" \
        --extra-index-url https://download.pytorch.org/whl/cpu

# ════════════════════════════════════════════════
# Stage 2 — runtime：最小运行时镜像
# ════════════════════════════════════════════════
FROM python:3.10-slim

LABEL description="Agent Platform: LangGraph + MCP + RAG + NL2SQL"

# ── Build Identity（Platform Readiness STOP B1）──────────────────
# 运行容器必须可回答「我是哪个 commit 构建的」。compose 侧以 build args
# 注入（未设置时 unknown）；启动日志与 /health 读取这两个 env。
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown
ENV GIT_COMMIT=${GIT_COMMIT} \
    BUILD_TIME=${BUILD_TIME}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH" \
    # HuggingFace 模型缓存固定到 /app/.cache（非 root HOME 由 compose 卷挂载持久化）
    HF_HOME=/app/.cache/huggingface

# 运行时依赖：curl（compose healthcheck 用）、libpq5（psycopg2）、中文字体（报告/图表渲染）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq5 fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

# ── 依赖 venv（来自 builder）──
COPY --from=builder /opt/venv /opt/venv

# ── 非 root 用户（uid 10001，固定值便于宿主侧对齐卷权限）──
# 用户层置于代码 COPY 之前，代码变更时可复用；运行时数据与缓存由 compose named volume 提供。
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app \
    && chown appuser:appuser /app

# ── 代码 ──
WORKDIR /app
COPY backend/ ./backend/
# 2026-09-21：alembic 退役，迁移统一走 scripts/init_db.py（已随 scripts/ 拷入）
COPY mcp_servers/ ./mcp_servers/
COPY scripts/ ./scripts/

USER appuser

EXPOSE 8000

# B6（2026-09-21 审查）：--timeout-graceful-shutdown 600 —— SIGTERM 后最多
# 等 600s 让在途请求（分钟级 SSE/长 RAG）自然收尾；compose 侧配套
# stop_grace_period: 700s（app/rag/mcp），worker 用 acks_late 重投兜底。
CMD ["python", "-m", "uvicorn", "backend.app.server:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "600"]

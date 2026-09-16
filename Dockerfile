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

# ── 代码 ──
WORKDIR /app
COPY backend/ ./backend/
# memory 库迁移在容器内执行（PG 未发布端口到宿主机，容器外无法连库）；
# 缺此文件 alembic 报 No 'script_location' key found
COPY alembic.ini ./
COPY mcp_servers/ ./mcp_servers/
COPY scripts/ ./scripts/

# ── 非 root 用户（uid 10001，固定值便于宿主侧对齐卷权限）──
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data/chroma /app/data/doc_db /app/data/long_term_memory /app/.cache/huggingface \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.app.server:app", "--host", "0.0.0.0", "--port", "8000"]

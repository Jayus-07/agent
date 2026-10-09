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

# PyPI 镜像源（可选，口径同 DEBIAN_MIRROR）：构建容器对国外域名（pypi.org/
# download.pytorch.org）不可路由；传 PIP_INDEX_URL 切国内镜像（torch 在 pypi
# 有包，主索引镜像即可覆盖）；默认空 = 官方源，海外克隆不受影响。
ARG PIP_INDEX_URL=
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

# 编译期依赖（仅存在于 builder 层，不进最终镜像）。
# Debian 源镜像（可选）：构建容器网络不经过宿主代理——fake-ip 域名在容器内
# 不可路由（2026-10-05 实测 apt 层 exit 100、容器内 deb.debian.org 全 000，
# 国内分流 DIRECT 可达）。传 DEBIAN_MIRROR=mirrors.aliyun.com 切国内源；
# 默认空 = 官方源，海外克隆不受影响。
ARG DEBIAN_MIRROR=
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 依赖单独成层：依赖声明与 Torch 约束未变时命中缓存
COPY pyproject.toml ./
COPY constraints/torch-cpu.txt ./constraints/torch-cpu.txt
# sentence-transformers 会传递引入 Torch。仅追加 CPU 索引会让 pip 在所有索引
# 中选择最高版本，可能从 PyPI 拉入整套 CUDA 依赖；先装 CPU wheel 并用约束锁定。
ARG TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-deps \
        --index-url ${TORCH_CPU_INDEX_URL} \
        -r constraints/torch-cpu.txt \
    && /opt/venv/bin/pip install -e ".[postgres,ragas]" \
        --constraint constraints/torch-cpu.txt \
        --extra-index-url ${TORCH_CPU_INDEX_URL}

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
# 与 builder 分开指定运行时镜像，切换运行时 apt 源时仍可复用 builder 依赖缓存
ARG RUNTIME_DEBIAN_MIRROR=
RUN if [ -n "$RUNTIME_DEBIAN_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org|https://$RUNTIME_DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i "s|http://deb.debian.org|https://$RUNTIME_DEBIAN_MIRROR|g" /etc/apt/sources.list; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
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
# appuser(uid 10001) 运行时要在代码目录下建 data/uploads（routes/data.py 模块导入期 mkdir；2026-10-07 容器部署实测 PermissionError 崩溃循环）
RUN mkdir -p /app/backend/app/data && chown -R appuser:appuser /app/backend/app/data
COPY scripts/ ./scripts/

USER appuser

EXPOSE 8000

# B6（2026-09-21 审查）：--timeout-graceful-shutdown 600 —— SIGTERM 后最多
# 等 600s 让在途请求（分钟级 SSE/长 RAG）自然收尾；compose 侧配套
# stop_grace_period: 700s（app/rag/mcp），worker 用 acks_late 重投兜底。
CMD ["python", "-m", "uvicorn", "backend.app.server:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "600"]

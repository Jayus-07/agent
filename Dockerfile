# Agent Platform — FastAPI + LangGraph Multi-Agent
FROM python:3.10-slim

LABEL description="Agent Platform: LangGraph + MCP + RAG + NL2SQL"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

# ── 依赖（分层缓存）──
COPY pyproject.toml .
RUN pip install --upgrade pip && \
    pip install -e ".[postgres]" --extra-index-url https://download.pytorch.org/whl/cpu

# ── 代码 ──
WORKDIR /app
COPY backend/ ./backend/
COPY mcp_servers/ ./mcp_servers/
COPY scripts/ ./scripts/

RUN mkdir -p /app/data/chroma /app/data/doc_db /app/data/long_term_memory

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.app.server:app", "--host", "0.0.0.0", "--port", "8000"]

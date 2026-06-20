# Agent Platform — FastAPI + LangGraph Multi-Agent 服务
FROM python:3.10-slim

LABEL description="Agent Platform: LangGraph Multi-Agent + RAG + SQL + Report"

# 环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=0 \
    TRANSFORMERS_OFFLINE=0 \
    # 默认值，docker-compose 可覆盖
    LLM_MODEL=qwen2.5:4b \
    OLLAMA_HOST=http://ollama:11434 \
    PGHOST=postgres \
    PGPORT=5432 \
    PGDATABASE=demo \
    PGUSER=postgres \
    PGPASSWORD=postgres \
    EMBEDDING_MODEL_PATH=/app/models/bge-small-zh-v1.5 \
    RERANKER_MODEL_PATH=/app/models/bge-reranker-base \
    CHROMA_PATH=/app/data/chroma \
    DOC_DB_PATH=/app/data/doc_db \
    DOCS_DIRECTORY=/app/data/docs

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    # matplotlib 中文字体
    fonts-wqy-microhei \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# 升级 pip
RUN pip install --upgrade pip setuptools wheel

# ========================================
# 安装依赖（分层缓存）
# ========================================

# Layer 1: PyTorch CPU 版（先安装以兼容 requirements.txt 中的 torch 依赖）
RUN pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu

# Layer 2: 核心依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Layer 3: FastAPI 服务依赖
RUN pip install --no-cache-dir \
    fastapi>=0.115.0 \
    uvicorn[standard]>=0.32.0

# ========================================
# 复制项目代码
# ========================================
WORKDIR /app
COPY . .

# 创建数据目录
RUN mkdir -p /app/data/chroma /app/data/doc_db /app/data/reports \
    /app/data/report_snapshots /app/data/long_term_memory \
    /app/models

# ========================================
# 入口
# ========================================
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

#!/bin/bash
set -e

echo "========================================="
echo "  Agent Platform — Container Startup"
echo "========================================="

# ── 工具函数 ────────────────────────────────

# 构造 Ollama API 地址: OLLAMA_HOST 可能是 "http://host:port" 或 "host:port"
_ollama_api() {
    local host="${OLLAMA_HOST:-http://localhost:11434}"
    if [[ "$host" != http://* && "$host" != https://* ]]; then
        host="http://$host"
    fi
    echo "$host"
}

OLLAMA_API="$(_ollama_api)"
echo "Ollama API: $OLLAMA_API"

# ── 1. 下载 Embedding 模型 ──────────────────
download_model() {
    local model_name="$1"
    local target_dir="$2"
    if [ -d "$target_dir" ] && [ -f "$target_dir/config.json" ]; then
        echo "[OK] 模型已存在: $target_dir"
    else
        echo "[INFO] 下载模型: $model_name → $target_dir"
        python -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('$model_name', cache_folder='/app/models')
m.save('$target_dir')
print('[OK] 下载完成:', '$target_dir')
"
    fi
}

echo ""
echo "── 检查 Embedding 模型 ──"
download_model "BAAI/bge-small-zh-v1.5" "/app/models/bge-small-zh-v1.5"
download_model "BAAI/bge-reranker-base" "/app/models/bge-reranker-base"

# ── 2. 等待 PostgreSQL ──────────────────────
echo ""
echo "── 等待 PostgreSQL ──"
until python -c "import psycopg2; psycopg2.connect(host='${PGHOST-postgres}', port=${PGPORT-5432}, dbname='${PGDATABASE-demo}', user='${PGUSER-postgres}', password='${PGPASSWORD-postgres}').close()" 2>/dev/null; do
    echo "  等待 PostgreSQL 就绪 (${PGHOST-postgres}:${PGPORT-5432})..."
    sleep 2
done
echo "[OK] PostgreSQL 已就绪"

# ── 3. 等待 Ollama ─────────────────────────
echo ""
echo "── 等待 Ollama (${OLLAMA_API}) ──"
until curl -s "${OLLAMA_API}/api/tags" > /dev/null 2>&1; do
    echo "  等待 Ollama 就绪..."
    sleep 3
done
echo "[OK] Ollama 已就绪"

# 拉取模型（如果还没拉取）
echo ""
echo "── 检查 LLM 模型: ${LLM_MODEL-qwen2.5:4b} ──"
if curl -s "${OLLAMA_API}/api/tags" | grep -q "\"name\":\"${LLM_MODEL-qwen2.5:4b}\""; then
    echo "[OK] 模型已存在: ${LLM_MODEL-qwen2.5:4b}"
else
    echo "[INFO] 拉取模型: ${LLM_MODEL-qwen2.5:4} (可能需要几分钟)..."
    curl -s -X POST "${OLLAMA_API}/api/pull" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"${LLM_MODEL-qwen2.5:4b}\"}"
    echo ""
    echo "[OK] 模型拉取完成: ${LLM_MODEL-qwen2.5:4b}"
fi

# ── 4. 初始化 ChromaDB 向量库 ──────────────
echo ""
echo "── 检查向量库 ──"
if [ -f "/app/data/chroma/chroma.sqlite3" ]; then
    echo "[OK] ChromaDB 向量库已存在"
else
    echo "[INFO] 未检测到向量库，将在首次 RAG 请求时自动创建"
fi

# ── 5. 启动 FastAPI ────────────────────────
echo ""
echo "========================================="
echo "  启动 FastAPI 服务"
echo "========================================="
echo ""

exec python -m api.server

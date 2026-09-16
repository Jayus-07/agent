#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# vLLM 服务器一键部署脚本（Ubuntu 22.04 / 20.04，NVIDIA GPU）
# 用法:  sudo bash deploy.sh
#        WITH_EMBEDDING=1 sudo bash deploy.sh   # 同时部署 BGE-M3 嵌入服务
# 产物:  Docker + vLLM 容器（OpenAI 兼容接口 :8000/v1，带 API Key）
#        可选 + TEI 嵌入容器（BGE-M3, :8080/embed）
# ══════════════════════════════════════════════════════════════
set -euo pipefail

# ── 可按需修改的配置 ──────────────────────────────────────────
MODEL="${MODEL:-Qwen/Qwen3-8B}"          # 模型名（HuggingFace 仓库）
API_KEY="${API_KEY:-sk-$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)}"
PORT="${PORT:-8000}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"     # 显存占用比例
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"  # 上下文长度，6G/24G 卡别设太大

# 嵌入模型开关:  WITH_EMBEDDING=1 时额外部署 BGE-M3（TEI 容器）
WITH_EMBEDDING="${WITH_EMBEDDING:-0}"
EMBED_MODEL="${EMBED_MODEL:-BAAI/bge-m3}"
EMBED_PORT="${EMBED_PORT:-8080}"
EMBED_ON_CPU="${EMBED_ON_CPU:-1}"        # 1=CPU 跑嵌入(省显存,推荐) / 0=同卡 GPU

# 与 LLM 共卡时给嵌入模型留显存
if [ "$WITH_EMBEDDING" = "1" ] && [ "$EMBED_ON_CPU" != "1" ] && [ "$GPU_MEM_UTIL" = "0.90" ]; then
  GPU_MEM_UTIL="0.82"
  MAX_MODEL_LEN="${MAX_MODEL_LEN_OVERRIDE:-16384}"
fi

echo "==> 模型: $MODEL"
echo "==> 端口: $PORT (仅监听本机，配合 SSH 隧道使用)"
echo "==> API Key: $API_KEY  (请保存好)"

# ── 1. 基础检查 ───────────────────────────────────────────────
if ! command -v nvidia-smi &>/dev/null; then
  echo "!! 未检测到 NVIDIA 驱动，先装驱动:"
  echo "   sudo apt install -y nvidia-driver-550 && sudo reboot"
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

# ── 2. 安装 Docker（已装则跳过）───────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "==> 安装 Docker..."
  curl -fsSL https://get.docker.com | bash
  systemctl enable --now docker
fi

# ── 3. 防火墙：8000 端口【不】对公网放行，只走 SSH 隧道 ────────
if command -v ufw &>/dev/null; then
  ufw allow ssh        # 只放行 SSH
  ufw --force enable
  echo "==> ufw 已启用: 仅 SSH 可达，8000 端口拒绝公网直连 ✓"
fi

# ── 4. 拉起 vLLM（模型走国内镜像 hf-mirror 加速下载）──────────
mkdir -p /opt/vllm-cache && cd /opt/vllm-cache

cat > docker-compose.yml <<EOF
services:
  vllm:
    image: vllm/vllm-openai:latest
    container_name: vllm
    restart: unless-stopped
    ports:
      - "127.0.0.1:${PORT}:8000"      # 只绑 127.0.0.1，公网摸不到
    environment:
      - HF_ENDPOINT=https://hf-mirror.com   # 国内服务器加速下载模型
      - VLLM_API_KEY=${API_KEY}
    volumes:
      - /opt/vllm-cache:/root/.cache/huggingface
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    command: >
      --model ${MODEL}
      --max-model-len ${MAX_MODEL_LEN}
      --gpu-memory-utilization ${GPU_MEM_UTIL}
      --enable-prefix-caching
      --api-key ${API_KEY}
EOF

COMPOSE_FILES="-f docker-compose.yml"

# ── 4b. 可选：BGE-M3 嵌入服务（TEI 容器）──────────────────────
if [ "$WITH_EMBEDDING" = "1" ]; then
  if [ "$EMBED_ON_CPU" = "1" ]; then
    echo "==> 部署嵌入模型 $EMBED_MODEL (CPU 模式，显存全留给 LLM)"
    cat > tei-compose.yml <<EOF
services:
  tei:
    image: ghcr.io/huggingface/text-embeddings-inference:cpu-1.7
    container_name: tei
    restart: unless-stopped
    ports:
      - "127.0.0.1:${EMBED_PORT}:80"
    volumes:
      - /opt/vllm-cache/tei:/data
    command: >
      --model-id ${EMBED_MODEL}
      --hf-hub-mirror https://hf-mirror.com
EOF
  else
    echo "==> 部署嵌入模型 $EMBED_MODEL (GPU 同卡模式，vLLM 显存已降至 ${GPU_MEM_UTIL})"
    cat > tei-compose.yml <<EOF
services:
  tei:
    image: ghcr.io/huggingface/text-embeddings-inference:latest
    container_name: tei
    restart: unless-stopped
    ports:
      - "127.0.0.1:${EMBED_PORT}:80"
    volumes:
      - /opt/vllm-cache/tei:/data
    command: >
      --model-id ${EMBED_MODEL}
      --hf-hub-mirror https://hf-mirror.com
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
EOF
  fi
  COMPOSE_FILES="$COMPOSE_FILES -f tei-compose.yml"
fi

docker compose $COMPOSE_FILES up -d

echo "==> 部署完成。首次启动需下载模型，观察日志:"
echo "    cd /opt/vllm-cache && docker compose logs -f"
echo ""
echo "==> 服务就绪后在【本地开发机】建隧道:"
if [ "$WITH_EMBEDDING" = "1" ]; then
  echo "    ssh -L ${PORT}:localhost:${PORT} -L ${EMBED_PORT}:localhost:${EMBED_PORT} \$(whoami)@服务器IP -N"
else
  echo "    ssh -L ${PORT}:localhost:${PORT} \$(whoami)@服务器IP -N"
fi
echo "    LLM   : http://localhost:${PORT}/v1"
if [ "$WITH_EMBEDDING" = "1" ]; then
  echo "    Embed : http://localhost:${EMBED_PORT}/embed  (POST {\"inputs\":\"文本\"})"
fi
echo "    API Key: ${API_KEY}" | tee /opt/vllm-cache/API_KEY.txt

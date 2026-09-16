# vLLM 服务器部署包

租好 GPU 服务器（Ubuntu + NVIDIA 卡）后，三步上线：

## 1. 服务器上执行

```bash
# 先装 NVIDIA 驱动（如果 nvidia-smi 不可用）
sudo apt update && sudo apt install -y nvidia-driver-550 && sudo reboot

# 只部署 LLM
sudo MODEL=Qwen/Qwen3-8B bash deploy.sh

# LLM + BGE-M3 嵌入服务（默认 CPU 跑嵌入，显存全留给 LLM）
sudo WITH_EMBEDDING=1 MODEL=Qwen/Qwen3-32B-AWQ bash deploy.sh

# 嵌入模型也放 GPU（脚本自动把 vLLM 显存占比降到 0.82 并压上下文长度）
sudo WITH_EMBEDDING=1 EMBED_ON_CPU=0 bash deploy.sh
```

脚本做了什么：装 Docker → 拉起 vLLM 容器（OpenAI 兼容接口）→ 模型走 hf-mirror 国内加速 → **8000 端口只绑 127.0.0.1，不对公网开放** → 生成 API Key 存到 `/opt/vllm-cache/API_KEY.txt`。`WITH_EMBEDDING=1` 时额外起 TEI 容器跑 BGE-M3（`:8080/embed`，同样只绑 127.0.0.1）。

## 2. 本地开发机建 SSH 隧道

```bash
ssh -L 8000:localhost:8000 -L 8080:localhost:8080 user@服务器IP -N
```

保持这个窗口开着（或用 `autossh` 常驻）。没部署嵌入服务就去掉 8080 那段。

## 3. 本地代码接入

```python
client = OpenAI(base_url="http://localhost:8000/v1", api_key="<deploy.sh输出的Key>")
```

或在 agent 项目里设环境变量：

```bash
export OPENAI_API_BASE=http://localhost:8000/v1
export OPENAI_API_KEY=<Key>
```

## 常用改配

| 需求 | 改法 |
|---|---|
| 换模型 | `MODEL=Qwen/Qwen3-14B-AWQ bash deploy.sh`（24G 卡推荐量化版） |
| 加嵌入服务 | `WITH_EMBEDDING=1`（默认 CPU；`EMBED_ON_CPU=0` 放 GPU） |
| 换嵌入模型 | `EMBED_MODEL=BAAI/bge-reranker-v2-m3` 等 TEI 支持的模型 |
| 显存不够/OOM | `MAX_MODEL_LEN=16384` 或降 `GPU_MEM_UTIL=0.85` |
| 看日志 | `docker compose logs -f`（在 /opt/vllm-cache） |
| 重启服务 | `docker compose -f docker-compose.yml -f tei-compose.yml restart` |

## 嵌入服务调用示例（BGE-M3）

```python
import requests
resp = requests.post(
    "http://localhost:8080/embed",      # 已过 SSH 隧道
    json={"inputs": "要向量化的文本"},
).json()
vector = resp[0]                        # 1024 维向量
```

## 安全要点

- 8000 端口绑定在 127.0.0.1 + ufw 仅放行 SSH → 公网无法直连，只能走隧道
- API Key 即使隧道泄露也有第二道门
- AutoDL 这类平台在控制台安全组里同样**不要**放行 8000

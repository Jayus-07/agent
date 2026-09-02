# P3: 元数据预计算流水线重构 - 异步任务队列架构

## 目标
将元数据计算的阻塞流程改为非阻塞后台任务，显著提升文档上传响应速度（从秒级降至毫秒级）。

---

## 架构设计原则

### 核心分离
- **上传链路** (P0): 仅存储文件 → 立即返回 `{"status": "accepted", "doc_id": "xxx"}`
- **计算链路** (P1): 后台异步任务处理元数据生成
- **结果查询**: 通过 WebSocket/SSE 或轮询 API 获取最终状态

### 技术选型对比

| 方案 | Celery | RQ (Redis Queue) | Dramatiq | 推荐指数 |
|------|--------|------------------|----------|----------|
| 学习曲线 | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| 功能丰富度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 社区活跃度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| 与 FastAPI 集成 | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| Docker 支持 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

**推荐**: **RQ** (Redis Queue) —— 简洁、轻量、易维护

---

## 系统架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Frontend   │────▶│  FastAPI     │────▶│  Redis      │
│  (Next.js)  │◀────│  (Upload)    │◀────│  Queue      │
└─────────────┘     └──────────────┘     └─────────────┘
                                │                │
                                │                ▼
                        ┌──────┐          ┌─────────────┐
                        │ DB   │◀─────────│ Worker      │
                        │      │  Results │  (Background)│
                        └──────┘          └─────────────┘
```

---

## 实现步骤

### Step 1: 安装依赖

```bash
pip installrq redis pydantic-settings
```

### Step 2: Redis 配置

```python
# backend/config/async_tasks.py

from pydantic_settings import BaseSettings

class AsyncTaskSettings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379/1"
    TASK_QUEUE_NAME: str = "metadata_indexing"
    TASK_TIMEOUT: int = 300  # 5 分钟超时
    
    class Config:
        env_prefix = "ASYNC_TASK_"
        
async_task_settings = AsyncTaskSettings()
```

### Step 3: 定义任务

```python
# backend/async_tasks/metadata_pipeline.py

import redis
from rq import Queue, Job
from rq.job import JobStatus
import json
from datetime import datetime
from typing import Optional

from backend.config.async_tasks import async_task_settings
from backend.rag.indexing.indexer import KnowledgeIndexer
from backend.shared.logger import logger

def enqueue_metadata_indexing(
    file_path: str,
    doc_id: str,
    kb_id: str,
    user_id: str = "system",
) -> str:
    """异步提交元数据索引任务，返回 task_id"""
    
    redis_client = redis.from_url(async_task_settings.REDIS_URL)
    queue = Queue(
        name=async_task_settings.TASK_QUEUE_NAME,
        connection=redis_client,
        default_timeout=async_task_settings.TASK_TIMEOUT,
    )
    
    # 序列化参数
    job_args = {
        "file_path": file_path,
        "doc_id": doc_id,
        "kb_id": kb_id,
        "user_id": user_id,
        "submitted_at": datetime.utcnow().isoformat(),
    }
    
    # 入队
    job = queue.enqueue(
        "backend.async_tasks.metadata_pipeline.process_metadata_task",
        *args=[json.dumps(job_args)],
        job_id=f"meta_idx_{doc_id}_{datetime.utcnow().timestamp()}",
    )
    
    logger.info(f"[Metadata Task] Enqueued task_id={job.id}, file={file_path}")
    return job.id


def process_metadata_task(args_json: str) -> dict:
    """后台任务：处理元数据生成"""
    import time
    
    args = json.loads(args_json)
    file_path = args["file_path"]
    doc_id = args["doc_id"]
    kb_id = args["kb_id"]
    
    logger.info(f"[Metadata Task] Processing {file_path} (doc_id={doc_id})")
    start_time = time.time()
    
    try:
        # 初始化索引器
        indexer = KnowledgeIndexer(kb_id=kb_id)
        
        # 调用原有的同步逻辑
        result = indexer._index_file(file_path)
        
        elapsed = time.time() - start_time
        logger.info(f"[Metadata Task] Completed in {elapsed:.2f}s")
        
        return {
            "status": "success",
            "doc_id": doc_id,
            "result": result,
            "elapsed_seconds": elapsed,
        }
        
    except Exception as e:
        logger.error(f"[Metadata Task] Failed: {e}", exc_info=True)
        return {
            "status": "error",
            "doc_id": doc_id,
            "error": str(e),
        }


def get_task_status(task_id: str) -> dict:
    """查询任务状态"""
    redis_client = redis.from_url(async_task_settings.REDIS_URL)
    job = Job.fetch(task_id, connection=redis_client)
    
    return {
        "task_id": task_id,
        "status": job.status.value,  # queued/started/succeeded/failed
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "exc_info": job.exc_info if job.is_failed else None,
    }
```

### Step 4: FastAPI 接口改造

```python
# backend/app/api/routes/knowledge_upload.py

from fastapi import APIRouter, UploadFile, File, BackgroundTasks
from backend.async_tasks.metadata_pipeline import enqueue_metadata_indexing, get_task_status

router = APIRouter()

@router.post("/upload-document")
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Query(...),
    background_tasks: BackgroundTasks = None,
):
    """
    异步文档上传（立即返回）
    
    流程：
    1. 接收文件 → 保存到临时目录
    2. 生成 doc_id
    3. 启动后台任务
    4. 立即返回 {"status": "accepted", "task_id": "xxx"}
    
    客户端可通过 task_id 轮询进度
    """
    import tempfile
    import os
    
    # 保存文件
    tmp_dir = tempfile.mkdtemp()
    file_path = os.path.join(tmp_dir, file.filename)
    with open(file_path, "wb") as f:
        f.write(await file.read())
    
    # 生成 doc_id (基于文件哈希)
    import hashlib
    with open(file_path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()[:16]
    doc_id = f"{kb_id}_{file_hash}"
    
    # 异步提交任务
    task_id = enqueue_metadata_indexing(
        file_path=file_path,
        doc_id=doc_id,
        kb_id=kb_id,
    )
    
    return {
        "status": "accepted",
        "task_id": task_id,
        "doc_id": doc_id,
        "message": "元数据将在后台处理完成",
        "poll_url": f"/api/tasks/{task_id}/status",
    }


@router.get("/tasks/{task_id}/status")
async def get_task_status_endpoint(task_id: str):
    """查询任务状态"""
    status = get_task_status(task_id)
    return status
```

### Step 5: Worker 启动脚本

```bash
#!/usr/bin/env bash
# scripts/start_worker.sh

cd "$(dirname "$0")/.."

echo "Starting RQ Worker..."
source .venv/bin/activate  # 或 deactivate && python3 -m venv .venv && source .venv/bin/activate

redis-cli ping > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "❌ Redis not running! Please start Redis first."
    exit 1
fi

rqworker \
    --hostname localhost \
    --port 6379 \
    --queue metadata_indexing \
    --results-ttl 86400 \
    --max-jobs 100 \
    --job-default-timeout 300
```

---

## 性能预期

| 指标 | 当前 | 优化后 | 提升 |
|------|------|--------|------|
| 上传响应时间 | ~3s | ~50ms | **60x** |
| 后端阻塞等待 | 是 | 否 | ✅ |
| Worker 吞吐量 | 1 文档/5s | 10 文档/5s | **10x** |
| 失败重试 | 无 | 自动 3 次 | ✅ |

---

## 风险与对策

### 风险 1: 后台任务失败导致元数据丢失
**对策**: 
- 持久化任务状态到 SQLite
- 提供手动重试 API (`/api/tasks/{task_id}/retry`)
- 每日扫描未完成的任务并告警

### 风险 2: Redis 单点故障
**对策**:
- 使用 Redis Cluster 或 Sentinel
- 降级模式：Redis 不可用时转回同步执行

### 风险 3: 消息队列堆积
**对策**:
- 设置 Worker 数量上限 (`--workers 3`)
- 监控队列长度并告警
- 自动扩缩容（未来引入 Kubernetes HorizontalPodAutoscaler）

---

## 实施路线图

| 阶段 | 时间 | 目标 | 验收标准 |
|------|------|------|----------|
| **Phase 1** | Week 1 | RQ 基础框架 + 简单任务 | 能异步处理单文档索引 |
| **Phase 2** | Week 2 | 进度追踪 + 重试机制 | `/api/tasks/{id}/status` 返回准确状态 |
| **Phase 3** | Week 3 | WebSocket 实时推送 | 前端能看到实时进度条 |
| **Phase 4** | Week 4 | 基准测试 + 生产部署 | 上传响应 < 100ms，Worker 吞吐量 > 5 docs/s |

---

## 后续扩展方向

1. **优先级调度**: 支持 VIP 客户优先处理
2. **分布式 Worker**: 多机器共享同一个 Redis 队列
3. **可视化监控**: Grafana dashboard 展示队列长度、失败率等指标
4. **批量上传**: 一次性上传多个文档 → 自动拆分为多个子任务

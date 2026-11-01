"""Redis 专用测试库的统一接线。

flushdb 是整库操作——xdist 多 worker 共享同一 db 会互删并行用例的键
（v19-v22 CI test_heartbeat_renews_admission_token 恒红实证：
test_stop_g4_matrix / test_task_admission 的 flushdb 清掉并行 worker
心跳中的 admission 键）。db 号按 worker 分片后各 worker 的 flush 互不
可见；Redis 默认 databases=16（0-15），4 worker 取 15-idx 不越界。
"""
from __future__ import annotations

import os


def redis_test_db_url(url: str) -> str:
    """REDIS_URL → 专用测试库 + host 钉死 127.0.0.1 + xdist worker 分片。

    host 显式 127.0.0.1：localhost 会在 ::1/127.0.0.1 间轮换解析，偶发连
    到不同端点（本机实测）。
    """
    base = url.rsplit("/", 1)[0]
    if "@localhost:" in base:
        base = base.replace("@localhost:", "@127.0.0.1:")
    worker = os.getenv("PYTEST_XDIST_WORKER", "")
    idx = int(worker[2:]) if worker.startswith("gw") and worker[2:].isdigit() else 0
    return f"{base}/{15 - idx}"

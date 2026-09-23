"""observability/worker_metrics.py — Worker 进程指标暴露（Phase2-F）。

背景：Prometheus 指标注册在进程内存，app /metrics 看不到独立 worker
容器/进程的计数器（无 multiprocess 共享目录，跨容器也不可共享）。
celery-exporter 只覆盖 broker 事件级指标，不含 lease/fenced/recovery
等运行时语义。

方案（prometheus_client 原生路径，不引入新服务）：
- docker-compose 在 worker 服务设 PROMETHEUS_MULTIPROC_DIR（必须在任何
  prometheus_client 指标创建前可见，prefork 父子进程都写文件）+
  WORKER_METRICS_PORT（默认 9809，仅容器网络内监听，不发布宿主机）。
- 主进程 worker_init：清理上次运行的残留 multiproc 文件（此时尚未 fork，
  清理安全）→ 启动聚合 HTTP server（MultiProcessCollector 读目录）。
- 子进程 worker_process_init：确保目录存在。
- 全程 best-effort：任何失败只记 warning，绝不阻塞 worker 启动/执行
  （observability 不做业务 blocker；authorization/lease 不是本模块职责）。
"""
from __future__ import annotations

import os

from backend.shared.logger import logger

DEFAULT_PORT = 9809


def _multiproc_dir() -> str:
    return os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()


def _metrics_port() -> int:
    raw = os.getenv("WORKER_METRICS_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    port = int(raw)  # 非整数 fail-fast（配置错误应尽早暴露）
    if not (0 < port < 65536):
        raise ValueError(f"WORKER_METRICS_PORT 非法: {port}")
    return port


def render_worker_metrics() -> bytes:
    """聚合 multiproc 目录产出 Prometheus 文本（供 HTTP handler 与测试）。"""
    from prometheus_client import CollectorRegistry, generate_latest
    from prometheus_client.multiprocess import MultiProcessCollector

    registry = CollectorRegistry()
    MultiProcessCollector(registry, _multiproc_dir())
    return generate_latest(registry)


def _start_aggregate_server() -> None:
    """在 worker 主进程启动聚合 HTTP server（守护线程，随进程退出）。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server 接口约定
            if self.path not in ("/metrics", "/"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = render_worker_metrics()
            except Exception:  # noqa: BLE001 — 目录异常时返回空而不是 500 循环
                logger.warning("[WorkerMetrics] 聚合失败", exc_info=True)
                body = b""
            self.send_response(200)
            self.send_header("Content-Type",
                             "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # 静默 access log（scrape 每 15s 一条）
            return

    port = _metrics_port()
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    server.daemon_threads = True
    import threading

    threading.Thread(target=server.serve_forever, daemon=True,
                     name=f"worker-metrics:{port}").start()
    logger.warning("[WorkerMetrics] multiprocess 指标端点已启动 :%s（dir=%s）",
                   port, _multiproc_dir())


def _cleanup_multiproc_dir() -> None:
    """清理 multiproc 目录残留文件。

    ⚠️ 时序红线（Phase2-G 两次实机复现）：
    1. **晚于指标构造**的 unlink：句柄写已删 inode，inc 静默丢失；
    2. **fork 模型下任何自动清理**都会误伤：billiard pool 子进程会
       re-import 模块链，即使挂在「最早 import 点」也会在子进程打开
       文件后再次执行 → unlink 活跃句柄（/proc/<pid>/fd 呈 deleted）。
    因此运行链路上**没有任何自动清理**；残留文件跨重启保留（counter
    语义天然兼容，dead pid 文件量级固定）。本函数仅供**停机窗口内的人工
    运维**显式调用。
    """
    d = _multiproc_dir()
    if not d:
        return
    try:
        for name in os.listdir(d):
            if name.startswith(("counter_", "histogram_", "gauge_",
                                "summary_", "info_")):
                try:
                    os.unlink(os.path.join(d, name))
                except OSError:
                    pass
    except FileNotFoundError:
        os.makedirs(d, exist_ok=True)


def cleanup_multiproc_dir_early() -> None:
    """人工运维清理入口（仅限停机窗口；运行链路绝不自动调用）。

    全程 best-effort：失败只记 warning。
    """
    try:
        _cleanup_multiproc_dir()
    except Exception:  # noqa: BLE001 — 观测失败不影响任务执行（§34）
        logger.warning("[WorkerMetrics] multiproc 目录清理失败（跳过）",
                       exc_info=True)


def start_worker_metrics() -> None:
    """worker_init 入口：起聚合 HTTP 服务（不做任何目录清理，见上）。"""
    try:
        if not _multiproc_dir():
            logger.warning("[WorkerMetrics] 未配置 PROMETHEUS_MULTIPROC_DIR，"
                           "worker 运行时指标仅存进程内存，不可抓取")
            return
        _start_aggregate_server()
    except Exception:  # noqa: BLE001 — 观测失败不影响任务执行（§34）
        logger.warning("[WorkerMetrics] 指标端点启动失败（best-effort 跳过）",
                       exc_info=True)


def ensure_multiproc_dir() -> None:
    """worker_process_init 入口：子进程确保目录存在（best-effort）。"""
    try:
        d = _multiproc_dir()
        if d:
            os.makedirs(d, exist_ok=True)
    except Exception:  # noqa: BLE001
        logger.warning("[WorkerMetrics] multiproc 目录创建失败", exc_info=True)


# ── Celery 信号挂载（celery_app include 本模块时生效）────────────
try:
    from celery.signals import worker_init, worker_process_init

    @worker_init.connect
    def _on_worker_init(**_kwargs):
        start_worker_metrics()

    @worker_process_init.connect
    def _on_worker_process_init(**_kwargs):
        ensure_multiproc_dir()
except Exception:  # noqa: BLE001 — 非 worker 进程（beat/API/eager 测试）无 celery
    pass

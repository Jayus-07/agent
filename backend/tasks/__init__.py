"""tasks — Celery 异步任务编排层。

边界约束（架构红线）：
- Celery 只负责任务调度与投递，不承载 Agent 执行状态；
- Agent 状态恢复完全依赖 LangGraph PostgresSaver checkpoint；
- Skill 层 / 域图层不感知本模块（不 import backend.tasks）。
"""
# ⚠️ Phase2-G P1 教训（实机复现）：**启动期禁止清理 multiproc 目录**。
# 曾把残留清理挂在本包 import（认为它是 celery 进程最早执行点），实测：
# billiard fork 出的 pool 子进程会再次执行本包 import → cleanup 把
# 子进程已打开的 counter_<pid>.db unlink（/proc/<pid>/fd 大量
# "(deleted)"）→ 之后所有运行时指标 inc 写幽灵 inode，Prometheus 聚合
# 为空——task_terminal/enqueued/lease 等全部静默丢失。
# 结论：残留文件**跨重启保留**（Prometheus counter 语义天然兼容：
# rate() 不受重启影响；dead pid 文件量级固定）。显式运维清理请手动调用
# worker_metrics.cleanup_multiproc_dir_early()（仅限停机窗口）。
from backend.tasks.celery_app import celery_app

__all__ = ["celery_app"]

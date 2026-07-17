"""
data_collection/scheduler.py — 统一调度入口

Phase 1 (MVP):
  - 提供任务注册 + 手动触发接口
  - run_now(name) 同步执行，返回 CollectResult
  - run_all() 按注册顺序执行全部任务

Phase 2:
  - 接入 APScheduler 的 AsyncIOScheduler
  - register_daily / register_hourly 实际启动定时任务

用法:
    scheduler = Scheduler()
    scheduler.register(
        name="采集商品数据",
        task=lambda: pipeline.run(source="static://datasets/products.json", ...),
    )
    result = scheduler.run_now("采集商品数据")
    results = scheduler.run_all()
"""

from typing import Any, Callable

from backend.data_collection.pipeline import CollectResult
from backend.shared.logger import logger


class Scheduler:
    """统一调度入口 — 注册 + 手动触发

    所有采集任务注册在此，后续只需在注册处新增任务即可。
    """

    def __init__(self):
        self._jobs: dict[str, dict[str, Any]] = {}

    def register(
        self,
        name: str,
        task: Callable[[], CollectResult],
        trigger: str = "manual",
        description: str = "",
        **trigger_kwargs: Any,
    ) -> str:
        """注册采集任务

        Args:
            name: 任务名称（唯一标识）
            task: 可调用对象，返回 CollectResult
            trigger: "manual" | "cron" | "interval"（Phase 2 启用）
            description: 任务描述
            **trigger_kwargs: cron/interval 参数（Phase 2 启用）

        Returns:
            str: 任务名称
        """
        self._jobs[name] = {
            "name": name,
            "task": task,
            "trigger": trigger,
            "description": description,
            "trigger_kwargs": trigger_kwargs,
        }
        logger.info(f"[Scheduler] 注册任务: {name} (trigger={trigger})")
        return name

    def run_all(self) -> dict[str, CollectResult]:
        """按注册顺序执行全部任务，返回 {name: CollectResult}"""
        results: dict[str, CollectResult] = {}
        for name, job in self._jobs.items():
            logger.info(f"[Scheduler] 执行 [{len(results)+1}/{len(self._jobs)}]: {name}")
            try:
                results[name] = job["task"]()
            except Exception as e:
                logger.error(f"[Scheduler] {name} 失败: {e}")
                results[name] = CollectResult(
                    task_id="scheduler",
                    source=name,
                    status="failed",
                    error=str(e),
                )
        return results

    def remove(self, name: str) -> None:
        """移除已注册的任务"""
        if name in self._jobs:
            del self._jobs[name]
            logger.info(f"[Scheduler] 移除任务: {name}")

    # ── Phase 2 预留 ──
    def start(self) -> None:
        """启动 APScheduler（Phase 2 实现）"""
        raise NotImplementedError("Phase 2: 接入 APScheduler")

    def stop(self) -> None:
        """停止 APScheduler（Phase 2 实现）"""
        raise NotImplementedError("Phase 2: 接入 APScheduler")

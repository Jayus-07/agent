"""tasks/eval_sampling_tasks.py — 在线流量采样评测每日 beat 任务（eval-closure B3）。

薄壳：核心逻辑全部在 backend/evaluation/online_sampler.py（与 Celery 解耦，
可测试、可手动触发）。本模块只做任务注册与日志包装。

调度：每日 01:30 UTC 由 celery_app.conf.beat_schedule 注册（crontab），
避开 02:00 rag-index-reconcile 与 06:05+ 客服报表窗口。
开关：EVAL_ONLINE_SAMPLER_ENABLED 默认关（judge 每条一次 LLM 调用，消耗
eval_gen 配额）——关闭时任务返回 skipped 正常结束，不算失败不重试。
幂等：run_id 按天固定（online-YYYYMMDD），重跑覆盖同键（records/samples 均upsert）。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(name="evaluation.online_sampling", acks_late=True)
def evaluation_online_sampling() -> dict:
    """每日在线采样评测：抽近期 trace → judge 打分 → eval_run_records 落库。

    skipped（disabled / trace 源不可达 / 无可评样本）是正常业务终态而非失败：
    采样默认关、环境无流量时每天空跑，显式抛错会造成无意义重试与告警噪音。
    持久化失败（persisted=False）同理——采样台账软失败已在 sampler 内记录
    warning，重跑（次日或手动）按 run_id 幂等覆盖补齐。
    """
    from backend.evaluation.online_sampler import run_online_sampling

    result = run_online_sampling()
    logger.info("[EvalSamplingTask] online sampling result: %s", result)
    return result

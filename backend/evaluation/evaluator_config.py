"""评测器（Judge/RAGAS）配置快照（C4-1/C4-2/C7-4，JUDGE-03/RAGAS-11/REPRO-07）。

把 judge / RAGAS 的模型、供应商、温度、prompt 版本与哈希、ragas 版本、
重试/超时等运行参数收集为可快照 dict，随 report.metadata 与统一
evaluation_snapshot_hash（C3-3）落盘——「评这个分的时候用的什么尺度」
必须可追溯，judge prompt 偷偷改过 = 评测尺度变了。

随机性口径（REPRO-07）：当前所有 provider 均不支持 seed 参数，
``seed_support`` 显式记 false——不支持就是不支持，不伪造 seed 值。
"""
from __future__ import annotations

from typing import Any

from backend.shared.logger import logger

SEED_SUPPORT = False  # REPRO-07：无任何 provider 支持 seed，显式口径


def collect_judge_config() -> dict[str, Any]:
    """自研 LLM-as-Judge（judge.py）的调用参数快照。

    judge 走 backend.infra.llm 主链路（main 角色），温度即全局
    LLM_TEMPERATURE；prompt = evaluation.judge.user（版本化 + 模板哈希）。
    全部软失败：单项缺失记 unknown，不阻塞评测。
    """
    config: dict[str, Any] = {
        "kind": "self_judge",
        "temperature_source": "LLM_TEMPERATURE",
        "seed_support": SEED_SUPPORT,
    }
    try:
        from backend.config import LLM_TEMPERATURE

        config["temperature"] = LLM_TEMPERATURE
    except Exception:
        config["temperature"] = "unknown"
    try:
        from backend.infra.llm.proxy import get_active_model_name

        config["model"] = get_active_model_name()
    except Exception:
        config["model"] = "unknown"
    try:
        from backend.config import model_roles

        role = model_roles.resolve_raw("main") or {}
        config["provider"] = role.get("provider", "unknown")
    except Exception:
        config["provider"] = "unknown"
    config.update(_judge_prompt_snapshot())
    return config


def _judge_prompt_snapshot() -> dict[str, Any]:
    """evaluation.judge.user 的版本与模板哈希（PG 权威，软失败）。"""
    out: dict[str, Any] = {
        "prompt_key": "evaluation.judge.user",
        "prompt_version": "unknown",
        "prompt_hash": "",
    }
    try:
        import asyncio

        from backend.prompts.service import prompt_service

        version = prompt_service.current_versions().get("evaluation.judge.user")
        out["prompt_version"] = version
        if version is not None:
            hash_value = asyncio.run(
                prompt_service.get_template_hash("evaluation.judge.user", int(version))
            )
            out["prompt_hash"] = str(hash_value)
    except Exception as e:  # noqa: BLE001 — 快照缺失不阻塞评测
        logger.debug(f"[eval-config] judge prompt 快照失败: {e}")
    return out


def collect_ragas_config(level: str = "standard") -> dict[str, Any]:
    """RAGAS 批量评估的版本与配置快照（RAGAS-11）。

    judge 模型 = eval_gen 角色（temperature 固定 0，ragas_bridge 口径）；
    版本号经 importlib.metadata 读取实际安装的 ragas 包。
    """
    config: dict[str, Any] = {
        "kind": "ragas",
        "level": level,
        "temperature": 0,
        "temperature_source": "ragas_bridge.get_eval_chat_model(temperature=0)",
        "seed_support": SEED_SUPPORT,
        "case_timeout_s": _ragas_case_timeout(),
        "metric_retries": _ragas_metric_retries(),
    }
    try:
        from importlib.metadata import version as pkg_version

        config["ragas_version"] = pkg_version("ragas")
    except Exception:
        config["ragas_version"] = "not_installed"
    try:
        from backend.evaluation.generation import resolve_eval_model

        model_name, provider = resolve_eval_model()
        config["model"] = model_name
        config["provider"] = provider
    except Exception as e:  # noqa: BLE001
        config["model"] = "unknown"
        config["provider"] = "unknown"
        logger.debug(f"[eval-config] eval_gen 解析失败: {e}")
    return config


def _ragas_case_timeout() -> int:
    try:
        from backend.evaluation.ragas_bridge import _RAGAS_CASE_TIMEOUT

        return int(_RAGAS_CASE_TIMEOUT)
    except Exception:
        return 0


def _ragas_metric_retries() -> int:
    try:
        from backend.evaluation.ragas_bridge import _RAGAS_METRIC_RETRIES

        return int(_RAGAS_METRIC_RETRIES)
    except Exception:
        return 0


__all__ = [
    "collect_judge_config",
    "collect_ragas_config",
    "SEED_SUPPORT",
]

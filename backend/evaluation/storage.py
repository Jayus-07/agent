"""评估结果持久化 — V1.0 重构新增。

设计要点:
1. 双写: PostgreSQL（聚合指标，用于趋势图）+ 文件系统（完整轨迹，用于追溯）
2. meta.json 强制包含 git_sha / dataset_version / prompt_versions，确保问题可追溯
3. run_id 格式: {timestamp} 便于排序和去重

可移植性：此文件仅依赖 stdlib + 已有项目模块（git/Path）。新项目复制后调整
DATA_ROOT 与 PGSQL 连接即可。
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary

# 数据根目录 — 相对项目根目录
DATA_ROOT = Path("data/eval_runs")


def get_git_sha() -> str:
    """获取当前 git commit SHA（短 hash，7 位）。"""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True, cwd=os.getcwd(),
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def get_git_branch() -> str:
    """获取当前 git 分支名。"""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, text=True, cwd=os.getcwd(),
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def collect_prompt_versions() -> dict[str, str]:
    """扫描 prompts/ 目录，提取每个 prompt 文件的版本号（文件名 .vN.yaml 模式）。"""
    prompts_dir = Path("backend/prompts")
    if not prompts_dir.exists():
        return {}
    versions: dict[str, str] = {}
    for path in prompts_dir.rglob("*.yaml"):
        # 文件名格式: {name}.v{N}.yaml
        name = path.stem  # 去掉 .yaml
        if ".v" in name:
            base, version = name.rsplit(".v", 1)
            versions[base] = f"v{version}"
        else:
            versions[name] = "unversioned"
    return versions


def collect_env_info() -> dict[str, str]:
    """收集环境信息（用于追溯）。"""
    env = {
        "python_version": subprocess.check_output(
            ["python", "--version"], stderr=subprocess.STDOUT, text=True,
        ).strip() if _cmd_exists("python") else "unknown",
        "trigger": os.getenv("EVAL_TRIGGER", "manual"),
        "ci_pr": os.getenv("GITHUB_PR_NUMBER", ""),
    }
    return env


def _cmd_exists(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def get_dataset_version(module: str) -> str:
    """从数据集 JSON 顶部读取 version 字段。"""
    from backend.evaluation.dataset import DATASET_DIR
    for fname in (f"{module}_v2.json", f"{module}.json"):
        path = DATASET_DIR / fname
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return str(data.get("version", "1.0"))
            except (json.JSONDecodeError, OSError):
                continue
    return "1.0"


def make_run_id() -> str:
    """生成 run_id — 时间戳格式: 2026-08-14T10-00-00。"""
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def persist_report(report: EvalReport) -> Path:
    """持久化 EvalReport 到文件系统。

    目录结构:
        data/eval_runs/{run_id}/
            report.json            # 全量报告
            per_case/{case_id}.json  # 每条 case 完整轨迹
            meta.json              # git_sha / dataset_version / prompt_versions
    """
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    run_id = make_run_id()
    run_dir = DATA_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. report.json — EvalReport 全量序列化
    report_dict = report.model_dump(mode="json")
    report_dict["run_id"] = run_id
    (run_dir / "report.json").write_text(
        json.dumps(report_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 2. per_case/{case_id}.json — 单 case 完整轨迹
    per_case_dir = run_dir / "per_case"
    per_case_dir.mkdir(exist_ok=True)
    for r in report.results:
        (per_case_dir / f"{r.case_id}.json").write_text(
            r.model_dump_json(indent=2, exclude={"expected"}),  # expected 已在 report
            encoding="utf-8",
        )

    # 3. meta.json — 追溯元数据
    meta = {
        "git_sha": get_git_sha(),
        "git_branch": get_git_branch(),
        "dataset_version": {
            m.module: get_dataset_version(m.module) for m in report.summaries
        },
        "prompt_versions": collect_prompt_versions(),
        "env": collect_env_info(),
        "run_at": datetime.now().isoformat(),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[storage] report persisted to: {run_dir}")
    return run_dir


def save_aggregates_to_db(summaries: list[ModuleSummary]) -> None:
    """保存聚合指标到 PostgreSQL（用于趋势图）。

    失败不抛异常 — DB 写失败不影响 FS 持久化。
    """
    try:
        from backend.observability.metrics_store import get_metrics_store
        store = get_metrics_store()
        for s in summaries:
            store.insert_eval_summary(
                module=s.module,
                total=s.total,
                passed=s.passed,
                failed=s.failed,
                errors=s.errors,
                pass_rate=s.pass_rate,
                metrics=s.metrics,
            )
    except Exception as e:
        # DB 写失败只 warn，不阻断评估流程
        import warnings
        warnings.warn(f"[storage] DB aggregate save failed: {e}", stacklevel=2)


def load_report(run_id: str) -> tuple[EvalReport, dict[str, Any]]:
    """从文件系统加载历史报告 + meta。

    Returns:
        (EvalReport, meta_dict)
    """
    run_dir = DATA_ROOT / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"Run not found: {run_dir}")

    report = EvalReport.model_validate_json(
        (run_dir / "report.json").read_text(encoding="utf-8")
    )
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    return report, meta


def list_runs(limit: int = 20) -> list[str]:
    """列出最近 N 个 run_id。"""
    if not DATA_ROOT.exists():
        return []
    runs = sorted(
        [d.name for d in DATA_ROOT.iterdir() if d.is_dir()],
        reverse=True,
    )
    return runs[:limit]

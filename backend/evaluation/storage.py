"""评估结果持久化 — 文件系统存储。

设计要点:
1. 文件系统存储：完整轨迹，用于追溯和对比
2. meta.json 强制包含 git_sha / dataset_version / prompt_versions，确保问题可追溯
3. run_id 格式: {timestamp}-{random} 便于排序和去重，避免秒级碰撞

可移植性：此文件仅依赖 stdlib + 已有项目模块（git/Path）。新项目复制后调整
DATA_ROOT 路径即可。
"""
from __future__ import annotations

import json
import re
import os
import secrets
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary

# 数据根目录 — 绝对路径，相对于项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_ROOT = _PROJECT_ROOT / "data" / "eval_runs"


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
    """从数据集查找版本字段。

    查找顺序：
    1. datasets/{module}/manifest.json → version
    2. datasets/{module}/cases.jsonl → 首行 metadata.dataset_version
    3. legacy JSON: {module}_v2.json / {module}_test_kb.json / {module}.json
    4. 默认 "1.0"
    """
    from backend.evaluation.dataset import DATASET_DIR

    split_dir = DATASET_DIR / module

    # 1. manifest.json
    manifest_path = split_dir / "manifest.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            ver = data.get("version")
            if ver:
                return str(ver)
        except (json.JSONDecodeError, OSError):
            pass

    # 2. cases.jsonl 首行 metadata.dataset_version
    jsonl_path = split_dir / "cases.jsonl"
    if jsonl_path.exists():
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
            if first_line:
                item = json.loads(first_line)
                ver = item.get("metadata", {}).get("dataset_version")
                if ver:
                    return str(ver)
        except (json.JSONDecodeError, OSError):
            pass

    # 3. legacy JSON
    for fname in (f"{module}_v2.json", f"{module}_test_kb.json", f"{module}.json"):
        if ".deprecated." in fname:
            continue
        path = DATASET_DIR / fname
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return str(data.get("version", "1.0"))
            except (json.JSONDecodeError, OSError):
                continue
    return "1.0"


def make_run_id() -> str:
    """生成 run_id — 时间戳 + 随机后缀: 2026-08-14T10-00-00-a1b2c3。"""
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    random_suffix = secrets.token_hex(3)  # 6 位 hex
    return f"{timestamp}-{random_suffix}"


def validate_run_id(run_id: str) -> str:
    """校验 run_id 只能作为 data/eval_runs 的单层目录名使用。"""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise ValueError(f"非法评测 run_id: {run_id!r}")
    return run_id


def persist_report(report: EvalReport, run_id: str | None = None) -> Path:
    """持久化 EvalReport 到文件系统。

    目录结构:
        data/eval_runs/{run_id}/
            report.json            # 全量报告
            per_case/{case_id}.json  # 每条 case 完整轨迹
            meta.json              # git_sha / dataset_version / prompt_versions
    """
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    requested_run_id = run_id or report.metadata.get("run_id")
    run_id = validate_run_id(str(requested_run_id)) if requested_run_id else make_run_id()
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

    print(f"[storage] 报告已持久化到: {run_dir}")
    return run_dir




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

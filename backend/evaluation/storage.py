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
import platform
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

# run 级生命周期状态文件（RUN-01/07/08）：running 期间落 status.json，
# 终态由 persist_report 收口；超过阈值仍处 running 的判为 stale
# （worker 被杀/进程消失不会自动更新文件，靠时间兜底暴露「永远运行中」）。
_STATUS_FILENAME = "status.json"
STALE_RUN_AFTER_SECONDS = int(os.getenv("EVAL_RUN_STALE_AFTER_SECONDS", "21600"))  # 默认 6h
# RUN-08：心跳粒度 stale 判定（runner 心跳线程每 30s touch）。
# 有 heartbeat_at 的 running run：心跳停止超 N 秒 → stale；无 heartbeat_at
# 的存量 run 回退 started_at + STALE_RUN_AFTER_SECONDS 兜底（向后兼容）。
HEARTBEAT_STALE_SECONDS = int(os.getenv("EVAL_RUN_HEARTBEAT_STALE_SECONDS", "600"))
HEARTBEAT_INTERVAL_SECONDS = float(os.getenv("EVAL_RUN_HEARTBEAT_SECONDS", "30"))
# C2-1：协作式取消——取消请求是独立文件，不打断正在执行的样本；
# case 循环在检查点轮询，命中后剩余样本记 skip（reason=cancelled）。
_CANCEL_FILENAME = "cancel.json"


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
        # M7：触发者身份（CLI --triggered-by / admin 发起时由调用方注入）
        "triggered_by": os.getenv("EVAL_TRIGGERED_BY", ""),
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


def mark_run_status(
    run_id: str,
    status: str,
    *,
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """写入 run 生命周期状态文件（running / completed / failed / cancelled）。

    running 由 service 在 run 开始时写入（含 started_at/pid/hostname）；
    终态由 persist_report（completed/cancelled）或 evaluate 异常路径（failed）收口。
    软失败：状态文件写不进去只打日志，不影响评测主流程。
    """
    if status not in {"running", "completed", "failed", "cancelled"}:
        raise ValueError(f"非法 run 状态: {status!r}")
    try:
        run_dir = DATA_ROOT / validate_run_id(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / _STATUS_FILENAME
        now = datetime.now().isoformat()
        existing: dict[str, Any] = {}
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        # C2-3：attempt 计数——每次同 run_id 进入 running +1，终态保留，
        # 失败重试语义显式化（report.metadata.attempt_no 随报告落盘）
        if status == "running":
            data = {
                "status": status,
                "started_at": now,
                "pid": os.getpid(),
                "hostname": platform.node(),
                "attempt_no": int(existing.get("attempt_no", 0)) + 1,
            }
        else:
            # 终态收口：保留 started_at / attempt_no（跨次续跑累计），
            # heartbeat_at 是运行期信号，终态不再有意义
            data = {
                "status": status,
                "started_at": existing.get("started_at", ""),
                "attempt_no": int(existing.get("attempt_no", 1) or 1),
                "finished_at": now,
            }
            if error:
                data["error"] = error[:2000]
        if extra:
            data.update(extra)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except OSError as e:
        print(f"[storage] run 状态文件写入失败（不影响评测）: {e}")


def touch_run_heartbeat(run_id: str) -> bool:
    """C2-5/RUN-08：运行期心跳——只更新 heartbeat_at，不触碰其他字段。

    仅在状态仍为 running 时写入（读到终态即放弃，避免心跳把已收口的
    终态覆盖回 running）；软失败返回 False。
    """
    try:
        path = DATA_ROOT / validate_run_id(run_id) / _STATUS_FILENAME
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") != "running":
            return False
        data["heartbeat_at"] = datetime.now().isoformat()
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        os.replace(tmp_path, path)
        return True
    except (OSError, json.JSONDecodeError) as e:
        logger_debug(f"[storage] 心跳写入失败（不影响评测）: {e}")
        return False


def logger_debug(message: str) -> None:
    try:
        from backend.shared.logger import logger

        logger.debug(message)
    except Exception:
        pass


def request_cancel(run_id: str, requested_by: str = "") -> dict[str, Any]:
    """C2-1/RUN-03：登记协作式取消请求（写 cancel.json，幂等）。

    重复取消返回相同请求内容、不产生重复副作用（RUN-04）；
    run 不存在或目录不可写时抛 OSError 由调用方转 4xx。
    """
    run_dir = DATA_ROOT / validate_run_id(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / _CANCEL_FILENAME
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass  # 坏文件视为未请求，重写覆盖
    data = {
        "requested": True,
        "requested_at": datetime.now().isoformat(),
        "requested_by": requested_by,
    }
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    os.replace(tmp_path, path)
    return data


def is_cancel_requested(run_id: str) -> bool:
    """轮询取消请求；文件不存在/坏行一律按未取消（评测主流程不被卡死）。"""
    path = DATA_ROOT / run_id / _CANCEL_FILENAME
    if not path.exists():
        return False
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("requested"))
    except (OSError, json.JSONDecodeError):
        return False


def read_run_status(run_id: str) -> dict[str, Any] | None:
    """读取 run 状态文件；running 且失联的补算 stale 标记（RUN-07/08）。

    stale 判定两级：有心跳的 run 按 heartbeat_at + HEARTBEAT_STALE_SECONDS
    （默认 10min）；无心跳字段（存量 run / 心跳线程未运行）回退
    started_at + STALE_RUN_AFTER_SECONDS（默认 6h）。
    返回 None = 从未写过状态文件（历史 run / 状态文件丢失）。
    """
    path = DATA_ROOT / run_id / _STATUS_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("status") == "running":
        now = datetime.now()
        heartbeat_at = str(data.get("heartbeat_at") or "")
        started_at = str(data.get("started_at") or "")
        age = -1.0
        if heartbeat_at:
            try:
                age = (now - datetime.fromisoformat(heartbeat_at)).total_seconds()
            except (TypeError, ValueError):
                age = -1.0
        stale = bool(age >= 0 and age > HEARTBEAT_STALE_SECONDS)
        if age < 0:
            # 心跳缺失/不可解析 → 启动时间兜底（原有口径）
            try:
                age = (now - datetime.fromisoformat(started_at)).total_seconds()
            except (TypeError, ValueError):
                age = -1.0
            stale = bool(age >= 0 and age > STALE_RUN_AFTER_SECONDS)
            data["stale_basis"] = "started_at_fallback" if age >= 0 else "unknown"
        else:
            data["stale_basis"] = "heartbeat"
        data["age_seconds"] = int(age) if age >= 0 else None
        data["stale"] = stale
    return data


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
    legacy_dataset_versions = {
        m.module: get_dataset_version(m.module) for m in report.summaries
    }
    eval_provenance = report.metadata.get("eval_provenance") or {}
    meta = {
        "git_sha": get_git_sha(),
        "git_branch": get_git_branch(),
        # 有 provenance 时把结构化 suite/scope 写入 dataset_version；无 provenance
        # 的旧报告继续保留原来的 module → version 结构。
        "dataset_version": eval_provenance or legacy_dataset_versions,
        "dataset_version_legacy": legacy_dataset_versions,
        "eval_provenance": eval_provenance,
        # M7 口径修正：PG 权威优先（prompts 表活跃版本），DB 不可达回退
        # 下方 yaml 文件扫描（旧口径保留在 meta.prompt_versions_yaml_source）
        "prompt_versions": (
            eval_provenance.get("prompt_snapshot")
            or _collect_prompt_versions_authoritative()
        ),
        "prompt_versions_yaml_source": collect_prompt_versions(),
        "env": collect_env_info(),
        # RAGAS-01/02：本次运行的评估器模式（self / self+ragas），随快照落盘
        "evaluator_mode": report.metadata.get("evaluator_mode", ""),
        # C3-3/REPRO-09：统一快照哈希 + 模板哈希（REPRO-04）随 meta 落盘
        "evaluation_snapshot_hash": report.metadata.get("evaluation_snapshot_hash", ""),
        "prompt_template_hashes": (
            (report.metadata.get("evaluation_snapshot_inputs") or {}).get(
                "prompt_template_hashes", {},
            )
        ),
        "run_at": datetime.now().isoformat(),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4. run 级摘要落 DB 台账（M7：ai.eval_run_records，软失败不影响文件主流程）
    try:
        from backend.evaluation.run_records import (
            record_run,
            record_run_samples,
        )

        record_run(report, run_id, meta)
        # C3-2：样本级 DB 镜像（开关默认关，见 run_records 口径）
        record_run_samples(report, run_id)
    except Exception as e:  # noqa: BLE001 — 台账软失败
        print(f"[storage] eval_run_records 台账写入失败（不影响文件）: {e}")

    # 5. 生命周期终态收口（RUN-01/10）：报告与台账均已落盘。
    #    终态裁决次序（C2-1/C5-2/C5-4）：取消请求命中 → cancelled；
    #    运行期守卫已标 failed（deadline/token 熔断）→ 保持 failed 不降级；
    #    否则 completed。
    if is_cancel_requested(run_id):
        mark_run_status(run_id, "cancelled")
    else:
        current_status = (read_run_status(run_id) or {}).get("status")
        if current_status == "failed":
            mark_run_status(
                run_id, "failed",
                extra={"error": (read_run_status(run_id) or {}).get("error", "")},
            )
        else:
            mark_run_status(run_id, "completed")

    # C3-1：非 completed 终态同步回 DB 台账（record_run 默认写 completed）
    try:
        from backend.evaluation.run_records import update_run_terminal_status

        final_status = (read_run_status(run_id) or {}).get("status")
        if final_status and final_status != "completed":
            update_run_terminal_status(
                run_id, final_status,
                error=str((read_run_status(run_id) or {}).get("error", "")),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[storage] run 终态同步失败（不影响文件）: {e}")

    print(f"[storage] 报告已持久化到: {run_dir}")
    return run_dir


def _collect_prompt_versions_authoritative() -> dict[str, str]:
    """PG 权威 prompt 版本快照，失败回退 yaml 扫描（口径见 run_records D7）。"""
    try:
        from backend.evaluation.run_records import collect_prompt_snapshot

        snapshot = collect_prompt_snapshot()
        if snapshot:
            return {k: str(v) for k, v in snapshot.items()}
    except Exception:
        pass
    return collect_prompt_versions()




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

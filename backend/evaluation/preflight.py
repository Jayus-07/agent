"""evaluation/preflight.py — 评测运行前置校验（P0-03）。

为什么存在：2026-10-01~10-05 每日 RAG Regression 连续失败 5 天——suite 的
``cases_hash`` 与 canonical 行尾不一致，但该错误在 CI 的 init_db / fixture
导入**之后**才在 ``load_dataset`` 里爆出（每轮白跑约 4 分钟）。本模块把
「这次评测到底能不能跑」的校验前移到一切重动作之前，一站式完成：

    selection 非空 → suite 文件存在/可读 → kb_id 非空 → fixture_set 在allowlist
    → dataset_version 非空 → case_ids 非空且全部存在于 canonical
    → canonical 内容 hash == suite 声明（行尾归一化口径）
    → fixture catalog 可加载且包含对应 fixture_set 语料
    → baseline 语料快照可加载

输出机器判定行（CI 直接 grep）：

    EVAL_PREFLIGHT_PASS=true
    selection=regression kb_id=rag_eval_kb fixture_set=baseline
    dataset_version=5.0.0-unified case_count=29 suite_hash=87c0... git_sha=abc1234

失败：``EVAL_PREFLIGHT_PASS=false`` + ``EVAL_PREFLIGHT_FAIL_STAGE=<stage>``，
退出码 1，在真正评测开始前终止。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from typing import Any

from backend.shared.logger import logger

# 与 runners/rag.py::build_eval_scope、loader._load_suite 保持同一 allowlist（G2）
FIXTURE_SET_ALLOWLIST = {"baseline", "expanded_100", "scale_20k"}
RAG_EVAL_KB_ID = "rag_eval_kb"


class PreflightError(ValueError):
    """preflight 校验失败——携带失败阶段与人类可读原因。"""

    def __init__(self, stage: str, message: str):
        super().__init__(f"[preflight:{stage}] {message}")
        self.stage = stage
        self.message = message


@dataclass
class PreflightResult:
    """校验结果：pass=True 时 identity 字段可用于机器判定输出。"""

    passed: bool
    fail_stage: str = ""
    reason: str = ""
    identity: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        """输出 EVAL_PREFLIGHT_PASS 机器判定行（含成功时的完整身份指纹）。"""
        if not self.passed:
            return (
                f"EVAL_PREFLIGHT_PASS=false\n"
                f"EVAL_PREFLIGHT_FAIL_STAGE={self.fail_stage}\n"
                f"EVAL_PREFLIGHT_REASON={self.reason}"
            )
        pairs = " ".join(f"{k}={v}" for k, v in self.identity.items())
        return f"EVAL_PREFLIGHT_PASS=true {pairs}"


def _fail(stage: str, reason: str) -> PreflightResult:
    return PreflightResult(passed=False, fail_stage=stage, reason=reason)


def run_preflight(module: str = "rag", selection: str | None = None) -> PreflightResult:
    """suite 化评测的运行前校验。任何一环不过即返回失败结果（不抛异常）。"""
    # 1. selection 非空
    selection = (selection or "").strip()
    if not selection:
        return _fail("selection_empty", "selection 为空——suite 化评测必须显式指定 selection")

    from backend.evaluation.dataset.loader import (
        DATASET_DIR,
        _load_jsonl,
        file_content_hash,
    )

    # 2. suite 文件存在 / 3. JSON 可读
    suite_path = DATASET_DIR / module / "suites" / f"{selection}.json"
    if not suite_path.exists():
        return _fail("suite_missing", f"suite 文件不存在: {suite_path}")
    try:
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return _fail("suite_unreadable", f"suite JSON 解析失败: {suite_path} ({e})")
    if not isinstance(suite, dict):
        return _fail("suite_unreadable", f"suite 根节点必须是对象: {suite_path}")

    kb_id = str(suite.get("kb_id", "")).strip()
    fixture_set = str(suite.get("fixture_set", "")).strip()
    dataset_version = str(suite.get("dataset_version", "")).strip()
    case_ids = suite.get("case_ids")

    # 4. kb_id 非空
    if not kb_id:
        return _fail("kb_id_empty", f"suite 缺少 kb_id: {suite_path}")
    # 5. fixture_set 在 allowlist（空串在这里显式拒绝，禁止静默传播）
    if fixture_set not in FIXTURE_SET_ALLOWLIST:
        return _fail(
            "fixture_set_invalid",
            f"fixture_set={fixture_set!r} 不在 allowlist {sorted(FIXTURE_SET_ALLOWLIST)}",
        )
    # 6. dataset_version 非空
    if not dataset_version:
        return _fail("dataset_version_empty", f"suite 缺少 dataset_version: {suite_path}")
    # 7. case_ids 非空
    if not isinstance(case_ids, list) or not case_ids:
        return _fail("case_ids_empty", f"suite case_ids 为空: {suite_path}")

    # 8. canonical 存在且 case_ids 全部可解析
    canonical_path = DATASET_DIR / module / "cases.jsonl"
    if not canonical_path.exists():
        return _fail("canonical_missing", f"canonical 不存在: {canonical_path}")
    try:
        all_cases = _load_jsonl(canonical_path, default_module=module)
    except (json.JSONDecodeError, OSError) as e:
        return _fail("canonical_unreadable", f"canonical 解析失败: {canonical_path} ({e})")
    case_map = {c.id for c in all_cases}
    missing = [cid for cid in case_ids if cid not in case_map]
    if missing:
        return _fail(
            "case_ids_missing",
            f"case_ids 引用了 canonical 中不存在的用例: {missing[:10]}"
            f"（共缺 {len(missing)} 条）",
        )

    # 9. suite 声明的 cases_hash == canonical 归一化 hash（行尾漂移在此显式暴露）
    declared = str(suite.get("cases_hash", "")).strip()
    if declared:
        actual = file_content_hash(canonical_path)
        if declared != actual:
            return _fail(
                "cases_hash_mismatch",
                f"suite 声明 cases_hash={declared} 与 canonical 归一化 hash={actual} 不一致"
                f"——内容已被修改且 suite 未重新生成（禁止原地改被引用数据，DATA-01）",
            )

    # 10. fixture catalog 可加载且包含对应 fixture_set 语料
    try:
        from backend.evaluation.dataset.fixture_catalog import load_fixture_catalog

        catalog = load_fixture_catalog()
        docs = catalog.documents(fixture_set)
        if not docs:
            return _fail(
                "fixture_set_empty_in_catalog",
                f"fixture catalog 中 fixture_set={fixture_set} 无语料文档",
            )
        catalog.validate()
    except PreflightError:
        raise
    except Exception as e:  # noqa: BLE001 — catalog 层任何失败都前移到 preflight
        return _fail("fixture_catalog_error", f"fixture catalog 校验失败: {e}")

    # 11. baseline 语料快照可加载（baseline 检索按 version 过滤，快照缺失=运行中才炸）
    if fixture_set == "baseline":
        try:
            from backend.evaluation.datasets.rag.snapshots import load_rag_snapshot

            snapshot = load_rag_snapshot("baseline")
            if snapshot.get("kb_id") != kb_id or snapshot.get("fixture_set") != fixture_set:
                return _fail(
                    "snapshot_mismatch",
                    f"baseline snapshot (kb={snapshot.get('kb_id')}, "
                    f"fixture={snapshot.get('fixture_set')}) 与 suite (kb={kb_id}, "
                    f"fixture={fixture_set}) 不一致",
                )
        except Exception as e:  # noqa: BLE001
            return _fail("snapshot_missing", f"baseline 语料快照加载失败: {e}")

    from backend.evaluation.storage import get_git_sha

    return PreflightResult(
        passed=True,
        identity={
            "selection": selection,
            "module": module,
            "kb_id": kb_id,
            "fixture_set": fixture_set,
            "dataset_version": dataset_version,
            "case_count": len(case_ids),
            "suite_hash": file_content_hash(suite_path),
            "git_sha": get_git_sha(),
        },
    )


def run_preflight_strict(module: str = "rag", selection: str | None = None) -> PreflightResult:
    """service 侧入口：失败直接抛 PreflightError（评测运行前 fail-fast）。"""
    result = run_preflight(module=module, selection=selection)
    if not result.passed:
        raise PreflightError(result.fail_stage, result.reason)
    logger.info("[preflight] %s", result.render().replace("\n", " | "))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测运行前置校验（P0-03）")
    parser.add_argument("--module", default="rag", help="评测模块（当前 suite 化的为 rag）")
    parser.add_argument("--selection", required=True, help="评测集 selection 名（如 regression）")
    args = parser.parse_args(argv)
    result = run_preflight(module=args.module, selection=args.selection)
    print(result.render())
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

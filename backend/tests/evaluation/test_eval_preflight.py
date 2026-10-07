"""评测 Preflight + 内容 hash 行尾无关性回归测试（P0-03）。

背景：2026-10-01~05 每日 RAG Regression 连续失败——suite cases_hash 在
Windows（CRLF checkout）生成、CI Linux（LF）计算，永不相等。本文件锁死：
1. file_content_hash 行尾归一化（CRLF/LF 同值）；
2. preflight 在真正运行前拦截 suite 缺陷（空 fixture_set / hash 漂移 /
   case_ids 缺失），并输出机器判定行。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from backend.evaluation.dataset.loader import file_content_hash
from backend.evaluation.preflight import run_preflight


def test_file_content_hash_is_eol_independent(tmp_path):
    """CRLF 与 LF 内容必须算出同一 hash（P0-03 根因回归锁）。"""
    lf = tmp_path / "lf.jsonl"
    crlf = tmp_path / "crlf.jsonl"
    lf.write_bytes(b'{"id": "R-1"}\n{"id": "R-2"}\n')
    crlf.write_bytes(b'{"id": "R-1"}\r\n{"id": "R-2"}\r\n')
    assert file_content_hash(lf) == file_content_hash(crlf)
    # 与裸 LF 字节 sha256 一致（= git 入库字节口径）
    expected = hashlib.sha256(lf.read_bytes()).hexdigest()[:16]
    assert file_content_hash(crlf) == expected


def test_preflight_passes_on_real_regression_suite():
    """仓库内 regression suite 必须通过全部校验，身份指纹完整。"""
    result = run_preflight(module="rag", selection="regression")
    assert result.passed, f"reason={result.fail_stage}: {result.reason}"
    assert result.identity["kb_id"] == "rag_eval_kb"
    assert result.identity["fixture_set"] == "baseline"
    assert result.identity["dataset_version"] == "5.0.0-unified"
    assert result.identity["case_count"] == 29
    rendered = result.render()
    assert "EVAL_PREFLIGHT_PASS=true" in rendered
    assert "fixture_set=baseline" in rendered


def test_preflight_rejects_empty_selection():
    result = run_preflight(module="rag", selection="  ")
    assert not result.passed
    assert result.fail_stage == "selection_empty"


def test_preflight_rejects_empty_fixture_set(tmp_path, monkeypatch):
    """任务书 §10：fixture_set 空串必须显式 fail-fast，禁止静默传播。"""
    monkeypatch.setattr(
        "backend.evaluation.dataset.loader.DATASET_DIR", tmp_path,
    )
    suite_dir = tmp_path / "rag" / "suites"
    suite_dir.mkdir(parents=True)
    (suite_dir / "broken.json").write_text(json.dumps({
        "name": "broken",
        "kb_id": "rag_eval_kb",
        "fixture_set": "",
        "dataset_version": "1.0",
        "case_ids": ["R-1"],
    }, ensure_ascii=False), encoding="utf-8")
    result = run_preflight(module="rag", selection="broken")
    assert not result.passed
    assert result.fail_stage == "fixture_set_invalid"


def test_preflight_rejects_cases_hash_mismatch(tmp_path, monkeypatch):
    """suite 声明 hash 与 canonical 不一致 → 运行前拦截（CI 5 天事故的回归锁）。"""
    monkeypatch.setattr(
        "backend.evaluation.dataset.loader.DATASET_DIR", tmp_path,
    )
    rag = tmp_path / "rag"
    (rag / "suites").mkdir(parents=True)
    (rag / "cases.jsonl").write_bytes(b'{"id": "R-1", "question": "q", "module": "rag"}\n')
    real_hash = hashlib.sha256(b'{"id": "R-1", "question": "q", "module": "rag"}\n').hexdigest()[:16]
    (rag / "suites" / "stale.json").write_text(json.dumps({
        "name": "stale",
        "kb_id": "rag_eval_kb",
        "fixture_set": "baseline",
        "dataset_version": "1.0",
        "case_ids": ["R-1"],
        "cases_hash": "0" * 16 if real_hash[0] != "0" else "1" * 16,
    }, ensure_ascii=False), encoding="utf-8")
    result = run_preflight(module="rag", selection="stale")
    assert not result.passed
    assert result.fail_stage == "cases_hash_mismatch"


def test_preflight_rejects_missing_case_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.evaluation.dataset.loader.DATASET_DIR", tmp_path,
    )
    rag = tmp_path / "rag"
    (rag / "suites").mkdir(parents=True)
    (rag / "cases.jsonl").write_bytes(b'{"id": "R-1", "question": "q", "module": "rag"}\n')
    real_hash = hashlib.sha256(b'{"id": "R-1", "question": "q", "module": "rag"}\n').hexdigest()[:16]
    (rag / "suites" / "missing.json").write_text(json.dumps({
        "name": "missing",
        "kb_id": "rag_eval_kb",
        "fixture_set": "baseline",
        "dataset_version": "1.0",
        "case_ids": ["R-1", "GHOST-9"],
        "cases_hash": real_hash,
    }, ensure_ascii=False), encoding="utf-8")
    result = run_preflight(module="rag", selection="missing")
    assert not result.passed
    assert result.fail_stage == "case_ids_missing"
    assert "GHOST-9" in result.reason


def test_preflight_cli_exit_codes(tmp_path, capsys):
    from backend.evaluation.preflight import main

    assert main(["--module", "rag", "--selection", "regression"]) == 0
    out = capsys.readouterr().out
    assert "EVAL_PREFLIGHT_PASS=true" in out

    assert main(["--module", "rag", "--selection", ""]) == 1
    out = capsys.readouterr().out
    assert "EVAL_PREFLIGHT_PASS=false" in out
    assert "EVAL_PREFLIGHT_FAIL_STAGE=selection_empty" in out


def test_all_declared_suite_hashes_match_normalized_canonical():
    """仓库一致性：9 个 rag suite 声明的 cases_hash 必须等于归一化 canonical hash。"""
    from backend.evaluation.dataset.loader import DATASET_DIR

    root = DATASET_DIR / "rag"
    canonical = file_content_hash(root / "cases.jsonl")
    for suite in sorted((root / "suites").glob("*.json")):
        data = json.loads(suite.read_text(encoding="utf-8"))
        declared = data.get("cases_hash")
        if declared:
            assert declared == canonical, (
                f"{suite.name} 声明 {declared} ≠ canonical 归一化 {canonical}"
            )


def test_service_rejects_broken_suite_before_run_starts(tmp_path, monkeypatch):
    """service 层 fail-fast：preflight 失败时不创建 run 目录、不进 runner。"""
    from backend.evaluation import service as svc
    from backend.evaluation.config import EvalConfig
    from backend.evaluation.preflight import PreflightError

    monkeypatch.setattr(
        "backend.evaluation.dataset.loader.DATASET_DIR", tmp_path,
    )
    config = EvalConfig(module="rag", selection="ghost")
    with pytest.raises(PreflightError):
        svc.EvaluationService().evaluate(config)

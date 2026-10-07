"""JSON-safe 三层防御 + 有效性落台账回归测试（P0-02/P0-04）。

验收锚点：
- C1：新生成 report.json / per_case / API 响应不得出现 NaN/Infinity/-Infinity
- B2：历史旧 report 内已存在 NaN 字面量 → 必须能兼容读取（不 500）
- 台账：record_run 把 validity/invalid_reason 写进 ai.eval_run_records（078 列）
"""
from __future__ import annotations

import json

import pytest

from backend.evaluation.models import EvalReport, EvalResult, ModuleSummary
from backend.evaluation.storage import persist_report
from backend.shared.jsonable import safe_jsonable


def _report_with_nan_metric() -> EvalReport:
    """模拟旧生产层产出的 NaN 指标（拒答 case 无 expected 集合时 0/0）。"""
    return EvalReport(
        module="rag",
        mode="offline",
        summaries=[ModuleSummary(
            module="rag", total=3, passed=2, failed=1, errors=0, skipped=0,
            pass_rate=2 / 3,
            metrics={"recall@5": 0.8, "mrr": float("nan")},
        )],
        results=[
            EvalResult(
                case_id="RC-001", module="rag", status="pass",
                expected={}, actual={},
                metrics={"recall@5": 0.9, "mrr": float("nan")},
            ),
            EvalResult(
                case_id="RC-002", module="rag", status="pass",
                expected={}, actual={},
                metrics={"recall@5": 0.7, "mrr": 0.5},
            ),
            EvalResult(
                case_id="RC-003", module="rag", status="fail",
                expected={}, actual={},
                metrics={"recall@5": float("inf")},
            ),
        ],
    )


# ── safe_jsonable：non-finite → None ────────────────────────────────────


def test_safe_jsonable_converts_non_finite_to_none():
    payload = {
        "a": float("nan"),
        "b": [float("inf"), float("-inf"), 1.5],
        "c": {"d": float("nan")},
        "ok": True,
    }
    cleaned = safe_jsonable(payload)
    assert cleaned["a"] is None
    assert cleaned["b"] == [None, None, 1.5]
    assert cleaned["c"]["d"] is None
    assert cleaned["ok"] is True
    # 清洗结果必须能被严格 JSON 往返
    assert json.loads(json.dumps(cleaned, allow_nan=False)) == cleaned


def test_safe_jsonable_keeps_normal_values():
    assert safe_jsonable(0.5) == 0.5
    assert safe_jsonable(-0.0) == 0.0
    assert safe_jsonable({"n": 3})["n"] == 3


# ── 第二层：persist 落盘禁绝 NaN ────────────────────────────────────────


def test_persist_report_rejects_non_finite(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.evaluation.storage.DATA_ROOT", tmp_path)
    run_dir = persist_report(_report_with_nan_metric(), run_id="nan-check-1")

    report_bytes = (run_dir / "report.json").read_bytes()
    assert b"NaN" not in report_bytes
    assert b"Infinity" not in report_bytes
    data = json.loads(report_bytes)
    rag = next(s for s in data["summaries"] if s["module"] == "rag")
    assert rag["metrics"]["mrr"] is None

    per_case = json.loads((run_dir / "per_case" / "RC-003.json").read_text(encoding="utf-8"))
    assert per_case["metrics"]["recall@5"] is None


# ── 第三层：历史 NaN 文件兼容读取（不 500） ─────────────────────────────


def test_legacy_nan_report_loads_and_api_safe(monkeypatch, tmp_path):
    """旧文件里的 NaN 字面量：pydantic 可解析 + API 出口清洗后无 non-finite。"""
    run_id = "legacy-nan-1"
    run_dir = tmp_path / run_id
    (run_dir / "per_case").mkdir(parents=True)
    (run_dir / "report.json").write_text(
        json.dumps({
            "timestamp": "2026-10-06T00:00:00",
            "module": "rag",
            "mode": "offline",
            "summaries": [{
                "module": "rag", "total": 1, "passed": 0, "failed": 1,
                "errors": 0, "skipped": 0, "pass_rate": 0.0,
                "metrics": {"mrr": float("nan")},
            }],
            "results": [{
                "case_id": "RC-001", "module": "rag", "status": "fail",
                "expected": {}, "actual": {},
                "metrics": {"mrr": float("nan")},
            }],
        }, ensure_ascii=False, allow_nan=True),
        encoding="utf-8",
    )
    (run_dir / "meta.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("backend.evaluation.storage.DATA_ROOT", tmp_path)

    from backend.evaluation.storage import load_report

    report, meta = load_report(run_id)  # 不抛 = 兼容读取
    payload = safe_jsonable(report.model_dump(mode="json"))
    # 模拟 API 出口：严格序列化必须成功且 NaN 已归 None
    strict = json.dumps(payload, allow_nan=False)
    assert "NaN" not in strict
    assert payload["results"][0]["metrics"]["mrr"] is None


# ── 指标生产层：不可计算 → None（禁止 NaN） ─────────────────────────────


def test_metric_producers_return_none_not_nan():
    from backend.evaluation.metrics.retrieval import (
        chunk_recall_at_k,
        context_noise_rate,
        mrr,
        ndcg_at_k,
        recall_at_k,
        reject_accuracy,
        stage_retrieval_metrics,
    )
    from backend.evaluation.metrics.semantic import (
        answer_relevancy_proxy,
        context_precision_semantic,
        context_recall_semantic,
        hallucination_rate,
        semantic_top1,
    )

    class _NoopScorer:
        def score_pairs(self, queries, docs):  # pragma: no cover - 不应被调用
            raise AssertionError("空输入不应触发 scorer")

    assert recall_at_k([], [], 5) is None
    assert mrr([], []) is None
    assert ndcg_at_k([], [], 10) is None
    assert context_noise_rate([], [], 10) is None
    assert chunk_recall_at_k([], set(), 5) is None
    assert reject_accuracy([], set()) is None

    stage = stage_retrieval_metrics(["d1"], [])
    assert stage["top1_accuracy"] is None

    recall = context_recall_semantic(["doc"], [], _NoopScorer())
    assert recall["context_recall"] is None
    precision = context_precision_semantic([], [], _NoopScorer())
    assert precision["context_precision"] is None
    assert semantic_top1([], [], _NoopScorer()) is None
    assert hallucination_rate({"faithfulness": None}) is None
    assert answer_relevancy_proxy("", "答案", _NoopScorer()) is None


# ── 台账：validity 列随 record_run 落库（078） ──────────────────────────


def test_record_run_writes_validity_columns(monkeypatch):
    """DB 是外部边界，mock engine 捕获 SQL——断言 validity/invalid_reason 进参数。"""
    captured: dict = {}

    class _FakeCursor:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def executemany(self, *a, **k):  # pragma: no cover - 样本镜像默认关
            raise AssertionError("不应触发样本镜像")

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _FakeCursor()

        def commit(self):
            captured["committed"] = True

    class _FakeEngine:
        def raw_connection(self):
            return _FakeConn()

    monkeypatch.setattr(
        "backend.infra.db.engine_for", lambda cfg: _FakeEngine(),
    )
    # prompt 快照走 PG 权威，同样 mock 掉外部依赖
    monkeypatch.setattr(
        "backend.evaluation.run_records.collect_prompt_snapshot",
        lambda: {"rag.qa": "v13"},
    )
    monkeypatch.setattr(
        "backend.evaluation.run_records.collect_model_binding_fingerprint",
        lambda: "fp",
    )

    from backend.evaluation.run_records import record_run
    from backend.evaluation.validity import RunValidity, classify_report_validity

    report = EvalReport(
        module="sql",
        mode="live",
        summaries=[ModuleSummary(module="sql", total=2, passed=0, failed=2,
                                 errors=0, skipped=0, pass_rate=0.0)],
        results=[
            EvalResult(
                case_id=f"SC-{i}", module="sql", status="fail", expected={},
                actual={"status": "failed", "error": "查询失败: 请求预算已超限: request_fallbacks"},
                metrics={},
                error_msg="查询失败 status=failed: 请求预算已超限: request_fallbacks",
            )
            for i in range(2)
        ],
    )
    ok = record_run(report, "run-budget-1", {"git_sha": "abc1234"})
    assert ok is True
    assert captured["committed"] is True
    expected = classify_report_validity(report)
    assert expected.validity is RunValidity.INVALID_BUDGET
    sql = captured["sql"]
    assert "validity" in sql and "invalid_reason" in sql
    params = captured["params"]
    assert RunValidity.INVALID_BUDGET.value in params
    assert "request_budget_exhausted" in params

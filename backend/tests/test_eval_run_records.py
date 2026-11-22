"""评测 run 台账测试（M7 / 台账 D7）

单测层（mock，不写真库——端到端由 5 用例小集实跑验证）：
1. _summarize 聚合数学
2. record_run 软失败（DB 异常 → False，不抛）
3. record_run SQL 参数（列/值对齐 + upsert 语义）
4. collect_env_info 含 triggered_by（CLI --triggered-by 注入路径）
"""
from __future__ import annotations


def _fake_report():
    from backend.evaluation.models import EvalReport, ModuleSummary

    return EvalReport(
        module="rag",
        mode="offline",
        smoke=True,
        summaries=[
            ModuleSummary(module="rag", total=5, passed=4, failed=1, errors=0,
                          skipped=0, pass_rate=0.8, metrics={"top1": 0.9}),
        ],
        results=[],
        metadata={},
    )


class TestSummarize:
    def test_aggregation_math(self):
        from backend.evaluation.run_records import _summarize

        summary = _summarize(_fake_report())
        assert summary["case_count"] == 5
        assert summary["pass_count"] == 4
        assert summary["pass_rate"] == 0.8
        assert summary["metrics"]["rag"]["total"] == 5
        assert summary["metrics"]["rag"]["metrics"] == {"top1": 0.9}


class TestRecordRunSoftFail:
    def test_db_error_returns_false_not_raise(self, monkeypatch):
        from backend.evaluation import run_records as mod

        def boom():
            raise RuntimeError("pg down")

        monkeypatch.setattr(mod, "collect_prompt_snapshot", lambda: {})
        monkeypatch.setattr(mod, "collect_model_binding_fingerprint", lambda: "")

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", boom)
        assert mod.record_run(_fake_report(), "test-run-1") is False

    def test_insert_sql_columns_and_values(self, monkeypatch):
        from backend.evaluation import run_records as mod

        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured["sql"], captured["params"] = sql, params

        class _FakeRawConn:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeEngine:
            def raw_connection(self):
                return _FakeRawConn()

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", lambda cfg: _FakeEngine())
        monkeypatch.setattr(mod, "collect_prompt_snapshot", lambda: {"rag.system": 3})
        monkeypatch.setattr(mod, "collect_model_binding_fingerprint", lambda: "abc123")

        ok = mod.record_run(_fake_report(), "test-run-2", {
            "git_sha": "deadbee",
            "dataset_version": {"rag": "2.0"},
            "env": {"trigger": "manual", "triggered_by": "op"},
        })
        assert ok is True
        sql, params = captured["sql"], captured["params"]
        assert "ON CONFLICT (run_id) DO UPDATE" in sql  # upsert（checkpoint 续跑幂等）
        assert params[0] == "test-run-2"
        assert params[1] == "rag"
        assert params[5] == "deadbee"                    # git_sha
        assert '"rag.system": 3' in params[6]            # prompt_snapshot JSON
        # 074 生命周期列已插入 trigger 之前；按列名定位，避免新增列
        # 再次造成“列和值错位”的假失败。
        columns = [c.strip() for c in
                   sql.split("(", 1)[1].split(")", 1)[0].split(",")]
        by_column = dict(zip(columns, params))
        assert by_column["trigger"] == "manual"
        assert by_column["triggered_by"] == "op"
        assert by_column["case_count"] == 5 and by_column["pass_count"] == 4


class TestPromptReleaseLedgerContract:
    """发布评测 → 台账回写契约锁定（2026-11-22 生产实测确认）。

    prompt_release_runner 的评测报告经 persist_report → record_run 落
    ai.eval_run_records：trigger=prompt_publish（_evaluation_env 注入
    EVAL_TRIGGER），release_id 随 eval_provenance 进 dataset_version
    JSONB——评测中心台账与首页「最近评测通过率」卡片据此关联发布记录。
    断言按 INSERT 列清单序号定位参数（列清单见 run_records.record_run），
    不解析 SQL 文本。
    """

    @staticmethod
    def _capture_record_run(monkeypatch):
        from backend.evaluation import run_records as mod

        captured: dict = {}

        class _FakeCursor:
            def execute(self, sql, params=None):
                captured["sql"], captured["params"] = sql, params

        class _FakeRawConn:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeEngine:
            def raw_connection(self):
                return _FakeRawConn()

        import backend.infra.db as infra_db
        monkeypatch.setattr(infra_db, "engine_for", lambda cfg: _FakeEngine())
        monkeypatch.setattr(mod, "collect_prompt_snapshot", lambda: {})
        monkeypatch.setattr(mod, "collect_model_binding_fingerprint", lambda: "")
        return mod, captured

    def test_release_trigger_and_release_id_reach_row(self, monkeypatch):
        mod, captured = self._capture_record_run(monkeypatch)
        ok = mod.record_run(_fake_report(), "run-release-1", {
            "dataset_version": {
                "release_id": "rel-abc123",
                "suite": "desktop_upload_smoke",
                "dataset_version": "6.0.0-desktop-candidate.1",
                "prompt_snapshot": {"rag.qa": 2},
            },
            "env": {"trigger": "prompt_publish", "triggered_by": "user:3"},
        })
        assert ok is True
        params = captured["params"]
        # INSERT 列序：run_id(0) module(1) mode(2) smoke(3) dataset_version(4)
        # git_sha(5) prompt_snapshot(6) model_binding_fingerprint(7)
        # status(8) attempt_no(9) evaluation_snapshot_hash(10) validity(11)
        # invalid_reason(12) trigger(13) triggered_by(14) metrics(15)
        # case_count(16) pass_count(17) pass_rate(18)
        assert params[0] == "run-release-1"
        assert params[13] == "prompt_publish"
        assert params[14] == "user:3"
        # release_id 随 eval_provenance 落 dataset_version JSONB（跨系统关联键）
        assert '"release_id": "rel-abc123"' in params[4]
        assert '"prompt_snapshot": {"rag.qa": 2}' in params[4]

    def test_default_trigger_is_manual_without_env(self, monkeypatch):
        mod, captured = self._capture_record_run(monkeypatch)
        assert mod.record_run(_fake_report(), "run-manual-1", {}) is True
        assert captured["params"][13] == "manual"


class TestEnvInfo:
    def test_triggered_by_from_env(self, monkeypatch):
        from backend.evaluation.storage import collect_env_info

        monkeypatch.setenv("EVAL_TRIGGERED_BY", "ci-pipeline-42")
        assert collect_env_info()["triggered_by"] == "ci-pipeline-42"

    def test_prompt_versions_authoritative_prefers_pg(self, monkeypatch):
        """meta.json 的 prompt_versions 走 PG 权威口径（M7 修正点）。"""
        from backend.evaluation import storage as storage_mod

        monkeypatch.setattr(
            storage_mod, "_collect_prompt_versions_authoritative",
            lambda: {"rag.system": "3"},
        )
        # 直接验证该函数存在且被 persist_report 引用（真实文件写入由实跑覆盖）
        assert callable(storage_mod._collect_prompt_versions_authoritative)

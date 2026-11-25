"""双轨收口与评测运行记录统一测试 (Dual-track Run Records Convergence Tests)

覆盖：
1. _normalize_ledger_row 投影转换与有效性/门禁裁决映射；
2. list_runs_unified 双轨混排、按时间倒序、去重与分页切片；
3. list_runs_unified 数据库软失败降级保障；
4. get_run_unified 优先 V2，优雅回退 ledger 与本地报告；
5. get_domain_overview 全域（rag, sql, cs, travel, agent, security）状态汇总聚合；
6. API 路由集成与非 UUID run_id 兼容性。
"""

import asyncio
from datetime import datetime, timezone
import pytest

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import Identity
import backend.app.api.routes.evaluation_v2 as routes
from backend.evaluation_v2 import repository


def _mock_identity(tenant_id: str = "default") -> Identity:
    return Identity(
        user_id="alice",
        tenant_id=tenant_id,
    )


def _mock_actor(role: str = "admin") -> OperatorIdentity:
    return OperatorIdentity(
        role=role,
        actor="user:alice",
    )


def test_normalize_ledger_row():
    # 1. 成功且通过用例
    row_pass = {
        "run_id": "run_20261125_001",
        "module": "security",
        "mode": "offline",
        "status": "completed",
        "validity": "PASS",
        "invalid_reason": "",
        "case_count": 20,
        "pass_count": 20,
        "pass_rate": 1.0,
        "trigger": "ci_smoke",
        "triggered_by": "ci-bot",
        "prompt_snapshot": '{"security.system": "1"}',
        "dataset_version": {"dataset_version": "1.2.0"},
        "created_at": datetime(2026, 11, 25, 8, 30, tzinfo=timezone.utc),
    }
    norm = repository._normalize_ledger_row(row_pass)
    assert norm["run_id"] == "run_20261125_001"
    assert norm["module"] == "security"
    assert norm["suite_name"] == "SECURITY (CLI/门禁)"
    assert norm["suite_version"] == "1.2.0"
    assert norm["job_status"] == "completed"
    assert norm["gate_verdict"] == "PASS"
    assert norm["total_cases"] == 20
    assert norm["passed_cases"] == 20
    assert norm["source"] == "ci_smoke"
    assert norm["prompt_versions"] == {"security.system": "1"}

    # 2. 失败/未通过用例
    row_fail = {
        "run_id": "run_20261125_002",
        "module": "sql",
        "status": "FAIL",
        "case_count": 10,
        "pass_count": 5,
        "pass_rate": 0.5,
        "trigger": "cli",
    }
    norm_fail = repository._normalize_ledger_row(row_fail)
    assert norm_fail["job_status"] == "failed"
    assert norm_fail["gate_verdict"] == "FAIL"


def test_list_runs_unified_mix_and_dedup(monkeypatch):
    t1 = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 11, 25, 7, 0, tzinfo=timezone.utc)

    # V2 运行
    v2_mock_runs = [
        {
            "run_id": "v2-run-1",
            "suite_version_id": "sv-1",
            "suite_key": "rag_suite",
            "suite_name": "RAG 评测方案",
            "module": "rag",
            "suite_version": "1.0.0",
            "job_status": "completed",
            "gate_verdict": "PASS",
            "cost_amount": None,
            "cost_currency": "USD",
            "total_cases": 15,
            "completed_cases": 15,
            "passed_cases": 14,
            "evaluation_mode": "self+ragas",
            "source": "manual",
            "prompt_versions": {},
            "created_by": "alice",
            "created_at": t3,
            "started_at": t3,
            "finished_at": t3,
        },
        # 与 ledger 冲突的 run_id，应保留 V2 版本
        {
            "run_id": "dup-run",
            "suite_version_id": "sv-dup",
            "suite_key": "dup_v2",
            "suite_name": "V2 原生保留",
            "module": "rag",
            "created_at": t1,
        },
    ]

    # Ledger 运行
    ledger_mock_rows = [
        {
            "run_id": "dup-run",
            "module": "rag",
            "case_count": 10,
            "pass_count": 10,
            "created_at": t1,
        },
        {
            "run_id": "cli-run-2",
            "module": "security",
            "case_count": 20,
            "pass_count": 20,
            "pass_rate": 1.0,
            "status": "completed",
            "validity": "PASS",
            "trigger": "ci_smoke",
            "created_at": t2,
        },
    ]

    monkeypatch.setattr(repository, "list_runs", lambda *args, **kwargs: v2_mock_runs)
    monkeypatch.setattr(repository, "_fetch_ledger_runs", lambda *args, **kwargs: ledger_mock_rows)

    # 1. include_ledger=False
    only_v2 = repository.list_runs_unified("default", include_ledger=False)
    assert len(only_v2) == 2
    assert only_v2[0]["run_id"] == "v2-run-1"

    # 2. include_ledger=True
    unified = repository.list_runs_unified("default", include_ledger=True)
    assert len(unified) == 3
    # 验证按时间倒序混排：t1 (dup-run V2) -> t2 (cli-run-2) -> t3 (v2-run-1)
    assert unified[0]["run_id"] == "dup-run"
    assert unified[0]["suite_name"] == "V2 原生保留"  # 确认去重保留了 V2
    assert unified[1]["run_id"] == "cli-run-2"
    assert unified[1]["module"] == "security"
    assert unified[2]["run_id"] == "v2-run-1"

    # 3. 验证分页切片
    paged = repository.list_runs_unified("default", limit=1, offset=1, include_ledger=True)
    assert len(paged) == 1
    assert paged[0]["run_id"] == "cli-run-2"


def test_list_runs_unified_soft_failure(monkeypatch):
    v2_runs = [{"run_id": "v2-1", "created_at": "2026-11-25T00:00:00Z"}]
    monkeypatch.setattr(repository, "list_runs", lambda *args, **kwargs: v2_runs)

    def _broken_fetch(*args, **kwargs):
        raise ConnectionError("数据库挂了")

    monkeypatch.setattr(repository, "_fetch_ledger_runs", _broken_fetch)

    # 遇到异常时不崩溃，安全退回到 V2 记录
    res = repository.list_runs_unified("default", include_ledger=True)
    assert len(res) == 1
    assert res[0]["run_id"] == "v2-1"


def test_get_run_unified_v2_and_ledger_fallback(monkeypatch):
    # 1. 原生 V2 命中
    monkeypatch.setattr(
        repository,
        "get_run",
        lambda tenant, run_id: {"run_id": run_id, "suite_name": "V2方案", "snapshot_json": {}},
    )
    r1 = repository.get_run_unified("default", "c50d3bd3-4fdd-4867-8a11-3dca7df97a60")
    assert r1["suite_name"] == "V2方案"

    # 2. V2 未找到，从 ledger / 本地报告回退
    def _not_found(tenant, run_id):
        raise repository.RepositoryNotFoundError("V2 不存在")

    monkeypatch.setattr(repository, "get_run", _not_found)

    # 模拟本地报告回退
    class _MockReport:
        module = "security"
        mode = "offline"
        timestamp = "2026-11-25T08:00:00Z"
        results = [type("R", (), {"status": "pass"})() for _ in range(20)]

    import backend.evaluation.storage as storage_mod
    monkeypatch.setattr(storage_mod, "load_report", lambda run_id: (_MockReport(), {"env": {"trigger": "cli"}}))

    r2 = repository.get_run_unified("default", "run_security_redteam_20261125")
    assert r2["run_id"] == "run_security_redteam_20261125"
    assert r2["module"] == "security"
    assert r2["total_cases"] == 20
    assert r2["passed_cases"] == 20
    assert r2["gate_verdict"] == "PASS"


def test_get_domain_overview(monkeypatch):
    t_rag = "2026-11-25T08:00:00Z"
    t_sql = "2026-11-25T08:30:00Z"
    t_sec = "2026-11-25T09:00:00Z"

    mock_unified_runs = [
        {
            "run_id": "run-sec",
            "module": "security",
            "total_cases": 20,
            "passed_cases": 20,
            "job_status": "completed",
            "source": "ci_smoke",
            "created_at": t_sec,
        },
        {
            "run_id": "run-sql",
            "module": "sql",
            "total_cases": 6,
            "passed_cases": 6,
            "job_status": "completed",
            "source": "ci_smoke",
            "created_at": t_sql,
        },
        {
            "run_id": "run-rag",
            "module": "rag",
            "total_cases": 20,
            "passed_cases": 18,
            "job_status": "completed",
            "source": "v2",
            "suite_version_id": "sv-rag",
            "created_at": t_rag,
        },
    ]

    monkeypatch.setattr(repository, "list_runs_unified", lambda *args, **kwargs: mock_unified_runs)

    overview = repository.get_domain_overview("default")
    assert "security" in overview
    assert "sql" in overview
    assert "rag" in overview
    assert "cs" in overview
    assert "travel" in overview
    assert "agent" in overview

    assert overview["security"]["pass_rate"] == 1.0
    assert overview["security"]["total_cases"] == 20
    assert overview["security"]["time"] == t_sec

    assert overview["sql"]["pass_rate"] == 1.0
    assert overview["sql"]["total_cases"] == 6

    assert overview["rag"]["pass_rate"] == 0.9
    assert overview["rag"]["passed_cases"] == 18

    # 未运行的域安全保留 None
    assert overview["travel"]["pass_rate"] is None
    assert overview["travel"]["running"] is False


@pytest.mark.asyncio
async def test_api_routes_dual_track_convergence(monkeypatch):
    identity = _mock_identity()
    actor = _mock_actor()

    # 1. 验证 GET /runs
    monkeypatch.setattr(
        repository,
        "list_runs_unified",
        lambda tenant, limit=50, offset=0, include_ledger=True: [
            {"run_id": "run-1", "source": "ledger", "module": "security"}
        ],
    )
    runs_res = await routes.list_runs(
        limit=10,
        offset=0,
        include_ledger=True,
        identity=identity,
        actor=actor,
    )
    assert len(runs_res) == 1
    assert runs_res[0]["run_id"] == "run-1"

    # 2. 验证 GET /runs/{run_id} 接收非 UUID 格式
    monkeypatch.setattr(
        repository,
        "get_run_unified",
        lambda tenant, run_id: {
            "run_id": run_id,
            "module": "security",
            "snapshot_json": {"source": "cli", "module": "security"},
        },
    )
    single_res = await routes.get_eval_run(
        run_id="run_non_uuid_cli_string",
        identity=identity,
        actor=actor,
    )
    assert single_res["run"]["run_id"] == "run_non_uuid_cli_string"
    assert single_res["fingerprint"]["module"] == "security"

    # 3. 验证 GET /domain-overview
    monkeypatch.setattr(
        repository,
        "get_domain_overview",
        lambda tenant: {
            "rag": {"pass_rate": 0.95, "running": False},
            "security": {"pass_rate": 1.0, "running": False},
        },
    )
    overview_res = await routes.get_domain_overview(identity=identity, actor=actor)
    assert overview_res["security"]["pass_rate"] == 1.0

"""任务级受控结果引用与 Schema 映射端到端（决策 #25）。

真实依赖：PostgreSQL（迁移 122 已落库，-m pg）。覆盖：
  - maybe_offload：阈值内不 offload / 超阈值落库 + 引用对象形状 /
    禁用开关（0 = 永不 offload）/ 不可序列化内联；
  - resolve_ref：整包往返、租户不匹配 PermissionError、过期 LookupError；
  - Schema 映射：ref_value 字段路径取值（缺失/None 显式报错）、
    resolve_param_templates 参数模板（整串保类型 / 嵌入式插值）；
  - cleanup_expired 兜底 GC；
  - 执行链（BaseSkill 治理路径真实执行）：大输出 offload →
    Reporter 上下文只见摘要（关键不变式）→ 下游参数注入解引用成功；
    禁用开关下保持现行为（L1 预览）；offload 失败软降级回内联。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.pg

TENANT = "e2e-ref-tenant"
OTHER_TENANT = "e2e-ref-tenant-b"

# BaseSkill 链路测试走 _CompatSkill，capability 取自 plan.nodes；
# 清理按这两个能力名圈定本文件产生的行。
_SWEEP_CAPS = ("test.cap", "dummy.cap")


def _db_exec(sql: str, params: dict | None = None) -> None:
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
        raw.commit()
    finally:
        raw.close()


def _db_one(sql: str, params: dict | None = None):
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
            row = cur.fetchone()
            return row[0] if row is not None else None
    finally:
        raw.close()


@pytest.fixture(autouse=True)
def _clean():
    yield
    _db_exec(
        "DELETE FROM ai.task_result_refs WHERE capability = ANY(%(cs)s)",
        {"cs": list(_SWEEP_CAPS)},
    )


# =====================================================
# 构造器
# =====================================================

def _big_payload(rows: int = 400) -> dict:
    """约 13KB 的 SQLResult 形态输出（默认阈值 8192B 之上）。"""
    return {
        "columns": ["sku", "qty"],
        "tables": ["sales"],
        "row_count": rows,
        "rows": [{"sku": f"SKU-{i:04d}", "qty": i} for i in range(rows)],
    }


def _offload_kwargs(**overrides) -> dict:
    kwargs = {
        "request_id": f"req-{uuid.uuid4().hex[:8]}",
        "thread_id": "sess-ref-test",
        "step_id": "s1",
        "capability": "test.cap",
        "tenant_id": TENANT,
        "user_id": "u-ref-e2e",
    }
    kwargs.update(overrides)
    return kwargs


class _BigTool:
    """返回大 dict 的假 Tool（structured 输出契约走 dict 形态）。"""

    name = "test_result_ref_tool"

    def __init__(self):
        self.calls: list[dict] = []

    def invoke(self, params: dict) -> dict:
        self.calls.append(dict(params))
        return _big_payload()


def _compat_state(step_id: str = "step_1") -> dict:
    return {
        "current_step_id": step_id,
        "step_results": {},
        "plan": {"nodes": {step_id: {"capability": "dummy.cap",
                                     "description": "测试步骤",
                                     "params": {}}},
                 "edges": {}},
    }


def _run_structured_compat(tool_fn, state: dict | None = None):
    """真实治理路径执行 BaseSkill（structured 输出，不走 mock）。"""
    from backend.skills.base import _CompatSkill

    class _StructuredCompat(_CompatSkill):
        output_type = "structured"

    return asyncio.run(
        _StructuredCompat(tool_fn).execute(state or _compat_state(),
                                           step_capability="dummy.cap"))


# =====================================================
# maybe_offload
# =====================================================

class TestMaybeOffload:
    def test_small_output_passthrough(self):
        from backend.orchestration.result_refs import maybe_offload

        out = {"rows": [{"a": 1}]}
        assert maybe_offload(out, **_offload_kwargs()) is out

    def test_threshold_zero_disables(self):
        from backend.orchestration.result_refs import maybe_offload

        out = _big_payload()
        assert maybe_offload(out, **_offload_kwargs(), threshold_bytes=0) is out

    def test_big_output_offloads_ref_shape_and_db_row(self):
        from backend.orchestration.result_refs import (
            is_result_ref, maybe_offload,
        )

        out = _big_payload()
        ref = maybe_offload(out, **_offload_kwargs())
        assert is_result_ref(ref)
        meta = ref["__result_ref__"]
        assert meta["ref_id"].startswith("taskref_")
        assert meta["row_count"] == 400
        assert meta["size_bytes"] > 8192
        assert meta["schema_summary"]["row_count"] == 400
        assert "sku" in meta["schema_summary"]["row_keys"]
        assert 0 < len(meta["summary"]) <= 500
        # 引用对象不携带数据本体（LLM 上下文不变式的基础）
        assert "SKU-0001" not in ref["__result_ref__"]["summary"]
        row = _db_one(
            "SELECT tenant_id FROM ai.task_result_refs WHERE id = %(id)s",
            {"id": meta["ref_id"]})
        assert row == TENANT

    def test_unserializable_output_inline(self):
        from backend.orchestration.result_refs import (
            is_result_ref, maybe_offload,
        )

        circular: dict = {}
        circular["self"] = circular
        result = maybe_offload(circular, **_offload_kwargs(threshold_bytes=1))
        assert not is_result_ref(result)
        assert result is circular


# =====================================================
# resolve_ref
# =====================================================

class TestResolveRef:
    def test_roundtrip_returns_full_payload(self):
        from backend.orchestration.result_refs import maybe_offload, resolve_ref

        payload = _big_payload(50)
        # 阈值 1 强制 offload（往返语义与真实阈值无关）
        ref = maybe_offload(payload, **_offload_kwargs(), threshold_bytes=1)
        resolved = resolve_ref(ref["__result_ref__"]["ref_id"], TENANT, "u1")
        assert resolved == payload

    def test_tenant_mismatch_permission_error(self):
        from backend.orchestration.result_refs import (
            maybe_offload, resolve_ref,
        )

        ref = maybe_offload(
            _big_payload(50), **_offload_kwargs(), threshold_bytes=1)
        with pytest.raises(PermissionError):
            resolve_ref(ref["__result_ref__"]["ref_id"], OTHER_TENANT, "u2")

    def test_missing_lookup_error(self):
        from backend.orchestration.result_refs import resolve_ref

        with pytest.raises(LookupError):
            resolve_ref(f"taskref_{uuid.uuid4().hex}", TENANT)

    def test_expired_lookup_error(self):
        from backend.orchestration.result_refs import (
            maybe_offload, resolve_ref,
        )

        ref = maybe_offload(
            _big_payload(50), **_offload_kwargs(), threshold_bytes=1)
        ref_id = ref["__result_ref__"]["ref_id"]
        _db_exec(
            "UPDATE ai.task_result_refs SET expires_at = now() - interval '1 minute'"
            " WHERE id = %(id)s",
            {"id": ref_id})
        with pytest.raises(LookupError):
            resolve_ref(ref_id, TENANT)


# =====================================================
# Schema 映射：ref_value / resolve_param_templates
# =====================================================

class TestSchemaMapping:
    def test_ref_value_walks_path_and_list_index(self):
        from backend.orchestration.result_refs import ref_value

        previous = {"s0": {"a": {"b": 7}, "rows": [{"sku": "SKU-0"}]}}
        assert ref_value(previous, "s0.a.b") == 7
        assert ref_value(previous, "s0.rows.0.sku") == "SKU-0"

    def test_ref_value_through_ref_object(self):
        from backend.orchestration.result_refs import maybe_offload, ref_value

        ref = maybe_offload(
            _big_payload(), **_offload_kwargs(), threshold_bytes=1)
        value = ref_value(
            {"s1": ref}, "s1.rows.3.sku",
            tenant_id=TENANT, user_id="u1")
        assert value == "SKU-0003"

    def test_ref_value_missing_and_none_fail_explicitly(self):
        from backend.orchestration.result_refs import ref_value

        previous = {"s0": {"a": None, "rows": []}}
        with pytest.raises(LookupError):
            ref_value(previous, "s0.a.b")
        with pytest.raises(LookupError):
            ref_value(previous, "s0.nope")
        with pytest.raises(LookupError):
            ref_value(previous, "s9.nope")

    def test_param_template_whole_string_keeps_type(self):
        from backend.orchestration.result_refs import resolve_param_templates

        previous = {"s0": {"limit": 5, "rows": [{"sku": "SKU-0"}]}}
        resolved = resolve_param_templates(
            {"n": "{{s0.limit}}", "sku": "{{s0.rows.0.sku}}"}, previous)
        assert resolved == {"n": 5, "sku": "SKU-0"}

    def test_param_template_embedded_interpolates(self):
        from backend.orchestration.result_refs import resolve_param_templates

        previous = {"s0": {"limit": 5}}
        resolved = resolve_param_templates(
            {"q": "前 {{s0.limit}} 条"}, previous)
        assert resolved == {"q": "前 5 条"}

    def test_param_template_no_template_untouched(self):
        from backend.orchestration.result_refs import resolve_param_templates

        params = {"q": "普通参数", "n": 3}
        assert resolve_param_templates(params, {}) == params

    def test_param_template_missing_fails_explicitly(self):
        from backend.orchestration.result_refs import resolve_param_templates

        with pytest.raises(LookupError):
            resolve_param_templates({"q": "{{s0.nope}}"}, {"s0": {}})


# =====================================================
# cleanup_expired
# =====================================================

class TestCleanupExpired:
    def test_deletes_only_expired_rows(self):
        from backend.orchestration.result_refs import (
            cleanup_expired, maybe_offload,
        )

        live = maybe_offload(
            _big_payload(50), **_offload_kwargs(), threshold_bytes=1)
        expired = maybe_offload(
            _big_payload(50), **_offload_kwargs(), threshold_bytes=1)
        live_id = live["__result_ref__"]["ref_id"]
        expired_id = expired["__result_ref__"]["ref_id"]
        _db_exec(
            "UPDATE ai.task_result_refs SET expires_at = now() - interval '1 hour'"
            " WHERE id = %(id)s",
            {"id": expired_id})
        deleted = cleanup_expired()
        assert deleted >= 1
        assert _db_one(
            "SELECT count(*) FROM ai.task_result_refs WHERE id = %(id)s",
            {"id": expired_id}) == 0
        assert _db_one(
            "SELECT count(*) FROM ai.task_result_refs WHERE id = %(id)s",
            {"id": live_id}) == 1


# =====================================================
# 执行链：BaseSkill offload → Reporter 摘要 → 下游解引用
# =====================================================

class TestExecutionChain:
    def test_big_output_offloads_and_reporter_sees_summary_only(self):
        from backend.agents.reporter.reporter import generate_final_answer
        from backend.orchestration.result_refs import (
            is_result_ref, materialize_step_results_for_context,
            resolve_previous_outputs,
        )

        result = _run_structured_compat(_BigTool())
        sr = result["step_results"]["step_1"]
        assert sr["status"] == "success"
        ref = sr["output"]
        assert is_result_ref(ref)
        assert _db_one(
            "SELECT count(*) FROM ai.task_result_refs WHERE id = %(id)s",
            {"id": ref["__result_ref__"]["ref_id"]}) == 1

        # LLM 上下文装配：引用对象 → 摘要文本（含 ref_id 与首元素，无整包）
        materialized = materialize_step_results_for_context({"step_1": sr})
        text = materialized["step_1"]["output"]
        assert isinstance(text, str)
        assert ref["__result_ref__"]["ref_id"] in text
        assert "SKU-0000" in text          # 首元素预览（设计内）
        assert "SKU-0001" not in text      # 整包数据不进 LLM 上下文
        assert materialized["step_1"]["result_ref_offloaded"] is True

        # Reporter 端到端（单步确定性透传路径，无 LLM）：最终答案基于摘要
        answer = generate_final_answer("查销量", {"step_1": sr}, allow_llm=False)
        assert "SKU-0000" in answer
        assert "SKU-0001" not in answer

        # 下游参数注入点解引用成功（保持整包数据流）
        resolved = resolve_previous_outputs({"step_1": ref}, tenant_id="default")
        assert resolved["step_1"] == _big_payload()
        assert resolved["step_1"]["rows"][1]["sku"] == "SKU-0001"

    def test_disable_switch_keeps_inline_behavior(self, monkeypatch):
        """env/配置置 0 = 永不 offload：部署零行为变化（L1 预览即现行为）。"""
        from backend.orchestration.result_refs import is_result_ref

        monkeypatch.setattr(
            "backend.config.settings.RESULT_REF_THRESHOLD_BYTES", 0)
        result = _run_structured_compat(_BigTool())
        output = result["step_results"]["step_1"]["output"]
        assert not is_result_ref(output)
        # 现行为：L1 预算 Guard 把超限输出裁成预览
        assert isinstance(output, dict)
        assert output.get("context_compacted") is True

    def test_offload_failure_falls_back_inline(self, monkeypatch):
        """offload 落库失败软降级回内联，不阻断 Skill 执行。"""
        from backend.orchestration.result_refs import is_result_ref

        def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "backend.orchestration.result_refs.maybe_offload", _boom)
        result = _run_structured_compat(_BigTool())
        output = result["step_results"]["step_1"]["output"]
        assert not is_result_ref(output)
        assert result["step_results"]["step_1"]["status"] == "success"

    def test_param_template_field_mapping_in_execute(self):
        """既有消费点示范：execute 参数模板 {{step_id.field}} 按字段取值。"""
        tool = _BigTool()
        state = _compat_state()
        state["previous_outputs"] = {"step_0": _big_payload()}
        state["plan"]["nodes"]["step_1"]["params"] = {
            "sku": "{{step_0.rows.0.sku}}",
            "n": "{{step_0.row_count}}",
        }
        result = _run_structured_compat(tool, state)
        assert result["step_results"]["step_1"]["status"] == "success"
        assert tool.calls, "Tool 应被调用"
        # 模板已替换为字段原类型值（str/int 透传，不是整包 dict）
        assert tool.calls[0]["sku"] == "SKU-0000"
        assert tool.calls[0]["n"] == 400

    def test_param_template_missing_fails_step_explicitly(self):
        """模板取值缺失 → 步骤 invalid_param 显式失败（不静默空串）。"""
        tool = _BigTool()
        state = _compat_state()
        state["previous_outputs"] = {"step_0": _big_payload()}
        state["plan"]["nodes"]["step_1"]["params"] = {
            "sku": "{{step_0.nope.deep}}",
        }
        result = _run_structured_compat(tool, state)
        sr = result["step_results"]["step_1"]
        assert sr["status"] == "failed"
        assert sr["error_type"] == "invalid_param"
        assert "参数模板取值失败" in (sr.get("error") or "")
        assert not tool.calls, "取值失败不得进入 Tool"

# -*- coding: utf-8 -*-
"""tests/skills/test_tool_contract_boundary.py — STOP G Tool Contract 边界行为

锁（docs/architecture/STOP_G_Preparation_Audit.md M2/M3/M4）：
  - unwrap_envelope：封套识别与解包的唯一出口（success→data / failed→封套保留 /
    非封套原样），skill_adapter 与 skill 边界共用；
  - validate_semantics：status=="failed" 为失败判定第一等依据，error 键嗅探保留兼容；
  - skill 边界：structured 声明下封套不允许透出（成功=裸 data，失败=failed 步骤）。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.shared.tool_envelope import unwrap_envelope
from backend.skills.base import BaseSkill, _CompatSkill
from backend.skills.validation import ValidationFailure, validate_semantics


def _state(step_id="step_1"):
    return {
        "current_step_id": step_id,
        "step_results": {},
        "plan": {"nodes": {step_id: {"capability": "dummy.cap",
                                     "description": "测试步骤",
                                     "params": {}}},
                 "edges": {}},
    }


class _StructuredCompat(_CompatSkill):
    output_type = "structured"


class _TextCompat(_CompatSkill):
    output_type = "text"


class _EnvelopeSuccessTool:
    def invoke(self, params):
        return json.dumps({"status": "success", "data": {"location": {"lat": 26.08}},
                           }, ensure_ascii=False)


class _EnvelopeFailedTool:
    def invoke(self, params):
        return json.dumps({"status": "failed", "error": "配额用尽"},
                          ensure_ascii=False)


class TestUnwrapEnvelope:
    def test_success_str_unwraps_data(self):
        raw = json.dumps({"status": "success", "data": {"a": 1}, "row_count": 2})
        is_env, payload = unwrap_envelope(raw)
        assert is_env is True
        assert payload == {"a": 1}

    def test_failed_keeps_envelope(self):
        raw = json.dumps({"status": "failed", "error": "配额用尽", "hint": "x"})
        is_env, payload = unwrap_envelope(raw)
        assert is_env is True
        assert payload["status"] == "failed"
        assert payload["error"] == "配额用尽"
        assert payload["hint"] == "x"

    def test_dict_input_unwraps_too(self):
        is_env, payload = unwrap_envelope({"status": "success", "data": [1, 2]})
        assert (is_env, payload) == (True, [1, 2])

    def test_non_envelope_dict_kept_as_is(self):
        """无 status 键的历史/业务 dict 原样（SQLResult、ExpertResult 等）。"""
        business = {"columns": ["x"], "rows": [], "status_field_note": "no envelope"}
        is_env, payload = unwrap_envelope(business)
        assert (is_env, payload) == (False, business)

    def test_success_without_data_key_not_envelope(self):
        """success 但缺 data 键：形状不完整，不强行解包。"""
        candidate = {"status": "success"}
        is_env, payload = unwrap_envelope(candidate)
        assert (is_env, payload) == (False, candidate)

    def test_invalid_json_and_non_str_kept_as_is(self):
        assert unwrap_envelope("不是JSON")[0] is False
        assert unwrap_envelope(12345)[0] is False
        assert unwrap_envelope(None)[0] is False


class TestSkillBoundaryStructured:
    """structured 声明的边界：封套不透出（M4 核心）。"""

    def _run(self, tool_fn):
        return asyncio.run(
            _StructuredCompat(tool_fn).execute(_state(), step_capability="dummy.cap"))

    def test_envelope_success_output_is_bare_data(self):
        out = self._run(_EnvelopeSuccessTool())
        sr = out["step_results"]["step_1"]
        assert sr["status"] == "success"
        assert sr["output"] == {"location": {"lat": 26.08}}
        is_envelope, _ = unwrap_envelope(sr["output"])
        assert is_envelope is False

    def test_envelope_failed_maps_to_failed_step(self):
        out = self._run(_EnvelopeFailedTool())
        sr = out["step_results"]["step_1"]
        # 失败封套 → failed 步骤（step 级 error 为既有粗口径「输出校验失败:
        # semantic」，可读 reason 由 validate_semantics 层测试锁定）
        assert sr["status"] == "failed"

    def test_non_envelope_json_str_backward_compatible(self):
        """非封套 JSON str → 解析为 dict 原样（既有契约，不因 M4 破坏）。"""
        class _PlainJsonTool:
            def invoke(self, params):
                return json.dumps({"summary": "洞察"}, ensure_ascii=False)

        out = self._run(_PlainJsonTool())
        assert out["step_results"]["step_1"]["output"] == {"summary": "洞察"}

    def test_business_dict_with_status_but_no_data_not_unwrapped(self):
        """业务 dict 恰好带 status 键但无 data 键：不被误拆。"""
        class _BusinessDictTool:
            def invoke(self, params):
                return {"status": "success", "note": "非封套形状"}

        out = self._run(_BusinessDictTool())
        assert out["step_results"]["step_1"]["output"] == {
            "status": "success", "note": "非封套形状"}


class TestSkillBoundaryText:
    """text 声明的边界：四象限中的 text 两态（M5 覆盖要求）。"""

    def _run(self, tool_fn):
        return asyncio.run(
            _TextCompat(tool_fn).execute(_state(), step_capability="dummy.cap"))

    def test_markdown_str_passthrough(self):
        class _MdTool:
            def invoke(self, params):
                return "## 报告\n- 要点1"

        out = self._run(_MdTool())
        assert out["step_results"]["step_1"]["output"] == "## 报告\n- 要点1"

    def test_plain_str_passthrough(self):
        class _PlainTool:
            def invoke(self, params):
                return "已完成导出：/tmp/x.csv"

        out = self._run(_PlainTool())
        assert out["step_results"]["step_1"]["output"] == "已完成导出：/tmp/x.csv"

    def test_envelope_json_str_not_unwrapped_for_text(self):
        """text 声明下封套 JSON str 原样透传（解包只属于 structured 声明）。"""
        out = self._run(_EnvelopeSuccessTool())
        output = out["step_results"]["step_1"]["output"]
        assert isinstance(output, str)
        assert json.loads(output)["status"] == "success"


class TestValidateSemanticsStatusFirst:
    """M3：status=="failed" 第一等依据；error 键嗅探保留兼容。"""

    def test_failed_envelope_rejected(self):
        with pytest.raises(ValidationFailure) as exc:
            validate_semantics("map.lookup", {}, {"status": "failed",
                                                  "error": "腾讯位置服务未配置"})
        # 可读 reason 在 envelope.details（step 落盘的粗粒度文案是既有口径）
        assert "腾讯位置服务未配置" in exc.value.envelope.details["reason"]

    def test_failed_envelope_not_found_wording_maps_not_found(self):
        with pytest.raises(ValidationFailure):
            validate_semantics("map.lookup", {}, {"status": "failed",
                                                  "error": "未能解析地址，查不到该地点"})

    def test_failed_envelope_without_error_still_rejected(self):
        with pytest.raises(ValidationFailure):
            validate_semantics("map.lookup", {}, {"status": "failed"})

    def test_legacy_error_key_dict_still_rejected(self):
        """兼容兜底：无 status 的裸 error dict（历史形态）仍判失败。"""
        with pytest.raises(ValidationFailure):
            validate_semantics("sql.query", {}, {"error": "连接超时"})

    def test_success_envelope_data_not_rejected(self):
        """解包后的裸业务 dict（无 error 无 failed status）放行。"""
        validate_semantics("map.lookup", {}, {"location": {"lat": 26.08}})

    def test_success_envelope_str_with_status_not_rejected(self):
        """str 形态的 success 封套（未走边界的直调场景）不误判失败。"""
        raw = json.dumps({"status": "success", "data": {"x": 1}})
        validate_semantics("map.lookup", {}, raw)


class TestSkillAdapterUnwrap:
    """M2：skill_adapter.call_sql 的手写解包迁移到 unwrap_envelope。"""

    def test_success_envelope_unwrapped(self):
        from backend.orchestration.workflow import skill_adapter

        fake_tool = MagicMock()
        fake_tool.ainvoke = _make_async(json.dumps(
            {"status": "success", "data": {"columns": ["m"], "rows": []}}))
        with patch("backend.orchestration.tools.execute_sql_tool", fake_tool):
            result = asyncio.run(skill_adapter.call_sql({"query": "SELECT 1"}))
        assert result == {"columns": ["m"], "rows": []}

    def test_failed_envelope_raises(self):
        from backend.orchestration.workflow import skill_adapter

        fake_tool = MagicMock()
        fake_tool.ainvoke = _make_async(json.dumps(
            {"status": "failed", "error": "只读角色拒绝"}))
        with patch("backend.orchestration.tools.execute_sql_tool", fake_tool):
            with pytest.raises(ValueError, match="只读角色拒绝"):
                asyncio.run(skill_adapter.call_sql({"query": "DELETE 1"}))

    def test_no_status_legacy_form_passthrough(self):
        from backend.orchestration.workflow import skill_adapter

        fake_tool = MagicMock()
        fake_tool.ainvoke = _make_async(json.dumps({"legacy": True}))
        with patch("backend.orchestration.tools.execute_sql_tool", fake_tool):
            result = asyncio.run(skill_adapter.call_sql({"query": "SELECT 1"}))
        assert result == {"legacy": True}


def _make_async(return_value):
    async def _ainvoke(params):
        return return_value
    return _ainvoke


class TestPendingApprovalReceiptKept:
    """审批待办回执保留（2026-10-02）：工具返回「需要人工审批后执行」提示时，
    此前被 validate_semantics 判死且 output=None，reporter 只能兜底「未找到
    相关信息」——用户看不到等待审批与审批单号。现契约：PERMISSION_DENIED
    语义失败保留提示文本进 step.output（error_type=permission），
    direct 链路 _coerce_final_answer 把它透出为 final_answer。"""

    def _run(self, tool_fn):
        # email.send 的 params_schema 要求 to/subject/body 必填——
        # 先过前置参数校验，才能到达语义校验的审批分支
        state = _state()
        state["plan"]["nodes"]["step_1"]["params"] = {
            "to": "mint1614@qq.com", "subject": "s", "body": "b"}
        return asyncio.run(
            _TextCompat(tool_fn).execute(state, step_capability="email.send"))

    def test_pending_approval_text_kept_in_output(self):
        out = self._run(_PendingApprovalTool())
        sr = out["step_results"]["step_1"]
        assert sr["status"] == "failed"
        assert sr["error_type"] == "permission"
        assert "需要人工审批后执行" in sr["output"]
        assert "审批单号" in sr["output"]

    def test_other_validation_failure_still_drops_output(self):
        """非审批类语义失败（如失败封套）维持原契约：output 置空。"""
        out = self._run(_EnvelopeFailedTool())
        sr = out["step_results"]["step_1"]
        assert sr["status"] == "failed"
        assert sr["output"] is None
        assert sr["error_type"] == "invalid_param"

    def test_coerce_final_answer_surfaces_approval_receipt(self):
        from backend.orchestration.graph.direct_executor import _coerce_final_answer

        receipt = "⏸ 该操作需要人工审批后执行（写操作安全策略）。审批单号: abc"
        step = {"status": "failed", "error_type": "permission", "output": receipt}
        assert _coerce_final_answer(step) == receipt
        # 普通失败仍返回空串（不透出垃圾）
        assert _coerce_final_answer({"status": "failed", "error_type": "invalid_param",
                                     "output": None}) == ""


class _PendingApprovalTool:
    """返回 ensure_approved 待审批提示形态的文本（与 tool_approval.py 同文案）。"""

    def invoke(self, params):
        return ("⏸ 该操作需要人工审批后执行（写操作安全策略）。\n\n"
                "- 操作: `send_email.send`\n"
                "- 审批单号: `test-rid`\n\n"
                "管理员在审批管理页批准后，重新发起相同操作即可执行。")

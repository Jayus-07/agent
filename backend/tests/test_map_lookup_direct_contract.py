# -*- coding: utf-8 -*-
"""tests/test_map_lookup_direct_contract.py — STOP G M4 实链路验收

锁：map.lookup 的 direct 路径上，统一封套 {"status","data"} 不允许到达用户。
链路全部真实：MapLookupSkill.execute（真实聚合 Tool + 真实封套出口）→
BaseSkill._normalize_output（structured 边界解包）→ direct_executor 的
_coerce_final_answer（真实 final_answer 组装）。唯一 stub 是第三方 HTTP 边缘
（lbs api.geocode，conftest 已切断 LBS 时走真实 not_configured 失败封套）。

缺陷背景：structured 输出曾按 text 透传到 final_answer，导致用户看到原始 JSON；
当前 Tool/Skill 输出契约见 docs/development/tool-skill-guide.md。
"""
from __future__ import annotations

import asyncio

import pytest

from backend.skills.map.skill import MapLookupSkill


def _state(step_id="step_map", capability="map.lookup", params=None):
    return {
        "current_step_id": step_id,
        "step_results": {},
        "plan": {"nodes": {step_id: {"capability": capability,
                                     "description": "地图查询",
                                     "params": params or {}}},
                 "edges": {}},
    }


def _final_answer_from_real_coercion(output, status="success"):
    """direct 支线真实 final_answer 组装（不经任何测试替身）。"""
    from backend.orchestration.graph.direct_executor import _coerce_final_answer
    return _coerce_final_answer({"capability": "map.lookup",
                                 "status": status, "output": output})


class TestMapLookupDirectContract:
    def test_success_envelope_never_reaches_final_answer(self, monkeypatch):
        """成功路径：真实 Tool 返回 ok() 封套 → 边界解包 → final_answer 无封套。"""
        from backend.config import map as map_cfg
        from backend.infra.lbs import api as lbs_api

        # 覆盖 conftest 的 LBS 切断（fixture 注释明确允许），HTTP 边缘给罐头响应
        monkeypatch.setattr(map_cfg, "TENCENT_LBS_ENABLED", True)
        monkeypatch.setattr(
            lbs_api, "geocode",
            lambda address, region=None: {
                "location": {"lat": 39.9042, "lng": 116.4074},
                "reliability": 9, "level": "门牌号",
            },
        )

        out = asyncio.run(MapLookupSkill().execute(
            _state(params={"action": "geocode", "address": "北京市东城区",
                           "city": "北京"}),
            step_capability="map.lookup",
        ))
        sr = out["step_results"]["step_map"]
        assert sr["status"] == "success", f"实链路执行失败: {sr.get('error')}"

        output = sr["output"]
        # 边界已解包：output 是裸业务 data，不再是 {"status","data"} 封套
        assert isinstance(output, dict)
        assert "status" not in output
        assert output["location"]["lat"] == pytest.approx(39.9042)

        answer = _final_answer_from_real_coercion(output)
        assert '"status"' not in answer
        assert "39.9042" in answer or "39.9" in answer

    def test_not_configured_envelope_maps_to_failed_step(self):
        """失败路径（conftest 已切断 LBS，走真实 not_configured 失败封套）：
        失败封套不透出为成功步骤，可读 reason 保留在 error_protocol/details。"""
        out = asyncio.run(MapLookupSkill().execute(
            _state(params={"action": "geocode", "address": "三坊七巷",
                           "city": "福州"}),
            step_capability="map.lookup",
        ))
        sr = out["step_results"]["step_map"]
        assert sr["status"] == "failed"
        # step 级 error 是粗粒度既有口径（「输出校验失败: semantic」），
        # 可读 reason 在 ValidationFailure.envelope.details —— 经日志承载；
        # 此处锁「失败语义被识别」这一契约本身
        answer = _final_answer_from_real_coercion(sr.get("output"),
                                                  status=sr["status"])
        assert '"status"' not in answer

    def test_tool_failure_envelope_never_reaches_final_answer(self, monkeypatch):
        """Tool 层「查不到」失败封套：边界转 failed 步骤，error 可读。"""
        from backend.config import map as map_cfg
        from backend.infra.lbs import api as lbs_api

        monkeypatch.setattr(map_cfg, "TENCENT_LBS_ENABLED", True)
        monkeypatch.setattr(lbs_api, "geocode", lambda address, region=None: None)

        out = asyncio.run(MapLookupSkill().execute(
            _state(params={"action": "geocode", "address": "不存在的地方xyz",
                           "city": "北京"}),
            step_capability="map.lookup",
        ))
        sr = out["step_results"]["step_map"]
        assert sr["status"] == "failed"
        # 「查不到」类失败必须判为 failed 步骤，封套不得成为成功输出
        assert not isinstance(sr.get("output"), dict) or \
            sr["output"].get("status") != "success"

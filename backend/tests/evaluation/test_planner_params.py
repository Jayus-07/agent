# -*- coding: utf-8 -*-
"""Planner 参数填充评估测试。

evaluate_planner_offline 新增 expected.params 断言：
  - 按 capability 声明关键参数，标量=相等断言，{"contains": s}=子串断言
  - 不做全量对比，只给写操作等误填代价高的参数一个可回归的度量
  - capability 未被规划 → 参数未校验，计失败
数据集 planner_params.json 需 live 模式（真实 LLM）跑，此处只测纯函数。
"""
import json
from pathlib import Path

import pytest

from backend.evaluation.runner import evaluate_planner_offline


DATASET = Path(__file__).parents[2] / "evaluation" / "datasets" / "planner_params.json"


def _eval(expected, caps, params=None):
    return evaluate_planner_offline("T-1", expected, caps, params)


class TestParamAssertions:
    BASE = {"capabilities": ["email.send"]}

    def test_scalar_equality_pass(self):
        r = _eval({**self.BASE, "params": {"email.send": {"action": "send"}}},
                  ["email.send"], {"email.send": {"action": "send"}})
        assert r.status == "pass"
        assert r.metrics["param_match"] == 1.0

    def test_contains_pass(self):
        r = _eval(
            {**self.BASE, "params": {"email.send": {"to": {"contains": "@qq.com"}}}},
            ["email.send"], {"email.send": {"to": "a@qq.com"}})
        assert r.status == "pass"

    def test_wrong_param_fails_with_readable_error(self):
        r = _eval(
            {**self.BASE, "params": {"email.send": {"to": {"contains": "@qq.com"}}}},
            ["email.send"], {"email.send": {"to": "b@163.com"}})
        assert r.status == "fail"
        assert r.metrics["param_match"] == 0.0
        assert "@qq.com" in (r.error_msg or "")

    def test_capability_not_planned_counts_as_fail(self):
        r = _eval({**self.BASE, "params": {"email.send": {"action": "send"}}},
                  ["rag.search"], {"rag.search": {}})
        assert r.status == "fail"
        assert "未被规划" in (r.error_msg or "")

    def test_no_params_declared_is_backward_compatible(self):
        """未声明 params 时行为与旧版一致，param_match=None 不参与判定"""
        r = _eval(self.BASE, ["email.send"])
        assert r.status == "pass"
        assert r.metrics["param_match"] is None

    def test_actual_params_recorded_in_result(self):
        r = _eval(self.BASE, ["email.send"], {"email.send": {"to": "x"}})
        assert r.actual["params"] == {"email.send": {"to": "x"}}


class TestParamsDataset:
    """planner_params.json 健全性：capability 已注册、params 声明合法"""

    def test_dataset_loads_and_caps_registered(self):
        from backend.orchestration.capability_registry import tool_registry

        data = json.loads(DATASET.read_text(encoding="utf-8"))
        cases = data["test_cases"]
        assert len(cases) == 8
        known = set(tool_registry.CAPABILITY_MAP.keys())
        for case in cases:
            for cap in case["expected"]["capabilities"]:
                assert cap in known, f"{case['id']}: capability {cap} 未注册"

    def test_write_ops_have_param_assertions(self):
        """写操作用例必须带 params 断言（本数据集的存在意义）"""
        data = json.loads(DATASET.read_text(encoding="utf-8"))
        for case in data["test_cases"]:
            if case["metadata"]["type"].startswith("write_op"):
                assert case["expected"].get("params"), \
                    f"{case['id']}: 写操作用例缺少 params 断言"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

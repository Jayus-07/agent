"""test_expert_node_timeout.py — 专家节点级限时兜底回归

迁移 B1（2026-09-29）：complaint/query/handoff 三节点此前未给
run_expert_safely 传限时，专家内任一环节挂起（如 LLM 无界等待）时整条
投诉/查询/转人工路径无上界。修复后三节点与 knowledge/action 同口径传入
CS_EXPERT_TIMEOUT_S。本文件验证：①超时路径确实生效（status=timeout）；
②节点后处理能安全消费 timeout 结果（不炸节点、expert_history 正常追加）。
"""
from __future__ import annotations

import importlib
import time
from unittest.mock import patch

import pytest

from backend.config import customer_service as cs_config

_CASES = [
    ("backend.customer_service.experts.query", "query_expert_node", "query"),
    ("backend.customer_service.experts.complaint", "complaint_expert_node", "complaint"),
    ("backend.customer_service.experts.handoff", "handoff_expert_node", "handoff"),
]


@pytest.mark.parametrize("module_path,node_name,expert", _CASES)
def test_expert_node_bounded_under_hang(module_path, node_name, expert):
    """专家内部挂死时节点在 CS_EXPERT_TIMEOUT_S 内返回 status=timeout。"""
    mod = importlib.import_module(module_path)
    node = getattr(mod, node_name)

    def _hang(*args, **kwargs):
        time.sleep(2)  # 远超测试限时——模拟无界挂起

    with patch.object(cs_config, "CS_EXPERT_TIMEOUT_S", 0.3), \
         patch.object(mod, f"execute_{expert}", _hang):
        t0 = time.monotonic()
        result = node({"user_message": "触发挂起"})
        elapsed = time.monotonic() - t0

    assert result["last_expert_result"]["status"] == "timeout"
    assert elapsed < 1.5  # 0.3s 限时 + 线程调度开销；修复前会挂满 2s
    # timeout 结果也应进入 expert_history（Supervisor 循环检测依赖它）
    history = result.get("expert_history") or []
    assert any(entry.get("expert") == expert for entry in history)

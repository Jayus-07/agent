"""test_event_attribution.py — 并行 Send 时 Skill 事件归属回归测试

背景（2026-09-15 实测）：BaseSkill.execute 返回全量 step_results（分支快照
携带其他步骤），_build_skill_events 旧实现把整个 dict 全部打上当前节点名，
导致并行 Send 时 step1 的失败挂在 rag_skill 名下、同一完成事件被两个 skill
各发一遍。修复后按 capability 反查属主节点过滤，且跳过非终态快照噪声。
"""
import pytest

from backend.orchestration.graph.events import (
    make_step_log_event,
    make_step_payload,
    stream_node_events,
)


SKILL_NODES = {"sql_skill", "rag_skill", "report_skill"}


def _collect(node_name: str, step_results: dict) -> list[dict]:
    output = {"step_results": step_results}
    return list(stream_node_events(
        node_name, output, SKILL_NODES, make_step_payload, make_step_log_event,
    ))


def test_parallel_snapshot_only_emits_owned_steps():
    """rag 分支快照携带 sql 步骤（running）→ 只发自己的 rag 终态事件。"""
    output = {
        "1": {"capability": "sql.query", "status": "running",
              "description": "查询项目预算数据"},
        "2": {"capability": "rag.search", "status": "success",
              "description": "检索项目管理经验", "output": "有效检索内容" * 3},
    }
    evts = _collect("rag_skill", output)
    assert [e["data"]["step_id"] for e in evts] == ["2"]
    assert all(e["data"]["node"] == "rag_skill" for e in evts)


def test_sql_branch_does_not_reemit_rag_success():
    """sql 分支快照携带 rag 已完成步骤 → 不得重复播报（旧实现串线的核心场景）。"""
    output = {
        "1": {"capability": "sql.query", "status": "success",
              "description": "查询项目预算数据",
              "output": {"sql": "SELECT 1", "rows": [], "columns": []}},
        "2": {"capability": "rag.search", "status": "success",
              "description": "检索项目管理经验", "output": "有效检索内容" * 3},
    }
    evts = _collect("sql_skill", output)
    assert [e["data"]["step_id"] for e in evts] == ["1"]


def test_failure_attributed_to_owner_node():
    """步骤失败事件必须挂在属主节点名下（旧实现挂在别人名下）。"""
    output = {
        "1": {"capability": "sql.query", "status": "failed",
              "description": "查询项目预算数据", "error": "table not found"},
        "2": {"capability": "rag.search", "status": "running",
              "description": "检索项目管理经验"},
    }
    evts = _collect("sql_skill", output)
    assert len(evts) == 1
    assert evts[0]["data"]["node"] == "sql_skill"
    assert evts[0]["data"]["step_id"] == "1"
    assert evts[0]["data"]["level"] == "error"


def test_unknown_capability_still_emits():
    """未知 capability（如 workflow 产物）不过滤，保持向后兼容。"""
    output = {
        "9": {"capability": "some.custom", "status": "success",
              "description": "自定义步骤", "output": "结果内容"},
    }
    evts = _collect("report_skill", output)
    assert [e["data"]["step_id"] for e in evts] == ["9"]


def test_skipped_status_label():
    """skipped 状态用"跳过"标签，不再误标"失败"。"""
    output = {
        "3": {"capability": "report.generate", "status": "skipped",
              "description": "生成报告", "error": "前置步骤执行失败"},
    }
    evts = _collect("report_skill", output)
    assert len(evts) == 1
    assert "跳过" in evts[0]["data"]["message"]

"""
supervisor.py 测试 — 调度逻辑（纯逻辑，不依赖 LLM）

覆盖:
  - _check_sql_fallback(): SQL 空结果 → RAG 降级条件判断
  - supervisor_node(): 依赖检查、步骤状态转换
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent.supervisor import _check_sql_fallback, supervisor_node


# ============================================================
# _check_sql_fallback 测试
# ============================================================

class TestCheckSqlFallback:
    """SQL 空结果 → RAG 降级"""

    def test_no_fallback_when_plan_already_has_rag(self):
        """计划中已有 RAG 步骤 → 不重复降级"""
        nodes = {
            "1": {"step_id": "1", "capability": "query_database",
                  "params": {"question": "查询数据"}},
            "2": {"step_id": "2", "capability": "search_knowledge",
                  "params": {"question": "检索"}},
        }
        edges = {}
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "output": "(无结果)"},
        }
        ready = []

        new_results, new_ready, modified = _check_sql_fallback(
            nodes, edges, step_results, ready
        )
        assert not modified
        assert len(new_ready) == 0

    def test_no_fallback_when_sql_not_empty(self):
        """SQL 有结果 → 不触发降级"""
        nodes = {
            "1": {"step_id": "1", "capability": "query_database",
                  "params": {"question": "查询数据"}},
        }
        edges = {}
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "output": "| id | name |\n| 1 | 技术部 |"},
        }
        ready = []

        new_results, new_ready, modified = _check_sql_fallback(
            nodes, edges, step_results, ready
        )
        assert not modified

    def test_no_fallback_when_sql_not_success(self):
        """SQL 未成功（失败/跳过）→ 不触发降级"""
        nodes = {
            "1": {"step_id": "1", "capability": "query_database",
                  "params": {"question": "查询数据"}},
        }
        edges = {}
        step_results = {
            "1": {"capability": "query_database", "status": "failed",
                  "output": "connection error"},
        }
        ready = []

        new_results, new_ready, modified = _check_sql_fallback(
            nodes, edges, step_results, ready
        )
        assert not modified

    def test_no_fallback_when_question_not_knowledge(self):
        """SQL 空结果 + 纯数据问题（不含知识库关键词）→ 不触发降级"""
        nodes = {
            "1": {"step_id": "1", "capability": "query_database",
                  "params": {"question": "统计各部门人数"}},
        }
        edges = {}
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "output": "(无结果)"},
        }
        ready = []

        new_results, new_ready, modified = _check_sql_fallback(
            nodes, edges, step_results, ready
        )
        assert not modified  # "统计各部门人数" 不含知识库关键词

    def test_triggers_fallback_for_knowledge_question(self):
        """SQL 空结果 + 知识类问题 → 触发 RAG 降级"""
        nodes = {
            "1": {"step_id": "1", "capability": "query_database",
                  "params": {"question": "叶菜类保鲜制度有哪些"}},
        }
        edges = {}
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "description": "查询制度", "output": "(无结果)"},
        }
        ready = []

        new_results, new_ready, modified = _check_sql_fallback(
            nodes, edges, step_results, ready
        )
        assert modified
        assert len(new_ready) == 1
        assert new_ready[0]["step_id"].endswith("_rag_fallback")
        assert "1_rag_fallback" in new_results
        assert new_results["1_rag_fallback"]["status"] == "pending"

    def test_fallback_with_chinese_empty_keyword(self):
        """中文空结果关键词: 无结果"""
        nodes = {
            "1": {"step_id": "1", "capability": "query_database",
                  "params": {"question": "请假流程怎么走"}},
        }
        edges = {}
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "output": "无结果"},
        }
        ready = []

        _, _, modified = _check_sql_fallback(nodes, edges, step_results, ready)
        assert modified  # "怎么" 是知识库关键词

    def test_fallback_with_english_empty_keyword(self):
        """英文空结果关键词: 0 rows"""
        nodes = {
            "1": {"step_id": "1", "capability": "query_database",
                  "params": {"question": "什么是微服务架构"}},
        }
        edges = {}
        step_results = {
            "1": {"capability": "query_database", "status": "success",
                  "output": "Query returned 0 rows"},
        }
        ready = []

        _, _, modified = _check_sql_fallback(nodes, edges, step_results, ready)
        assert modified  # "什么是" 是知识库关键词


# ============================================================
# supervisor_node 测试
# ============================================================

class TestSupervisorNode:
    """调度节点逻辑"""

    def test_empty_plan_returns_all_done(self):
        state = {
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {},
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True

    def test_dependency_met_dispatches_step(self):
        """无依赖步骤 → 就绪派发"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询数据", "params": {}},
                },
                "edges": {},
            },
            "step_results": {},
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is False
        assert len(result["_ready_dispatch"]) == 1
        assert result["_ready_dispatch"][0]["step_id"] == "1"
        # 步骤设为 running
        assert result["step_results"]["1"]["status"] == "running"

    def test_dependency_not_met_waits(self):
        """依赖未满足 → 不派发"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询", "params": {}},
                    "2": {"step_id": "2", "capability": "generate_report",
                          "description": "报告", "params": {}},
                },
                "edges": {"2": ["1"]},  # step 2 依赖 step 1
            },
            "step_results": {},
        }
        result = supervisor_node(state)
        # step 1 无依赖 → 就绪
        ready_ids = [r["step_id"] for r in result["_ready_dispatch"]]
        assert "1" in ready_ids
        assert "2" not in ready_ids  # 依赖未满足

    def test_dep_failed_skips_step(self):
        """依赖失败 → 步骤跳过"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询", "params": {}},
                    "2": {"step_id": "2", "capability": "generate_report",
                          "description": "报告", "params": {}},
                },
                "edges": {"2": ["1"]},
            },
            "step_results": {
                "1": {"step_id": "1", "capability": "query_database",
                      "status": "failed", "error": "DB down"},
            },
        }
        result = supervisor_node(state)
        assert result["step_results"]["2"]["status"] == "skipped"

    def test_all_steps_success_triggers_done(self):
        """全部步骤成功 → _all_steps_done=True"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询", "params": {}},
                },
                "edges": {},
            },
            "step_results": {
                "1": {"step_id": "1", "capability": "query_database",
                      "status": "success", "output": "data"},
            },
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True

    def test_running_step_waits(self):
        """有运行中的步骤 → 等待"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询", "params": {}},
                },
                "edges": {},
            },
            "step_results": {
                "1": {"step_id": "1", "capability": "query_database",
                      "status": "running", "started_at": 1000.0},
            },
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is False
        assert len(result["_ready_dispatch"]) == 0

    def test_invalid_capability_fails_step(self):
        """未注册的 capability → 直接标记失败"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "unknown_cap",
                          "description": "未知能力", "params": {}},
                },
                "edges": {},
            },
            "step_results": {},
        }
        result = supervisor_node(state)
        assert result["step_results"]["1"]["status"] == "failed"
        assert "未注册" in result["step_results"]["1"]["error"]

    def test_mixed_status_count(self):
        """混合状态：部分成功 + 部分失败 → 全部完成"""
        state = {
            "plan": {
                "nodes": {
                    "1": {"step_id": "1", "capability": "query_database",
                          "description": "查询1", "params": {}},
                    "2": {"step_id": "2", "capability": "query_database",
                          "description": "查询2", "params": {}},
                },
                "edges": {},
            },
            "step_results": {
                "1": {"step_id": "1", "capability": "query_database",
                      "status": "success", "output": "ok"},
                "2": {"step_id": "2", "capability": "query_database",
                      "status": "failed", "error": "timeout"},
            },
        }
        result = supervisor_node(state)
        assert result["_all_steps_done"] is True

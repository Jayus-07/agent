# -*- coding: utf-8 -*-
"""test_domain_checkpoint_namespace.py — checkpoint namespace 隔离（STOP B B9）

背景：三图（主图/CS/旅游）共用 agent_memory 库同一组 PostgresSaver 表
（checkpoints/checkpoint_blobs/checkpoint_writes），唯一隔离手段是 thread_id
构成。锁定：
- CS 域图 thread = 裸 conversation_id；
- 旅游域图 thread = `travel:{conversation_id}`（无会话 → `travel-{uuid}`
  一次性，且逐次调用不重复——「共享比报错更危险」）；
- 同一会话的 CS / 旅游 thread 必不相等（先客服后旅游不得互相覆盖 checkpoint）；
- 主图每轮 thread = `agent-{session}-{ms}-{uuid8}`、任务 thread =
  `task-{task_id}`，与域图 thread 无重叠（resume 只发生在主图任务 thread
  上，B9 的「resume 恢复到错误域节点」结构性不存在）。
"""
from __future__ import annotations

from backend.orchestration.graph.cs_graph_node import _build_invoke_config as cs_config
from backend.orchestration.graph.travel_graph_node import (
    _build_invoke_config as travel_config,
)


def _thread(config: dict) -> str:
    return config["configurable"]["thread_id"]


class TestDomainGraphThreadIds:
    def test_cs_thread_namespaced_with_tenant_user(self):
        """2026-09-24 Platform STOP C（P0-3）：thread 扩展 tenant/user namespace
        ——裸 conversation_id 在两租户同 conv 时共享 checkpoint（实机复现
        A 厦门/B 杭州互染）。"""
        cfg = cs_config("conv-9", tenant_id="t1", user_id="u1")
        assert _thread(cfg) == "cs:t1:u1:conv-9"

    def test_cs_thread_without_tenant_keeps_legacy_shape(self):
        assert _thread(cs_config("conv-9")) == "cs:conv-9"

    def test_cs_thread_without_conversation_has_no_thread(self):
        """conversation_id 为空时 CS 不设 thread（不与任何会话共享）。"""
        assert "configurable" not in cs_config("")

    def test_travel_thread_namespaced_with_tenant_user(self):
        cfg = travel_config("conv-9", tenant_id="t1", user_id="u1")
        assert _thread(cfg) == "travel:t1:u1:conv-9"

    def test_travel_thread_fallback_unique_per_call(self):
        a = _thread(travel_config(""))
        b = _thread(travel_config(""))
        assert a.startswith("travel-")
        assert b.startswith("travel-")
        assert a != b

    def test_same_conversation_cs_and_travel_threads_differ(self):
        """namespace 隔离不变量：同会话跨域绝不共享 checkpoint thread。"""
        assert (_thread(cs_config("conv-9", tenant_id="t1", user_id="u1"))
                != _thread(travel_config("conv-9", tenant_id="t1", user_id="u1")))

    def test_same_conv_different_tenants_threads_differ(self):
        """P0-3 核心不变量：同 conv 跨租户必不同 thread。"""
        a = _thread(travel_config("conv-9", tenant_id="tA", user_id="uA"))
        b = _thread(travel_config("conv-9", tenant_id="tB", user_id="uB"))
        assert a != b

    def test_same_tenant_different_users_threads_differ(self):
        a = _thread(travel_config("conv-9", tenant_id="t1", user_id="uA"))
        b = _thread(travel_config("conv-9", tenant_id="t1", user_id="uB"))
        assert a != b

    def test_travel_thread_never_collides_with_task_thread(self):
        """task-{task_id} 与 travel:{ns}:{conv} 构成不同——任务 resume 不会落进
        旅游域图 thread（域图不在任务 resume 路径上）。"""
        assert _thread(travel_config("task-abc")) == "travel:task-abc"
        assert _thread(travel_config("task-abc")) != "task-abc"

    def test_all_domain_threads_carry_distinct_roots(self):
        """四个 thread 根（agent-/task-/cs:/travel:）两两不同。"""
        conv = "conv-x"
        main_like = f"agent-{conv}-1758690000000-deadbeef"
        task_like = f"task-{conv}"
        cs_like = _thread(cs_config(conv, tenant_id="t1", user_id="u1"))
        travel_like = _thread(travel_config(conv, tenant_id="t1", user_id="u1"))
        assert len({main_like, task_like, cs_like, travel_like}) == 4

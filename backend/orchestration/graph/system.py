"""system.py — MultiAgentSystem 运行时入口

提供 ask()（同步）和 stream_events()（SSE 流式）两种调用方式。
两者共享 GraphRunner 单一执行核心（runner.py）——双轨编排语义已收敛，
图执行 / 事件捕获 / trace 重建 / 持久化 / 错误路径全部在 runner 内单点维护。

Tracing（2026-07-16）：ask() / stream_events() 自动产出 TraceRecord + Span 树，
通过 trace_collector 统一收集，API 层无需额外处理。
"""
from __future__ import annotations

import threading
import time
from typing import Generator

from backend.config import ENABLE_TOKEN_STREAMING
from backend.orchestration.graph.builder import build_graph
from backend.orchestration.graph.runner import (
    _ANSWER_EVENT,
    GraphRunner,
    trace_from_state,
)
from backend.orchestration.tool_registry import tool_registry
from backend.shared.logger import logger


class MultiAgentSystem:
    """Multi-Agent 工作流系统入口"""

    def __init__(self):
        logger.info("[MultiAgent] 初始化 Multi-Agent 工作流系统...")
        from backend.orchestration.graph.checkpointer import build_main_checkpointer
        self._graph = build_graph(checkpointer=build_main_checkpointer())
        from backend.memory import memory_manager
        self._memory = memory_manager
        self._skill_nodes = tool_registry.get_skill_node_names()
        self._last_sources: list[dict] = []
        # 统一执行核心：ask / stream_events 共享（重构 #2）
        self._runner = GraphRunner(
            graph=self._graph,
            memory=self._memory,
            skill_nodes=self._skill_nodes,
        )

        # P1 性能优化：后台预热 SQLAgent + RAG，首次请求不再冷启动
        threading.Thread(target=self._prewarm, daemon=True, name="prewarm").start()

    def _prewarm(self) -> None:
        """后台预热：SQLAgent 连接池 + RAG 管道初始化。

        非阻塞——init 立即返回，预热在后台线程进行。
        首次请求如果预热未完成，SQLSkill/BusinessAnalysisSkill 会惰性等待。
        """
        t0 = time.time()
        try:
            from backend.sql.sql_agent import get_sql_agent
            _ = get_sql_agent()
            logger.info(f"[MultiAgent] SQLAgent 预热完成 ({(time.time()-t0)*1000:.0f}ms)")
        except Exception as e:
            logger.warning(f"[MultiAgent] SQLAgent 预热失败（非致命）: {e}")

    # =====================================================
    # 同步入口
    # =====================================================

    def ask(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        user_id: str = "default",
        model: str = "",
    ) -> str:
        """处理用户问题，返回最终 Markdown 回答。

        复用 GraphRunner 事件流（fallback_deltas=False：不做打字机增量），
        物化后取 _answer 内部事件作为回答。语义与旧同步实现一致。
        model: 按请求模型覆盖（空 = 全局默认）。
        """
        logger.info(f"[MultiAgent] 收到问题: {(question or '')[:80]}... (session={session_id}, kb={kb_id}, user={user_id})")

        events = list(self._runner.iter_events(
            question,
            session_id,
            kb_id=kb_id,
            user_id=user_id,
            fallback_deltas=False,
            model=model,
        ))

        answer = ""
        error_message = ""
        for evt in events:
            name = evt.get("event")
            if name == _ANSWER_EVENT:
                answer = evt["data"].get("answer", "")
            elif name == "done":
                self._last_sources = evt["data"].get("sources", [])
            elif name == "error":
                error_message = evt["data"].get("message", "")

        if error_message:
            if "用户中止" in error_message:
                return answer
            # 对齐旧同步实现的错误话术（error message 形如 "执行失败: ..."）
            return f"## 系统错误\n\n{error_message}\n\n请稍后重试。"
        return answer

    # =====================================================
    # SSE 流式入口
    # =====================================================

    def stream_events(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        stop_event=None,
        user_id: str = "default",
        model: str = "",
    ) -> Generator[dict, None, None]:
        """SSE 流式处理。委托 GraphRunner 统一执行核心，过滤内部事件。

        P1 真 token 级流式：skill 节点 LLM 生成期间 delta 持续流出，
        TTFT 从"整图跑完"提前到"首个生成 chunk 到达"。
        """
        for evt in self._runner.iter_events(
                question,
                session_id,
                kb_id=kb_id,
                stop_event=stop_event,
                user_id=user_id,
                fallback_deltas=True,
                model=model):
            if evt.get("event") == _ANSWER_EVENT:
                continue  # 内部事件（ask 用），不属于 SSE 协议
            yield evt

    # =====================================================
    # 兼容委托（既有测试直接调用）
    # =====================================================

    def _trace_from_state(self, trace, state: dict):
        """兼容委托：trace 重建实现已迁至 runner.trace_from_state。"""
        trace_from_state(trace, state)

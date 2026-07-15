"""
system.py — MultiAgentSystem 运行时入口

提供 ask()（同步）和 stream_events()（SSE 流式）两种调用方式。
"""

import re
import time
from typing import Generator

from backend.orchestration.graph.builder import build_graph, _parse_event
from backend.orchestration.state import AgentState
from backend.shared.logger import logger


class MultiAgentSystem:
    """Multi-Agent 工作流系统入口"""

    def __init__(self):
        logger.info("[MultiAgent] 初始化 Multi-Agent 工作流系统...")
        self._graph = build_graph()
        from backend.memory import memory_manager
        self._memory = memory_manager
        self._last_sources = []
        logger.info("[MultiAgent] 就绪")

    def ask(self, question: str, session_id: str = "default", kb_id: str = "default") -> str:
        """处理用户问题，返回最终 Markdown 回答。"""
        if not question or not question.strip():
            return "## 提示\n\n请输入有效问题。"

        logger.info(f"[MultiAgent] 收到问题: {question[:80]}... (session={session_id}, kb={kb_id})")

        l1 = self._memory.start_session(session_id, question)

        initial_state: AgentState = {
            "question": question.strip(),
            "kb_id": kb_id,
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {},
            "current_step_id": None,
            "messages": list(l1.messages),
            "final_answer": "",
            "alerts": [],
            "_supervisor_loop_count": 0,
            "_plan_critiqued": False,
            "_plan_changed": False,
        }

        try:
            final_state = self._graph.invoke(initial_state)
            answer = final_state.get("final_answer", "")
            if not answer:
                answer = self._fallback_summary(final_state)

            step_results = final_state.get("step_results", {})
            self._last_sources = self._extract_sources(step_results, answer)

            self._memory.end_turn(session_id, question, answer)
            return answer
        except Exception as e:
            logger.error(f"[MultiAgent] 执行失败: {e}")
            self._memory.end_turn(session_id, question, f"[错误] {e}")
            return f"## 系统错误\n\n处理问题失败: {e}\n\n请稍后重试。"

    @staticmethod
    def _extract_sources(step_results: dict, final_answer: str) -> list[dict]:
        from backend.orchestration.reporter.reporter import _extract_sources_from_steps
        from backend.orchestration.reporter.context_filter import parse_sources_from_text
        sources = _extract_sources_from_steps(step_results)
        if not sources and final_answer:
            sources = parse_sources_from_text(final_answer)
        return sources

    def stream_events(
        self,
        question: str,
        session_id: str = "default",
        kb_id: str = "default",
        stop_event=None,
    ) -> Generator[dict, None, None]:
        """
        SSE 流式处理：产出 4 种事件类型 (status / log / delta / done)。
        stop_event: threading.Event，前端主动断开时 set，生成器在检查点退出。
        """

        if not question or not question.strip():
            yield {"event": "error", "data": {"message": "请输入有效问题", "ts": time.time()}}
            return

        l1 = self._memory.start_session(session_id, question)
        start_time = time.time()

        initial_state: AgentState = {
            "question": question.strip(),
            "kb_id": kb_id,
            "plan": {"nodes": {}, "edges": {}},
            "step_results": {},
            "current_step_id": None,
            "messages": list(l1.messages),
            "final_answer": "",
            "alerts": [],
            "_supervisor_loop_count": 0,
            "_plan_critiqued": False,
            "_plan_changed": False,
        }

        final_answer = ""
        all_step_results = {}
        skill_set = {"sql_skill", "rag_skill", "report_skill"}

        try:
            for event in self._graph.stream(initial_state):
                if stop_event is not None and stop_event.is_set():
                    yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                    return

                node_name, node_output = _parse_event(event)
                if node_name is None:
                    continue

                # ═══ event: status ═══
                yield {"event": "status", "data": {"node": node_name, "ts": time.time()}}

                # ═══ Planner ═══
                if node_name == "planner":
                    plan = node_output.get("plan", {})
                    nodes = plan.get("nodes", {})
                    descriptions = [n.get("description", "") for n in nodes.values()]
                    yield {
                        "event": "log", "data": {
                            "level": "info", "node": node_name, "step_id": "planning",
                            "message": f"任务分解完成，共 {len(nodes)} 个子任务",
                            "payload": {"task_count": len(nodes), "tasks": descriptions},
                            "ts": time.time(),
                        },
                    }

                # ═══ Critique ═══
                elif node_name == "critique":
                    plan_changed = node_output.get("_plan_changed", False)
                    plan = node_output.get("plan", {})
                    nodes = plan.get("nodes", {})
                    yield {
                        "event": "log", "data": {
                            "level": "warn" if plan_changed else "info",
                            "node": node_name, "step_id": "critique",
                            "message": f"计划已修正，共 {len(nodes)} 个步骤" if plan_changed else "计划审查通过，无需修正",
                            "payload": {"plan_changed": plan_changed, "task_count": len(nodes)},
                            "ts": time.time(),
                        },
                    }

                # ═══ Supervisor ═══
                elif node_name == "supervisor":
                    step_results = node_output.get("step_results", {})
                    all_step_results.update(step_results)

                    for sid, sr in step_results.items():
                        status = sr.get("status", "?")
                        if status == "?":
                            continue
                        desc = sr.get("description", sid)
                        level = "info"
                        if status == "failed":
                            level = "error"
                        elif status == "skipped":
                            level = "warn"
                        payload: dict = {"description": desc, "status": status}
                        if sr.get("error"):
                            payload["error"] = sr["error"]
                        if sr.get("started_at") is not None:
                            payload["started_at"] = round(sr["started_at"], 3)
                        if sr.get("finished_at") is not None:
                            payload["elapsed"] = round(sr["finished_at"] - sr.get("started_at", sr["finished_at"]), 2)
                            payload["finished_at"] = round(sr["finished_at"], 3)
                        yield {
                            "event": "log", "data": {
                                "level": level, "node": node_name, "step_id": sid,
                                "message": f"{'完成' if status == 'success' else '失败' if status == 'failed' else '跳过' if status == 'skipped' else '派发'}: {desc}",
                                "payload": payload, "ts": time.time(),
                            },
                        }

                    ready = node_output.get("_ready_dispatch", [])
                    if ready:
                        dispatch_info = [{"step_id": r["step_id"], "skill": r["worker"]} for r in ready]
                        yield {
                            "event": "log", "data": {
                                "level": "info", "node": node_name, "step_id": "dispatch",
                                "message": f"调度 {len(ready)} 个任务到 Skill",
                                "payload": {"dispatched": dispatch_info}, "ts": time.time(),
                            },
                        }

                # ═══ Skills ═══
                elif node_name in skill_set:
                    step_results = node_output.get("step_results", {})
                    for sid, sr in step_results.items():
                        status = sr.get("status", "?")
                        if status == "?":
                            continue
                        level = "info"
                        if status == "failed":
                            level = "error"
                        elif status == "skipped":
                            level = "warn"
                        desc = sr.get("description", sid)
                        output = sr.get("output", "")
                        payload: dict = {
                            "description": desc, "status": status,
                            "output_preview": str(output)[:500] if output else "",
                        }
                        if sr.get("error"):
                            payload["error"] = sr["error"]
                        if sr.get("started_at") is not None:
                            payload["started_at"] = round(sr["started_at"], 3)
                        if sr.get("finished_at") is not None:
                            payload["elapsed"] = round(sr["finished_at"] - sr.get("started_at", sr["finished_at"]), 2)
                            payload["finished_at"] = round(sr["finished_at"], 3)
                        if isinstance(output, dict):
                            for k in ("sql", "query", "params", "row_count", "result_count", "top_k"):
                                if k in output:
                                    payload[k] = output[k]
                        yield {
                            "event": "log", "data": {
                                "level": level, "node": node_name, "step_id": sid,
                                "message": f"{'完成' if status == 'success' else '失败'}: {desc}",
                                "payload": payload, "ts": time.time(),
                            },
                        }

                # ═══ Reporter ═══
                elif node_name == "reporter":
                    final_answer = node_output.get("final_answer", "")
                    yield {
                        "event": "log", "data": {
                            "level": "info", "node": node_name, "step_id": "summary",
                            "message": f"汇总生成最终回答 ({len(final_answer)} 字符)",
                            "payload": {"char_count": len(final_answer)}, "ts": time.time(),
                        },
                    }

            # ═══ event: delta ═══
            if final_answer:
                sentences = re.split(r'(?<=[。！？\n])', final_answer)
                sentences = [s for s in sentences if s.strip()]
                for sentence in sentences:
                    if stop_event is not None and stop_event.is_set():
                        yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                        return
                    yield {"event": "delta", "data": {"content": sentence, "ts": time.time()}}
                    time.sleep(0.02)

            if stop_event is not None and stop_event.is_set():
                yield {"event": "error", "data": {"message": "用户中止", "ts": time.time()}}
                return

            # ═══ event: done ═══
            from backend.orchestration.reporter.reporter import _extract_sources_from_steps
            from backend.orchestration.reporter.context_filter import parse_sources_from_text
            elapsed = time.time() - start_time
            sources = _extract_sources_from_steps(all_step_results)
            if not sources and final_answer:
                sources = parse_sources_from_text(final_answer)
            yield {"event": "done", "data": {"elapsed": round(elapsed, 1), "sources": sources}}

        except Exception as e:
            logger.error(f"[MultiAgent] 流式执行失败: {e}")
            yield {"event": "error", "data": {"message": f"执行失败: {e}", "ts": time.time()}}
        finally:
            self._memory.end_turn(session_id, question, final_answer or "")

    def _fallback_summary(self, state: AgentState) -> str:
        step_results = state.get("step_results", {})
        lines = ["## 执行结果", ""]
        for step_id, sr in sorted(step_results.items()):
            status = sr.get("status", "?")
            desc = sr.get("description", step_id)
            if status == "success":
                lines.append(f"### {desc}")
                lines.append(str(sr.get("output", "")))
                lines.append("")
            elif status == "failed":
                lines.append(f"### {desc} ❌")
                lines.append(f"失败: {sr.get('error', '')}")
                lines.append("")
        return "\n".join(lines) if len(lines) > 2 else "## 无结果\n\n未能获取任何有效数据。"

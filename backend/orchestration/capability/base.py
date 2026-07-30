"""capability/base.py — BaseCapability 双层结构

设计原则（按企业方案 2026-07-30）：
- BaseCapability 为根抽象（统一接口 run(inputs) -> dict）
- BaseSkill: Tool-like Skill（确定性 + LangChain Tool）— 向后兼容现有 6 个 Skill
- BaseAgentSkill: Business Agent Skill（LLM 推理 + 业务结论）

不要：
- BaseSkill + is_agent_skill: bool 标识（混在一起会混乱）
- 不要在 BaseSkill 里硬塞 LLM 调用逻辑

生命周期对比：
- BaseSkill:      工具调用 + 确定性执行 + 无状态
- BaseAgentSkill: LLM 推理 + 不确定性输出 + 业务语义

LLM 调用统一走 infra/llm/proxy.py（统一 token 统计 / trace / 限流）
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from backend.shared.logger import logger


class BaseCapability(ABC):
    """所有 Capability 的根抽象

    子类只需声明:
      - name: str                       — Skill 标识
      - capabilities: list[str]         — 注册的 capability 名

    实现:
      - run(inputs: dict) -> dict       — 统一执行接口
    """

    name: ClassVar[str] = ""
    capabilities: ClassVar[list[str]] = []

    @abstractmethod
    async def run(self, inputs: dict) -> dict:
        """执行 capability（统一入口）

        Args:
            inputs: 调用参数（dict 形式，由调用方定义 schema）

        Returns:
            dict: 执行结果
        """
        ...

    async def run_with_retry(
        self,
        inputs: dict,
        max_retries: int = 2,
        timeout_sec: float = 60,
    ) -> dict:
        """带重试 + 超时的执行（BaseAgentSkill 推荐用）"""
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self.run(inputs),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError(f"{self.name} 超时 ({timeout_sec}s)")
                logger.warning(f"[{self.name}] 超时 (attempt={attempt + 1})")
            except Exception as e:
                last_error = e
                logger.warning(f"[{self.name}] 失败 (attempt={attempt + 1}): {e}")
            if attempt < max_retries:
                continue
            break
        raise last_error or RuntimeError(f"{self.name} unknown error")


class BaseAgentSkill(BaseCapability):
    """Business Agent Skill 抽象 — 用于"已知任务"推理

    与 BaseSkill 区别：
    - 输入是结构化数据（不是简单 query）
    - 输出是业务结论 + confidence（不是检索结果）
    - 内部一定调 LLM（走 infra/llm/proxy.py）

    子类示例：
        class InventoryAnalyzer(BaseAgentSkill):
            name = "inventory_analyzer"
            capabilities = ["inventory.analyze"]

            async def _reason(self, sales, inventory, rules):
                # 调 LLM 推理
                return {"anomalies": [...], "confidence": 0.85}

            async def run(self, inputs):
                return await self._reason(
                    sales=inputs["sales"],
                    inventory=inputs["inventory"],
                    rules=inputs.get("rules", ""),
                )
    """

    @abstractmethod
    async def run(self, inputs: dict) -> dict:
        """子类实现：调 LLM 推理 + 返回业务结论"""
        ...

    async def _call_llm(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> str:
        """统一 LLM 调用入口（走 infra/llm/proxy.py）

        所有 Agent Skill 必须通过此方法调 LLM，而不是直接实例化 ChatOpenAI 等。
        这样保证 token 统计 / trace / 限流 / 模型切换统一管理。
        """
        from backend.infra.llm import llm
        from backend.infra.llm.proxy import _last_call_meta

        # 清空上次 meta，准备记录本次调用
        for k in list(_last_call_meta.keys()):
            _last_call_meta.pop(k, None)

        # 构造 messages（OpenAI 格式）
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await llm.ainvoke(messages)
            content = (
                response.content
                if hasattr(response, "content")
                else str(response)
            )
            # 记录 token 用量（由 proxy 自动捕获）
            meta = {
                "prompt_tokens": _last_call_meta.get("prompt_tokens", 0),
                "completion_tokens": _last_call_meta.get("completion_tokens", 0),
                "model": _last_call_meta.get("model", ""),
            }
            logger.debug(
                f"[{self.name}] LLM 调用完成: {meta['prompt_tokens']}+"
                f"{meta['completion_tokens']} tokens"
            )
            return content if isinstance(content, str) else str(content)
        except Exception as e:
            logger.error(f"[{self.name}] LLM 调用失败: {e}")
            raise


# 类型别名（向后兼容）
__all__ = [
    "BaseCapability",
    "BaseAgentSkill",
]


# 辅助：让现有 BaseSkill 代码可以"看起来"继承自 BaseCapability
# （实际 BaseSkill 在 skills/base.py，仍是 ABC，但提供 is_capability=True 标识）
def is_capability(obj: Any) -> bool:
    return isinstance(obj, BaseCapability)
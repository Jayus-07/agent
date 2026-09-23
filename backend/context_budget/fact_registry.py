"""context_budget.fact_registry — ProtectedFactRegistry（P2-3，2026-09-23）

统一的关键事实登记表：L5 摘要的关键实体不再只依赖单一正则链，也不再
依赖 LLM「自觉记住」。事实来源（优先级从高到低）：

  1. domain_state / confirmation   域图结构化状态（确认态、工单态）
  2. business_context              结构化业务上下文（订单/预算实体）
  3. structured_tool_output        工具结构化输出里的关键 ID
  4. user_constraint               用户显式约束（「预算不超过 X」）
  5. regex                         会话文本确定性抽取（既有链路，保留）

LLM 只负责语言摘要；事实数据库由 Registry 承担。摘要后确定性校验 +
追加补丁的既有语义不变（零额外 LLM 调用）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# 事实来源枚举（低基数，可进 Prometheus label）
SOURCE_REGEX = "regex"
SOURCE_BUSINESS = "business_context"
SOURCE_TOOL_OUTPUT = "structured_tool_output"
SOURCE_DOMAIN_STATE = "domain_state"
SOURCE_USER_CONSTRAINT = "user_constraint"
SOURCE_CONFIRMATION = "confirmation"

PRIORITY_NORMAL = "normal"
PRIORITY_CRITICAL = "critical"


@dataclass
class ProtectedFactEntry:
    """一条受保护事实（结构统一，来源/优先级显式）。"""

    type: str                    # 金额/订单号/SKU/百分比/日期/…（与既有正则口径一致）
    value: str
    semantic_role: str | None = None   # 如 budget（语义金额标签）
    source_message_id: int | str | None = None
    source: str = SOURCE_REGEX
    priority: str = PRIORITY_NORMAL

    def matches(self, summary_lower: str) -> bool:
        return self.value.lower() in summary_lower

    def to_prompt_line(self) -> str:
        if self.semantic_role:
            return f"- {self.type}（{self.semantic_role}）: {self.value}"
        return f"- {self.type}: {self.value}"


@dataclass
class RegistryOutcome:
    facts: list[ProtectedFactEntry] = field(default_factory=list)
    missing: list[ProtectedFactEntry] = field(default_factory=list)
    patched_summary: str | None = None


class ProtectedFactRegistry:
    """关键事实集合：去重合并多来源，摘要前注入 prompt，摘要后校验补丁。"""

    def __init__(self) -> None:
        self._facts: list[ProtectedFactEntry] = []
        self._seen: set[tuple[str, str]] = set()

    # ── 登记入口 ────────────────────────────────────────────

    def add(self, entry: ProtectedFactEntry) -> bool:
        key = (entry.type, entry.value)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._facts.append(entry)
        return True

    def add_regex_facts(self, regex_facts: Iterable[Any]) -> None:
        """收编既有 extract_protected_facts 的产出（结构兼容映射）。"""
        for f in regex_facts or []:
            self.add(ProtectedFactEntry(
                type=getattr(f, "type", "其他"),
                value=str(getattr(f, "value", "")),
                semantic_role=getattr(f, "label", None),
                source_message_id=getattr(f, "source_message_id", None),
                source=SOURCE_REGEX,
            ))

    def add_business_fact(
        self, fact_type: str, value: str, *,
        semantic_role: str | None = None,
        source: str = SOURCE_BUSINESS,
        priority: str = PRIORITY_CRITICAL,
        source_message_id: int | str | None = None,
    ) -> bool:
        """业务态/确认态/结构化工具输出的关键事实（默认 critical）。"""
        return self.add(ProtectedFactEntry(
            type=fact_type, value=str(value), semantic_role=semantic_role,
            source_message_id=source_message_id, source=source,
            priority=priority,
        ))

    # ── 消费出口 ────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._facts)

    @property
    def facts(self) -> list[ProtectedFactEntry]:
        return list(self._facts)

    def by_source(self, source: str) -> list[ProtectedFactEntry]:
        return [f for f in self._facts if f.source == source]

    def to_prompt_text(self, max_facts: int = 40) -> str:
        """渲染进摘要 prompt（critical 优先，其余按登记序）。"""
        if not self._facts:
            return "（无）"
        ordered = (
            [f for f in self._facts if f.priority == PRIORITY_CRITICAL]
            + [f for f in self._facts if f.priority != PRIORITY_CRITICAL]
        )
        return "\n".join(f.to_prompt_line() for f in ordered[:max_facts])

    def validate_and_patch(self, summary: str) -> RegistryOutcome:
        """摘要后确定性校验：遗漏事实追加 [关键实体] 小节（零额外 LLM）。"""
        if not self._facts:
            return RegistryOutcome(facts=[], missing=[],
                                   patched_summary=summary)
        lowered = (summary or "").lower()
        missing = [f for f in self._facts if not f.matches(lowered)]
        patched = summary
        if missing:
            patch = "\n\n[关键实体]\n" + "\n".join(
                f.to_prompt_line() for f in missing)
            patched = summary + patch
        return RegistryOutcome(facts=self._facts, missing=missing,
                               patched_summary=patched)

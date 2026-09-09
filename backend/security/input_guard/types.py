"""types.py — Input Guard 统一结果模型

GuardResult 是 Guard 层与上层（API / MultiAgentSystem）之间唯一的
契约对象，字段设计对齐 orchestrator 的 RouteDecision（pydantic v2）。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class GuardAction(str, Enum):
    """Guard 决策动作。

    ALLOW   — 进入正常链路（Router → Planner → ...）
    CLARIFY — 短路：请求模糊/边界，向用户返回澄清话术
    BLOCK   — 短路：安全拦截（注入/有害/格式违规），附 Audit
    DEGRADE — 放行但受限：标记风险进入链路（敏感域预判/超范围），
              真正的权限/拦截由下游既有机制（行级安全、Evidence Gate）执行
    """

    ALLOW = "allow"
    CLARIFY = "clarify"
    BLOCK = "block"
    DEGRADE = "degrade"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GuardCategory(str, Enum):
    """问题分类（与动作正交：同一类别可能因置信度不同走不同动作）。"""

    BUSINESS_QUERY = "business_query"   # 正常业务问题
    GREETING = "greeting"               # 问候/能力咨询
    CHITCHAT = "chitchat"               # 闲聊
    OUT_OF_SCOPE = "out_of_scope"       # 超出系统能力范围
    GARBAGE = "garbage"                 # 无意义垃圾输入
    PROMPT_INJECTION = "prompt_injection"
    HARMFUL = "harmful"                 # 真正要求执行恶意行为
    SENSITIVE_DATA = "sensitive_data"   # 敏感域/越权嫌疑（仅预判）
    INVALID = "invalid"                 # 空输入
    TOO_LONG = "too_long"               # 超长/超 token
    ABNORMAL = "abnormal"               # 异常 Unicode / 控制字符 / 重复轰炸
    AMBIGUOUS = "ambiguous"             # 模糊问题（需要澄清）


class GuardResult(BaseModel):
    """Input Guard 统一结果。"""

    action: GuardAction
    category: GuardCategory
    risk_level: RiskLevel = RiskLevel.LOW
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str = ""
    # NFKC 归一化 + 零宽剥离后的查询（供下游/审计使用）
    normalized_query: str = ""
    # 面向用户的短路话术（BLOCK/CLARIFY 时非空）
    message: str = ""
    # 决策来源层：rule | llm | fallback（Guard 自身异常兜底）
    layer: str = "rule"
    # 敏感域预判（交给 Permission Guard 的输入；Guard 本身不做权限判定）
    domain: str | None = None
    sensitivity: RiskLevel | None = None
    # True 表示该请求理论上需要权限系统校验（当前项目无 RBAC，仅预留标记）
    needs_permission: bool = False
    policy_version: str = "1.0"
    # LLM Guard 是否参与决策（指标统计：LLM Guard 调用率）
    llm_consulted: bool = False

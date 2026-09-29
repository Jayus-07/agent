"""travel/agents/ — Requirement Agent 及后续业务 Agent 包（v3 §1 / Phase 2）

Phase 2 只落 RequirementAgent：自然语言需求理解 / 槽位抽取 / 缺失检测 /
TripBrief 生成。合并、指纹、版本与变更追踪在 services/requirement_service.py；
重开/取消会话信号在 core/intent_signals.py。本包禁止 import graph_builder /
orchestration（只被上层调用）。
"""

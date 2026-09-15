"""config/guard.py — Input Guard（Query Guard）配置

输入侧安全与质量门禁的运行时配置。设计原则：
- 默认值安全、保守：Guard 总开关默认开启，格式/注入/范围检测默认开启
- LLM Guard 默认关闭（避免每个问题都走 LLM 造成延迟/成本/吞吐劣化；
  边界问题在 LLM Guard 关闭时降级为 CLARIFY，宁可多问一句也不放行或误杀）
- 阈值全部可经 .env 覆盖，不硬编码在检测逻辑中
"""
import os


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


# ── 总开关 ──────────────────────────────────────────────
# Guard 整体开关（关闭后请求直通，仅建议排障时临时使用）
ENABLE_INPUT_GUARD = _env_bool("ENABLE_INPUT_GUARD", "true")

# ── 格式检查 ────────────────────────────────────────────
# 最大字符数（与 ChatRequest.max_length 对齐，双重防御）
GUARD_MAX_INPUT_CHARS = int(os.getenv("GUARD_MAX_INPUT_CHARS", "2000"))
# 最大 token 数（tiktoken cl100k_base 估算）
GUARD_MAX_INPUT_TOKENS = int(os.getenv("GUARD_MAX_INPUT_TOKENS", "2000"))
# 单一字符重复占比阈值（超过判定为大量重复垃圾输入）
GUARD_MAX_REPEAT_RATIO = float(os.getenv("GUARD_MAX_REPEAT_RATIO", "0.7"))
# 触发重复检测的最小长度（短串如"哈哈"不检测）
GUARD_REPEAT_MIN_LEN = int(os.getenv("GUARD_REPEAT_MIN_LEN", "16"))

# ── 分层检测开关 ────────────────────────────────────────
# Prompt Injection / 有害请求 规则检测
ENABLE_INJECTION_GUARD = _env_bool("ENABLE_INJECTION_GUARD", "true")
# 业务范围 / 闲聊 / 垃圾输入识别
ENABLE_SCOPE_GUARD = _env_bool("ENABLE_SCOPE_GUARD", "true")
# LLM Guard：只处理规则层无法拍板的边界问题（默认关闭）
ENABLE_LLM_GUARD = _env_bool("ENABLE_LLM_GUARD", "false")
# 规则层置信度低于该阈值的"边界判定"才允许进入 LLM Guard
LLM_GUARD_THRESHOLD = float(os.getenv("LLM_GUARD_THRESHOLD", "0.75"))
# LLM Guard 单次推理超时（秒）
LLM_GUARD_TIMEOUT = int(os.getenv("LLM_GUARD_TIMEOUT", "10"))

# ── 策略版本（写入 Audit / Trace，便于回溯规则变更）──────
GUARD_POLICY_VERSION = os.getenv("GUARD_POLICY_VERSION", "1.1")  # 1.1: vague 误杀整改（查询意图豁免+组织域名词）

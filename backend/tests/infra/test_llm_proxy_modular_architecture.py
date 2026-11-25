"""
test_llm_proxy_modular_architecture.py — LLMProxy 模块化架构守卫单测
"""
import ast
from pathlib import Path


class TestLLMProxyArchitectureGuard:
    """守护 LLM Proxy 门面架构，防止代码膨胀与契约漂移。"""

    def test_proxy_line_count_within_budget(self):
        proxy_path = Path("backend/infra/llm/proxy.py")
        assert proxy_path.exists(), "proxy.py 文件必须存在"
        lines = proxy_path.read_text(encoding="utf-8").splitlines()
        # 原 2710 行，要求门面重构后行数严格控制在 700 行以内（大幅缩减 74% 以上）
        assert len(lines) <= 700, f"proxy.py 行数 ({len(lines)}) 超出 700 行预算"

    def test_proxy_exports_all_required_symbols(self):
        import backend.infra.llm.proxy as proxy

        required_symbols = [
            # 门面核心
            "llm",
            "get_llm",
            "_LLMProxy",
            "_BoundLLMProxy",
            # 配置常量
            "LLM_ALLOW_DEGRADED_ANSWER",
            "LLM_MAX_RETRIES",
            "LLM_RETRY_BACKOFF_BASE",
            "LLM_FALLBACK_MODEL",
            "LLM_CONTEXT_LENGTH",
            "OLLAMA_ENABLED",
            # 上下文与诊断
            "set_current_user_id",
            "scoped_current_user_id",
            "_thread_local_user_id",
            "set_request_model",
            "get_request_model_name",
            "run_with_role_cancellation",
            "scoped_call_diagnostics",
            "invalidate_runtime_caches",
            # 流式
            "set_stream_sink",
            "reset_stream_sink",
            "emit_stream_delta",
            "extract_chunk_text",
            "extract_chunk_reasoning",
            # 计费与审计
            "_record_tokens",
            "record_llm_result",
            "_turn_usage_var",
            "get_turn_usage",
            "reset_turn_usage",
            # 韧性与熔断
            "_call_with_resilience",
            "_acall_with_resilience",
            "is_degraded_response",
            # 预算与限流
            "RateLimitError",
            "_enforce_rate_limit",
            "_preflight_context",
            "_apreflight_context",
            "enforce_model_input_price_tier",
            # 模型解析与绑定
            "get_active_model_name",
            "get_llm_for_role",
            "bind_tools_for_model",
            "bind_llm_for_role",
        ]
        for symbol in required_symbols:
            assert hasattr(proxy, symbol), f"proxy 必须导出符号: {symbol}"

    def test_gateway_submodules_exist_and_importable(self):
        from backend.infra.llm.gateway import (
            accounting,
            bound_proxy,
            budget_guard,
            context,
            resilience,
            streaming,
        )

        assert accounting is not None
        assert bound_proxy is not None
        assert budget_guard is not None
        assert context is not None
        assert resilience is not None
        assert streaming is not None

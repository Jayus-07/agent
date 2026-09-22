"""Phase 5 生产硬化测试（2026-09-22）

覆盖（§37）：
- Config：L5 开关默认值 / timeout 默认值 / 模型角色解析链（DB → config → inherit）/ env override
- Amount：裸金额识别 / 裸金额更新（最新值优先 supersede）/ 大数字非金额不误判
- L5 Disabled：kill switch 下不调用 LLM、确定性 fallback 生效
- Non-thinking：qwen 系摘要调用显式带 enable_thinking=False + max_tokens + 低温
- Provider unavailable：摘要模型不可用 → 安全回退，主聊天不受阻
"""
import pytest

import backend.config as config
import backend.context_budget.auto_compact as ac_mod
from backend.context_budget.auto_compact import (
    extract_protected_facts,
    format_facts_for_prompt,
    run_incremental_summary,
    _provider_supports_thinking_flag,
    _resolve_summary_model,
    _summary_invoke_kwargs,
)
from backend.config import model_roles


# ── Config（§四/§五）────────────────────────────────────────────

_CONTEXT_KEYS = (
    "CONTEXT_L5_ENABLED",
    "CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS",
    "CONTEXT_L5_SUMMARY_MAX_TOKENS",
    "CONTEXT_L5_SUMMARY_TEMPERATURE",
)


@pytest.fixture()
def _clean_memory_config(monkeypatch, tmp_path):
    """隔离 .env（load_dotenv）与环境变量，reload 后读纯代码默认值。"""
    for key in _CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    # load_dotenv 从调用方文件位置向上搜索 .env，chdir 无效 → 直接短路
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    import importlib
    importlib.reload(config.memory)
    yield config.memory
    importlib.reload(config.memory)  # 恢复真实配置态


class TestConfigFormalization:
    def test_l5_enabled_default_true(self, _clean_memory_config):
        assert _clean_memory_config.CONTEXT_L5_ENABLED is True

    def test_timeout_default_30(self, _clean_memory_config):
        assert _clean_memory_config.CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS == 30

    def test_summary_max_tokens_and_temperature_defaults(
            self, _clean_memory_config):
        m = _clean_memory_config
        assert m.CONTEXT_L5_SUMMARY_MAX_TOKENS == 512
        assert m.CONTEXT_L5_SUMMARY_TEMPERATURE == 0.2

    def test_env_override_takes_effect(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONTEXT_L5_SUMMARY_MAX_TOKENS", "256")
        monkeypatch.chdir(tmp_path)
        import importlib
        importlib.reload(config.memory)
        try:
            assert config.memory.CONTEXT_L5_SUMMARY_MAX_TOKENS == 256
        finally:
            importlib.reload(config.memory)

    def test_all_configs_exported_from_backend_config(self):
        """§四：全部配置在 backend.config 有正式定义（re-export 可读）。"""
        for name in ("CONTEXT_BUDGET_ENABLED", "CONTEXT_L5_ENABLED",
                     "CONTEXT_L5_SUMMARY_TIMEOUT_SECONDS",
                     "CONTEXT_L5_SUMMARY_MODEL",
                     "CONTEXT_L5_SUMMARY_MAX_TOKENS",
                     "CONTEXT_L5_SUMMARY_TEMPERATURE",
                     "CONTEXT_OUTPUT_RESERVE_TOKENS",
                     "CONTEXT_SAFETY_RESERVE_TOKENS",
                     "TOOL_INLINE_MAX_TOKENS", "TOOL_PREVIEW_MAX_TOKENS",
                     "PREVIOUS_OUTPUTS_MAX_TOKENS",
                     "CONTEXT_L4_TRIGGER_RATIO", "CONTEXT_L5_TRIGGER_RATIO",
                     "CONTEXT_L5_TARGET_RATIO", "CONTEXT_L4_KEEP_RECENT_TURNS"):
            assert hasattr(config, name), f"backend.config 缺少 {name}"

    def test_role_registered_and_resolves(self):
        """context_compactor 角色已注册；无 DB 绑定时 inherit main。"""
        assert "context_compactor" in model_roles.MODEL_ROLES
        model_roles.reset_overrides()
        try:
            info = model_roles.resolve_effective("context_compactor")
            assert info["source"].startswith(model_roles.SOURCE_INHERIT) \
                or info["source"] == model_roles.SOURCE_DEFAULT
        finally:
            model_roles.reset_overrides()


# ── 模型解析链（§七）────────────────────────────────────────────

class TestSummaryModelResolution:
    @pytest.fixture(autouse=True)
    def _clean(self):
        model_roles.reset_overrides()
        yield
        model_roles.reset_overrides()

    def test_db_binding_highest_priority(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_L5_SUMMARY_MODEL", "env-model")
        model_roles.set_override("context_compactor", "db-model")
        assert _resolve_summary_model() == "db-model"

    def test_config_fallback_before_inherit(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_L5_SUMMARY_MODEL", "env-model")
        assert _resolve_summary_model() == "env-model"

    def test_inherit_main_when_no_binding_no_config(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_L5_SUMMARY_MODEL", "")
        assert _resolve_summary_model()  # 继承 main 的模型名（非空）

    def test_role_resolution_failure_falls_back_to_config(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_L5_SUMMARY_MODEL", "env-model")
        real = model_roles.resolve_effective

        def _boom(role):
            raise RuntimeError("registry down")

        monkeypatch.setattr(model_roles, "resolve_effective", _boom)
        assert _resolve_summary_model() == "env-model"


# ── 摘要调用参数收口（§八/§十）──────────────────────────────────

class TestSummaryInvokeKwargs:
    def test_qwen_model_disables_thinking(self, monkeypatch):
        monkeypatch.setattr(config, "CONTEXT_L5_SUMMARY_MAX_TOKENS", 512)
        monkeypatch.setattr(config, "CONTEXT_L5_SUMMARY_TEMPERATURE", 0.2)
        kwargs = _summary_invoke_kwargs("qwen3.8-flash")
        assert kwargs["max_tokens"] == 512
        assert kwargs["temperature"] == 0.2
        assert kwargs["extra_body"] == {"enable_thinking": False}

    def test_non_qwen_model_no_thinking_param(self, monkeypatch):
        kwargs = _summary_invoke_kwargs("doubao-seed-2.0-mini")
        assert "extra_body" not in kwargs
        assert kwargs["max_tokens"] == 512

    def test_provider_flag_detection(self):
        assert _provider_supports_thinking_flag("qwen3.7-flash") is True
        assert _provider_supports_thinking_flag("") is False

    def test_invoke_passes_kwargs(self, monkeypatch):
        """端到端：llm.invoke 收到关 thinking 的调用参数。"""
        captured: dict = {}

        class _FakeLLM:
            def invoke(self, prompt, **kwargs):
                captured.update(kwargs)

                class _R:
                    content = "[用户目标]\n摘要"
                    response_metadata = {"token_usage": {}}
                return _R()

        import backend.infra.llm as llm_pkg
        monkeypatch.setattr(llm_pkg, "llm", _FakeLLM())
        monkeypatch.setattr(ac_mod, "_resolve_summary_model",
                            lambda: "qwen3.8-flash")
        store = _FakeStore(rows=_DELTA_ROWS)
        run_incremental_summary("s-1", store)
        assert captured.get("extra_body") == {"enable_thinking": False}
        assert captured.get("max_tokens") == 512


# ── 裸金额（§15-§18）────────────────────────────────────────────

class TestBareAmount:
    def test_should_protect(self):
        rows = [(1, "预算 100,000"), (2, "预算不超过100,000"),
                (3, "退款是100000"), (4, "退款金额 1,299.50"),
                (5, "最高成本 50000"), (6, "售价 8999"),
                (7, "上限 50000，下限 10000")]
        values = {f.value for f in extract_protected_facts(rows)
                  if f.type == "金额"}
        assert {"100,000", "100000", "1,299.50", "50000", "8999", "10000"} \
            <= values

    def test_should_not_misjudge_big_numbers(self):
        rows = [(1, "订单号 100000"), (2, "用户 ID 100000"),
                (3, "SKU 100000"), (4, "库存数量 100000"),
                (5, "错误码 100000"), (6, "库存还有 35件"),
                (7, "价格涨了 18%"), (8, "预算 2026-10-15 前到位")]
        values = {f.value for f in extract_protected_facts(rows)
                  if f.type == "金额"}
        assert "100000" not in values
        assert "35件" not in values  # 数量规则管，不归金额
        assert "2026" not in values  # 日期不误判为金额

    def test_amount_update_latest_wins(self):
        """「预算 100,000」→「预算改成 120,000」：只保留 120,000。"""
        rows = [
            (1, "user", )[:2] + () if False else (1, "预算 100,000"),
        ]
        rows = [(1, "预算 100,000"), (2, "好的，总预算 100,000 已记录"),
                (5, "预算改成 120,000")]
        amounts = [f for f in extract_protected_facts(rows) if f.label == "预算"]
        assert len(amounts) == 1
        assert amounts[0].value == "120,000"
        assert amounts[0].source_message_id == 5

    def test_currency_rules_unchanged(self):
        """既有货币规则不被新规则覆盖：¥5000 / 5000元 / $100 照旧。"""
        rows = [(1, "预算 ¥5000，退款 3000元，押金 $100")]
        facts = extract_protected_facts(rows)
        values = {f.value for f in facts if f.type == "金额"}
        assert "¥5000" in values
        assert "3000元" in values
        assert "$100" in values

    def test_prompt_rendering_includes_label(self):
        facts = extract_protected_facts([(1, "预算 100,000")])
        text = format_facts_for_prompt(facts)
        assert "预算" in text and "100,000" in text


# ── L5 kill switch（§五/§27）────────────────────────────────────

class _FakeStore:
    def __init__(self, summary=None, through_id=None, boundary_id=100,
                 rows=None):
        self.summary, self.through_id = summary, through_id
        self.boundary_id, self.rows = boundary_id, rows or []
        self.load_calls, self.saved = [], None

    def get_summary_state(self):
        return {"summary": self.summary, "through_id": self.through_id,
                "token_count": None}

    def summarizable_before_id(self, keep_recent_turns):
        return self.boundary_id

    def load_delta_messages(self, after_id, before_id, limit):
        self.load_calls.append((after_id, before_id, limit))
        return self.rows

    def save_summary_state(self, summary, through_id, token_count):
        self.saved = {"summary": summary, "through_id": through_id,
                      "token_count": token_count}
        return True


_DELTA_ROWS = [
    (31, "user", "帮我查订单 ORD20260922001 的退款进度，预算 100,000"),
    (32, "assistant", "好的，已记录"),
]


class TestL5KillSwitch:
    def test_disabled_no_llm_call(self, monkeypatch):
        """CONTEXT_L5_ENABLED=false：L1-L4 保持，摘要 API=0，确定性回退。"""
        monkeypatch.setattr(config, "CONTEXT_L5_ENABLED", False)
        calls: list = []

        def _no_llm(prompt):
            calls.append(prompt)
            raise AssertionError("开关关闭不得调用 LLM")

        from backend.context_budget.manager import ContextBudgetManager
        m = ContextBudgetManager()
        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout", _no_llm)
        m._run_l5 = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("disabled 不得进入 _run_l5"))
        msgs, used = m._maybe_auto_compact(
            list(range(5)), used=int(0.95 * 3072), budget=3072)
        assert calls == []
        assert used == int(0.95 * 3072)  # 消息原样返回（确定性降级）


# ── Provider unavailable（§九）──────────────────────────────────

class TestProviderUnavailableFallback:
    def test_timeout_safe_fallback(self, monkeypatch):
        def _timeout(prompt):
            raise TimeoutError("L5 摘要 LLM 调用超时（30s）")

        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout", _timeout)
        store = _FakeStore(summary="旧摘要", through_id=30, boundary_id=40,
                           rows=_DELTA_ROWS)
        outcome = run_incremental_summary("s-1", store)
        assert outcome is None
        assert store.saved is None, "失败不得推进水位线"

    def test_provider_error_safe_fallback(self, monkeypatch):
        def _boom(prompt):
            raise RuntimeError("provider 5xx")

        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout", _boom)
        store = _FakeStore(summary="旧摘要", through_id=30, boundary_id=40,
                           rows=_DELTA_ROWS)
        assert run_incremental_summary("s-1", store) is None
        assert store.saved is None

    def test_empty_summary_safe_fallback(self, monkeypatch):
        class _Empty:
            content = "   "
            response_metadata = {}

        monkeypatch.setattr(ac_mod, "_invoke_llm_with_timeout",
                            lambda prompt: _Empty())
        store = _FakeStore(summary="旧摘要", through_id=30, boundary_id=40,
                           rows=_DELTA_ROWS)
        assert run_incremental_summary("s-1", store) is None
        assert store.saved is None

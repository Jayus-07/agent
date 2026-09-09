"""test_fixtures.py — 验证 CS 测试 fixtures 本身正确工作。

确保 Phase 2 测试依赖的基础设施（CS_ENABLED 切换、DB mock、
LLM mock、空 KB 处理）在真实测试中可用。
"""
import pytest


class TestCsEnabledToggle:
    def test_cs_enabled_fixture_sets_true(self, cs_enabled):
        import backend.config as cfg
        assert cfg.CS_ENABLED is True

    def test_cs_disabled_fixture_sets_false(self, cs_disabled):
        import backend.config as cfg
        assert cfg.CS_ENABLED is False

    def test_toggle_isolation(self, cs_enabled):
        """cs_enabled fixture 只影响当前测试，不影响其他测试。"""
        import backend.config as cfg
        assert cfg.CS_ENABLED is True


class TestDbMock:
    def test_mock_db_pool_has_expected_methods(self, mock_db_pool):
        assert hasattr(mock_db_pool, "fetchrow")
        assert hasattr(mock_db_pool, "fetch")
        assert hasattr(mock_db_pool, "execute")

    @pytest.mark.asyncio
    async def test_mock_db_pool_fetchrow_returns_none(self, mock_db_pool):
        result = await mock_db_pool.fetchrow("SELECT 1")
        assert result is None

    @pytest.mark.asyncio
    async def test_mock_db_pool_configurable(self, mock_db_pool):
        mock_db_pool.fetchrow.return_value = {"status": "active"}
        result = await mock_db_pool.fetchrow("SELECT status FROM t")
        assert result == {"status": "active"}


class TestLlmMock:
    def test_mock_llm_response_default(self, mock_llm_response):
        result = mock_llm_response()
        assert result()["intent"] == "KNOWLEDGE"

    def test_mock_llm_response_custom_intent(self, mock_llm_response):
        mock = mock_llm_response("AFTER_SALES", confidence=0.7)
        result = mock()
        assert result["intent"] == "AFTER_SALES"
        assert result["confidence"] == 0.7

    def test_mock_rag_chain_default(self, mock_rag_chain):
        chain = mock_rag_chain()
        assert chain._last_meta["can_answer"] is True

    @pytest.mark.asyncio
    async def test_mock_rag_chain_custom_answer(self, mock_rag_chain):
        chain = mock_rag_chain(answer="退货窗口30天。")
        result = await chain.ask("退货政策？")
        assert result == "退货窗口30天。"


class TestEmptyKB:
    def test_empty_knowledge_bases(self, empty_knowledge_bases):
        import backend.config.customer_service as cs_mod
        assert cs_mod.CS_KNOWLEDGE_BASES == {}

    def test_single_kb(self, single_kb):
        import backend.config.customer_service as cs_mod
        assert len(cs_mod.CS_KNOWLEDGE_BASES) == 1
        assert "cs_faq" in cs_mod.CS_KNOWLEDGE_BASES

    def test_kb_isolation(self):
        """KB fixture 不影响未使用该 fixture 的测试。"""
        import backend.config.customer_service as cs_mod
        assert len(cs_mod.CS_KNOWLEDGE_BASES) == 6


class TestAssertCsError:
    def test_assert_cs_error_passes(self, assert_cs_error):
        from backend.customer_service.errors import ValidationError
        assert_cs_error(ValidationError(), "VALIDATION_ERROR")

    def test_assert_cs_error_wrong_type(self, assert_cs_error):
        with pytest.raises(AssertionError):
            assert_cs_error(ValueError("not CS error"))

    def test_assert_cs_error_wrong_code(self, assert_cs_error):
        from backend.customer_service.errors import ValidationError
        with pytest.raises(AssertionError):
            assert_cs_error(ValidationError(), "AUTH_FAILED")

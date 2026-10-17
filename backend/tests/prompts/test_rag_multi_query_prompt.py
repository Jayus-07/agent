"""rag.multi_query Prompt 契约（PR #52，Phase A）。

旧模板只要求"语义等价但表达不同"，实测三条几乎都在复述原问题（同一个
问题换三种问法），MultiQuery 因此拿不到新的证据面。本文件锁死新模板的
可验证契约，并验证改写结果不会被既有的解析/去重/合法性/数量过滤误杀。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from backend.prompts.loader import load_defaults
from backend.prompts.registry import PROMPT_REGISTRY
from backend.prompts.renderer import PromptRenderer

KEY = "rag.multi_query"

PURCHASE_Q = "采购新品上架需要哪些部门协作？"
REFUND_Q = "商品质量问题的退货运费由谁承担？"


def _template() -> str:
    return load_defaults()[KEY]


def _render(count: int, question: str) -> str:
    spec = PROMPT_REGISTRY.get(KEY)
    return PromptRenderer().render(_template(), {"count": count, "question": question}, spec=spec)


class TestPromptContract:
    def test_registry_variables_unchanged(self):
        """变量契约保持 count + question，模板引擎仍是 str.format。"""
        spec = PROMPT_REGISTRY.get(KEY)
        assert [v.name for v in spec.variables] == ["count", "question"]
        raw = yaml.safe_load(
            open("backend/prompts/defaults/rag_multi_query.yaml", encoding="utf-8")
        )
        assert raw["key"] == KEY
        assert raw["template_engine"] == "str_format"
        assert [v["name"] for v in raw["variables"]] == ["count", "question"]

    @pytest.mark.parametrize("count", [1, 2, 3, 5])
    def test_renders_for_every_configured_count(self, count):
        """count 不为 3 时也必须可渲染，且条数由 {count} 决定而非硬编码。"""
        text = _render(count, PURCHASE_Q)
        assert f"扩展为 {count} 条" in text
        assert f"前 {count} 条" in text
        assert PURCHASE_Q in text

    def test_original_query_must_be_first(self):
        text = _render(3, PURCHASE_Q)
        assert "原问题必须是第一条" in text
        assert "第1条：完整保留原始问题。" in text

    def test_evidence_faces_are_orthogonal_not_paraphrase(self):
        """两条改写分别覆盖"部门/职责/流程"与"资质/质检/合规"证据面。"""
        text = _render(3, PURCHASE_Q)
        for word in ("参与部门", "岗位角色", "职责分工", "业务流程", "审批环节"):
            assert word in text
        for word in ("资质", "证明材料", "质检标准", "合规认证", "业务条件"):
            assert word in text
        assert "禁止仅仅变换语序" in text

    def test_no_fabrication_guardrails(self):
        text = _render(3, PURCHASE_Q)
        assert "不得虚构用户未提供的金额、时限" in text
        assert "不得预先认定某部门、资质或标准必然存在" in text

    def test_simple_question_is_not_forced_to_expand(self):
        text = _render(3, "头疼应该吃什么药？")
        assert "对简单事实问题，不强制添加无关部门或认证" in text
        # 模板本身不因问题文本而改变规则（无按题硬编码分支）
        assert text.replace("头疼应该吃什么药？", "") == _render(
            3, PURCHASE_Q
        ).replace(PURCHASE_Q, "")

    def test_output_format_is_plain_line_per_query(self):
        text = _render(3, PURCHASE_Q)
        assert "每行一条纯文本查询，不要编号" in text
        assert "不回答问题，不解释，不输出证据结论" in text

    @pytest.mark.parametrize(
        "forbidden",
        ["RC-024", "RC-005", "pr_smoke", "AQL", "CE / FCC", "CE/FCC",
         "returns_退货政策", "qc_质检标准SOP", "purchase_采购流程", "golden"],
    )
    def test_no_eval_assets_or_case_ids_in_prompt(self, forbidden):
        """禁止把 RC 编号、Golden 文件名、具体标准写进 Prompt 做特判。"""
        assert forbidden not in _template()


class _FakeLLM:
    def __init__(self, content):
        self._content = content
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return SimpleNamespace(content=self._content)


@pytest.fixture
def rewrite_env(monkeypatch):
    """把 _rewrite 的外部依赖全部替换为 Fake（不触网、不触 DB、不记账）。"""
    import backend.rag.retrieval.multi_query as mq
    from backend.infra.llm import proxy as llm_proxy

    monkeypatch.setattr(llm_proxy, "enforce_model_input_price_tier", lambda *a, **k: None)
    monkeypatch.setattr(mq, "get_context", lambda: SimpleNamespace(
        mq_triggered=False, mq_reason="", mq_variants=1, mq_filtered=1,
    ))

    def _install(content):
        llm = _FakeLLM(content)
        monkeypatch.setattr(mq, "_resolve_rewrite_llm", lambda: llm)
        return llm

    return _install


class TestRewritePipeline:
    def test_evidence_face_variants_survive_filters(self, rewrite_env):
        import backend.rag.retrieval.multi_query as mq

        rewrite_env(
            "1. 采购新品上架需要哪些部门协作？\n"
            "2. 采购部 质检部 法务部 在新品上架中的职责分工与审批环节\n"
            "3. 新品上架所需资质证明 质检标准 合规认证要求\n"
        )

        out = mq._rewrite(PURCHASE_Q)

        assert out[0] == PURCHASE_Q
        assert len(out) == mq.MULTI_QUERY_COUNT == 3
        assert out[1] != out[2]
        # 三条必须是不同证据面，而不是同一问法的三种语序
        assert out[1].startswith("采购部")
        assert "资质" in out[2]

    def test_label_echo_and_numbering_are_stripped(self, rewrite_env):
        """模型回显的标签行不得挤占变体名额。"""
        import backend.rag.retrieval.multi_query as mq

        rewrite_env(
            "以下是查询：\n"
            "1. 采购新品上架需要哪些部门协作？\n"
            "2. 退货流程中运费承担的责任部门与审批环节\n"
            "改写结果：\n"
        )

        out = mq._rewrite(REFUND_Q)

        assert out[0] == REFUND_Q
        assert all(not line.endswith(("：", ":")) for line in out)

    def test_near_duplicate_variant_is_filtered(self, rewrite_env):
        """仅换语序的"改写"必须被相似度去重挡掉，不允许占名额。"""
        import backend.rag.retrieval.multi_query as mq

        rewrite_env("采购上架协作部门\n新品上架所需资质证明 质检标准\n")

        out = mq._rewrite(PURCHASE_Q)

        assert "采购上架协作部门" not in out
        assert "新品上架所需资质证明 质检标准" in out

    def test_llm_failure_falls_back_to_original_query(self, rewrite_env, monkeypatch):
        import backend.rag.retrieval.multi_query as mq

        class _Boom:
            def invoke(self, *a, **k):
                raise RuntimeError("provider down")

        monkeypatch.setattr(mq, "_resolve_rewrite_llm", lambda: _Boom())

        assert mq._rewrite(PURCHASE_Q) == [PURCHASE_Q]

    def test_variant_count_is_capped_by_config(self, rewrite_env, monkeypatch):
        import backend.rag.retrieval.multi_query as mq

        rewrite_env(
            "甲 采购部职责与审批环节\n"
            "乙 供应商资质证明材料\n"
            "丙 质检标准与合规认证\n"
            "丁 单据留痕与时限要求\n"
        )
        monkeypatch.setattr(mq, "MULTI_QUERY_COUNT", 3)

        out = mq._rewrite(PURCHASE_Q)

        assert out[0] == PURCHASE_Q
        assert len(out) == 3

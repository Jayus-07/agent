"""Reporter 多步 RAG 引用编号与来源卡保持一致。"""

import time

from backend.agents.reporter.reporter import (
    _extract_sources_from_steps,
    generate_final_answer,
)
from backend.orchestration.graph.events import make_done_event


def _rag_step(output: str) -> dict:
    return {
        "capability": "rag.search",
        "description": "知识库检索",
        "status": "success",
        "output": output,
    }


def _multi_rag_steps() -> dict:
    return {
        "s1": _rag_step(
            "第一步结论引用 [E1]。\n\n---\n\n### 参考文献\n\n"
            "1. **差旅制度.pdf** (制度规范) — 第 1 页\n"
        ),
        "s2": _rag_step(
            "第二步结论引用 [E1]，补充规则引用 [E2]。\n\n---\n\n"
            "### 参考文献\n\n"
            "1. **报销 FAQ.md** (FAQ) — 申请条件\n"
            "2. **费用说明.pdf** (制度规范) — 第 4 页\n"
        ),
    }


def test_multi_rag_steps_use_one_citation_index_across_answer_and_sources():
    step_results = _multi_rag_steps()
    answer = generate_final_answer(
        "差旅报销规则是什么？",
        step_results,
        context_filter=False,
        allow_llm=False,
    )

    assert "第一步结论引用 [E1]" in answer
    assert "第二步结论引用 [E2]，补充规则引用 [E3]" in answer
    assert answer.count("### 参考文献") == 1
    assert "1. **差旅制度.pdf**" in answer
    assert "2. **报销 FAQ.md**" in answer
    assert "3. **费用说明.pdf**" in answer

    sources = _extract_sources_from_steps(step_results)
    assert [(source["index"], source["filename"]) for source in sources] == [
        (1, "差旅制度.pdf"),
        (2, "报销 FAQ.md"),
        (3, "费用说明.pdf"),
    ]

    done = make_done_event(answer, step_results, start_time=time.time())
    assert done["event"] == "done"
    assert done["data"]["answer"] == answer
    assert done["data"]["sources"] == sources


def test_same_filename_without_doc_id_is_not_merged_across_steps():
    step_results = {
        "s1": _rag_step(
            "第一步引用 [E1]。\n\n---\n\n### 参考文献\n\n"
            "1. **制度.pdf** (制度规范) — 第 1 页\n"
        ),
        "s2": _rag_step(
            "第二步引用 [E1]。\n\n---\n\n### 参考文献\n\n"
            "1. **制度.pdf** (制度规范) — 第 1 页\n"
        ),
    }

    answer = generate_final_answer(
        "查询制度依据",
        step_results,
        context_filter=False,
        allow_llm=False,
    )
    sources = _extract_sources_from_steps(step_results)

    assert "第一步引用 [E1]" in answer
    assert "第二步引用 [E2]" in answer
    assert answer.count("1. **制度.pdf**") == 1
    assert answer.count("2. **制度.pdf**") == 1
    assert [(source["index"], source["filename"]) for source in sources] == [
        (1, "制度.pdf"),
        (2, "制度.pdf"),
    ]


def test_same_document_different_pages_keep_distinct_citations_across_steps():
    step_results = {
        "s1": _rag_step(
            "第一步依据制度第 1 页 [E1] 和第 4 页 [E2]。\n\n---\n\n"
            "### 参考文献\n\n"
            "1. **制度.pdf** (制度规范) — 第 1 页 · 适用范围 "
            "<!--doc:manual-v1--><!--source_meta:version_id=v2;effective_from=2026-01-01-->\n"
            "2. **制度.pdf** (制度规范) — 第 4 页 · 报销流程 "
            "<!--doc:manual-v1--><!--source_meta:version_id=v2;effective_from=2026-01-01-->\n"
        ),
        "s2": _rag_step(
            "第二步补充 FAQ [E1]。\n\n---\n\n### 参考文献\n\n"
            "1. **报销 FAQ.md** (FAQ) — 申请条件 <!--doc:faq-v1-->\n"
        ),
    }

    answer = generate_final_answer(
        "报销范围和流程是什么？",
        step_results,
        context_filter=False,
        allow_llm=False,
    )
    sources = _extract_sources_from_steps(step_results)
    done = make_done_event(answer, step_results, start_time=time.time())

    assert "第 1 页 [E1] 和第 4 页 [E2]" in answer
    assert "第二步补充 FAQ [E3]" in answer
    assert "1. **制度.pdf** (制度规范) — 第 1 页 · 适用范围" in answer
    assert "2. **制度.pdf** (制度规范) — 第 4 页 · 报销流程" in answer
    assert "3. **报销 FAQ.md**" in answer
    assert [(source["index"], source["filename"], source.get("pages"))
            for source in sources] == [
        (1, "制度.pdf", [1]),
        (2, "制度.pdf", [4]),
        (3, "报销 FAQ.md", None),
    ]
    assert [(source.get("version_id"), source.get("effective_from"))
            for source in sources] == [
        ("v2", "2026-01-01"),
        ("v2", "2026-01-01"),
        (None, None),
    ]
    assert done["data"]["sources"] == sources


def test_unmapped_citation_cannot_point_to_a_later_step_source():
    step_results = {
        "s1": _rag_step(
            "第一步存在未匹配引用 [E2]，有效引用 [E1]。\n\n---\n\n"
            "### 参考文献\n\n"
            "1. **差旅制度.pdf** (制度规范) — 第 1 页 <!--doc:travel-v1-->\n"
        ),
        "s2": _rag_step(
            "第二步 FAQ 依据 [E1]。\n\n---\n\n### 参考文献\n\n"
            "1. **报销 FAQ.md** (FAQ) — 申请条件 <!--doc:faq-v1-->\n"
        ),
    }

    answer = generate_final_answer(
        "差旅报销条件是什么？",
        step_results,
        context_filter=False,
        allow_llm=False,
    )

    assert "第一步存在未匹配引用 [引用来源未能对应]" in answer
    assert "有效引用 [E1]" in answer
    assert "第二步 FAQ 依据 [E2]" in answer


def test_unmapped_citation_without_reference_section_is_not_reused():
    step_results = {
        "s1": _rag_step(
            "第一步的结论部分较长，内容本身也需要核对来源，"
            "当前正文仍带有引用编号 [E1]，但结果未附参考文献清单。"
        ),
        "s2": _rag_step(
            "第二步 FAQ 引用 [E1]。\n\n---\n\n### 参考文献\n\n"
            "1. **报销 FAQ.md** (FAQ) — 申请条件 <!--doc:faq-v1-->\n"
        ),
    }

    answer = generate_final_answer(
        "差旅报销条件是什么？",
        step_results,
        context_filter=False,
        allow_llm=False,
    )

    assert "当前正文仍带有引用编号 [引用来源未能对应]" in answer
    assert "第二步 FAQ 引用 [E1]" in answer


def test_single_rag_output_marks_citations_without_sources():
    step_results = {
        "rag": _rag_step(
            "这是一段足够长的知识库回答，包含引用编号 [E9]，"
            "但没有对应的参考文献，不能将它指向其他来源。"
        ),
    }

    answer = generate_final_answer(
        "相关制度是什么？",
        step_results,
        context_filter=False,
        allow_llm=False,
    )

    assert "引用编号 [引用来源未能对应]" in answer
    assert "[E9]" not in answer


def test_single_rag_output_keeps_citations_with_parseable_sources():
    step_results = {
        "rag": _rag_step(
            "制度规定的报销范围见第 4 页 [E1]，申请流程需要按章节材料核对。"
            "\n\n---\n\n### 参考文献\n\n"
            "1. **报销制度.pdf** (制度规范) — 第 4 页 · 申请流程 "
            "<!--doc:manual-v1-->\n"
        ),
    }

    answer = generate_final_answer(
        "报销流程是什么？",
        step_results,
        context_filter=False,
        allow_llm=False,
    )

    assert "第 4 页 [E1]" in answer
    assert "1. **报销制度.pdf** (制度规范) — 第 4 页 · 申请流程" in answer
    assert "引用来源未能对应" not in answer


def test_llm_reporter_receives_renumbered_rag_evidence(monkeypatch):
    from types import SimpleNamespace

    import backend.agents.reporter.reporter as reporter_module
    import backend.config as config

    class FakePromptService:
        def render_sync(self, _key, **_kwargs):
            return SimpleNamespace(text="system prompt")

    class FakeLLM:
        prompt = ""

        def invoke(self, messages):
            self.prompt = messages[1][1]
            return SimpleNamespace(content="综合结论引用 [E3]")

    fake_llm = FakeLLM()
    monkeypatch.setattr(reporter_module, "prompt_service", FakePromptService())
    monkeypatch.setattr(reporter_module, "llm", fake_llm)
    monkeypatch.setattr(config, "ENABLE_TOKEN_STREAMING", False)

    answer = generate_final_answer(
        "差旅报销规则是什么？",
        _multi_rag_steps(),
        context_filter=False,
        allow_llm=True,
    )

    assert "第一步结论引用 [E1]" in fake_llm.prompt
    assert "第二步结论引用 [E2]，补充规则引用 [E3]" in fake_llm.prompt
    assert "### 参考文献" not in fake_llm.prompt
    assert "综合结论引用 [E3]" in answer
    assert answer.count("### 参考文献") == 1
    assert "3. **费用说明.pdf**" in answer


def test_llm_output_cannot_add_a_citation_without_a_source(monkeypatch):
    from types import SimpleNamespace

    import backend.agents.reporter.reporter as reporter_module
    import backend.config as config

    class FakePromptService:
        def render_sync(self, _key, **_kwargs):
            return SimpleNamespace(text="system prompt")

    class FakeLLM:
        def invoke(self, _messages):
            return SimpleNamespace(content="综合结论引用 [99]，有效来源引用 [3]")

    monkeypatch.setattr(reporter_module, "prompt_service", FakePromptService())
    monkeypatch.setattr(reporter_module, "llm", FakeLLM())
    monkeypatch.setattr(config, "ENABLE_TOKEN_STREAMING", False)

    answer = generate_final_answer(
        "差旅报销规则是什么？",
        _multi_rag_steps(),
        context_filter=False,
        allow_llm=True,
    )

    assert "综合结论引用 [引用来源未能对应]" in answer
    assert "有效来源引用 [3]" in answer
    assert "[99]" not in answer


def test_streaming_llm_marks_unknown_citations_without_delta_mismatch(monkeypatch):
    from types import SimpleNamespace

    import backend.agents.reporter.reporter as reporter_module
    import backend.config as config
    import backend.infra.llm.proxy as llm_proxy

    class FakePromptService:
        def render_sync(self, _key, **_kwargs):
            return SimpleNamespace(text="system prompt")

    class FakeLLM:
        def stream(self, _messages):
            yield SimpleNamespace(content="综合结论引用 [E")
            yield SimpleNamespace(content="99]，有效来源引用 [E3]")

    deltas = []
    monkeypatch.setattr(reporter_module, "prompt_service", FakePromptService())
    monkeypatch.setattr(reporter_module, "llm", FakeLLM())
    monkeypatch.setattr(config, "ENABLE_TOKEN_STREAMING", True)
    monkeypatch.setattr(
        llm_proxy, "emit_stream_delta", lambda text, **_kwargs: deltas.append(text),
    )

    answer = generate_final_answer(
        "差旅报销规则是什么？",
        _multi_rag_steps(),
        context_filter=False,
        allow_llm=True,
    )
    answer_body = answer.split("\n\n### 参考文献", maxsplit=1)[0]

    assert "综合结论引用 [引用来源未能对应]" in answer_body
    assert "有效来源引用 [E3]" in answer_body
    assert "[E99]" not in answer_body
    assert "".join(deltas) == answer_body

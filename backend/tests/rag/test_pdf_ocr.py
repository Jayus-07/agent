"""PDF OCR 兜底的回归测试。

覆盖：
1. PdfParser 文本层为空 → 按页走 OCR 兜底产出段落；文本层正常 → 不触发 OCR
2. OCR 不可用 → 扫描件照旧产出空 AST（下游 ChunkingEmptyError 兜底）
3. 供应商路由：off / 未知 / dashscope key 探测
4. DashScope OCR 用量写入 llm_usage_store（component="ocr"，tokens 页可见）
5. 上传预检与 OCR 联动：OCR 开启放行扫描件，OCR=off 入口拒绝
"""
import base64
import sys
import types
from types import SimpleNamespace

import pytest

import backend.rag.preprocessing.parser.ocr as ocr_mod
import backend.rag.preprocessing.parser.pdf_parser as pdf_parser_mod
from backend.config import rag as rag_cfg
from backend.rag.preprocessing.parser.pdf_parser import PdfParser
from backend.shared.processing_context import ProcessingBinding, bind_processing


# ============ 测试基建 ============

def _make_pdf(tmp_path, name, pages, text=None):
    import fitz
    p = tmp_path / name
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text if isinstance(text, str) else text(i))
    doc.save(str(p))
    doc.close()
    return str(p)


@pytest.fixture
def ocr_on(monkeypatch):
    """模拟 OCR 供应商可用，记录每页调用。"""
    calls = {"n": 0}

    def fake_ocr_image(png_bytes):
        calls["n"] += 1
        return f"第{calls['n']}页 OCR 识别文本内容，用于段落断言。"

    monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
    monkeypatch.setattr(ocr_mod, "ocr_image", fake_ocr_image)
    return calls


# ============ PdfParser 兜底触发 ============

class TestPdfParserOcrFallback:

    def test_scanned_pdf_triggers_ocr_per_page(self, tmp_path, ocr_on):
        pdf = _make_pdf(tmp_path, "scan.pdf", pages=2)
        ast = PdfParser().parse(pdf)
        assert ocr_on["n"] == 2, "每页各调一次 OCR"
        texts = [n.text for n in ast.root.children if n.type == "paragraph"]
        assert any("第1页 OCR" in t for t in texts)
        assert any("第2页 OCR" in t for t in texts)

    def test_text_pdf_skips_ocr(self, tmp_path, ocr_on):
        """文本层足够长（> RAG_OCR_MIN_TEXT_CHARS）→ 完全不走 OCR。"""
        long_text = "这是一份足够长的正常文本文档内容，" * 10
        pdf = _make_pdf(tmp_path, "text.pdf", pages=1, text=long_text)
        PdfParser().parse(pdf)
        assert ocr_on["n"] == 0, "有文本层的 PDF 不得触发 OCR"

    def test_scanned_pdf_records_ocr_required_but_disabled(self, tmp_path, monkeypatch):
        """扫描件需要 OCR 但供应商关闭时，AST 要保留可解释的跳过原因。"""
        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        pdf = _make_pdf(tmp_path, "scan-disabled.pdf", pages=1)

        ast = PdfParser().parse(pdf)

        assert ast.ocr_required is True
        assert ast.ocr_attempted is False
        assert ast.ocr_triggered is False

    def test_min_text_chars_threshold(self, tmp_path, ocr_on, monkeypatch):
        """文本层仅有页码级噪音（< 阈值）→ 视为无文本层，触发 OCR。"""
        monkeypatch.setattr(pdf_parser_mod, "RAG_OCR_MIN_TEXT_CHARS", 50)
        pdf = _make_pdf(tmp_path, "noise.pdf", pages=1, text="第1页")
        PdfParser().parse(pdf)
        assert ocr_on["n"] == 1

    def test_ocr_unavailable_returns_empty_ast(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        pdf = _make_pdf(tmp_path, "scan.pdf", pages=2)
        ast = PdfParser().parse(pdf)
        assert ast.root.children == [], "OCR 不可用时不产出节点，交由下游报业务失败"


# ============ 供应商路由 ============

class TestProviderRouting:

    def test_off_unavailable(self, monkeypatch):
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "off")
        assert ocr_mod.ocr_available() is False

    def test_unknown_provider_unavailable(self, monkeypatch):
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "baidu")
        assert ocr_mod.ocr_available() is False

    def test_rapidocr_identity_uses_lineage_model_contract(self, monkeypatch):
        from backend.rag.indexing.processing_lineage import ModelIdentity

        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "rapidocr")

        identity = ocr_mod.get_ocr_model_identity()

        assert isinstance(identity, ModelIdentity)
        assert identity.role == "ocr"
        assert identity.engine_type == "ocr"
        assert identity.provider == "rapidocr"
        assert identity.model_name == "RapidOCR"

    def test_dashscope_with_key_available(self, monkeypatch):
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setenv("OCR_DASHSCOPE_API_KEY", "sk-test")
        assert ocr_mod.ocr_available() is True

    def test_dashscope_without_key_unavailable(self, monkeypatch):
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        for k in ("OCR_DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY", "EMBEDDING_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        assert ocr_mod.ocr_available() is False

    def test_rapidocr_importable(self):
        """默认供应商 rapidocr_onnxruntime 已安装（本次决策的落地前提）。"""
        rapidocr = pytest.importorskip("rapidocr_onnxruntime")
        assert hasattr(rapidocr, "RapidOCR")


# ============ DashScope 用量统计 ============

class TestDashScopeUsageRecording:

    def _fake_openai_module(self, prompt_tokens=100, completion_tokens=50):
        class FakeCompletions:
            def create(self, **kwargs):
                return SimpleNamespace(
                    usage=SimpleNamespace(prompt_tokens=prompt_tokens,
                                          completion_tokens=completion_tokens),
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content="识别出的文本"))],
                )

        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())

        return types.SimpleNamespace(OpenAI=FakeOpenAI)

    def test_usage_recorded_as_ocr_component(self, monkeypatch):
        """DashScope OCR 的 token 用量进 llm_usage_store，tokens 看板可查。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setenv("OCR_DASHSCOPE_API_KEY", "sk-test")
        monkeypatch.setitem(sys.modules, "openai", self._fake_openai_module())

        recorded = []
        import backend.observability.llm_usage_store as store_mod
        monkeypatch.setattr(store_mod, "get_llm_usage_store",
                            lambda: SimpleNamespace(record=recorded.append))

        binding = ProcessingBinding(
            run_id="run-ocr", step_id="step-ocr", role="ocr", stage="ocr"
        )
        with bind_processing(binding):
            text = ocr_mod._ocr_image_dashscope(b"\x89PNG-fake")
        assert text == "识别出的文本"
        assert len(recorded) == 1
        evt = recorded[0]
        assert evt["component"] == "ocr"
        assert evt["provider"] == "dashscope"
        assert evt["prompt_tokens"] == 100
        assert evt["completion_tokens"] == 50
        assert evt["total_tokens"] == 150
        assert evt["model"] == rag_cfg.RAG_OCR_DASHSCOPE_MODEL
        assert evt["run_id"] == "run-ocr"
        assert evt["step_id"] == "step-ocr"
        assert evt["role"] == "ocr"
        assert evt["stage"] == "ocr"

    def test_record_failure_soft(self, monkeypatch):
        """用量记录失败不影响 OCR 主流程。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setenv("OCR_DASHSCOPE_API_KEY", "sk-test")
        monkeypatch.setitem(sys.modules, "openai", self._fake_openai_module())

        import backend.observability.llm_usage_store as store_mod
        def _boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(store_mod, "get_llm_usage_store", _boom)

        text = ocr_mod._ocr_image_dashscope(b"\x89PNG-fake")
        assert text == "识别出的文本"


# ============ 上传预检与 OCR 联动 ============

class _FakeUploadFile:
    def __init__(self, filename, data, content_type="application/pdf"):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._pos = 0

    async def read(self, n=-1):
        if n <= 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
        else:
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
        return chunk


class _FakeRequest:
    def __init__(self):
        import types as _t
        self.client = _t.SimpleNamespace(host="127.0.0.1")
        self.headers = {"user-agent": "pytest"}


@pytest.fixture
def upload_env(tmp_path, monkeypatch):
    import backend.config.database as config_database
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(config_database, "DOCS_DIRECTORY", str(docs))
    import backend.config as config_pkg
    monkeypatch.setattr(config_pkg, "DOCS_DIRECTORY", str(docs))
    return docs


def test_precheck_allows_scanned_pdf_when_ocr_on(upload_env, tmp_path, monkeypatch):
    """OCR 启用 → 无文本层 PDF 在上传入口放行（交给索引链路走 OCR 兜底）。"""
    import asyncio
    from backend.app.api.routes.rag_upload import sync_upload_impl
    monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "rapidocr")
    blank = _make_pdf(tmp_path, "scan.pdf", pages=1)
    with open(blank, "rb") as f:
        data = f.read()

    res = asyncio.run(sync_upload_impl(
        _FakeUploadFile("scan.pdf", data), _FakeRequest(),
        50 * 1024 * 1024, str(tmp_path / "tmp"), 64,
        emit_bytes=1024 * 1024, emit_ms=10 ** 9,
        kb_id="policy_general", department="general",
    ))
    assert res["ok"] is True, f"OCR 开启时扫描件不应被入口拒绝: {res}"


def test_precheck_rejects_scanned_pdf_when_ocr_off(upload_env, tmp_path, monkeypatch):
    """OCR=off → 无文本层 PDF 在入口明确拒绝，不浪费后台索引。"""
    import asyncio
    from backend.app.api.routes.rag_upload import sync_upload_impl
    monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "off")
    blank = _make_pdf(tmp_path, "scan.pdf", pages=1)
    with open(blank, "rb") as f:
        data = f.read()

    res = asyncio.run(sync_upload_impl(
        _FakeUploadFile("scan.pdf", data), _FakeRequest(),
        50 * 1024 * 1024, str(tmp_path / "tmp"), 64,
        emit_bytes=1024 * 1024, emit_ms=10 ** 9,
        kb_id="policy_general", department="general",
    ))
    assert res["ok"] is False
    assert "文本层" in res["error"]


# ============ 云端按页缓存 + 限流（D6 ③，2026-09-17）============

class TestCloudCacheAndThrottle:
    """dashscope 供应商：同页图像缓存命中不重复计费；相邻调用限流拉开间隔。"""

    @pytest.fixture
    def cloud_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(rag_cfg, "RAG_OCR_CACHE_ENABLED", True)
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MIN_INTERVAL_MS", 0)
        monkeypatch.setattr(ocr_mod, "_ocr_cache_dir", tmp_path / "ocr_cache")
        ocr_mod._last_call_mono = 0.0  # 重置全局限流状态，避免用例间串扰
        calls = {"n": 0}

        def fake_call(png):
            calls["n"] += 1
            return f"识别文本-{calls['n']}"

        monkeypatch.setattr(ocr_mod, "_ocr_image_dashscope", fake_call)
        return calls

    def test_cache_hit_no_recost(self, cloud_env):
        png = b"\x89PNG-same-page"
        ocr_mod.reset_ocr_tracking()
        t1 = ocr_mod.ocr_image(png)
        t2 = ocr_mod.ocr_image(png)
        assert t1 == t2 == "识别文本-1"
        assert cloud_env["n"] == 1, "同页图像第二次调用必须命中缓存，不得重复计费"
        stats = ocr_mod.get_ocr_result_meta()
        assert stats["calls"] == 2
        assert stats["cache_hits"] == 1
        assert stats["cache_status"] == "hit"

    def test_different_page_both_called(self, cloud_env):
        ocr_mod.ocr_image(b"page-a")
        ocr_mod.ocr_image(b"page-b")
        assert cloud_env["n"] == 2, "不同页面图像各自调用"

    def test_cache_disabled_calls_every_time(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(rag_cfg, "RAG_OCR_CACHE_ENABLED", False)
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MIN_INTERVAL_MS", 0)
        monkeypatch.setattr(ocr_mod, "_ocr_cache_dir", tmp_path / "ocr_cache")
        ocr_mod._last_call_mono = 0.0
        calls = {"n": 0}

        def fake_call(png):
            calls["n"] += 1
            return "x"

        monkeypatch.setattr(ocr_mod, "_ocr_image_dashscope", fake_call)
        ocr_mod.ocr_image(b"p")
        ocr_mod.ocr_image(b"p")
        assert calls["n"] == 2, "缓存关闭时每次都直连"

    def test_throttle_enforces_min_interval(self, tmp_path, monkeypatch):
        import time as _time
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MIN_INTERVAL_MS", 60)
        monkeypatch.setattr(ocr_mod, "_ocr_cache_dir", tmp_path / "ocr_cache")
        ocr_mod._last_call_mono = 0.0
        monkeypatch.setattr(ocr_mod, "_ocr_image_dashscope", lambda png: "x")
        t0 = _time.monotonic()
        ocr_mod.ocr_image(b"p1")
        ocr_mod.ocr_image(b"p2")
        assert _time.monotonic() - t0 >= 0.06, "第二次调用须被限流拉开最小间隔"

    def test_cache_persists_to_disk(self, cloud_env, tmp_path):
        import json as _json
        ocr_mod.ocr_image(b"\x89PNG-disk-page")
        files = list((tmp_path / "ocr_cache").glob("*.json"))
        assert len(files) == 1
        assert _json.loads(files[0].read_text(encoding="utf-8"))["text"] == "识别文本-1"

    def test_tracking_counts_partial_page_failure(self, monkeypatch):
        """一份扫描件中成功页和失败页都必须进入同一 OCR 阶段统计。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "rapidocr")
        ocr_mod.reset_ocr_tracking()
        calls = {"n": 0}

        def _fake_rapidocr(_png):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("page failed")
            return "识别文本"

        monkeypatch.setattr(ocr_mod, "_ocr_image_rapidocr", _fake_rapidocr)

        assert ocr_mod.ocr_image(b"page-1") == "识别文本"
        with pytest.raises(RuntimeError, match="page failed"):
            ocr_mod.ocr_image(b"page-2")

        stats = ocr_mod.get_ocr_result_meta()
        assert stats["calls"] == 2
        assert stats["successes"] == 1
        assert stats["failures"] == 1
        assert stats["model_name"] == "RapidOCR"


# ============ ocr_triggered 标记贯通（AST → chunk metadata → quality_issues）============

class TestOcrTriggeredPropagation:
    """§5.1 质量记录：OCR 触发必须全链路可追溯，不得静默发生。"""

    def test_ast_flag_set_on_scanned_pdf(self, tmp_path, ocr_on):
        pdf = _make_pdf(tmp_path, "scan_flag.pdf", pages=2)
        ast = PdfParser().parse(pdf)
        assert ast.ocr_triggered is True
        assert ast.ocr_pages == 2

    def test_ast_flag_not_set_for_text_pdf(self, tmp_path, ocr_on):
        long_text = "这是一份足够长的正常文本文档内容，" * 10
        pdf = _make_pdf(tmp_path, "text_flag.pdf", pages=1, text=long_text)
        ast = PdfParser().parse(pdf)
        assert ast.ocr_triggered is False
        assert ast.ocr_pages == 0

    def test_chunks_marked_ocr_triggered(self, tmp_path, ocr_on):
        from backend.rag.preprocessing.pipeline import parse_and_chunk
        pdf = _make_pdf(tmp_path, "scan_chunks.pdf", pages=1)
        chunks = parse_and_chunk(pdf)
        assert chunks, "扫描件经 OCR 应产出 chunk 而非空"
        assert all(c.metadata.get("ocr_triggered") == "true" for c in chunks)
        assert any(int(c.metadata.get("ocr_pages") or 0) == 1 for c in chunks)

    def test_append_quality_issue_helper(self):
        from backend.rag.indexing.indexer import _append_quality_issue
        meta = {}
        _append_quality_issue(meta, "a")
        _append_quality_issue(meta, "b")
        assert meta["quality_issues"] == "a, b", "逗号拼接且既有串保留"

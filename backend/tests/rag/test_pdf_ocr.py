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

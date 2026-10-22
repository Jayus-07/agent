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


def _make_scanned_pdf(tmp_path, name, pages):
    """创建有可见像素、但没有文字层的扫描件页面。"""
    import fitz

    path = tmp_path / name
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        pixmap = fitz.Pixmap(
            fitz.csRGB, fitz.IRect(0, 0, 400, 600), 0,
        )
        pixmap.clear_with(0)
        page.insert_image(page.rect, stream=pixmap.tobytes("png"))
    doc.save(str(path))
    doc.close()
    return str(path)


def _make_hybrid_pdf(tmp_path, name="hybrid.pdf", *, native_text=True):
    """第一页有原生文字，第二页是整页图片，模拟混合扫描 PDF。"""
    import fitz

    path = tmp_path / name
    doc = fitz.open()
    first = doc.new_page()
    if native_text:
        first.insert_text(
            (72, 72), "这页文字层完整，足以跳过 OCR。" * 8,
        )
    scanned = doc.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 600), 0)
    pixmap.clear_with(0)
    scanned.insert_image(scanned.rect, stream=pixmap.tobytes("png"))
    doc.save(str(path))
    doc.close()
    return str(path)


def _make_rich_page_with_image(tmp_path, name="rich-image-region.pdf"):
    """文字层充足的一页内嵌大图，检查只 OCR 图片 bbox。"""
    import fitz

    path = tmp_path / name
    doc = fitz.open()
    page = doc.new_page()
    for index in range(5):
        page.insert_text(
            (72, 72 + index * 22),
            "该页原生文字充足，图片区域应单独识别而不重复整页文字。" * 2,
        )
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 300), 0)
    pixmap.clear_with(255)
    page.insert_image(
        fitz.Rect(72, 220, 320, 500), stream=pixmap.tobytes("png"),
    )
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def ocr_on(monkeypatch):
    """模拟 OCR 供应商可用，记录裁剪图像尺寸。"""
    calls = {"n": 0, "sizes": []}

    def fake_ocr_image(png_bytes):
        calls["n"] += 1
        import fitz
        pixmap = fitz.Pixmap(png_bytes)
        calls["sizes"].append((pixmap.width, pixmap.height))
        return f"第{calls['n']}页 OCR 识别文本内容，用于段落断言。"

    monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
    monkeypatch.setattr(ocr_mod, "ocr_image", fake_ocr_image)
    return calls


# ============ PdfParser 兜底触发 ============

class TestPdfParserOcrFallback:

    def test_scanned_pdf_triggers_ocr_per_page(self, tmp_path, ocr_on):
        pdf = _make_scanned_pdf(tmp_path, "scan.pdf", pages=2)
        ast = PdfParser().parse(pdf)
        assert ocr_on["n"] == 2, "每页各调一次 OCR"
        texts = [n.text for n in ast.root.children if n.type == "paragraph"]
        assert any("第1页 OCR" in t for t in texts)
        assert any("第2页 OCR" in t for t in texts)

    def test_upload_ocr_budget_overrides_default_for_one_parse_only(
        self, tmp_path, monkeypatch,
    ):
        pdf = _make_scanned_pdf(tmp_path, "budget-scan.pdf", pages=3)
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MAX_PAGES", 1)
        calls = {"n": 0}

        def fake_ocr(_png):
            calls["n"] += 1
            return f"OCR 页 {calls['n']} 的测试正文。"

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        monkeypatch.setattr(ocr_mod, "ocr_image", fake_ocr)

        override_ast = PdfParser().parse(pdf, ocr_page_budget=2)
        assert calls["n"] == 2
        assert override_ast.ocr_page_budget == 2
        assert override_ast.ocr_page_budget_source == "upload_override"
        assert override_ast.ocr_page_limit == 1

        calls["n"] = 0
        default_ast = PdfParser().parse(pdf)
        assert calls["n"] == 1
        assert default_ast.ocr_page_budget == 1
        assert default_ast.ocr_page_budget_source == "default"
        assert default_ast.ocr_page_limit == 2

    @pytest.mark.parametrize("budget", [0, 501])
    def test_upload_ocr_budget_rejects_out_of_range_values(
        self, tmp_path, budget,
    ):
        pdf = _make_pdf(tmp_path, "budget-bound.pdf", pages=1, text="正文内容")
        with pytest.raises(ValueError, match="OCR 页预算"):
            PdfParser().parse(pdf, ocr_page_budget=budget)

    def test_local_ocr_retries_empty_page_once_at_higher_dpi(
        self, tmp_path, monkeypatch,
    ):
        from types import SimpleNamespace

        pdf = _make_scanned_pdf(tmp_path, "retry-scan.pdf", pages=1)
        calls = []

        def fail_then_succeed(png_bytes):
            import fitz
            pixmap = fitz.Pixmap(png_bytes)
            calls.append((pixmap.width, pixmap.height))
            return "" if len(calls) == 1 else "第二次识别成功，保留页面文本。"

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        monkeypatch.setattr(ocr_mod, "ocr_image", fail_then_succeed)
        monkeypatch.setattr(
            ocr_mod, "get_ocr_model_identity",
            lambda: SimpleNamespace(provider="rapidocr"),
        )

        ast = PdfParser().parse(pdf)

        assert len(calls) == 2
        assert calls[1][0] > calls[0][0]
        assert ast.ocr_failed_page_numbers == []
        assert ast.ocr_pages == 1
        assert any(
            node.type == "paragraph" and "第二次识别成功" in node.text
            for node in ast.root.children
        )

    def test_cloud_ocr_failure_is_not_retried(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        pdf = _make_scanned_pdf(tmp_path, "cloud-no-retry.pdf", pages=1)
        calls = []

        def fail_ocr(_png_bytes):
            calls.append(1)
            return ""

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        monkeypatch.setattr(ocr_mod, "ocr_image", fail_ocr)
        monkeypatch.setattr(
            ocr_mod, "get_ocr_model_identity",
            lambda: SimpleNamespace(provider="dashscope"),
        )

        ast = PdfParser().parse(pdf)

        assert len(calls) == 1
        assert ast.ocr_failed_page_numbers == [1]

    def test_text_pdf_skips_ocr(self, tmp_path, ocr_on):
        """文本层足够长（> RAG_OCR_MIN_TEXT_CHARS）→ 完全不走 OCR。"""
        long_text = "这是一份足够长的正常文本文档内容，" * 10
        pdf = _make_pdf(tmp_path, "text.pdf", pages=1, text=long_text)
        PdfParser().parse(pdf)
        assert ocr_on["n"] == 0, "有文本层的 PDF 不得触发 OCR"

    def test_scanned_pdf_records_ocr_required_but_disabled(self, tmp_path, monkeypatch):
        """扫描件需要 OCR 但供应商关闭时，AST 要保留可解释的跳过原因。"""
        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        pdf = _make_scanned_pdf(tmp_path, "scan-disabled.pdf", pages=1)

        ast = PdfParser().parse(pdf)

        assert ast.ocr_required is True
        assert ast.ocr_attempted is False
        assert ast.ocr_triggered is False
        issue = next(
            item for item in ast.parse_issues
            if item["code"] == "pdf_ocr_unavailable"
        )
        assert issue["severity"] == "error"
        assert issue["page_numbers"] == [1]
        assert issue["regions"] == ast.ocr_required_regions

    def test_min_text_chars_threshold(self, tmp_path, ocr_on, monkeypatch):
        """纯文字短页不应因字符量低而重复 OCR。"""
        monkeypatch.setattr(pdf_parser_mod, "RAG_OCR_MIN_TEXT_CHARS", 50)
        pdf = _make_pdf(tmp_path, "noise.pdf", pages=1, text="第1页")
        ast = PdfParser().parse(pdf)
        assert ocr_on["n"] == 0
        assert ast.ocr_required is False

    def test_hybrid_pdf_ocr_targets_only_sparse_image_page(self, tmp_path, ocr_on):
        pdf = _make_hybrid_pdf(tmp_path)

        ast = PdfParser().parse(pdf)

        assert ocr_on["n"] == 1
        assert ast.ocr_required_pages == [2]
        assert ast.ocr_pages == 1
        assert ast.pdf_image_pages == [2]
        ocr_nodes = [
            node for node in ast.root.children
            if node.type == "paragraph" and "OCR 识别文本" in node.text
        ]
        assert len(ocr_nodes) == 1
        assert ocr_nodes[0].page_number == 2

    def test_rich_text_page_ocr_uses_image_region_only(self, tmp_path, ocr_on):
        pdf = _make_rich_page_with_image(tmp_path)

        ast = PdfParser().parse(pdf)

        assert ocr_on["n"] == 1
        assert ast.ocr_required is True
        assert ast.ocr_required_pages == [1]
        assert ast.ocr_required_regions[0]["page"] == 1
        assert ast.ocr_required_regions[0]["bbox"] == [72.0, 220.0, 320.0, 500.0]
        assert ast.pdf_image_pages == [1]
        assert ast.pdf_unparsed_visual_pages == [1]
        page_width = 612 * rag_cfg.RAG_OCR_DPI / 72
        assert ocr_on["sizes"][0][0] < page_width
        ocr_node = next(
            node for node in ast.root.children
            if node.type == "paragraph" and "OCR 识别文本" in node.text
        )
        assert ocr_node.page_number == 1
        assert ocr_node.bbox == (72.0, 220.0, 320.0, 500.0)
        assert any(
            issue["code"] == "pdf_visual_content_unparsed"
            for issue in ast.parse_issues
        )

    def test_ocr_region_failure_records_page_and_bbox(self, tmp_path, monkeypatch):
        def fail_ocr(_png):
            raise RuntimeError("OCR engine error")

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        monkeypatch.setattr(ocr_mod, "ocr_image", fail_ocr)
        pdf = _make_rich_page_with_image(tmp_path, "failed-image-region.pdf")

        ast = PdfParser().parse(pdf)

        assert ast.ocr_failed_page_numbers == [1]
        assert ast.ocr_failed_regions == [
            {"page": 1, "bbox": [72.0, 220.0, 320.0, 500.0]},
        ]
        issue = next(
            item for item in ast.parse_issues
            if item["code"] == "pdf_ocr_failed"
        )
        assert issue["severity"] == "error"
        assert issue["regions"] == ast.ocr_failed_regions

    def test_pdf_table_detector_failure_is_recorded(self, tmp_path, monkeypatch):
        def fail_table_detection(_page):
            raise RuntimeError("table detector unavailable")

        monkeypatch.setattr(
            pdf_parser_mod.fitz.Page,
            "find_tables",
            fail_table_detection,
        )
        pdf = _make_pdf(
            tmp_path, "table-detector-failure.pdf", pages=1,
            text="这份文档有足够长的原生文本，不需要扫描件 OCR。" * 4,
        )

        ast = PdfParser().parse(pdf)

        assert ast.pdf_table_detection_failed_pages == [1]
        assert ast.parse_status == "partial"
        assert ast.parse_issues[0]["code"] == "pdf_table_detection_failed"

    def test_ocr_unavailable_returns_empty_ast(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        pdf = _make_scanned_pdf(tmp_path, "scan.pdf", pages=2)
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

    def test_dashscope_with_db_binding_available(self, monkeypatch):
        """都用 DB：dashscope 引擎的可用性由数据库 OCR 绑定 + 供应商 Key 决定。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(
            ocr_mod.model_roles,
            "resolve_effective",
            lambda _role: {"source": ocr_mod.model_roles.SOURCE_DB},
        )
        monkeypatch.setattr(
            ocr_mod,
            "_resolve_ocr_runtime_config",
            lambda: {
                "provider": "dashscope",
                "model": rag_cfg.RAG_OCR_DASHSCOPE_MODEL,
                "api_key": "sk-test",
                "base_url": "https://db.example/v1",
            },
        )
        assert ocr_mod.ocr_available() is True

    def test_dashscope_env_key_without_db_binding_unavailable(self, monkeypatch):
        """都用 DB：仅设置旧 env Key 而无数据库绑定时，云端 OCR 不可用。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setenv("OCR_DASHSCOPE_API_KEY", "sk-test")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
        monkeypatch.setenv("EMBEDDING_API_KEY", "sk-test")
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
        # 都用 DB：注入数据库形状的 OCR 运行时配置（api_key 只来自 DB 凭据）
        monkeypatch.setattr(
            ocr_mod,
            "_resolve_ocr_runtime_config",
            lambda: {
                "provider": "dashscope",
                "model": rag_cfg.RAG_OCR_DASHSCOPE_MODEL,
                "api_key": "sk-test",
                "base_url": "https://db.example/v1",
            },
        )
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
        monkeypatch.setattr(
            ocr_mod,
            "_resolve_ocr_runtime_config",
            lambda: {
                "provider": "dashscope",
                "model": rag_cfg.RAG_OCR_DASHSCOPE_MODEL,
                "api_key": "sk-test",
                "base_url": "https://db.example/v1",
            },
        )
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
    blank = _make_scanned_pdf(tmp_path, "scan.pdf", pages=1)
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
    blank = _make_scanned_pdf(tmp_path, "scan.pdf", pages=1)
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
        # 都用 DB：注入数据库形状的 OCR 运行时配置（api_key 只来自 DB 凭据）
        monkeypatch.setattr(
            ocr_mod,
            "_resolve_ocr_runtime_config",
            lambda: {
                "provider": "dashscope",
                "model": rag_cfg.RAG_OCR_DASHSCOPE_MODEL,
                "api_key": "sk-test",
                "base_url": "https://db.example/v1",
            },
        )
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
        monkeypatch.setattr(
            ocr_mod,
            "_resolve_ocr_runtime_config",
            lambda: {
                "provider": "dashscope",
                "model": rag_cfg.RAG_OCR_DASHSCOPE_MODEL,
                "api_key": "sk-test",
                "base_url": "https://db.example/v1",
            },
        )
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
        monkeypatch.setattr(
            ocr_mod,
            "_resolve_ocr_runtime_config",
            lambda: {
                "provider": "dashscope",
                "model": rag_cfg.RAG_OCR_DASHSCOPE_MODEL,
                "api_key": "sk-test",
                "base_url": "https://db.example/v1",
            },
        )
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

    def test_upstream_model_switch_invalidates_cache(self, tmp_path, monkeypatch):
        """OCR-03/04：upstream A→B 后必须 MISS，且不读取 A 的结果。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(rag_cfg, "RAG_OCR_CACHE_ENABLED", True)
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MIN_INTERVAL_MS", 0)
        monkeypatch.setattr(ocr_mod, "_ocr_cache_dir", tmp_path / "ocr_cache")
        ocr_mod._last_call_mono = 0.0
        runtime = {
            "provider": "dashscope",
            "model": "ocr-registered",
            "upstream_model": "ocr-upstream-a",
            "api_key": "sk-test",
            "base_url": "https://db.example/v1",
        }
        monkeypatch.setattr(ocr_mod, "_resolve_ocr_runtime_config", lambda: dict(runtime))
        calls = {"n": 0}

        def fake_call(png):
            calls["n"] += 1
            return f"文本-{runtime['upstream_model']}"

        monkeypatch.setattr(ocr_mod, "_ocr_image_dashscope", fake_call)
        png = b"\x89PNG-same-page"

        assert ocr_mod.ocr_image(png) == "文本-ocr-upstream-a"  # OCR-01 MISS
        assert ocr_mod.ocr_image(png) == "文本-ocr-upstream-a"  # OCR-02 HIT
        assert calls["n"] == 1

        runtime["upstream_model"] = "ocr-upstream-b"
        assert ocr_mod.ocr_image(png) == "文本-ocr-upstream-b"  # OCR-03 MISS
        assert calls["n"] == 2
        assert ocr_mod.ocr_image(png) == "文本-ocr-upstream-b"  # OCR-04 不再读到 A
        assert calls["n"] == 2

    def test_display_name_change_keeps_cache(self, tmp_path, monkeypatch):
        """OCR-05：只改注册显示名（upstream 不变）不得触发重识别。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(rag_cfg, "RAG_OCR_CACHE_ENABLED", True)
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MIN_INTERVAL_MS", 0)
        monkeypatch.setattr(ocr_mod, "_ocr_cache_dir", tmp_path / "ocr_cache")
        ocr_mod._last_call_mono = 0.0
        runtime = {
            "provider": "dashscope",
            "model": "display-name-1",
            "upstream_model": "same-upstream",
            "api_key": "sk-test",
            "base_url": "https://db.example/v1",
        }
        monkeypatch.setattr(ocr_mod, "_resolve_ocr_runtime_config", lambda: dict(runtime))
        calls = {"n": 0}

        def fake_call(png):
            calls["n"] += 1
            return "识别文本"

        monkeypatch.setattr(ocr_mod, "_ocr_image_dashscope", fake_call)
        png = b"\x89PNG-display"
        ocr_mod.ocr_image(png)
        runtime["model"] = "display-name-2"
        ocr_mod.ocr_image(png)
        assert calls["n"] == 1, "显示名变化不应导致重新识别"

    def test_cache_hit_restores_upstream_model_attribution(self, tmp_path, monkeypatch):
        """全文档命中缓存时也必须能报出真实出站模型。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(rag_cfg, "RAG_OCR_CACHE_ENABLED", True)
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MIN_INTERVAL_MS", 0)
        monkeypatch.setattr(ocr_mod, "_ocr_cache_dir", tmp_path / "ocr_cache")
        ocr_mod._last_call_mono = 0.0
        runtime = {
            "provider": "dashscope",
            "model": "ocr-registered",
            "upstream_model": "ocr-upstream-x",
            "api_key": "sk-test",
            "base_url": "https://db.example/v1",
        }
        monkeypatch.setattr(ocr_mod, "_resolve_ocr_runtime_config", lambda: dict(runtime))
        monkeypatch.setattr(
            ocr_mod, "_ocr_image_dashscope", lambda png: "识别文本",
        )
        png = b"\x89PNG-cache-attribution"

        ocr_mod.reset_ocr_tracking()
        ocr_mod.ocr_image(png)          # MISS 落盘
        assert ocr_mod.get_ocr_result_meta()["upstream_model"] == "ocr-upstream-x"

        ocr_mod.reset_ocr_tracking()
        ocr_mod.ocr_image(png)          # HIT，不进入 Provider 分支
        meta = ocr_mod.get_ocr_result_meta()
        assert meta["cache_status"] == "hit"
        assert meta["upstream_model"] == "ocr-upstream-x"

    def test_legacy_cache_without_upstream_falls_back_to_runtime(self, tmp_path, monkeypatch):
        """旧缓存没有 upstream_model 字段时回退当前 runtime，不返回 None。"""
        import json as _json

        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(rag_cfg, "RAG_OCR_CACHE_ENABLED", True)
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MIN_INTERVAL_MS", 0)
        cache_dir = tmp_path / "ocr_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(ocr_mod, "_ocr_cache_dir", cache_dir)
        ocr_mod._last_call_mono = 0.0
        monkeypatch.setattr(
            ocr_mod, "_resolve_ocr_runtime_config",
            lambda: {
                "provider": "dashscope",
                "model": "ocr-registered",
                "upstream_model": "ocr-upstream-current",
                "api_key": "sk-test",
                "base_url": "https://db.example/v1",
            },
        )
        png = b"\x89PNG-legacy-cache"
        path = ocr_mod._cache_path(png)
        path.write_text(_json.dumps({"text": "旧文本", "model": "old"}), encoding="utf-8")

        ocr_mod.reset_ocr_tracking()
        assert ocr_mod.ocr_image(png) == "旧文本"
        assert ocr_mod.get_ocr_result_meta()["upstream_model"] == "ocr-upstream-current"

    def test_failed_ocr_is_not_cached(self, tmp_path, monkeypatch):
        """OCR-07：失败结果不得写缓存冒充成功内容。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "dashscope")
        monkeypatch.setattr(rag_cfg, "RAG_OCR_CACHE_ENABLED", True)
        monkeypatch.setattr(rag_cfg, "RAG_OCR_MIN_INTERVAL_MS", 0)
        monkeypatch.setattr(ocr_mod, "_ocr_cache_dir", tmp_path / "ocr_cache")
        ocr_mod._last_call_mono = 0.0
        monkeypatch.setattr(
            ocr_mod, "_resolve_ocr_runtime_config",
            lambda: {
                "provider": "dashscope",
                "model": "ocr-registered",
                "upstream_model": "ocr-upstream",
                "api_key": "sk-test",
                "base_url": "https://db.example/v1",
            },
        )

        def fake_call(png):
            raise RuntimeError("upstream 500")

        monkeypatch.setattr(ocr_mod, "_ocr_image_dashscope", fake_call)
        with pytest.raises(RuntimeError):
            ocr_mod.ocr_image(b"\x89PNG-fail")
        cache_dir = tmp_path / "ocr_cache"
        assert not cache_dir.exists() or list(cache_dir.glob("*.json")) == []

    def test_tracking_counts_partial_page_failure(self, monkeypatch):
        """一份扫描件中成功页和失败页都必须进入同一 OCR 阶段统计。"""
        monkeypatch.setattr(rag_cfg, "RAG_OCR_PROVIDER", "rapidocr")
        ocr_mod.reset_ocr_tracking()
        calls = {"n": 0}

        def _fake_rapidocr(_png):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("page failed")
            ocr_mod._ocr_last_confidence.set(0.62)
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
        assert stats["confidence_mean"] == 0.62
        assert stats["confidence_min"] == 0.62
        assert stats["confidence_scored_regions"] == 1
        assert stats["confidence_unavailable_regions"] == 0


# ============ ocr_triggered 标记贯通（AST → chunk metadata → quality_issues）============

class TestOcrTriggeredPropagation:
    """§5.1 质量记录：OCR 触发必须全链路可追溯，不得静默发生。"""

    def test_ast_flag_set_on_scanned_pdf(self, tmp_path, ocr_on):
        pdf = _make_scanned_pdf(tmp_path, "scan_flag.pdf", pages=2)
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
        pdf = _make_scanned_pdf(tmp_path, "scan_chunks.pdf", pages=1)
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

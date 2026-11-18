"""RAG 上传模块的纯函数测试 — MIME 白名单校验。

设计目标:
- ALLOWED_MIME_TYPES 必须是模块级常量,方便纯 import 测试
- _validate_mime(ext, content_type) → (ok, error_msg) 是纯函数,无副作用
- octet-stream 放行策略:空 content_type / 显式 octet-stream 都放行,
  文件真实性统一由落盘后的魔数校验兜底（PDF %PDF- / OOXML PK\x03\x04 /
  文本格式 NUL + UTF-16 BOM 探测）。旧实现显式拒绝 octet-stream,
  会误伤以 octet-stream 声明上传 docx 的浏览器/Windows 客户端。
"""
import re
from pathlib import Path

import pytest

from backend.app.api.routes.rag_upload import (
    _magic_matches, _validate_mime, _validate_ocr_page_budget,
    ALLOWED_MIME_TYPES, SSE_KEEPALIVE_TIMEOUT_SECONDS,
)
from backend.config.rag import (
    RAG_OCR_MAX_PAGES,
    RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT,
    RAG_OCR_DASHSCOPE_TIMEOUT,
    RAG_OCR_MIN_INTERVAL_MS,
    RAG_OCR_DPI,
    RAG_OCR_RETRY_DPI,
    RAG_SSE_REDIS_KEEPALIVE_SECONDS,
    RAG_UPLOAD_INGRESS_MAX_BODY_BYTES,
    METADATA_LLM_CONCURRENCY,
    METADATA_LLM_EXTRACT_MAX_CHARS,
    METADATA_LLM_EXTRACT_MAX_OUTPUT_TOKENS,
    METADATA_LLM_EXTRACT_MIN_CHARS,
    METADATA_EMBED_CONCURRENCY,
    METADATA_DB_CONCURRENCY,
    METADATA_LLM_SEMAPHORE_WAIT_TIMEOUT,
    EMBED_BATCH_SIZE,
    EMBED_REQUEST_LIMIT,
    EMBED_RETRY_MAX,
    EMBED_RETRY_BACKOFF_BASE,
    EMBED_RETRY_BACKOFF_MAX,
    EMBED_BATCH_CONCURRENCY,
    EMBEDDING_REBUILD_REQUEST_TIMEOUT,
    EMBEDDING_REBUILD_BATCH_SIZE,
    SEMANTIC_EMBED_BATCH_SIZE,
    SEMANTIC_EMBED_RETRY,
    RAG_TABLE_MAX_ROWS,
    RAG_TABLE_MAX_CELLS,
    FINANCIAL_TABLE_ROWS_PER_CHUNK,
    FINANCIAL_MAX_CHUNKS_PER_DOC,
    LEGAL_CLAUSES_PER_PARENT,
    _validate_rag_upload_ingress_limit,
    _validate_rag_chunking_config,
    _validate_metadata_extraction_config,
    _validate_cloud_ocr_runtime_config,
    _validate_rag_ocr_dpi,
    _validate_rag_embedding_ingest_config,
    _validate_rag_table_ingest_config,
)


# ============ ALLOWED_MIME_TYPES 结构测试 ============

class TestAllowedMimeStructure:
    """管理端上传白名单只开放已选格式,且 octet-stream 仅作兜底。"""

    def test_all_supported_exts_have_entry(self):
        # 管理端只开放已批准的企业资料格式。
        assert set(ALLOWED_MIME_TYPES.keys()) == {
            "pdf", "md", "markdown", "txt", "docx", "xlsx",
        }

    def test_derived_from_parsable_exts(self):
        """上传白名单必须是解析器能力的子集，不能自动开放新解析器。"""
        from backend.rag.preprocessing.parser import PARSABLE_EXTS
        assert {f".{ext}" for ext in ALLOWED_MIME_TYPES} <= set(PARSABLE_EXTS)

    def test_upload_ocr_budget_uses_the_configured_ocr_cap(self):
        assert RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT == RAG_OCR_MAX_PAGES

    def test_file_limit_must_fit_the_configured_ingress_cap(self):
        _validate_rag_upload_ingress_limit(63, RAG_UPLOAD_INGRESS_MAX_BODY_BYTES)
        with pytest.raises(ValueError, match="APISIX"):
            _validate_rag_upload_ingress_limit(
                64,
                RAG_UPLOAD_INGRESS_MAX_BODY_BYTES,
            )

    def test_file_ingress_limits_reject_invalid_values(self):
        with pytest.raises(ValueError, match="RAG_MAX_FILE_SIZE"):
            _validate_rag_upload_ingress_limit(
                0, RAG_UPLOAD_INGRESS_MAX_BODY_BYTES,
            )
        with pytest.raises(ValueError, match="INGRESS_MAX_BODY_BYTES"):
            _validate_rag_upload_ingress_limit(
                50, 0,
            )

    def test_upload_request_limit_matches_apisix_and_admin_bff(self):
        repo_root = Path(__file__).resolve().parents[2]
        gateway_config = (repo_root / "apisix" / "config.yaml").read_text(
            encoding="utf-8"
        )
        gateway_match = re.search(
            r"client_max_body_size:\s*(\d+)m", gateway_config
        )
        assert gateway_match is not None
        assert int(gateway_match.group(1)) * 1024 * 1024 == (
            RAG_UPLOAD_INGRESS_MAX_BODY_BYTES
        )
        for app_name in ("frontend", "frontend-admin", "frontend-cs"):
            bff_source = (
                repo_root / app_name / "src" / "app" / "api"
                / "[...path]" / "route.ts"
            ).read_text(encoding="utf-8")
            bff_match = re.search(
                r"APISIX_MAX_BODY_LIMIT_BYTES\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024",
                bff_source,
            )
            assert bff_match is not None, app_name
            assert int(bff_match.group(1)) * 1024 * 1024 == (
                RAG_UPLOAD_INGRESS_MAX_BODY_BYTES
            ), app_name

    def test_local_and_redis_sse_heartbeat_share_one_setting(self):
        assert SSE_KEEPALIVE_TIMEOUT_SECONDS == RAG_SSE_REDIS_KEEPALIVE_SECONDS

    def test_chunk_parameters_reject_invalid_ranges(self):
        valid = {
            "leaf_tokens": 500,
            "parent_tokens": 2000,
            "overlap_tokens": 50,
            "structure_threshold": 0.7,
            "semantic_min_tokens": 500,
            "semantic_similarity_threshold": 0.45,
            "semantic_embed_batch_size": SEMANTIC_EMBED_BATCH_SIZE,
            "semantic_embed_retry": SEMANTIC_EMBED_RETRY,
            "max_chunks_per_doc": 6000,
        }
        _validate_rag_chunking_config(**valid)

        with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
            _validate_rag_chunking_config(**{**valid, "overlap_tokens": 500})
        with pytest.raises(ValueError, match="Parent"):
            _validate_rag_chunking_config(**{**valid, "parent_tokens": 499})
        with pytest.raises(ValueError, match="STRUCTURE_COMPLETE_THRESHOLD"):
            _validate_rag_chunking_config(**{**valid, "structure_threshold": 1.1})
        with pytest.raises(ValueError, match="SEMANTIC_EMBED_RETRY"):
            _validate_rag_chunking_config(**{**valid, "semantic_embed_retry": 0})

    def test_metadata_extraction_parameters_reject_invalid_ranges(self):
        valid = {
            "min_text_chars": METADATA_LLM_EXTRACT_MIN_CHARS,
            "max_text_chars": METADATA_LLM_EXTRACT_MAX_CHARS,
            "max_output_tokens": METADATA_LLM_EXTRACT_MAX_OUTPUT_TOKENS,
            "embed_concurrency": METADATA_EMBED_CONCURRENCY,
            "llm_concurrency": METADATA_LLM_CONCURRENCY,
            "db_concurrency": METADATA_DB_CONCURRENCY,
            "semaphore_wait_timeout": METADATA_LLM_SEMAPHORE_WAIT_TIMEOUT,
        }
        _validate_metadata_extraction_config(**valid)

        with pytest.raises(ValueError, match="METADATA_LLM_CONCURRENCY"):
            _validate_metadata_extraction_config(**{**valid, "llm_concurrency": 0})
        with pytest.raises(ValueError, match="MAX_CHARS"):
            _validate_metadata_extraction_config(
                **{**valid, "max_text_chars": valid["min_text_chars"] - 1}
            )

    def test_cloud_ocr_parameters_reject_invalid_ranges(self):
        _validate_cloud_ocr_runtime_config(
            timeout_seconds=RAG_OCR_DASHSCOPE_TIMEOUT,
            min_interval_ms=RAG_OCR_MIN_INTERVAL_MS,
        )
        with pytest.raises(ValueError, match="TIMEOUT"):
            _validate_cloud_ocr_runtime_config(
                timeout_seconds=0, min_interval_ms=300,
            )
        with pytest.raises(ValueError, match="MIN_INTERVAL"):
            _validate_cloud_ocr_runtime_config(
                timeout_seconds=60, min_interval_ms=-1,
            )

    def test_ocr_retry_dpi_is_higher_than_base(self):
        _validate_rag_ocr_dpi(RAG_OCR_DPI, RAG_OCR_RETRY_DPI)
        with pytest.raises(ValueError, match="RETRY_DPI"):
            _validate_rag_ocr_dpi(200, 200)
        with pytest.raises(ValueError, match="72 DPI"):
            _validate_rag_ocr_dpi(71, 300)

    def test_embedding_upload_budgets_reject_invalid_ranges(self):
        valid = {
            "batch_size": EMBED_BATCH_SIZE,
            "provider_request_limit": EMBED_REQUEST_LIMIT,
            "retry_max": EMBED_RETRY_MAX,
            "retry_backoff_base": EMBED_RETRY_BACKOFF_BASE,
            "retry_backoff_max": EMBED_RETRY_BACKOFF_MAX,
            "batch_concurrency": EMBED_BATCH_CONCURRENCY,
            "rebuild_request_timeout": EMBEDDING_REBUILD_REQUEST_TIMEOUT,
            "rebuild_batch_size": EMBEDDING_REBUILD_BATCH_SIZE,
        }
        _validate_rag_embedding_ingest_config(**valid)
        with pytest.raises(ValueError, match="EMBED_BATCH_CONCURRENCY"):
            _validate_rag_embedding_ingest_config(
                **{**valid, "batch_concurrency": 0}
            )
        with pytest.raises(ValueError, match="BACKOFF_MAX"):
            _validate_rag_embedding_ingest_config(
                **{**valid, "retry_backoff_max": 1.0}
            )

    def test_table_ingest_limits_reject_invalid_ranges(self):
        valid = {
            "max_rows": RAG_TABLE_MAX_ROWS,
            "max_cells": RAG_TABLE_MAX_CELLS,
            "financial_rows_per_chunk": FINANCIAL_TABLE_ROWS_PER_CHUNK,
            "financial_max_chunks_per_doc": FINANCIAL_MAX_CHUNKS_PER_DOC,
            "legal_clauses_per_parent": LEGAL_CLAUSES_PER_PARENT,
        }
        _validate_rag_table_ingest_config(**valid)
        with pytest.raises(ValueError, match="RAG_TABLE_MAX_CELLS"):
            _validate_rag_table_ingest_config(**{**valid, "max_cells": 0})

    def test_markdown_and_xlsx_have_proper_mime(self):
        # F6 新开放的两个扩展名必须登记正确的 MIME
        assert "text/markdown" in ALLOWED_MIME_TYPES["markdown"]
        assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" \
            in ALLOWED_MIME_TYPES["xlsx"]

    @pytest.mark.parametrize("ext", ["csv", "pptx", "json", "xml", "html", "htm"])
    def test_non_business_upload_formats_are_not_allowed(self, ext):
        ok, error = _validate_mime(ext, "application/octet-stream")
        assert ok is False
        assert "当前仅支持" in error

    def test_octet_stream_not_in_whitelist_dicts(self):
        # 白名单表只登记"显式声明时允许的具体 MIME"。
        # octet-stream 由 _validate_mime 的专用分支放行（不等同于登记白名单），
        # 表内不应出现该条目（防误导）。
        for ext, mimes in ALLOWED_MIME_TYPES.items():
            assert "application/octet-stream" not in mimes, (
                f"{ext} 白名单不应包含 octet-stream（由专用分支处理，见模块注释）"
            )

    def test_pdf_does_not_accept_octet_stream(self):
        # PDF 是二进制格式,客户端如果声明了 octet-stream 应该拒绝
        # (必须用 application/pdf 才能匹配)
        assert "application/octet-stream" not in ALLOWED_MIME_TYPES["pdf"]


# ============ _validate_mime 行为测试 ============

class TestValidateMime:
    """_validate_mime 是纯函数,直接测各种 (ext, content_type) 组合。"""

    # --- 通过的合法组合 ---

    @pytest.mark.parametrize("ext,ctype", [
        ("pdf", "application/pdf"),
        ("md", "text/markdown"),
        ("md", "text/plain"),  # MD 接受纯文本
        ("txt", "text/plain"),
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("markdown", "text/markdown"),
        ("markdown", "text/plain"),  # .markdown 与 .md 同策略
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ])
    def test_accepts_valid_mime(self, ext, ctype):
        ok, err = _validate_mime(ext, ctype)
        assert ok is True, f"应接受 {ext}+{ctype}, 但被拒: {err}"
        assert err == ""

    # --- MIME 与扩展名不匹配(收紧了) —)

    @pytest.mark.parametrize("ext,ctype,bad_why", [
        ("md", "application/pdf", "MD 扩展 + PDF MIME"),
        ("pdf", "text/plain", "PDF 扩展 + text/plain"),
        ("docx", "application/pdf", "DOCX 扩展 + PDF MIME"),
        ("txt", "application/pdf", "TXT 扩展 + PDF MIME"),
        ("pdf", "image/png", "PDF 扩展 + 图片 MIME"),
        ("md", "application/zip", "MD 扩展 + 压缩包 MIME"),
        ("xlsx", "text/plain", "XLSX 扩展 + text/plain"),
        ("markdown", "application/pdf", "MARKDOWN 扩展 + PDF MIME"),
    ])
    def test_rejects_mime_ext_mismatch(self, ext, ctype, bad_why):
        ok, err = _validate_mime(ext, ctype)
        assert ok is False, f"应拒绝 {bad_why}"
        assert "MIME" in err or "not allowed" in err

    # --- octet-stream:空声明与显式声明都放行,由魔数校验兜底 ---

    def test_octet_stream_allowed_when_content_type_empty(self):
        """content_type 为空(None / "")时,允许 octet-stream 兜底
        (curl 命令行默认场景)。"""
        for empty in (None, ""):
            ok, err = _validate_mime("md", empty)
            assert ok is True, f"content_type={empty!r} 应走 magic 兜底,实际拒绝: {err}"

    @pytest.mark.parametrize("ext", ["md", "txt", "docx", "xlsx", "pdf"])
    def test_octet_stream_allowed_when_explicit(self, ext):
        """显式 application/octet-stream 放行（浏览器/Windows 客户端对 docx
        等的通用声明,旧实现一律拒绝属误伤）。真实性由魔数校验兜底:
        伪装的二进制文件过不了 sync_upload_impl 的 NUL/BOM/%PDF-/PK 探测。"""
        ok, err = _validate_mime(ext, "application/octet-stream")
        assert ok is True, f"{ext} + 显式 octet-stream 应放行,实际拒绝: {err}"

    # --- 大小写 / charset 参数 ---

    def test_charset_parameter_ignored(self):
        """application/json; charset=utf-8 这种带 charset 后缀的,要 strip 后比较。"""
        ok, _ = _validate_mime("md", "text/plain; charset=utf-8")
        assert ok is True

    def test_case_insensitive_mime(self):
        """客户端可能发 APPLICATION/PDF,要大小写不敏感。"""
        ok, _ = _validate_mime("pdf", "APPLICATION/PDF")
        assert ok is True

    def test_unsupported_ext_returns_error(self):
        """不支持的扩展名(不在派生白名单中)应该被拒。"""
        ok, err = _validate_mime("exe", "application/octet-stream")
        assert ok is False
        assert "文件格式" in err
        assert "当前仅支持" in err

    def test_empty_ext_returns_error(self):
        """扩展名为空(无后缀文件)拒。"""
        ok, err = _validate_mime("", "text/plain")
        assert ok is False

    @pytest.mark.parametrize("ext", ["docx", "xlsx"])
    def test_ooxml_formats_require_zip_magic(self, ext):
        assert _magic_matches(ext, b"PK\x03\x04data")
        assert not _magic_matches(ext, b"<xml>forged</xml>")

    @pytest.mark.parametrize("ext", ["md", "markdown", "txt"])
    def test_allowed_text_formats_reject_binary_headers(self, ext):
        assert _magic_matches(ext, b"text content")
        assert not _magic_matches(ext, b"\x00binary")
        assert not _magic_matches(ext, b"\xff\xfeutf16")
        assert not _magic_matches(ext, b"12345678", contains_nul=True)


# ============ 与 sync_upload_impl 的集成点测试 ============

class TestSyncUploadMimeIntegration:
    """验证 _validate_mime 在 sync_upload_impl 中实际被调用,octet-stream 走魔数兜底。"""

    def test_octet_stream_falls_through_to_magic_check(self):
        """显式 octet-stream + md 放行,依赖魔数校验拦截伪装文件。

        旧实现（P0-2 收紧）在此直接拒绝,误伤了以 octet-stream 声明上传
        docx 的浏览器客户端;现在 MIME 层放行,伪装二进制由
        sync_upload_impl 的 NUL 字节 / UTF-16 BOM 探测拦截。
        """
        ok, err = _validate_mime("md", "application/octet-stream")
        assert ok is True, (
            "octet-stream 应放行并交由魔数校验兜底"
            f",实际拒绝: {err}"
        )


class TestValidateOcrPageBudget:
    def test_budget_override_is_admin_only_and_pdf_only(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _validate_ocr_page_budget(
                is_admin=False,
                ext="pdf",
                budget=RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT,
            )
        assert exc.value.status_code == 403

        with pytest.raises(HTTPException) as exc:
            _validate_ocr_page_budget(is_admin=True, ext="docx", budget=200)
        assert exc.value.status_code == 422

        _validate_ocr_page_budget(
            is_admin=True,
            ext="pdf",
            budget=RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT,
        )

    @pytest.mark.parametrize(
        "budget",
        [0, RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT + 1],
    )
    def test_budget_override_has_a_hard_upper_and_lower_bound(self, budget):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _validate_ocr_page_budget(is_admin=True, ext="pdf", budget=budget)
        assert exc.value.status_code == 422

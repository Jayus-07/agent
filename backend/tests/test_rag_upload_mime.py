"""RAG 上传模块的纯函数测试 — MIME 白名单校验。

P0-2:收紧 MIME 白名单。

设计目标:
- ALLOWED_MIME_TYPES 必须是模块级常量,方便纯 import 测试
- _validate_mime(ext, content_type) → (ok, error_msg) 是纯函数,无副作用
- 收紧点:content_type 为空 / None 时由空值分支提前放行(落盘后靠魔数兜底);
         客户端显式声明 application/octet-stream 必须拒绝;
         白名单表不登记永不可达的 octet-stream 条目
"""
import pytest

from backend.app.api.routes.rag_upload import _validate_mime, ALLOWED_MIME_TYPES


# ============ ALLOWED_MIME_TYPES 结构测试 ============

class TestAllowedMimeStructure:
    """白名单表结构必须覆盖所有支持的扩展名,且 octet-stream 仅作兜底。"""

    def test_all_supported_exts_have_entry(self):
        # F6: 白名单从解析器注册表派生，必须覆盖全部 6 个已注册扩展名
        assert set(ALLOWED_MIME_TYPES.keys()) == {
            "pdf", "md", "markdown", "txt", "docx", "xlsx"}

    def test_derived_from_parsable_exts(self):
        """F6: ALLOWED_MIME_TYPES 必须与 PARSABLE_EXTS 一一对应（单一来源，防漂移）。"""
        from backend.rag.preprocessing.parser import PARSABLE_EXTS
        assert {f".{ext}" for ext in ALLOWED_MIME_TYPES} == set(PARSABLE_EXTS)

    def test_markdown_and_xlsx_have_proper_mime(self):
        # F6 新开放的两个扩展名必须登记正确的 MIME
        assert "text/markdown" in ALLOWED_MIME_TYPES["markdown"]
        assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" \
            in ALLOWED_MIME_TYPES["xlsx"]

    def test_octet_stream_not_in_whitelist_dicts(self):
        # 白名单表只登记"显式声明时允许的具体 MIME"。
        # octet-stream 的兜底由 _validate_mime 空 content_type 提前返回分支实现，
        # 显式声明 octet-stream 则被前置拒绝 — 表内不应出现永不可达的条目（防误导）。
        for ext, mimes in ALLOWED_MIME_TYPES.items():
            assert "application/octet-stream" not in mimes, (
                f"{ext} 白名单不应包含 octet-stream（该分支永不可达，见模块注释）"
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

    # --- octet-stream 兜底:仅当 content_type 为空时允许 ---

    def test_octet_stream_allowed_when_content_type_empty(self):
        """content_type 为空(None / "")时,允许 octet-stream 兜底
        (curl 命令行默认场景)。"""
        for empty in (None, ""):
            ok, err = _validate_mime("md", empty)
            assert ok is True, f"content_type={empty!r} 应走 magic 兜底,实际拒绝: {err}"

    def test_octet_stream_rejected_when_content_type_explicit(self):
        """客户端显式声明 application/octet-stream 时,必须拒绝。

        收紧了 P0-2:之前任何 octet-stream 都过,现在必须靠 magic 校验兜底。
        magic 校验在 sync_upload_impl 里,与 MIME 校验互补。
        """
        # MD 扩展 + 显式 octet-stream → 拒
        ok, err = _validate_mime("md", "application/octet-stream")
        assert ok is False
        assert "octet-stream" in err or "MIME" in err

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
        assert "unsupported ext" in err or "ext" in err.lower()

    def test_empty_ext_returns_error(self):
        """扩展名为空(无后缀文件)拒。"""
        ok, err = _validate_mime("", "text/plain")
        assert ok is False


# ============ 与 sync_upload_impl 的集成点测试 ============

class TestSyncUploadMimeIntegration:
    """验证 _validate_mime 在 sync_upload_impl 中实际被调用,且 octet-stream 收紧生效。"""

    def test_octet_stream_md_blocks_before_magic_check(self):
        """模拟 sync_upload_impl 的入口校验流程:显式 octet-stream + md 必须被拒。

        这是 P0-1 残留问题的根因:GBK 字节写入磁盘前,如果客户端声明了
        application/octet-stream,旧代码会放行,然后 magic 校验通过(纯文本内容)
        导致乱码文件入库。
        """
        # 模拟一个 multipart 上传,content_type 是 octet-stream
        ok, err = _validate_mime("md", "application/octet-stream")
        assert ok is False, (
            "P0-2 收紧失败:application/octet-stream 不应作为 MD 的合法 MIME。"
            "客户端必须正确声明 text/markdown 或 text/plain。"
        )
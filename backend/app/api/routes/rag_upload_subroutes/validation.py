"""RAG Upload 文件格式探测、MIME与OCR预算预检。"""
from __future__ import annotations

import hashlib
import os

from fastapi import HTTPException

from backend.app.api.routes.rag_upload_subroutes.base import *
from backend.rag.preprocessing.parser import PARSABLE_EXTS

def _validate_ocr_page_budget(
    *, is_admin: bool, ext: str, budget: int | None,
) -> None:
    """校验可选的管理员单次 PDF OCR 预算覆盖。"""
    if budget is None:
        return
    if not is_admin:
        raise HTTPException(status_code=403, detail="提高单次 OCR 页预算仅限管理员")
    if ext != "pdf":
        raise HTTPException(status_code=422, detail="OCR 页预算只适用于 PDF 文件")
    if not 1 <= budget <= RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=(
                "OCR 页预算必须在 1 到 "
                f"{RAG_OCR_MAX_PAGES_UPLOAD_HARD_LIMIT} 页之间"
            ),
        )


def _pdf_has_text_layer(
    path: str, max_pages: int = RAG_PDF_MAX_PAGES,
) -> bool | None:
    """检测 PDF 是否有文本层（扫描件预检，纯函数便于测试）。

    使用解析器的页数预算与 OCR 触发阈值，避免上传预检维护第二套边界。
    Returns:
        True  — 资源预算内存在足够文本 → 正常解析
        False — 检查页全部无文本 → 扫描件/纯图片，索引必然失败，入口即拒
        None  — 无法判定（加密/损坏/PyMuPDF 异常）→ 放行，交给解析层报错

    判定阈值与解析器共用 RAG_OCR_MIN_TEXT_CHARS。
    """
    try:
        import fitz  # PyMuPDF（pdf_parser 同源依赖）
        with fitz.open(path) as doc:
            if doc.is_encrypted:
                return None
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                if len(page.get_text().strip()) >= RAG_OCR_MIN_TEXT_CHARS:
                    return True
            return False
    except Exception:
        return None


def sha256_of_file(
    path: str, chunk_size: int = RAG_UPLOAD_CHUNK_SIZE,
) -> str:
    """分块流式计算文件 SHA256 — 避免整文件读入内存。

    背景:旧实现 hashlib.sha256(f.read()) 对 50MB 上限的文件一次性
    读入内存,并发上传时内存峰值翻倍。改为固定块大小流式读取。
    """
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _validate_mime(ext: str, content_type: str | None) -> tuple[bool, str]:
    """校验 MIME 与扩展名一致性（纯函数）。

    Args:
        ext: 文件扩展名（小写，无点），如 "md" / "pdf"。
        content_type: HTTP 声明的 Content-Type（含 charset 后缀也 OK），可能为 None/""。

    Returns:
        (ok, error_msg) — ok=True 时 error_msg 为空字符串。

    规则:
      - ext 不在 SUPPORTED_EXTS → 拒（unsupported ext）
      - content_type 为 None / "" → 通过（走 magic 校验兜底）
      - 显式 content_type（去 charset 后）必须在 ALLOWED_MIME_TYPES[ext] 内
      - application/octet-stream 放行：这是 Windows / 部分浏览器客户端对
        "未知二进制"的通用声明，一律拒绝会误伤合法的 docx/xlsx 上传；
        文件真实性由落盘后的魔数校验兜底（PDF %PDF- / OOXML PK\x03\x04 /
        文本格式 NUL + UTF-16 BOM 探测）。
    """
    if ext not in SUPPORTED_EXTS:
        suffix = f".{ext}" if ext else "无扩展名"
        return False, (
            f"不支持 {suffix} 文件格式。当前仅支持"
            f" {MANAGEMENT_UPLOAD_FORMAT_LABEL}。"
        )
    if not content_type or not content_type.strip():
        # content_type 为空（curl 命令行等场景）→ 走 magic 校验兜底
        return True, ""
    # strip charset 参数（如 "text/plain; charset=utf-8" → "text/plain"）
    ctype = content_type.split(";", 1)[0].strip().lower()
    if not ctype:
        return True, ""
    # octet-stream 放行（历史教训改为由魔数校验兜底）：旧实现在此直接拒绝，
    # 结果是浏览器/Windows 客户端上传 docx（常声明 octet-stream）被误拒。
    # 二进制格式有强魔数（%PDF- / PK\x03\x04），文本格式有 NUL + BOM 探测，
    # 伪装文件进不到索引环节。
    if ctype == "application/octet-stream":
        return True, ""
    if ctype not in ALLOWED_MIME_TYPES[ext]:
        return False, f".{ext} 文件 MIME 类型不匹配：{ctype}"
    return True, ""


def _magic_matches(
    ext: str, head: bytes, *, contains_nul: bool | None = None,
) -> bool:
    """按扩展名校验上传文件头；文本格式拒绝明显的二进制伪装。"""
    if ext == "pdf":
        return head.startswith(b"%PDF-")
    if ext in {"docx", "xlsx"}:
        return head.startswith(b"PK\x03\x04")
    if ext in {"md", "markdown", "txt"}:
        has_nul = b"\x00" in head if contains_nul is None else contains_nul
        return not has_nul and head[:2] not in (b"\xff\xfe", b"\xfe\xff")
    return True


for _fn in [
    _validate_ocr_page_budget,
    _pdf_has_text_layer,
    sha256_of_file,
    _validate_mime,
    _magic_matches
]:
    facade.register(_fn.__name__, _fn)

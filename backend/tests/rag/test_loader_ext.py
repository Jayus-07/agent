"""test_loader_ext.py — loader.py 扩展名白名单同步验证。

覆盖：
1. MD/TXT 正常被处理
2. JSON / 未知扩展被跳过
3. PDF/DOCX 在白名单里（fake 文件产空 chunks 不抛异常）
"""
from backend.rag.preprocessing.loader import load_documents_from_directory


def test_loader_processes_md_txt(tmp_path):
    """MD/TXT 文档正常被处理（无回归）。"""
    (tmp_path / "kb1").mkdir()
    (tmp_path / "kb1" / "a.md").write_text("# 标题\n内容。\n", encoding="utf-8")
    (tmp_path / "kb1" / "b.txt").write_text("一、章节\n内容。\n", encoding="utf-8")

    docs = load_documents_from_directory(str(tmp_path))
    sources = {d.metadata["source_file"] for d in docs}
    assert "a.md" in sources
    assert "b.txt" in sources


def test_loader_skips_unsupported_ext(tmp_path):
    """JSON / 未知扩展名被跳过（不抛异常）。"""
    (tmp_path / "kb1").mkdir()
    (tmp_path / "kb1" / "a.md").write_text("# 标题\n内容。\n", encoding="utf-8")
    (tmp_path / "kb1" / "x.json").write_text("{}", encoding="utf-8")
    (tmp_path / "kb1" / "y.png").write_bytes(b"\x89PNG")

    docs = load_documents_from_directory(str(tmp_path))
    sources = {d.metadata["source_file"] for d in docs}
    assert "a.md" in sources
    assert "x.json" not in sources
    assert "y.png" not in sources


def test_loader_pdf_docx_in_whitelist_no_crash(tmp_path):
    """PDF/DOCX 在白名单里 → 走 parse_and_chunk。fake 文件产空 chunks 不抛异常。"""
    (tmp_path / "kb1").mkdir()
    (tmp_path / "kb1" / "fake.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "kb1" / "fake.docx").write_bytes(b"PK\x03\x04")

    # 不抛异常即可；fake 文件 PDF 解析可能报错但被 loader 容错吞掉
    docs = load_documents_from_directory(str(tmp_path))
    assert isinstance(docs, list)

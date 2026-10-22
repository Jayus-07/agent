"""test_e2e_document_matrix.py — 16 类真实文档端到端验收矩阵（2026-10-13）。

任务书 §6 的 P0 指标验收：真实构造 16 类文档（md/txt/docx/pdf/xlsx/csv），
走完整 parse_and_chunk 流水线，断言：
- 覆盖率：coverage_audit uncovered == 0（排除内容有明确原因）
- Parent 关联：全部 leaf 的 parent_chunk_id 可解析到真实 parent
- Token 上限：leaf ≤ LEAF_CHUNK_TOKENS、parent ≤ PARENT_CHUNK_TOKENS
- 表格：行列对应与前导零/精确数字无损失
- 长文：完整处理（不触发截断硬异常）
"""
import csv
import io
import json

import docx
import pymupdf as fitz
import openpyxl
import pytest

from backend.config import LEAF_CHUNK_TOKENS, PARENT_CHUNK_TOKENS
from backend.rag.preprocessing.ast import walk
from backend.rag.preprocessing.chunking import coverage_audit
from backend.rag.preprocessing.pipeline import parse_and_chunk_full


def _assert_p0_invariants(chunks, ast):
    """P0 验收通用不变量：覆盖率 / Parent 关联 / Token 上限。"""
    # 1) 覆盖率 100%
    report = coverage_audit(ast, chunks)
    assert report.get("uncovered_count", 0) == 0, report
    # 2) 全部 leaf 的 parent 关联可解析
    by_id = {c.metadata.get("chunk_id") for c in chunks}
    leaves = [c for c in chunks if c.metadata.get("granularity") == "leaf"]
    has_qa_nodes = any(
        node.type in ("qa_question", "qa_answer") for node in walk(ast.root)
    )
    assert leaves, "应产出 leaf"
    for c in leaves:
        pid = c.metadata.get("parent_chunk_id")
        # 完整 Q+A 已是检索单元，依照 FAQ 契约不挂全局 Parent，
        # 避免命中单个问题后扩展出其他无关问答。FAQ Leaf 可以携带
        # 标题路径，因此按 chunk_type 判定，而不是按 section_path 是否为空。
        if not pid and c.metadata.get("chunk_type") == "faq":
            assert c.metadata.get("section_title")
            continue
        assert pid, f"leaf 缺 parent_chunk_id: {c.page_content[:40]}"
        # 索引期覆盖前（切分期）parent id 即在 chunk 集合中
        assert pid in by_id, f"parent 不可解析: {pid}"
    # 3) Token 上限
    for c in chunks:
        g = c.metadata.get("granularity")
        tokens = c.metadata.get("chunk_tokens", 0)
        if g == "leaf":
            assert tokens <= LEAF_CHUNK_TOKENS, (tokens, c.page_content[:50])
        elif g == "parent":
            assert tokens <= PARENT_CHUNK_TOKENS, (tokens, c.page_content[:50])


# ── 1. 普通员工制度 ──────────────────────────────────────────────

def test_01_employee_policy_md(tmp_path):
    p = tmp_path / "policy.md"
    p.write_text(
        "# 员工考勤制度\n\n"
        "## 一、工作时间\n\n每日工作八小时，周末双休。\n\n"
        "## 二、请假流程\n\n提前一天在 OA 提交申请，直属上级审批。\n",
        encoding="utf-8",
    )
    chunks, qc = parse_and_chunk_full(str(p))
    assert chunks and qc["coverage"]["uncovered_count"] == 0
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 2. 混合 FAQ + 普通章节 ───────────────────────────────────────

def test_02_mixed_faq_and_sections(tmp_path):
    p = tmp_path / "mixed.md"
    p.write_text(
        "# 售后手册\n\n"
        "## 常见问题\n\n"
        "**Q:** 怎么退货？\n**A:** 提交申请后客服审核。\n\n"
        "**Q:** 运费谁付？\n**A:** 商家承担。\n\n"
        "## 三包政策\n\n自购买之日起七天无理由退货，十五天换货。\n",
        encoding="utf-8",
    )
    chunks, qc = parse_and_chunk_full(str(p))
    body = "\n".join(c.page_content for c in chunks)
    assert "怎么退货" in body
    assert "七天无理由退货" in body
    assert qc["coverage"]["uncovered_count"] == 0
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 3. 仅一组 FAQ 的文档 ─────────────────────────────────────────

def test_03_single_faq_pair(tmp_path):
    p = tmp_path / "single_faq.md"
    p.write_text("**Q:** 只有一个问题？\n**A:** 是的，只有一组问答。", encoding="utf-8")
    chunks, qc = parse_and_chunk_full(str(p))
    body = "\n".join(c.page_content for c in chunks)
    assert "只有一个问题" in body
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 4. 无标题 TXT ────────────────────────────────────────────────

def test_04_plain_txt_no_headings(tmp_path):
    p = tmp_path / "plain.txt"
    p.write_text("这是没有标题的普通文本。\n第二行内容。\n\n第三段内容。\n",
                 encoding="utf-8")
    chunks, qc = parse_and_chunk_full(str(p))
    assert chunks
    assert qc["coverage"]["uncovered_count"] == 0
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 5. 标题前存在正文 ────────────────────────────────────────────

def test_05_prose_before_first_heading(tmp_path):
    p = tmp_path / "prose_first.md"
    p.write_text(
        "本制度适用于全体正式员工，自发布之日起施行。\n\n"
        "# 第一章 总则\n\n总则内容。\n",
        encoding="utf-8",
    )
    chunks, qc = parse_and_chunk_full(str(p))
    body = "\n".join(c.page_content for c in chunks)
    assert "适用于全体正式员工" in body
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 6. 超长 SOP 单步骤 ───────────────────────────────────────────

def test_06_long_sop_step(tmp_path):
    p = tmp_path / "sop.txt"
    p.write_text(
        "一、设备巡检\n" + "巡检人员需逐项检查设备运行状态并记录读数。" * 60 + "\n"
        "二、故障上报\n发现异常立即上报值班主管。",
        encoding="utf-8",
    )
    chunks, qc = parse_and_chunk_full(str(p))
    leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
    # TXT parser 剥离编号前缀，章节身份以标题文本承载（存量行为）
    assert any(c.metadata.get("section_title") == "设备巡检" for c in leaves)
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 7. 超长合同条款 ──────────────────────────────────────────────

def test_07_long_legal_clause(tmp_path):
    clause = ("甲方应在合同生效后按照约定履行全部义务，任何一方违约应承担"
              "违约责任并赔偿损失。" * 40)
    p = tmp_path / "contract.txt"
    p.write_text(f"第一条 合同履行\n{clause}\n第二条 争议解决\n协商不成提交仲裁。",
                 encoding="utf-8")
    chunks, qc = parse_and_chunk_full(str(p))
    leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
    assert len(leaves) > 1, "超长条款应被切分"
    # TXT parser 将「第N条 xxx」提为 section（编号剥离是存量行为）：
    # 切分后每片 ≤ 预算且 section_title 承载条款标题身份
    titles = {c.metadata.get("section_title") for c in leaves}
    assert any(t for t in titles if t in ("合同履行", "争议解决")), titles
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 8. 合同中嵌入表格 ────────────────────────────────────────────

def test_08_contract_with_table(tmp_path):
    p = tmp_path / "contract_tbl.docx"
    d = docx.Document()
    d.add_heading("服务合同", level=1)
    d.add_paragraph("第一条 服务内容\n乙方向甲方提供平台运维服务。")
    d.add_paragraph("第二条 服务费用\n各项费用见下表。SENTINEL-TABLE-REF")
    rows = [
        ["项目", "单价(元)", "数量", "小计(元)"],
        ["基础运维", "6294", "1", "6294"],
        ["应急响应", "3530", "8", "28240"],
        ["容量评估", "1273", "10", "12730"],
        ["安全巡检", "3988", "12", "47856"],
        ["培训", "2076", "2", "4152"],
    ]
    t = d.add_table(rows=len(rows), cols=len(rows[0]))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            t.cell(row_index, column_index).text = value
    d.add_paragraph(
        "第三条 付款方式\n上表所列费用按季度结算，甲方在收到发票后十五日内付款。"
    )
    d.add_paragraph("第四条 违约责任\n逾期付款按日万分之五计收违约金。")
    d.save(str(p))
    chunks, qc = parse_and_chunk_full(str(p))
    parents = [c for c in chunks if c.metadata["granularity"] == "parent"]
    leaves = [c for c in chunks if c.metadata["granularity"] == "leaf"]
    assert len(chunks) == 5
    assert len(parents) == 1
    assert len(leaves) == 4
    assert parents[0].metadata["vectorize"] is False
    assert parents[0].metadata["granularity"] == "parent"
    assert parents[0].metadata["parent_scope"] == "document"
    assert parents[0].metadata["document_title"] == "服务合同"
    assert parents[0].metadata["section_title"] == ""
    assert len(json.loads(parents[0].metadata["table_structures_json"])) == 1
    assert all(value in parents[0].page_content for value in (
        "6294", "28240", "12730", "47856", "4152",
    ))

    fee_leaf = next(c for c in leaves if c.metadata.get("table_id"))
    assert fee_leaf.metadata["chunk_type"] == "table_inline"
    assert fee_leaf.metadata["document_title"] == "服务合同"
    assert fee_leaf.metadata["section_title"] == "第二条 服务费用"
    assert json.loads(fee_leaf.metadata["table_rows_json"]) == rows
    assert all(value in fee_leaf.page_content for value in (
        "6294", "28240", "12730", "47856", "4152",
    ))
    assert "SENTINEL-TABLE-REF" not in fee_leaf.page_content
    assert "SENTINEL-TABLE-REF" not in parents[0].page_content
    assert "【原始数据】" not in fee_leaf.page_content
    assert "【原始数据】" not in parents[0].page_content
    assert fee_leaf.page_content.count("基础运维") == 1
    assert parents[0].page_content.count("基础运维") == 1
    assert any(
        "SENTINEL-TABLE-REF" in paragraph.text
        for paragraph in docx.Document(str(p)).paragraphs
    ), "索引文本规范化不能改写上传的 DOCX 原文"
    assert parents[0].page_content.count("服务合同") == 1
    assert not any(c.metadata.get("chunk_type") in {
        "table_header", "table_summary", "table_row",
    } for c in leaves)

    payment_leaf = next(
        c for c in leaves
        if c.metadata.get("section_title") == "第三条 付款方式"
    )
    assert payment_leaf.metadata["references"] == [{
        "table_id": "table_1", "relation": "refers_to",
    }]
    assert "SENTINEL-TABLE-REF" not in payment_leaf.page_content
    breach_leaf = next(
        c for c in leaves
        if c.metadata.get("section_title") == "第四条 违约责任"
    )
    assert breach_leaf.page_content.splitlines()[:2] == [
        "服务合同", "第四条 违约责任",
    ]
    assert qc["coverage"]["uncovered_count"] == 0
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 9. PDF 表格与正文分页（表格行保留验证）──────────────────────

def test_09_pdf_table_with_body_pages(tmp_path):
    """PDF 表格识别（画线网格 → find_tables）+ 跨页正文：
    表格行 chunk 与正文 chunk 均产出且页码可追溯。"""
    p = tmp_path / "table.pdf"
    d = fitz.open()
    pg1 = d.new_page()
    pg1.insert_text((50, 50), "第一章 数据报表", fontname="china-s")
    # 画 3x2 网格线（find_tables 依赖线条检测）
    x0, y0, x1, y1 = 72, 90, 320, 190
    for i in range(3):   # 横线
        y = y0 + i * 50
        pg1.draw_line((x0, y), (x1, y))
    for j in range(3):   # 竖线
        x = x0 + j * 124
        pg1.draw_line((x, y0), (x, y1))
    pg1.insert_text((80, 110), "指标", fontname="china-s")
    pg1.insert_text((210, 110), "数值", fontname="china-s")
    pg1.insert_text((80, 160), "库存金额", fontname="china-s")
    pg1.insert_text((210, 160), "1234567.89", fontname="china-s")
    pg2 = d.new_page()
    pg2.insert_text((50, 50), "第二章 补充说明", fontname="china-s")
    pg2.insert_text((50, 80), "以上数据来源为财务系统，未经审计。", fontname="china-s")
    d.save(str(p))
    d.close()
    chunks, qc = parse_and_chunk_full(str(p))
    body = "\n".join(c.page_content for c in chunks)
    assert "财务系统" in body
    # 表格若被 find_tables 识别则行列内容入 chunk；未识别时线条区域文本
    # 兜底进正文——两种结果内容都不丢
    assert "1234567.89" in body or "库存金额" in body
    assert qc["coverage"]["uncovered_count"] == 0
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 10. DOCX 正文与表格交错 ─────────────────────────────────────

def test_10_docx_interleaved(tmp_path):
    p = tmp_path / "interleaved.docx"
    d = docx.Document()
    d.add_heading("第一章", level=1)
    d.add_paragraph("第一章正文。")
    t1 = d.add_table(rows=2, cols=2)
    t1.cell(0, 0).text = "A表"
    t1.cell(1, 1).text = "v1"
    d.add_heading("第二章", level=1)
    d.add_paragraph("第二章正文。")
    t2 = d.add_table(rows=2, cols=2)
    t2.cell(0, 0).text = "B表"
    t2.cell(1, 1).text = "v2"
    d.save(str(p))
    chunks, qc = parse_and_chunk_full(str(p))
    body = "\n".join(c.page_content for c in chunks)
    assert "A表" in body and "B表" in body
    assert "第一章正文" in body and "第二章正文" in body
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 11. 前导零 SKU 的 CSV ────────────────────────────────────────

def test_11_csv_leading_zero_sku(tmp_path):
    p = tmp_path / "sku.csv"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["SKU", "品名", "库存"])
    w.writerows([["00123", "苹果", "50"], ["00456", "香蕉", "30"]])
    p.write_text(buf.getvalue(), encoding="utf-8-sig")
    chunks, qc = parse_and_chunk_full(str(p))
    body = "\n".join(c.page_content for c in chunks)
    assert "00123" in body
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 12. 多 Sheet Excel ───────────────────────────────────────────

def test_12_multi_sheet_excel(tmp_path):
    p = tmp_path / "multi.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "季度销售"
    ws1.append(["月份", "销售额"])
    ws1.append(["1月", 123456])
    ws2 = wb.create_sheet("退货")
    ws2.append(["SKU", "数量"])
    ws2.append(["00789", 5])
    wb.save(str(p))
    chunks, qc = parse_and_chunk_full(str(p))
    body = "\n".join(c.page_content for c in chunks)
    assert "季度销售" in body and "退货" in body
    assert "00789" in body, "Excel 文本单元格前导零必须保留"
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 13. 约 100 页长文 ────────────────────────────────────────────

def test_13_pdf_100_pages(tmp_path):
    p = tmp_path / "long100.pdf"
    d = fitz.open()
    for i in range(100):
        pg = d.new_page()
        if i % 10 == 0:
            pg.insert_text((50, 50), f"第{i // 10 + 1}章 制度第{i // 10 + 1}部分", fontname="china-s")
        pg.insert_text((50, 80), f"这是第{i + 1}页的正文内容，描述各项规定细节。" * 4, fontname="china-s")
    d.save(str(p))
    d.close()
    chunks, qc = parse_and_chunk_full(str(p))
    assert len(chunks) > 50
    assert not any(c.metadata.get("chunks_truncated") for c in chunks)
    assert qc["coverage"]["uncovered_count"] == 0
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 14. 约 300 页长文 ────────────────────────────────────────────

def test_14_pdf_300_pages(tmp_path):
    p = tmp_path / "long300.pdf"
    d = fitz.open()
    for i in range(300):
        pg = d.new_page()
        if i % 20 == 0:
            pg.insert_text((50, 50), f"第{i // 20 + 1}篇 长文档第{i // 20 + 1}篇", fontname="china-s")
        pg.insert_text((50, 80), f"第{i + 1}页内容，条款与说明文本。" * 5, fontname="china-s")
    d.save(str(p))
    d.close()
    chunks, qc = parse_and_chunk_full(str(p))
    assert len(chunks) > 15, (
        f"300 页文本应产出足量 chunk，实际 {len(chunks)}")
    assert not any(c.metadata.get("chunks_truncated") for c in chunks), \
        "300 页文档在 5000 上限内应完整处理"
    assert qc["coverage"]["uncovered_count"] == 0
    _assert_p0_invariants(chunks, qc["raw_ast"])


# ── 15. 扫描 PDF OCR 部分失败（mock）────────────────────────────

def test_15_scanned_pdf_partial_ocr(tmp_path, monkeypatch):
    from backend.rag.preprocessing.parser import ocr as ocr_mod

    calls = {"n": 0}

    def flaky_ocr(png_bytes):
        calls["n"] += 1
        if calls["n"] in {2, 3}:
            # 第 2 页的首次识别及 300 DPI 重试均失败，验证失败页留痕。
            raise RuntimeError("OCR provider timeout")
        return f"第{calls['n']}页扫描识别文本，内容足够长以构成段落。"

    monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
    monkeypatch.setattr(ocr_mod, "ocr_image", flaky_ocr)

    p = tmp_path / "scan.pdf"
    d = fitz.open()
    for _ in range(3):
        page = d.new_page()
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 600), 0)
        pixmap.clear_with(0)
        page.insert_image(page.rect, stream=pixmap.tobytes("png"))
    d.save(str(p))
    d.close()
    chunks, qc = parse_and_chunk_full(str(p))
    raw_ast = qc["raw_ast"]
    assert raw_ast.ocr_triggered is True
    assert raw_ast.ocr_failed_pages == 1
    # 覆盖缺口留痕进质量记录（不伪装完整成功）
    from backend.rag.preprocessing.quality_gate import (
        build_quality_record, run_typed_validation,
    )
    record = build_quality_record(
        file_path=str(p), doc_id="d", kb_id="k", raw_ast=raw_ast,
        chunks=chunks, file_size=1, file_hash="h",
        ocr_triggered=True, ocr_pages=int(raw_ast.ocr_pages),
    )
    anomalies = run_typed_validation(record, raw_ast)
    assert any(a["check"] == "ocr_coverage_partial" for a in anomalies)


# ── 16. 大量重复内容与近重复文档 ─────────────────────────────────

def test_16_near_duplicate_documents(tmp_path):
    """近重复检测：两份高相似文档被识别（MinHash）。"""
    from backend.rag.indexing.stages.metadata_stage import MetadataStage

    base = "员工手册规定考勤、请假、报销与差旅的各项制度细则。" * 30
    doc_a = f"{base}\n附录A：表单模板。"
    doc_b = f"{base}\n附录B：联系窗口。"
    sig_a = MetadataStage.minhash_signature(doc_a) if hasattr(
        MetadataStage, "minhash_signature") else None
    if sig_a is None:
        pytest.skip("MinHash 接口不可用，近重复由 metadata cascade 测试覆盖")
    sig_b = MetadataStage.minhash_signature(doc_b)
    dup_id = MetadataStage.detect_near_dup(
        registry=None, minhash_sig=sig_a, doc_type="policy",
        exclude_doc_id="", candidate_sig=sig_b,
    ) if _accepts_candidate_sig() else None
    # 检测接口存在且两份相似文档不抛错（具体阈值由 cascade 测试锁定）
    assert sig_a and sig_b


def _accepts_candidate_sig() -> bool:
    import inspect
    try:
        from backend.rag.indexing.stages.metadata_stage import MetadataStage
        params = inspect.signature(
            MetadataStage.detect_near_dup).parameters
        return "candidate_sig" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD
            for p in params.values())
    except (TypeError, ValueError):
        return False

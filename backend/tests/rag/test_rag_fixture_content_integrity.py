"""test_rag_fixture_content_integrity.py — RAG 内容完整性修复回归（2026-10-18）。

背景：CI `python -m backend.evaluation.import_fixture baseline` 在 4 份真实
baseline fixture 上覆盖失败（supplier 6/7、employee_handbook 4/10、
tech_manual 4/19、inventory 1/9）。证据链根因：

1. Cleaner 的「孤立图注」启发式被逐节点执行：结构化节点自身就是独立段落，
   启发式的「前后空行」条件恒成立 → 供应商考核指标、章节标题被清空；
2. Parent 与 Leaf 共用同一个 SimHash 已见集合：Parent 文本包含 Leaf 正文，
   导致正文高度相似的 Leaf 被判 duplicate；
3. 通用过滤器用固定长度/中文占比阈值误杀有效短段落、英文技术字段与配置行。

本文件的用例全部离线运行：真实 fixture 走 Parser → Cleaner → Chunker →
Filter → Coverage/QualityGate，不连付费 Provider、不写共享数据库、不调用
远程 Embedding。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.documents import Document

from backend.rag.indexing.indexer import _filter_index_chunks
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import coverage_audit, is_table_chunk
from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.rag.preprocessing.filter import ChunkFilter
from backend.rag.preprocessing.pipeline import parse_and_chunk_full
from backend.rag.preprocessing.quality_gate import (
    audit_chunk_integrity,
    build_quality_record,
    cleaning_coverage_audit,
    hard_anomalies,
    run_typed_validation,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "data" / "docs" / "rag_test_kb" / "general"
MANIFEST = (
    REPO_ROOT / "backend" / "evaluation" / "fixtures" / "rag_eval_kb" / "manifest.json"
)

FAILED_FIXTURES = {
    "baseline_employee_handbook": "hr_员工手册.docx",
    "baseline_inventory": "ops_库存管理制度.pdf",
    "baseline_supplier": "supplier_供应商管理规范.md",
    "baseline_tech_manual": "tech_技术手册.pdf",
}


@pytest.fixture(autouse=True)
def _ocr_budget_full(monkeypatch):
    """OCR 页预算给满硬上限 500——把验收口径显式化，不依赖本地/CI 缺省值。

    hard_anomalies 把「OCR 需求页有遗漏」计为硬异常（ocr_coverage_partial，
    fail-closed 不允许部分内容 active），OCR 全跑是本文件验收语义的一部分：
    预算裁剪/引擎缺失都会红。剩余文字层 baseline 文档的 OCR 需求页均远小于
    默认 100 页预算，但口径显式化后不随缺省值漂移。
    """
    from backend.config import rag as rag_cfg

    monkeypatch.setattr(rag_cfg, "RAG_OCR_MAX_PAGES", 500)



def _run_offline(fixture_name: str):
    """真实 fixture 的离线全链路：解析 → 清洗 → 切分 → 过滤 → 覆盖对账。"""
    path = FIXTURE_DIR / FAILED_FIXTURES[fixture_name]
    assert path.is_file(), f"缺少真实 baseline fixture: {path}"
    chunks, qc = parse_and_chunk_full(str(path))
    chunk_filter = ChunkFilter()
    kept, filtered = _filter_index_chunks(
        chunks, chunk_filter, str(path), str(qc.get("doc_type") or ""),
    )
    coverage = coverage_audit(
        qc["normalized_ast"], kept,
        normalize_pii=any(
            (chunk.metadata or {}).get("pii_masked")
            for chunk in kept
            if (chunk.metadata or {}).get("granularity") == "leaf"
        ),
    )
    return path, chunks, kept, filtered, coverage, qc


# ── 1. 四份真实失败 fixture 的内容恢复 ─────────────────────────────


def test_supplier_metrics_survive_cleaning_and_filtering():
    """供应商规范的 4 项考核指标不得被清洗/过滤删除（P0-A 验收条款）。"""
    _path, chunks, kept, filtered, coverage, qc = _run_offline("baseline_supplier")
    body = "\n".join(chunk.page_content for chunk in kept)

    for metric in (
        "质量合格率（目标 ≥ 98%）",
        "交期达成率（目标 ≥ 95%）",
        "售后退货率（目标 ≤ 3%）",
        "合规与配合度（资料及时性、整改响应）",
    ):
        assert metric in body, f"考核指标被删除: {metric}"

    # 清洗不得再清空节点：原始 12 个内容叶节点全部保留到审计分母
    assert coverage["leaf_total"] == 12
    assert coverage["uncovered_count"] == 0
    assert qc["cleaning_coverage"]["uncovered_count"] == 0
    assert filtered == []
    assert not any("empty" in str(d["reason"]) for d in filtered)


def test_employee_handbook_missing_leaves_restored():
    """员工手册 4 个被 Parent 去重挤掉的叶节点必须回到最终 Leaf。"""
    _path, _chunks, kept, filtered, coverage, qc = _run_offline(
        "baseline_employee_handbook"
    )
    body = "\n".join(chunk.page_content for chunk in kept)

    for restored in (
        "试用期 3 个月",
        "标准工时 9:30",
        "法定节假日按国家规定执行",
        "每月 10 日发薪",
    ):
        assert restored in body, f"叶节点内容缺失: {restored}"

    assert filtered == []
    assert coverage["leaf_total"] == 12
    assert coverage["uncovered_count"] == 0
    assert qc["cleaning_coverage"]["uncovered_count"] == 0


def test_inventory_short_appendix_content_kept():
    """库存制度里的短段落「附录 A：常用单据」是有效来源内容，不得 too_short。"""
    _path, _chunks, kept, filtered, coverage, _qc = _run_offline("baseline_inventory")
    body = "\n".join(chunk.page_content for chunk in kept)

    assert "附录 A" in body
    assert filtered == []
    assert coverage["uncovered_count"] == 0


def test_tech_manual_english_config_content_kept():
    """技术手册的英文技术栈/Git 规范/日志字段不得被中文占比规则误杀。"""
    _path, _chunks, kept, filtered, coverage, _qc = _run_offline("baseline_tech_manual")
    body = "\n".join(chunk.page_content for chunk in kept)

    for fragment in ("TypeScript", "Git Flow", "trace_id", "Conventional"):
        assert fragment in body, f"英文/配置内容缺失: {fragment}"

    assert filtered == []
    assert coverage["uncovered_count"] == 0


# 300-500p 长书整体移出离线完整性验收（与 UF-15-01 扫描件同款「显式排除 +
# 归属注释」先例，2026-11-01 实测定性）：
# - 全链路（find_tables + coverage_audit + 全预算 OCR ~157 需求页）单进程
#   实测 30 分钟量级，曾把 xdist worker 拖过 pytest-timeout 全局预算引发
#   node down 连环（rag 全量 96% 卡死根因，诊断栈留档）；
# - progit_en_501p 的「source_text 审计缺口」已修（2026-11-22：嵌入字体缺
#   advance width 导致结构化提取丢零宽字符，解析器现按字符级坐标恢复，
#   见 test_zero_width_font_pdf_content_recovered）；保留排除仅剩
#   「纯图页依赖 OCR 供应商」与耗时原因，OCR 链路由 OCR 集成测试覆盖；
# - 长文档行为已有专门测试覆盖（test_chunk_pages_locality /
#   test_cleaner_template_lines 等，基于 progit 实测切片）。
LONG_BOOK_FIXTURE_IDS = frozenset({"UF-14-01", "UF-14-02"})


FINANCIAL_TABLE_FIXTURE = (
    REPO_ROOT / "backend" / "evaluation" / "fixtures" / "rag_eval_kb" / "files"
    / "09_pdf_cross_page_table" / "report.pdf"
)


@pytest.mark.timeout(600)
def test_financial_table_short_cell_rows_survive_all_symbols_filter():
    """UF-09-01 回归：财务表格短 cell 行组不得被 all_symbols 规则误杀。

    根因（2026-11-22 生产实测）：表格行组把空 cell 序列化成分隔逗号，
    "核销/转回/动" 这类 1-2 字短 cell 行符号占比天然超阈值，被 all_symbols
    过滤且表格不豁免 → coverage_audit 记真缺口 → 付费前 fail-closed 拦截。
    all_symbols 现与 too_short/low_chinese_ratio/duplicate 同款表格豁免。
    """
    assert FINANCIAL_TABLE_FIXTURE.is_file(), "缺少真实 fixture: report.pdf"
    chunks, qc = parse_and_chunk_full(str(FINANCIAL_TABLE_FIXTURE))
    chunk_filter = ChunkFilter()
    kept, filtered = _filter_index_chunks(
        chunks, chunk_filter, str(FINANCIAL_TABLE_FIXTURE),
        str(qc.get("doc_type") or ""),
    )
    table_filtered = [
        d for d in filtered
        if isinstance(d.get("chunk_type"), str) and is_table_chunk(d["chunk_type"])
    ]
    assert table_filtered == [], (
        f"表格 chunk 被过滤（数据未入库）: "
        f"{[(d['reason'], d['preview'][:40]) for d in table_filtered]}"
    )
    coverage = coverage_audit(
        qc["normalized_ast"], kept,
        normalize_pii=any(
            (chunk.metadata or {}).get("pii_masked")
            for chunk in kept
            if (chunk.metadata or {}).get("granularity") == "leaf"
        ),
        excluded_texts=[
            d.get("content", "") for d in filtered
            if not (isinstance(d.get("chunk_type"), str)
                    and is_table_chunk(d["chunk_type"]))
        ],
    )
    assert coverage["uncovered_count"] == 0, (
        f"表格行覆盖缺口: {coverage['uncovered_samples'][:3]}"
    )


@pytest.mark.timeout(600)
def test_zero_width_font_pdf_content_recovered(tmp_path):
    """UF-14-01 回归：零宽字体 PDF 的结构化提取必须有字符级兜底。

    根因（2026-11-22 生产实测）：progit_en 嵌入字体缺 advance width，
    dict/rawdict/words 把零宽 span 全部丢弃（多数正文页 dict 提取 0 字符），
    只有 get_text("text") 保留；页级源文本审计因此记大量缺口，
    coverage_rate 0.6987 < 0.95 → error 硬拦。解析器现从 texttrace 字符级
    坐标恢复零宽内容，恢复页在审计端按未验证处理（与 OCR 页同语义）。
    用 501p 原书裁剪出的无图页子集做快速回归（子集内页码已重排）。
    """
    import fitz

    source = (
        REPO_ROOT / "backend" / "evaluation" / "fixtures" / "rag_eval_kb"
        / "files" / "14_long_300_500p" / "progit_en_501p.pdf"
    )
    assert source.is_file(), "缺少真实 fixture: progit_en_501p.pdf"
    subset_path = tmp_path / "progit_subset.pdf"
    doc = fitz.open(source)
    try:
        # 页 12-16 与 26-31（原书 1-based）：零宽正文页、无图片块、无 OCR 需求；
        # 源 PDF 含损坏对象（insert_pdf 会挂），select 就地裁剪。不能用
        # garbage 回收保存——MuPDF 会重写字体对象补齐 advance width，把
        # 零宽病灶修掉，回归就测不到解析器兜底了。
        doc.select([11, 12, 13, 14, 15, 25, 26, 27, 28, 29, 30])
        doc.save(str(subset_path))
    finally:
        doc.close()

    chunks, qc = parse_and_chunk_full(str(subset_path))
    raw_ast = qc["raw_ast"]
    recovered = set(raw_ast.pdf_zero_width_recovered_pages or [])
    assert recovered, "零宽字体 PDF 未触发字符级恢复"
    # 子集 11 页全部是零宽恢复页：恢复内容必须回到可检索文本。
    # 不能逐行精确断言——text 模式对该字体做系统性双写折叠
    # （Getting→Geting、Masood→Masod），与 texttrace 恢复路径的解码不同，
    # 只能量级核对（恢复文本须接近 native 文字层的字符总量）。
    import re

    def _norm(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).casefold()

    body = _norm("\n".join(
        chunk.page_content for chunk in chunks
        if (chunk.metadata or {}).get("granularity") == "leaf"
    ))
    doc = fitz.open(str(subset_path))
    try:
        native_total = _norm("".join(
            page.get_text("text") for page in doc
        ))
    finally:
        doc.close()
    assert len(body) >= len(native_total) * 0.8, (
        f"零宽恢复内容量级不足: recovered={len(body)} native={len(native_total)}"
    )
    # 恢复页在审计端按未验证处理：不得再产出 error 级源文本审计缺口
    codes = {
        str(issue.get("code")): str(issue.get("severity") or "")
        for issue in (raw_ast.parse_issues or [])
    }
    gap_severity = codes.get("pdf_source_text_audit_gap")
    assert gap_severity != "error", (
        f"零宽恢复页仍被审计缺口硬拦: {codes}"
    )
    assert "pdf_zero_width_text_recovered" in codes, (
        f"恢复页未留痕 parse_issue: {codes}"
    )


@pytest.mark.timeout(900)
def test_all_baseline_fixtures_complete_offline():
    """28 份 baseline 中文字层文档的离线完整性验收（免费，无任何外部调用）。

    排除口径：UF-15-01 扫描件（OCR 供应商链路，见循环内注释）与
    LONG_BOOK_FIXTURE_IDS 两本 300-500p 长书（成本与产品缺口见上方注释，
    长文档行为由专门测试覆盖）。排除是显式 assert 的——manifest 漂移导致
    悄悄漏测会在此暴露。
    """
    documents = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
    baseline = [d for d in documents if d["fixture_set"] == "baseline"]
    assert len(baseline) == 28
    # 排除名单必须真实存在于 manifest（防漂移）
    baseline_ids = {d["fixture_doc_id"] for d in baseline}
    assert LONG_BOOK_FIXTURE_IDS <= baseline_ids, (
        f"长书排除名单与 manifest 漂移: {LONG_BOOK_FIXTURE_IDS - baseline_ids}")

    failures: list[str] = []
    for document in baseline:
        fixture_id = document["fixture_doc_id"]
        path = REPO_ROOT / document["source_file"]
        assert path.is_file(), f"缺少真实 fixture: {path}"

        # 扫描件场景（如 UF-15-01，manifest is_scanned=true / profile=ocr）
        # 依赖 OCR 供应商，不在"离线零外部调用"承诺范围内；其链路由
        # OCR 集成测试与付费评测覆盖。
        if document.get("legacy_metadata", {}).get("is_scanned") \
                or document.get("profile") == "ocr":
            continue
        if fixture_id in LONG_BOOK_FIXTURE_IDS:
            continue

        chunks, qc = parse_and_chunk_full(str(path))
        chunk_filter = ChunkFilter()
        kept, filtered = _filter_index_chunks(
            chunks, chunk_filter, str(path), str(qc.get("doc_type") or ""),
        )
        coverage = coverage_audit(
            qc["normalized_ast"], kept,
            normalize_pii=any(
                (chunk.metadata or {}).get("pii_masked")
                for chunk in kept
                if (chunk.metadata or {}).get("granularity") == "leaf"
            ),
            # P2 收口（2026-10-19）：被显式规则过滤的 chunk 是合法排除，
            # 以原文纳入覆盖对照豁免——但表格 chunk 不豁免（与 indexer
            # 付费前预检同口径：表格被过滤意味着数据未入库，必须计入
            # 缺口）。此前这里把表格过滤内容也豁免了，口径比预检宽，
            # UF-09-01/13-01 的表格短 cell 被 all_symbols 误杀在 CI 漏检。
            excluded_texts=[
                d.get("content", "") for d in filtered
                if not (isinstance(d.get("chunk_type"), str)
                        and is_table_chunk(d["chunk_type"]))
            ],
        )
        integrity = audit_chunk_integrity(kept)
        record = build_quality_record(
            file_path=str(path), doc_id=fixture_id, kb_id="rag_eval_kb",
            raw_ast=qc["raw_ast"], chunks=kept,
            file_size=path.stat().st_size, file_hash="sha256",
            doc_type=qc.get("doc_type", ""),
            strategy_name=qc.get("strategy_name", ""),
            completeness=qc.get("completeness"),
            filtered_details=filtered,
            route_decision=qc.get("route_decision"),
            coverage=coverage,
            cleaning_coverage=qc.get("cleaning_coverage"),
        )
        hard = hard_anomalies(run_typed_validation(record, qc["raw_ast"]))
        cleaning = qc.get("cleaning_coverage") or {}

        problems = []
        if coverage.get("uncovered_count"):
            problems.append(f"coverage_uncovered={coverage['uncovered_count']}")
        if coverage.get("table_rows_uncovered"):
            problems.append(f"rows_uncovered={coverage['table_rows_uncovered']}")
        if coverage.get("table_cells_uncovered"):
            problems.append(f"cells_uncovered={coverage['table_cells_uncovered']}")
        if cleaning.get("uncovered_count"):
            problems.append(f"cleaning_uncovered={cleaning['uncovered_count']}")
        if cleaning.get("error"):
            problems.append("cleaning_audit_error")
        if integrity["empty_chunk_count"]:
            problems.append(f"empty_chunks={integrity['empty_chunk_count']}")
        if integrity["duplicate_chunk_id_count"]:
            problems.append("duplicate_chunk_id")
        if integrity["invalid_parent_reference_count"]:
            problems.append("invalid_parent_reference")
        if hard:
            problems.append(f"hard_anomalies={[a['check'] for a in hard]}")
        if problems:
            failures.append(f"{fixture_id}: {', '.join(problems)}")

    assert not failures, "baseline 文字层文档离线完整性失败:\n" + "\n".join(failures)


# ── 2. Parent / Leaf 去重隔离（P0-B）───────────────────────────────


def _leaf(text: str, chunk_id: str, parent_id: str = "") -> Document:
    return Document(
        page_content=text,
        metadata={
            "granularity": "leaf",
            "chunk_id": chunk_id,
            "parent_chunk_id": parent_id,
        },
    )


def test_parent_simhash_does_not_duplicate_leaf():
    """Parent 文本包含 Leaf 正文：不得把可检索 Leaf 判成 duplicate。"""
    leaf_text = "供应商考核指标必须保留在可检索 Leaf 中，不能被 Parent 判重删除。" * 2
    parent_text = "五、考核指标\n" + leaf_text
    chunks = [
        Document(
            page_content=parent_text,
            metadata={"granularity": "parent", "chunk_id": "parent-0"},
        ),
        _leaf(leaf_text, "leaf-0", parent_id="parent-0"),
    ]

    kept, filtered = _filter_index_chunks(
        chunks, ChunkFilter(), "supplier.md", "policy",
    )

    assert filtered == []
    kept_ids = {chunk.metadata["chunk_id"] for chunk in kept}
    assert {"parent-0", "leaf-0"} <= kept_ids


def test_duplicate_leaf_still_filtered():
    """去重能力不能被整体关闭：真正的重复 Leaf 仍应判 duplicate。"""
    text = "重复出现的公司差旅住宿标准为每晚上限四百元，超出部分由个人承担。" * 2
    chunks = [_leaf(text, "leaf-0"), _leaf(text, "leaf-1")]

    kept, filtered = _filter_index_chunks(
        chunks, ChunkFilter(), "travel.md", "policy",
    )

    assert [chunk.metadata["chunk_id"] for chunk in kept] == ["leaf-0"]
    assert [item["reason"] for item in filtered] == ["duplicate"]


# ── 3. 通用过滤器（P0-C）──────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "附录 A：常用单据",
        "已发货",
        "SO-2026-0001",
        "TLS 1.3 / mTLS / OAuth2",
    ],
)
def test_valid_short_content_is_kept(text):
    """有效短内容（短段落/状态值/订单号/技术标识）不得被 too_short 删除。"""
    keep, reason = ChunkFilter().should_keep(
        text, {"granularity": "leaf", "chunk_id": "c0"},
    )
    assert keep, reason


@pytest.mark.parametrize("text", ["3", "----", "•"])
def test_degenerate_short_text_still_filtered(text):
    """长度阈值仍需拦住无内容碎片（裸页码/纯符号）。"""
    keep, reason = ChunkFilter().should_keep(
        text, {"granularity": "leaf", "chunk_id": "c0"},
    )
    assert not keep
    assert reason == "too_short"


def test_english_technical_line_not_low_chinese_ratio():
    text = "GET /v1/orders returns 200 with pagination metadata in JSON body."
    keep, reason = ChunkFilter().should_keep(
        text, {"granularity": "leaf", "chunk_id": "c0"},
    )
    assert keep, reason


# ── 4. 清洗范围与证据（P0-A.4）────────────────────────────────────


def test_structured_node_scope_keeps_short_metric_line():
    """同一短指标行：全篇文本模式（旧行为）会误删，结构化节点模式必须保留。"""
    metric = "质量合格率（目标 ≥ 98%）"
    block = "第一部分正文内容。\n\n" + metric + "\n\n第二部分正文内容。"
    cleaner = DocumentCleaner()

    legacy = cleaner.clean(block, source_type="text")
    assert metric not in legacy.text  # 旧启发式的失败路径（回归基线）

    structured = cleaner.clean(block, source_type="text", structured=True)
    assert metric in structured.text
    assert structured.dropped == []
    assert legacy.dropped and legacy.dropped[0]["operation"] == "isolated_caption"


def test_cleaner_records_drop_evidence_for_deterministic_noise():
    """合法噪音删除必须留下操作名/分类/原行证据，供审计区分正文丢失。"""
    template = "本页有Unihan新版汉字：“𥻵”，这些字符可能会错误显示。"
    text = f"第一段正文内容保留。\n\n{template}\n\n第二段正文内容保留。"
    result = DocumentCleaner().clean(text, source_type="pdf", structured=True)

    assert "第一段正文内容保留" in result.text
    assert "第二段正文内容保留" in result.text
    assert any(
        item["operation"] == "template_line"
        and item["category"] == "deterministic_noise"
        for item in result.dropped
    )
    assert any(change.startswith("dropped_template_lines") for change in result.changes)


def test_bare_numeric_line_is_preserved_but_audit_classifies_it():
    """合并 #66 后不再猜测裸数字行是页码：内容保留，审计仍可判为噪音形态。

    金额/比率/编号与页码在单行文本上不可区分，删除不可逆；因此清洗不再
    删除裸数字行，清洗审计只在“该行确实消失”时才把它归为合法噪音。
    """
    result = DocumentCleaner().clean(
        "第一段正文内容保留。\n\n3\n\n第二段正文内容保留。",
        source_type="pdf", structured=True,
    )

    assert "\n3\n" in result.text
    assert not any(
        item["operation"] == "page_number_line" for item in result.dropped
    )
    assert DocumentCleaner.classify_noise_line("3") == "page_number"


def test_caption_heuristic_never_deletes_the_only_line_of_a_block():
    """单行块没有「前后空行」证据，不得整块删除（版面启发式的最小保护）。"""
    result = DocumentCleaner().clean("临时通知栏", source_type="text")
    assert "临时通知栏" in result.text


# ── 5. 清洗对账审计（P0-D 边界一）──────────────────────────────────


def _ast_with_clean_pair(raw: str, cleaned: str) -> DocumentAST:
    return DocumentAST(
        root=DocumentNode(
            type="section", text="", level=0,
            children=[DocumentNode(type="paragraph", text=cleaned, raw_text=raw)],
        ),
        raw_text=raw,
    )


def test_cleaning_audit_detects_body_loss():
    """清洗后正文消失 → 覆盖审计之外仍必须被发现并 fail-closed。"""
    raw = "质量合格率（目标 ≥ 98%）"
    ast = _ast_with_clean_pair(raw, "")

    report = cleaning_coverage_audit(ast)
    assert report["uncovered_count"] == 1
    assert report["lost_line_count"] == 1

    record = build_quality_record(
        file_path="supplier.md", doc_id="d1", kb_id="kb-test", raw_ast=ast,
        chunks=[_leaf("占位 Leaf 内容用于通过 chunk 存在性检查。", "leaf-0")],
        file_size=1, file_hash="sha256", coverage=None,
        cleaning_coverage=report,
    )
    hard = [a["check"] for a in hard_anomalies(run_typed_validation(record, ast))]
    assert "cleaner_content_loss" in hard


def test_cleaning_audit_allows_deterministic_noise_only():
    """页码/页脚等确定性噪音的删除属于合法清洗，不计内容丢失。"""
    ast = _ast_with_clean_pair("第 3 页 共 10 页", "")

    report = cleaning_coverage_audit(ast)
    assert report["uncovered_count"] == 0
    assert report["legal_noise_line_count"] == 1


def test_cleaning_audit_error_is_hard_failure(monkeypatch):
    """审计自身异常不得当作通过（fail-closed）。"""
    ast = _ast_with_clean_pair("正文内容。", "正文内容。")
    monkeypatch.setattr(
        DocumentCleaner, "normalize_for_compare",
        lambda self, _text: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    report = cleaning_coverage_audit(ast)
    assert report["error"]

    record = build_quality_record(
        file_path="x.md", doc_id="d1", kb_id="kb-test", raw_ast=ast,
        chunks=[_leaf("占位 Leaf 内容用于通过 chunk 存在性检查。", "leaf-0")],
        file_size=1, file_hash="sha256", coverage=None,
        cleaning_coverage=report,
    )
    hard = [a["check"] for a in hard_anomalies(run_typed_validation(record, ast))]
    assert "cleaning_audit_failed" in hard


# ── 6. 付费前免费预检（P0-E）────────────────────────────────────


def test_prepaid_integrity_fails_before_metadata_llm(tmp_path, monkeypatch):
    """内容已确定缺失时必须在元数据 LLM / embedding 之前失败。"""
    from types import SimpleNamespace

    import backend.rag.indexing.indexer as indexer_module
    import backend.rag.preprocessing.pipeline as pipeline_module
    import backend.rag.preprocessing.quality_gate as quality_gate
    from backend.rag.indexing.indexer import IncrementalIndexer

    covered = "已覆盖的正文内容。" * 8
    missing = "质量门禁必须发现未覆盖的正文内容。" * 8
    source = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text=covered),
            DocumentNode(type="paragraph", text=missing),
        ]),
        raw_text=covered + missing,
    )
    chunks = [_leaf(covered, "leaf-0")]
    coverage = coverage_audit(source, chunks)

    monkeypatch.setattr(
        pipeline_module, "parse_and_chunk_full",
        # indexer 现以 tenant_id/kb_id 等关键字调用（§4 权限范围），
        # patch 签名必须吞掉全部关键字参数，否则测试自身 TypeError。
        lambda _path, **_kwargs: (chunks, {"raw_ast": source, "coverage": coverage}),
    )
    persisted: list[dict] = []
    monkeypatch.setattr(
        quality_gate, "persist_quality_record",
        lambda record: persisted.append(record) or "prepaid-record.json",
    )

    class Registry:
        def __init__(self):
            self.status = "parsing"
            self.updates = []

        def get_by_path(self, _path):
            return {"status": self.status}

        def register_in_progress(self, *_args, **_kwargs):
            self.status = "parsing"

        def update_fields(self, _path, fields):
            self.updates.append(fields)

    class Collector:
        def __init__(self):
            self.spans = {}

        def start(self, **_kwargs):
            return SimpleNamespace(id="trace-prepaid", tags={})

        def start_span(self, span_id, **kwargs):
            span = SimpleNamespace(
                span_id=span_id, input=kwargs.get("input"), metrics={},
                status="running", duration_ms=0,
            )
            self.spans[span_id] = span
            return span

        def end_span(self, span, status="success", metrics=None, **_kwargs):
            span.status = status
            span.metrics.update(metrics or {})

        def finish(self, *_args, **_kwargs):
            return None

    paid_calls: list[str] = []

    async def metadata_stub(self, _full_text, base_meta, **_kwargs):
        paid_calls.append("metadata_llm")
        return dict(base_meta)

    registry = Registry()
    collector = Collector()
    vector_writes: list = []
    monkeypatch.setattr(indexer_module, "trace_collector", collector)
    monkeypatch.setattr(IncrementalIndexer, "_build_doc_metadata", metadata_stub)
    indexer = IncrementalIndexer(
        docs_dir=str(tmp_path),
        vectordb=SimpleNamespace(
            add_documents=lambda docs, **_kwargs: vector_writes.append(docs) or [],
        ),
        doc_db=SimpleNamespace(), embedding=SimpleNamespace(),
        registry=registry, candidate_mode=False,
    )
    document = tmp_path / "gap.txt"
    document.write_text("测试覆盖缺口的索引内容。" * 5, encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        indexer._index_file(str(document))

    assert "coverage_gap" in str(excinfo.value)
    assert "付费前预检" in str(excinfo.value)
    assert paid_calls == [], "付费调用不得发生在内容完整性失败之前"
    assert vector_writes == []
    # 失败也要留证：质量记录已落盘且 registry 落 failed
    assert persisted and persisted[0]["coverage"] == coverage
    assert "coverage_gap" in persisted[0]["quality_issues"]
    assert registry.updates[-1]["quality_status"] == "failed"
    assert collector.spans["index_quality"].status == "error"

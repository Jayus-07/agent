"""
chunking.py 测试 — Document Type Aware Chunking Strategy

覆盖: 4 种策略 + Router + 向后兼容
"""
import pytest

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.documents import Document

from preprocessing.chunking import (
    GeneralChunkStrategy,
    ManualPolicyChunkStrategy,
    ProjectReportChunkStrategy,
    ChunkStrategyRouter,
    _find_sections,
)


# ============================================================
# Test fixtures
# ============================================================

def _make_doc(text: str, source: str = "test.txt") -> list:
    return [Document(page_content=text, metadata={"source": source})]


# ============================================================
# _find_sections
# ============================================================

class TestFindSections:
    """章节头检测辅助函数"""

    def test_numbered_sections(self):
        text = "4.2.1 冷藏肉类\n内容A\n4.2.2 蔬菜叶菜类\n内容B"
        sections = _find_sections(text)
        assert len(sections) == 2
        assert sections[0]["id"] == "4.2.1"
        assert sections[0]["title"] == "冷藏肉类"
        assert sections[1]["id"] == "4.2.2"
        assert sections[1]["title"] == "蔬菜叶菜类"

    def test_chinese_numbering(self):
        text = "一、概述\n内容\n二、细则\n内容"
        sections = _find_sections(text)
        assert len(sections) == 2
        assert "一" in sections[0]["id"]
        assert sections[0]["title"] == "概述"
        assert sections[1]["title"] == "细则"

    def test_chapter_heading(self):
        text = "第一章 总则\n内容\n第二章 操作规范\n内容"
        sections = _find_sections(text)
        assert len(sections) == 2
        assert sections[1]["title"] == "操作规范"

    def test_mixed_patterns(self):
        text = "4.2.1 冷藏\n内容\n一、概述\n内容\n第3条 规定\n内容"
        sections = _find_sections(text)
        assert len(sections) == 3

    def test_no_sections(self):
        text = "这是一段没有章节编号的普通文本。"
        sections = _find_sections(text)
        assert sections == []

    def test_long_line_not_header(self):
        """超过 80 字符的行不算标题"""
        text = "4.2.1 " + "X" * 80 + "\n内容"
        sections = _find_sections(text)
        assert sections == []  # too long


# ============================================================
# GeneralChunkStrategy
# ============================================================

class TestGeneralChunkStrategy:
    """通用兜底策略"""

    def test_splits_text(self):
        strategy = GeneralChunkStrategy(chunk_size=500, chunk_overlap=50)
        text = "这是第一段。" * 100
        chunks = strategy.split(_make_doc(text), "/tmp/test.txt")
        assert len(chunks) > 1

    def test_small_text_stays_one_chunk(self):
        strategy = GeneralChunkStrategy()
        text = "很短的内容"
        chunks = strategy.split(_make_doc(text), "/tmp/test.txt")
        assert len(chunks) == 1

    def test_chunk_metadata_present(self):
        strategy = GeneralChunkStrategy()
        chunks = strategy.split(_make_doc("A" * 2000), "/tmp/test.txt")
        for c in chunks:
            assert "parent_doc_id" in c.metadata
            assert "chunk_index" in c.metadata
            assert "total_chunks" in c.metadata
            assert "source_file" in c.metadata
            assert c.metadata["source_file"] == "test.txt"

    def test_respects_chunk_size(self):
        strategy = GeneralChunkStrategy(chunk_size=200, chunk_overlap=0)
        chunks = strategy.split(_make_doc("X" * 800), "/tmp/test.txt")
        for c in chunks:
            assert len(c.page_content) <= 250  # 允许轻微溢出

    def test_empty_docs(self):
        strategy = GeneralChunkStrategy()
        chunks = strategy.split([], "/tmp/test.txt")
        assert chunks == []


# ============================================================
# ManualPolicyChunkStrategy
# ============================================================

POLICY_TEXT = """4.2.1 冷藏肉类（猪、牛、羊）
到店时间：每日 06:00 前送达。
保质期：从屠宰日起不超过 72 小时。

4.2.2 蔬菜叶菜类（生菜、菠菜、油麦菜等）
到店当天必须上架。当日 22:00 未售完的叶菜，一律报损处理。
报损时需由当班组长扫描条形码，系统记录损耗量。
不允许隔夜翻包重新上架。

4.2.3 海鲜活鲜
暂养池溶氧量需保持 ≥6 mg/L，每 2 小时检查一次并填写记录表。
活鲜死亡超过 20 分钟应立即捞出，作为冻品以 5 折价格销售。

4.2.4 熟食（自制）
制作完成后，在加热柜中保温（≥65℃）最长 4 小时。
"""


class TestManualPolicyChunkStrategy:
    """制度/政策文档章节切分"""

    def setup_method(self):
        self.strategy = ManualPolicyChunkStrategy()

    def test_splits_by_numbered_sections(self):
        chunks = self.strategy.split(_make_doc(POLICY_TEXT), "/tmp/t.txt")
        assert len(chunks) >= 3  # at least 3 of the 4 sections

    def test_section_metadata_present(self):
        chunks = self.strategy.split(_make_doc(POLICY_TEXT), "/tmp/t.txt")
        sections_with_id = [c for c in chunks if c.metadata.get("section_id")]
        assert len(sections_with_id) >= 3
        ids = [c.metadata["section_id"] for c in sections_with_id]
        assert "4.2.1" in ids
        assert "4.2.2" in ids

    def test_section_integrity(self):
        """条款不被切碎：4.2.2 的完整内容在一个 chunk 中"""
        chunks = self.strategy.split(_make_doc(POLICY_TEXT), "/tmp/t.txt")
        veg_chunk = None
        for c in chunks:
            if c.metadata.get("section_title") == "蔬菜叶菜类（生菜、菠菜、油麦菜等）":
                veg_chunk = c
                break
        assert veg_chunk is not None
        # 关键内容必须在同一个 chunk
        assert "到店当天必须上架" in veg_chunk.page_content
        assert "一律报损处理" in veg_chunk.page_content
        assert "不允许隔夜翻包重新上架" in veg_chunk.page_content

    def test_chapter_heading_split(self):
        text = "第一章 总则\n第一条 目的\n本制度的目的是...\n\n第二章 适用范围\n本制度适用于..."
        chunks = self.strategy.split(_make_doc(text), "/tmp/t.txt")
        assert len(chunks) >= 2

    def test_no_sections_single_chunk(self):
        text = "这是一段没有任何章节编号的普通文本。没有结构，没有编号。"
        chunks = self.strategy.split(_make_doc(text), "/tmp/t.txt")
        assert len(chunks) == 1

    def test_section_title_metadata(self):
        chunks = self.strategy.split(_make_doc(POLICY_TEXT), "/tmp/t.txt")
        titles = [c.metadata.get("section_title") for c in chunks if c.metadata.get("section_title")]
        assert "冷藏肉类（猪、牛、羊）" in titles

    def test_chunk_metadata_structure(self):
        chunks = self.strategy.split(_make_doc(POLICY_TEXT), "/tmp/t.txt")
        for c in chunks:
            assert "parent_doc_id" in c.metadata
            assert "source_file" in c.metadata
            assert "chunk_index" in c.metadata
            assert "total_chunks" in c.metadata


# ============================================================
# ProjectReportChunkStrategy
# ============================================================

MD_PROJECT_TEXT = """# 项目概述
这是项目的简要介绍。

## 架构设计
系统采用微服务架构，包含以下模块：
- 用户服务
- 订单服务
- 支付服务
- 网关服务

### 详细模块说明
每个模块的具体职责如下：
""" + "详细说明。" * 200 + """

## 技术栈
- 后端：SpringBoot
- 数据库：MySQL、Redis
- 中间件：Kafka
"""


class TestProjectReportChunkStrategy:
    """项目/报告文档标题优先切分"""

    def setup_method(self):
        self.strategy = ProjectReportChunkStrategy(max_section_chars=800)

    def test_splits_by_markdown_headers(self):
        chunks = self.strategy.split(_make_doc(MD_PROJECT_TEXT), "/tmp/p.md")
        assert len(chunks) >= 3  # # 项目概述, ## 架构设计, ## 技术栈

    def test_header_metadata_preserved(self):
        chunks = self.strategy.split(_make_doc(MD_PROJECT_TEXT), "/tmp/p.md")
        # Verify at least some chunks have header metadata
        headers_with_h1 = [c for c in chunks if c.metadata.get("Header 1")]
        headers_with_h2 = [c for c in chunks if c.metadata.get("Header 2")]
        assert len(headers_with_h1) + len(headers_with_h2) >= 2

    def test_long_section_sub_chunked(self):
        """超过 PROJECT_CHUNK_SIZE 的 section 被递归子切"""
        # Use a simpler test: create text with one header + lots of content
        long_text = "## 长章节\n" + ("这是测试内容。" * 200)  # ~1200 chars
        strategy = ProjectReportChunkStrategy(max_section_chars=500)
        chunks = strategy.split(_make_doc(long_text), "/tmp/t.md")
        # With 500 char limit and 1200 char section, should produce >1 chunk
        assert len(chunks) > 1

    def test_short_section_intact(self):
        """短 section 完整保留"""
        text = "# 标题\n简短内容，不超过限制。\n\n## 第二节\n另一段短内容。"
        strategy = ProjectReportChunkStrategy(max_section_chars=5000)
        chunks = strategy.split(_make_doc(text), "/tmp/t.md")
        assert len(chunks) == 2  # two headers → two chunks

    def test_text_file_headers(self):
        """.txt 文件中的 # 标题也能识别"""
        text = "# 项目概述\n这是内容。\n\n## 架构\n更多内容。"
        strategy = ProjectReportChunkStrategy(max_section_chars=5000)
        chunks = strategy.split(_make_doc(text), "/tmp/t.txt")
        assert len(chunks) >= 2

    def test_no_headers_single_chunk(self):
        text = "没有标题的普通文本内容，没有 markdown 标记。"
        strategy = ProjectReportChunkStrategy()
        chunks = strategy.split(_make_doc(text), "/tmp/t.md")
        assert len(chunks) == 1


# ============================================================
# ChunkStrategyRouter
# ============================================================

class TestChunkStrategyRouter:
    """路由分发"""

    def setup_method(self):
        self.router = ChunkStrategyRouter()

    def test_routes_manual_text(self):
        """含"流程""操作步骤"关键词 → manual"""
        text = "操作步骤\n1. 开机\n2. 检查\n处理流程\n故障处理..."
        chunks = self.router.route(_make_doc(text), "/tmp/manual_doc.txt")
        assert len(chunks) >= 1

    def test_routes_policy_text(self):
        """含"制度""规定"关键词 → policy"""
        text = "4.2.1 冷藏肉类\n管理制度\n本规定适用于..."
        chunks = self.router.route(_make_doc(text), "/tmp/policy_doc.txt")
        assert len(chunks) >= 1
        # policy → ManualPolicyChunkStrategy, should find sections
        ids = [c.metadata.get("section_id") for c in chunks if c.metadata.get("section_id")]
        assert "4.2.1" in ids

    def test_routes_resume_text(self):
        """含"工作经历""教育背景"关键词 → general fallback（resume 策略已删除）"""
        text = "基本信息\n工作经历\n2020-2023 工程师\n教育背景\n大学本科"
        chunks = self.router.route(_make_doc(text), "/tmp/resume_doc.txt")
        # 走 GeneralChunkStrategy fallback，仍能正常分块
        assert len(chunks) >= 1
        for c in chunks:
            assert "parent_doc_id" in c.metadata
            assert "source_file" in c.metadata

    def test_routes_project_text(self):
        """含"项目""架构设计"关键词 → project"""
        text = "# 项目概述\n## 架构设计\n微服务架构..."
        chunks = self.router.route(_make_doc(text), "/tmp/project.md")
        assert len(chunks) >= 1

    def test_routes_general_fallback(self):
        """无关键词 → general fallback"""
        text = "这是一段没有特定文档类型特征的普通文本。今天天气不错。"
        chunks = self.router.route(_make_doc(text), "/tmp/general.txt")
        assert len(chunks) >= 1

    def test_empty_docs(self):
        chunks = self.router.route([], "/tmp/empty.txt")
        assert chunks == []

    def test_debug_metadata_structure(self):
        """所有 chunk 都有必需的元数据字段"""
        text = "4.2.1 测试\n内容\n4.2.2 测试2\n内容"
        chunks = self.router.route(_make_doc(text), "/tmp/test.txt")
        for c in chunks:
            assert "parent_doc_id" in c.metadata
            assert "chunk_index" in c.metadata
            assert "total_chunks" in c.metadata
            assert "source_file" in c.metadata
            assert "file_path" in c.metadata


# ============================================================
# Backward Compatibility
# ============================================================

class TestBackwardCompatibility:
    """向后兼容：现有接口不受影响"""

    def test_split_documents_signature(self):
        """split_documents 签名不变"""
        import inspect
        from preprocessing.loader import split_documents
        sig = inspect.signature(split_documents)
        params = list(sig.parameters.keys())
        assert "docs" in params
        assert "file_path" in params
        assert "chunk_size" in params
        assert "chunk_overlap" in params

    def test_chunk_is_valid_document(self):
        """产出的 Document 对象可正常使用"""
        router = ChunkStrategyRouter()
        chunks = router.route(
            _make_doc("测试内容" * 100), "/tmp/test.txt"
        )
        assert len(chunks) > 0
        for c in chunks:
            assert isinstance(c, Document)
            assert isinstance(c.page_content, str)
            assert isinstance(c.metadata, dict)
            assert len(c.page_content) > 0

    def test_manual_policy_terminology_handled(self):
        """术语说明"""
        text = ("4.2.2 蔬菜叶菜类\n"
                "到店当天必须上架。\n"
                "当日22:00未售完的叶菜，一律报损处理。\n"
                "不允许隔夜翻包重新上架。")
        strategy = ManualPolicyChunkStrategy()
        chunks = strategy.split(_make_doc(text), "/tmp/t.txt")
        for c in chunks:
            # 关键短语完整性
            if "蔬菜叶菜" in c.page_content:
                assert "一律报损处理" in c.page_content
                assert "不允许隔夜翻包重新上架" in c.page_content
                break

    def test_section_id_unique_per_chunk(self):
        """同一 section_id 不出现在多个 chunk 中（除非递归子切）"""
        strategy = ManualPolicyChunkStrategy()
        chunks = strategy.split(_make_doc(POLICY_TEXT), "/tmp/t.txt")
        ids = [c.metadata.get("section_id") for c in chunks if c.metadata.get("section_id")]
        # Manual policy strategy should not create duplicate section_ids
        assert len(ids) == len(set(ids))  # all unique

    def test_original_chunk_flow_unchanged(self):
        """从 split_documents 到 embedding 的数据流不变"""
        from preprocessing.loader import split_documents
        chunks = split_documents(_make_doc("测试数据"), "/tmp/test_data.txt")
        assert isinstance(chunks, list)
        assert len(chunks) > 0
        assert isinstance(chunks[0], Document)
        # Verify metadata keys that downstream code depends on
        meta = chunks[0].metadata
        required_keys = ["parent_doc_id", "chunk_index", "source_file", "file_path"]
        for key in required_keys:
            assert key in meta, f"Missing required metadata key: {key}"

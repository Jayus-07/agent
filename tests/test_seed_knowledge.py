"""知识域生成器测试 — KnowledgeDoc, KnowledgeChunk。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seed_data.core.context import GenerationContext
from seed_data.core.profile import SeedProfile
from seed_data.generators.knowledge import (
    KnowledgeDocGenerator,
    KnowledgeChunkGenerator,
)
from seed_data.utils import constants


@pytest.fixture
def ctx_empty():
    """空 Context（知识生成器不依赖 Master Data）。"""
    profile = SeedProfile.from_name("tiny")
    return GenerationContext(profile, seed=42)


# ======================= TestKnowledgeDocGenerator =======================

class TestKnowledgeDocGenerator:
    def test_generate_one(self, ctx_empty):
        gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        doc = gen.generate_one(ctx_empty)
        assert doc["doc_id"].startswith("KD")
        assert doc["title"], "文档缺少标题"
        assert doc["content"], "文档缺少内容"
        assert doc["content_type"] == "MARKDOWN"
        assert doc["category"] in {c["code"] for c in constants.KNOWLEDGE_CATEGORIES}
        assert doc["version"].startswith("v")

    def test_generate_many(self, ctx_empty):
        gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        docs = gen.generate_many(ctx_empty, count=20)
        assert len(docs) == 20
        ids = [d["doc_id"] for d in docs]
        assert len(ids) == len(set(ids))

    def test_template_docs_have_real_content(self, ctx_empty):
        """模板生成的文档包含真实的 SOP/FAQ 内容。"""
        gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        docs = gen.generate_many(ctx_empty, count=50)

        # 至少有部分文档内容长度 > 200 字符（模板文档）
        long_docs = [d for d in docs if len(d["content"]) > 200]
        assert len(long_docs) > 0, "应该有模板生成的详细文档"

    def test_category_distribution(self, ctx_empty):
        """文档应覆盖多个知识分类。"""
        gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        docs = gen.generate_many(ctx_empty, count=100)

        categories = {d["category"] for d in docs}
        # 至少覆盖 3 个不同分类
        assert len(categories) >= 3, f"仅覆盖了 {len(categories)} 个分类"

    def test_validity_dates(self, ctx_empty):
        gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        docs = gen.generate_many(ctx_empty, count=20)

        for doc in docs:
            assert doc["valid_from"], "缺少生效日期"
            if doc["valid_to"]:
                assert doc["valid_from"] <= doc["valid_to"], \
                    f"生效日期 > 失效日期: {doc['valid_from']} > {doc['valid_to']}"


class TestKnowledgeChunkGenerator:
    def test_generate_from_docs(self, ctx_empty):
        # 先生成文档
        doc_gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        docs = doc_gen.generate_many(ctx_empty, count=10)
        ctx_empty.register_batch("knowledge_doc", docs)

        # 生成分块
        chunk_gen = KnowledgeChunkGenerator(ctx_empty.rng, ctx_empty.profile)
        chunks = chunk_gen.generate_many(ctx_empty)
        ctx_empty.register_batch("knowledge_chunk", chunks)

        assert len(chunks) > 0, "应为每篇文档生成至少 1 个分块"

    def test_all_chunks_reference_valid_doc(self, ctx_empty):
        doc_gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        docs = doc_gen.generate_many(ctx_empty, count=10)
        ctx_empty.register_batch("knowledge_doc", docs)

        chunk_gen = KnowledgeChunkGenerator(ctx_empty.rng, ctx_empty.profile)
        chunks = chunk_gen.generate_many(ctx_empty)
        ctx_empty.register_batch("knowledge_chunk", chunks)

        doc_ids = {d["doc_id"] for d in docs}
        chunk_doc_ids = {c["doc_id"] for c in chunks}
        # 所有 chunk 的 doc_id 都应存在
        assert chunk_doc_ids.issubset(doc_ids), \
            f"存在无效 doc_id 引用: {chunk_doc_ids - doc_ids}"

    def test_chunks_in_sequence(self, ctx_empty):
        """每个文档的 chunk 应按 chunk_index 排序。"""
        doc_gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        docs = doc_gen.generate_many(ctx_empty, count=10)
        ctx_empty.register_batch("knowledge_doc", docs)

        chunk_gen = KnowledgeChunkGenerator(ctx_empty.rng, ctx_empty.profile)
        chunks = chunk_gen.generate_many(ctx_empty)
        ctx_empty.register_batch("knowledge_chunk", chunks)

        # 按 doc_id 分组
        from collections import defaultdict
        by_doc = defaultdict(list)
        for c in chunks:
            by_doc[c["doc_id"]].append(c)

        for doc_id, doc_chunks in by_doc.items():
            indices = [c["chunk_index"] for c in doc_chunks]
            assert indices == sorted(indices), \
                f"doc {doc_id} 的 chunk 序号不连续: {indices}"
            assert indices == list(range(len(indices))), \
                f"doc {doc_id} 的 chunk 序号应从 0 开始: {indices}"


class TestKnowledgePipeline:
    """集成测试：完整 KnowledgeDoc → KnowledgeChunk 链路。"""

    def test_full_pipeline(self, ctx_empty):
        # 1. 生成文档
        doc_gen = KnowledgeDocGenerator(ctx_empty.rng, ctx_empty.profile)
        docs = doc_gen.generate_many(ctx_empty, count=20)
        ctx_empty.register_batch("knowledge_doc", docs)

        # 2. 生成分块
        chunk_gen = KnowledgeChunkGenerator(ctx_empty.rng, ctx_empty.profile)
        chunks = chunk_gen.generate_many(ctx_empty)
        ctx_empty.register_batch("knowledge_chunk", chunks)

        # 3. 验证
        assert ctx_empty.count("knowledge_doc") == 20
        assert ctx_empty.count("knowledge_chunk") > 0
        assert ctx_empty.count("knowledge_chunk") >= ctx_empty.count("knowledge_doc")

        # 4. 每个文档至少 1 个 chunk
        doc_ids_with_chunks = {c["doc_id"] for c in chunks}
        all_doc_ids = {d["doc_id"] for d in docs}
        assert doc_ids_with_chunks == all_doc_ids, \
            "部分文档没有生成 chunk"

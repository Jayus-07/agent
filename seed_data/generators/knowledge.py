"""知识库文档生成器 — KnowledgeDoc + KnowledgeChunk。

策略:
- 手写 10 篇骨架模板 → 提供可验证的 RAG 检索基准
- 20 篇基于模板 + 随机变体
- 70 篇 Faker 段落填充（模拟导入历史文档）
- 每篇自动按 500 字符分块，生成对应 KnowledgeChunk
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from typing import Any

from seed_data.core.generator import BaseGenerator
from seed_data.utils import constants

# 从 product.py 导入知识模板
from seed_data.generators.product import KNOWLEDGE_TEMPLATES


class KnowledgeDocGenerator(BaseGenerator):
    """知识文档生成器 — 混合骨架模板 + Faker 填充。"""

    entity_name = "knowledge_doc"

    def __init__(self, rng: random.Random, profile: "SeedProfile"):  # noqa: F821
        super().__init__(rng, profile)
        # 展平所有模板
        self._templates: list[dict[str, Any]] = []
        for cat_code, docs in KNOWLEDGE_TEMPLATES.items():
            for doc in docs:
                self._templates.append({**doc, "category_code": cat_code})

    def _random_date(self, ctx: "GenerationContext",  # noqa: F821
                     days_back: int = 365) -> str:
        """生成随机日期。"""
        d = datetime.now() - timedelta(days=self.rng.randint(1, days_back))
        return d.strftime("%Y-%m-%d")

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        """生成一篇文档。60% 概率用模板，40% 随机。"""
        use_template = self.rng.random() < 0.6 and len(self._templates) > 0

        if use_template:
            tmpl = self.rng.choice(self._templates)
            # 获取对应知识分类
            cat_code = tmpl.get("category_code", "PRODUCT")
            title = tmpl["title"]
            content = tmpl["content"]
            source = "INTERNAL"
        else:
            # 随机文档: Faker 段落填充
            cat = self.rng.choice(constants.KNOWLEDGE_CATEGORIES)
            cat_code = cat["code"]
            title = ctx.faker.sentence()[:80]
            num_paragraphs = self.rng.randint(3, 10)
            paragraphs = []
            for _ in range(num_paragraphs):
                para = ctx.faker.paragraph(nb_sentences=self.rng.randint(2, 6))
                paragraphs.append(para)
            content = "\n\n".join(paragraphs)
            source = self.rng.choice(["INTERNAL", "IMPORT", "URL"])

        lang = self.rng.choice(["zh", "zh", "zh", "en", "en"])  # 中文为主
        valid_from = self._random_date(ctx, days_back=730)
        valid_to = None
        # 10% 概率有过期日期
        if self.rng.random() < 0.1:
            valid_days = self.rng.randint(30, 365)
            valid_to_date = datetime.strptime(valid_from, "%Y-%m-%d") + timedelta(days=valid_days)
            valid_to = valid_to_date.strftime("%Y-%m-%d")

        tags = self._generate_tags(ctx, cat_code)

        return {
            "doc_id": ctx.next_id("knowledge_doc", "KD"),
            "category": cat_code,
            "category_name": self._cat_name(cat_code),
            "title": title,
            "content": content,
            "content_type": "MARKDOWN",
            "source": source,
            "version": f"v{self.rng.randint(1, 5)}.{self.rng.randint(0, 9)}",
            "language": lang,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "is_active": True,
            "author_id": f"user_{self.rng.randint(1, 20):03d}",
            "last_updated_at": self._random_date(ctx, days_back=180),
            "tags": tags,
            "channel": self.rng.choice(["Amazon", "Shopify", "TikTok Shop", "All"]),
            "embedding_status": "PENDING",
        }

    def _generate_tags(self, ctx: "GenerationContext", cat_code: str) -> list[str]:  # noqa: F821
        """按分类生成相关标签。"""
        tag_map = {
            "AMAZON_SOP": ["Amazon", "SOP", "账号", "政策", "运营"],
            "LISTING": ["Listing", "标题", "关键词", "优化", "SEO"],
            "AD": ["广告", "PPC", "ACoS", "投放", "优化"],
            "CUSTOMER_SERVICE": ["客服", "FAQ", "退货", "物流", "售后"],
            "WAREHOUSE": ["仓库", "FBA", "发货", "库存", "包装"],
            "PRODUCT": ["产品", "规格", "材质", "使用说明"],
            "TRAINING": ["培训", "入职", "SOP", "手册"],
            "POLICY": ["制度", "报销", "考勤", "绩效"],
        }
        base = tag_map.get(cat_code, ["通用"])
        extra = [ctx.faker.word() for _ in range(self.rng.randint(0, 3))]
        return base + extra

    @staticmethod
    def _cat_name(cat_code: str) -> str:
        for c in constants.KNOWLEDGE_CATEGORIES:
            if c["code"] == cat_code:
                return c["name"]
        return cat_code


class KnowledgeChunkGenerator(BaseGenerator):
    """知识分块生成器 — 对已生成的 KnowledgeDoc 按 500 字符窗口分块。"""

    entity_name = "knowledge_chunk"

    def __init__(self, rng: random.Random, profile: "SeedProfile"):  # noqa: F821
        super().__init__(rng, profile)

    def generate_one(self, ctx: "GenerationContext") -> dict:  # noqa: F821
        """不单独使用，统一通过 generate_many 批量分块。"""
        return {}

    def generate_many(self, ctx: "GenerationContext",  # noqa: F821
                      count: int | None = None) -> list[dict]:
        """对 ctx 中所有 KnowledgeDoc 自动分块。"""
        docs = ctx.get_entities("knowledge_doc")
        if not docs:
            return []

        results = []
        chunk_size = 500  # 每块约 500 字符

        for doc_idx, doc in enumerate(docs):
            content = doc.get("content", "")
            doc_id = doc.get("doc_id", f"KD{doc_idx:04d}")

            # 简单按段落 + 长度分块
            paragraphs = content.split("\n\n")
            chunks = self._split_into_chunks(paragraphs, chunk_size)

            for chunk_idx, chunk_text in enumerate(chunks):
                results.append({
                    "chunk_id": ctx.next_id("knowledge_chunk", "KCH"),
                    "doc_id": doc_id,
                    "chunk_index": chunk_idx,
                    "content": chunk_text,
                    "token_count": len(chunk_text),  # 字符级近似
                })

        return results

    def _split_into_chunks(self, paragraphs: list[str], max_size: int) -> list[str]:
        """将段落列表按 max_size 分块。"""
        chunks = []
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) + 2 <= max_size:
                current += ("\n\n" + para) if current else para
            else:
                if current:
                    chunks.append(current)
                # 如果单段超过 max_size，强行分段
                if len(para) > max_size:
                    for i in range(0, len(para), max_size):
                        chunks.append(para[i:i + max_size])
                    current = ""
                else:
                    current = para

        if current:
            chunks.append(current)

        return chunks

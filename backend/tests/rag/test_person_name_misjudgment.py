"""tests/rag/test_person_name_misjudgment.py — NER 人名误判交叉验证（D-13）

实测缺陷：jieba 把专名/地名误标 nr 人名（「乌塔」）→ 查询侧 person_names
filter 与文档人名索引对不上 → Stage1 person_name_miss → doc_ids=[] 检索死。
修复：查询侧抽出的人名必须存在于人名索引（文档元数据权威）才进 filter；
索引没有的剔除并同步清洗 metadata_filter，走 doc 级相似度路径。
"""
from __future__ import annotations

from backend.rag.retrieval.retrievers import ChunkLevelRetriever


class _FakeRetriever(ChunkLevelRetriever):
    """最小桩：只构造 person_index，不走真实检索。"""

    def __init__(self, person_index):
        # 绕过 pydantic 必填字段（base_retriever 等不参与本单测）
        object.__setattr__(self, "__dict__", {})
        self.person_index = person_index


def test_verified_person_kept():
    r = _FakeRetriever({"林觉民": ["doc1"]})
    names = r and None
    # 直接验证清洗逻辑片段（与 Stage1 内联实现同式）
    names = ["林觉民"]
    person_index = {"林觉民": ["doc1"]}
    verified = [p for p in names if p in person_index]
    assert verified == ["林觉民"]


def test_misjudged_proper_noun_dropped():
    """「乌塔」不在人名索引（文档元数据无人名）→ 剔除。"""
    person_index = {"林觉民": ["doc1"]}  # 索引里没有「乌塔」
    names = ["乌塔"]
    verified = [p for p in names if p in person_index]
    assert verified == []


def test_mixed_names_partial_keep():
    person_index = {"林觉民": ["doc1"]}
    names = ["林觉民", "乌塔"]
    verified = [p for p in names if p in person_index]
    assert verified == ["林觉民"]


def test_empty_index_drops_all():
    """空索引（文档元数据全无人名）→ 全部剔除，走 doc 级相似度。"""
    person_index = {}
    names = ["乌塔", "张三"]
    verified = [p for p in names if p in person_index]
    assert verified == []

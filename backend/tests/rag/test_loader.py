"""loader 回归测试 — 全量加载路径 load_documents_from_directory 走新流水线。

防止 ChunkStrategyRouter 签名变更后，这条启动路径静默崩溃（P0 回归）。
"""
from backend.rag.preprocessing.loader import load_documents_from_directory

MD = """# 售后制度
## 退货流程
客服审核退货原因。
"""


def test_load_documents_from_directory_produces_chunks_with_doc_id(tmp_path):
    # 目录结构: {kb_id}/{department}/{file}
    doc_dir = tmp_path / "policy_general" / "general"
    doc_dir.mkdir(parents=True)
    (doc_dir / "售后.md").write_text(MD, encoding="utf-8")

    docs = load_documents_from_directory(str(tmp_path))

    assert docs
    for d in docs:
        assert d.metadata.get("doc_id"), "chunk 缺 doc_id（级联删除/检索过滤依赖它）"
        assert d.metadata.get("kb_id") == "policy_general"

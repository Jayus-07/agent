"""切分流水线编排 — Parser → Cleaner → Analyzer → Router → Strategy。"""
from __future__ import annotations

from typing import List

from langchain_core.documents import Document

from backend.rag.preprocessing.cleaner import DocumentCleaner
from backend.rag.preprocessing.metadata import classify_doc_type
from backend.rag.preprocessing.parser import parse_file
from backend.rag.preprocessing.structure_analyzer import StructureAnalyzer
from backend.rag.preprocessing.chunking import ChunkStrategyRouter
from backend.rag.preprocessing.ast import walk
from backend.shared.logger import logger


def parse_and_chunk(file_path: str, doc_type_hint: str = "") -> List[Document]:
    """单文件完整切分流水线。返回 leaf + parent 双粒度 chunk。"""
    raw_ast = parse_file(file_path)

    # 结构安全清洗：清洗每个节点文本，保留结构
    cleaner = DocumentCleaner()
    source_type = "pdf" if file_path.lower().endswith(".pdf") else "text"
    for node in walk(raw_ast.root):
        if node.type not in ("table",):  # table 的 rows 不在 text 清洗范围
            node.text = cleaner.clean(node.text, source_type=source_type).text

    normalized_ast, report = StructureAnalyzer().analyze(raw_ast)

    doc_type = doc_type_hint or classify_doc_type(raw_ast.raw_text, filename=file_path, file_path=file_path)
    strategy = ChunkStrategyRouter().route(doc_type, report)
    logger.info(
        f"[ChunkPipeline] {file_path} doc_type={doc_type} "
        f"completeness={report.completeness} → {strategy.__class__.__name__}"
    )
    return strategy.split(normalized_ast, file_path)

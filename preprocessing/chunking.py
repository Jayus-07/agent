"""
chunking.py — Document Type Aware Chunking Strategy Router

Each strategy splits documents differently based on classified doc_type:
  manual/policy   → ManualPolicyChunkStrategy     (chapter/section boundaries)
  project/report  → ProjectReportChunkStrategy     (header-first, size-capped)
  general         → GeneralChunkStrategy           (RecursiveCharacterTextSplitter fallback)
"""

import hashlib
import os
import re
from abc import ABC, abstractmethod
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

from config import (
    GENERAL_CHUNK_SIZE,
    GENERAL_CHUNK_OVERLAP,
    PROJECT_CHUNK_SIZE,
)
from preprocessing.metadata import classify_doc_type
from utils.logger import logger


# ============================================================
# Section pattern: match numbered/Chinese headings for manual/policy
# ============================================================

# Matches: "4.2.1 标题", "一、标题", "第X章 标题", "1) 标题", "1. 标题"
# Markdown 标题 (## xxx, ### xxx)
_MD_HEADER = re.compile(r'^(#{1,3})\s+(.+)$', re.MULTILINE)

# 中文编号标题 (一、xxx, 1. xxx, 第X章 xxx)
_CN_SECTION = re.compile(
    r'(?:^|\n)\s*'
    r'('
    r'\d+(?:\.\d+)*\s+'                              # 4.2.1 style
    r'|第[一二三四五六七八九十百千\d]+[章节条]\s+'   # 第X章/第X条
    r'|[一二三四五六七八九十]+、\s*'                  # 一、二、
    r'|\d+[\)、.]\s*'                                 # 1) 1. 1、
    r')'
    r'([^\n]+)',
    re.MULTILINE,
)


def _find_sections(text: str) -> List[dict]:
    """Find all section headers — Markdown #/##/### + Chinese numbered headers."""
    sections = []

    # 1) Markdown headers (priority: they often wrap Chinese numbers like "## 一、xxx")
    for m in _MD_HEADER.finditer(text):
        level = len(m.group(1))  # 1=h1, 2=h2, 3=h3
        title = m.group(2).strip()
        sid = title
        if len(sid) > 80:
            continue
        sections.append({
            "start": m.start(),
            "end": m.end(),
            "id": title.rstrip("、.。)"),
            "title": title,
            "full": sid,
            "level": level,
        })

    # 2) Chinese numbered headers (fallback for documents without markdown)
    if not sections:
        for m in _CN_SECTION.finditer(text):
            sid = (m.group(1) + m.group(2)).strip()
            if len(sid) > 80:
                continue
            sections.append({
                "start": m.start(),
                "end": m.end(),
                "id": m.group(1).strip().rstrip("、.。)"),
                "title": m.group(2).strip(),
                "full": sid,
                "level": 2,
            })

    return sections



# ============================================================
# Common separators for RecursiveCharacterTextSplitter
# ============================================================

_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]


# ============================================================
# Base Strategy
# ============================================================

class ChunkStrategy(ABC):
    """Abstract base for document-type-aware chunking strategies."""

    @abstractmethod
    def split(self, docs: List[Document], file_path: str) -> List[Document]:
        """Split documents into chunks with type-appropriate metadata."""
        ...

    def _enrich_metadata(self, chunks: List[Document], file_path: str) -> List[Document]:
        """Attach standard metadata (parent_doc_id, chunk_index, source_file, file_path)."""
        parent_doc_id = hashlib.md5(file_path.encode()).hexdigest()[:10]
        source_file = os.path.basename(file_path)

        for i, chunk in enumerate(chunks):
            chunk.metadata.update({
                "parent_doc_id": parent_doc_id,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "source_file": source_file,
                "file_path": file_path,
            })

        return chunks


# ============================================================
# GeneralChunkStrategy (fallback)
# ============================================================

class GeneralChunkStrategy(ChunkStrategy):
    """Fallback strategy: RecursiveCharacterTextSplitter with generous chunk sizes."""

    def __init__(self, chunk_size: int = None, chunk_overlap: int = None):
        self._chunk_size = chunk_size or GENERAL_CHUNK_SIZE
        self._chunk_overlap = chunk_overlap or GENERAL_CHUNK_OVERLAP

    def split(self, docs: List[Document], file_path: str) -> List[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            length_function=len,
            separators=_SEPARATORS,
        )

        all_chunks = []
        for doc in docs:
            sub_texts = splitter.split_text(doc.page_content)
            for text in sub_texts:
                chunk_doc = Document(
                    page_content=text,
                    metadata=doc.metadata.copy(),
                )
                all_chunks.append(chunk_doc)

        return self._enrich_metadata(all_chunks, file_path)


# ============================================================
# ManualPolicyChunkStrategy
# ============================================================

class ManualPolicyChunkStrategy(ChunkStrategy):
    """
    For manuals and policy documents.
    Splits by section headers (4.2.1, 一、, 第X章, etc.).
    Guarantees clause integrity — NO secondary RecursiveCharacterTextSplitter.
    """

    def split(self, docs: List[Document], file_path: str) -> List[Document]:
        full_text = "\n".join(d.page_content for d in docs)
        sections = _find_sections(full_text)

        if not sections:
            # No section headers found — treat whole document as one chunk
            chunk = Document(
                page_content=full_text,
                metadata=docs[0].metadata.copy() if docs else {},
            )
            chunk.metadata["section_id"] = ""
            chunk.metadata["section_title"] = ""
            return self._enrich_metadata([chunk], file_path)

        chunks = []
        for i, sec in enumerate(sections):
            # Content: from this section's end to the next section's start (or EOF)
            start = sec["end"]
            end = sections[i + 1]["start"] if i + 1 < len(sections) else len(full_text)
            content = full_text[start:end].strip()

            if not content:
                continue

            base_meta = docs[0].metadata.copy() if docs else {}
            base_meta["section_id"] = sec["id"]
            base_meta["section_title"] = sec["title"]

            chunk = Document(page_content=f"{sec['full']}\n{content}", metadata=base_meta)
            chunks.append(chunk)

        return self._enrich_metadata(chunks, file_path)



# ============================================================
# ProjectReportChunkStrategy
# ============================================================

class ProjectReportChunkStrategy(ChunkStrategy):
    """
    For project reports and general reports.
    First splits by #/## headers (MarkdownHeaderTextSplitter for .md;
    regex-based header detection for .txt/.pdf).
    Then sub-chunks individual sections only if > PROJECT_CHUNK_SIZE.
    Preserves Markdown header metadata (Header 1, Header 2, Header 3).
    """

    def __init__(self, max_section_chars: int = None):
        self._max_section_chars = max_section_chars or PROJECT_CHUNK_SIZE

    def split(self, docs: List[Document], file_path: str) -> List[Document]:
        ext = os.path.splitext(file_path)[1].lower()
        full_text = "\n".join(d.page_content for d in docs)
        base_meta = docs[0].metadata.copy() if docs else {}

        # Phase 1: split by headers
        if ext == ".md":
            header_chunks = self._split_by_markdown_headers(full_text, base_meta)
        else:
            header_chunks = self._split_by_text_headers(full_text, base_meta)

        # Phase 2: sub-chunk long sections
        result = []
        for hc in header_chunks:
            text = hc.page_content
            if len(text) <= self._max_section_chars:
                result.append(hc)
            else:
                sub = self._sub_chunk(text, hc.metadata)
                result.extend(sub)

        return self._enrich_metadata(result, file_path)

    def _split_by_markdown_headers(self, text: str, base_meta: dict) -> List[Document]:
        """Use MarkdownHeaderTextSplitter for .md files."""
        splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "Header 1"),
                ("##", "Header 2"),
                ("###", "Header 3"),
            ]
        )
        sub_docs = splitter.split_text(text)

        chunks = []
        for sd in sub_docs:
            # Merge: base_meta as fallback, sub-doc metadata (header info) takes priority
            merged = dict(base_meta)
            merged.update(sd.metadata)
            # Map header metadata to section_id/section_title
            if "Header 1" in sd.metadata:
                merged["section_title"] = sd.metadata["Header 1"]
                merged["section_id"] = sd.metadata["Header 1"]
            elif "Header 2" in sd.metadata:
                merged["section_title"] = sd.metadata["Header 2"]
                merged["section_id"] = sd.metadata["Header 2"]
            elif "Header 3" in sd.metadata:
                merged["section_title"] = sd.metadata["Header 3"]
                merged["section_id"] = sd.metadata["Header 3"]
            chunk = Document(page_content=sd.page_content, metadata=merged)
            chunks.append(chunk)

        if not chunks:
            # Fallback: single chunk
            chunk = Document(page_content=text, metadata=dict(base_meta))
            chunk.metadata["section_id"] = ""
            chunk.metadata["section_title"] = ""
            chunks = [chunk]

        return chunks

    def _split_by_text_headers(self, text: str, base_meta: dict) -> List[Document]:
        """For .txt/.pdf files classified as project/report: detect # headers manually."""
        header_pattern = re.compile(r'^(#{1,3})\s+(.+)$', re.MULTILINE)
        matches = list(header_pattern.finditer(text))

        if not matches:
            chunk = Document(page_content=text, metadata=dict(base_meta))
            chunk.metadata["section_id"] = ""
            chunk.metadata["section_title"] = ""
            return [chunk]

        chunks = []
        for i, m in enumerate(matches):
            level = len(m.group(1))
            title = m.group(2).strip()
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip()

            meta = dict(base_meta)
            meta[f"Header {level}"] = title
            if level <= 2:
                meta["section_title"] = title
                meta["section_id"] = title

            chunk = Document(page_content=content, metadata=meta)
            chunks.append(chunk)

        return chunks

    def _sub_chunk(self, text: str, metadata: dict) -> List[Document]:
        """Recursively split a single long section into sub-chunks."""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._max_section_chars,
            chunk_overlap=100,
            length_function=len,
            separators=_SEPARATORS,
        )
        sub_texts = splitter.split_text(text)
        return [
            Document(page_content=t, metadata=dict(metadata))
            for t in sub_texts
        ]


# ============================================================
# ChunkStrategyRouter
# ============================================================

class ChunkStrategyRouter:
    """Routes to the appropriate chunking strategy based on classified doc_type."""

    def __init__(self, fallback_chunk_size: int = None, fallback_chunk_overlap: int = None):
        self._strategies = {
            # 按章节切分（保持章节完整性）
            "manual": ManualPolicyChunkStrategy(),
            "policy": ManualPolicyChunkStrategy(),
            "sop": ManualPolicyChunkStrategy(),
            "ad_policy": ManualPolicyChunkStrategy(),
            "product_spec": ManualPolicyChunkStrategy(),
            # 综合文档用通用切分
            "listing": ProjectReportChunkStrategy(),
            "faq": ProjectReportChunkStrategy(),
            "training": ProjectReportChunkStrategy(),
        }
        self._fallback = GeneralChunkStrategy(
            chunk_size=fallback_chunk_size,
            chunk_overlap=fallback_chunk_overlap,
        )

    def route(self, docs: List[Document], file_path: str) -> List[Document]:
        """Classify and route to the appropriate chunking strategy."""
        if not docs:
            return []

        full_text = "\n".join(d.page_content for d in docs)
        doc_type = classify_doc_type(full_text.lower())

        strategy = self._strategies.get(doc_type, self._fallback)
        filename = os.path.basename(file_path)

        logger.info(
            f"[ChunkStrategyRouter] {filename} → doc_type={doc_type} "
            f"→ strategy={strategy.__class__.__name__}"
        )

        chunks = strategy.split(docs, file_path)

        # Debug logging: per-chunk details
        logger.debug(f"  filename={filename}")
        logger.debug(f"  doc_type={doc_type}")
        logger.debug(f"  chunk_count={len(chunks)}")
        for i, c in enumerate(chunks):
            logger.debug(
                f"    chunk[{i}]: len={len(c.page_content)}, "
                f"section_id={c.metadata.get('section_id', '-')}, "
                f"section_title={c.metadata.get('section_title', '-')}"
            )

        return chunks

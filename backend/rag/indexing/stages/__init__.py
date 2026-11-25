"""索引管线 Stage 子包 — 现代企业级 Pipeline 管道模式与单一职责拆分。

各阶段职责：
  - contracts: 阶段产物 dataclass 契约与 trace span 统一收口
  - parse_stage: 文件加载、多格式解析、OCR 统计与 AST 初切
  - filter_stage: SHA256 查重早退、语义切分过滤与 PII 脱敏
  - quality_stage: 付费前完整性预检、Chunk 身份协议、付费后入库前门禁与留痕
  - metadata_stage: LLM 文档级元数据抽取与 Near-dup 识别
  - enrich_stage: 表格语义描述、实体/时间/模拟问题对齐与预算预检
  - embedding_stage: 批量向量化、退避重试与向量缓存
  - persist_stage: ChunkStore、DocDB、VectorDB 写入、BM25 快照发布与原子回滚
  - pipeline: 串联所有 Stage 的高内聚低耦合调度编排器（IndexingPipeline）
"""

from backend.rag.indexing.stages.contracts import (
    ChunkFilterResult,
    DocMetadataArtifact,
    EmbeddingArtifact,
    ParsedDocument,
    PersistResult,
    StageContext,
    stage_span,
)
from backend.rag.indexing.stages.embedding_stage import EmbeddingStage
from backend.rag.indexing.stages.enrich_stage import EnrichStage
from backend.rag.indexing.stages.filter_stage import FilterStage
from backend.rag.indexing.stages.metadata_stage import MetadataStage
from backend.rag.indexing.stages.parse_stage import ParseStage
from backend.rag.indexing.stages.persist_stage import PersistStage
from backend.rag.indexing.stages.pipeline import IndexingPipeline
from backend.rag.indexing.stages.quality_stage import QualityStage

__all__ = [
    "ChunkFilterResult",
    "DocMetadataArtifact",
    "EmbeddingArtifact",
    "EmbeddingStage",
    "EnrichStage",
    "FilterStage",
    "IndexingPipeline",
    "MetadataStage",
    "ParsedDocument",
    "ParseStage",
    "PersistResult",
    "PersistStage",
    "QualityStage",
    "StageContext",
    "stage_span",
]

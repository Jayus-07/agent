"""索引管线 Stage 子包 — 阶段 3 模块化拆分（任务 #3）。

把 indexer.py 的巨型方法按阶段物理拆分为独立模块，阶段间以
contracts.py 的 dataclass 产物衔接，trace span 通过 stage_span
上下文管理器统一收口（自动处理异常时的 span 关闭，杜绝泄漏）。

现状（增量拆分第一步，保持对外契约不变）：
  - metadata_stage.MetadataStage  ← indexer._build_doc_metadata /
                                    _finalize_unified_metadata / _detect_near_dup
  - embedding_stage.EmbeddingStage ← indexer._embed_with_retry 及其辅助簇

indexer.IncrementalIndexer 保留同名薄委托方法，_index_file_inner
编排逻辑与全部测试契约（含 return_detail dict 结构）零改动。
后续把 _index_file_inner 的 parse/chunk_filter/persist 段也迁入
对应 Stage 时，直接消费 contracts 里的 dataclass 产物。
"""

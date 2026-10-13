# TASK: 企业 RAG 上传、结构识别、Chunk 切分与元数据治理

- 分支：`task/rag-upload-chunk-governance-20261013`
- worktree：`D:/Program Files/workplace/agent-wt-rag-chunk-gov-20261013`
- 基线：origin/main @ 2d1d0b1
- 状态：IN_PROGRESS

## 目标

收口 RAG 上传入库链路：混合格式不丢内容、超长文档完整处理、Parent/Leaf 身份契约修复、
元数据可追溯、发布安全。复用既有 Parser/AST/StructureAnalyzer/ChunkStrategyRouter/
UnifiedMetadata/DecisionEnvelope/Parent-Leaf/MetadataStage/QualityGate/候选发布机制，
不新建平行系统。

## 范围与批次

| 批次 | 内容 | 状态 |
| --- | --- | --- |
| B1 | P0 正确性：P0-01 FAQ 丢失 / P0-02 AST 覆盖 / P0-03 Parent-Leaf ID 契约 / P0-04 表格位置 / P0-05 长度限制 | 进行中 |
| B2 | P0 超长文档：分区批处理、Excel/CSV 前导零、OCR 覆盖展示、幂等重试 | 待开工 |
| B3 | P1 Structure Profile + Router 审计记录（先验证调用方再收敛策略） | 待开工 |
| B4 | P1 元数据治理：full_text 重复拼接、长文摘要覆盖、simulated_questions 预算 | 待开工 |
| B5 | 索引与发布安全核查（CAS 续跑 / BM25 快照一致性 / 去重误判 / pending_review 绕过） | 待开工 |
| B6 | 16 类真实文档测试集 + 定向验收 + rag.md 文档同步 | 待开工 |

## 验收门禁（P0）

- 可索引 AST 有效节点覆盖率 100%，排除内容有明确原因。
- 全部 Leaf 的 Parent 关系可解析；需要 Parent 的 Leaf 关联率 100%。
- 每个 Chunk 满足实际 Token 上限（Leaf≤500 / Parent≤2000，配置可覆盖）。
- 表格行列对应与精确数字无损失（含前导零 SKU）。
- 无假成功 active：截断/OCR 漏页/部分失败必须落 quality_issues 或失败态。
- 更新失败不影响上一代 Active 索引；pending_review 不被检索绕过。

## 关键证据（已定位）

| 编号 | 位置 | 问题 |
| --- | --- | --- |
| P0-01 | parser/markdown_parser.py:28、txt_parser.py:45、docx_parser.py:72 | looks_like_qa_doc 命中（min_pairs=1）即整篇只产 Q/A，其余正文/表格全丢 |
| P0-02 | chunking.py Structure._sections / Step._split_by_sections / QAChunkStrategy | 根节点直接子叶（首标题前正文）丢失；Step 丢 section 外叶子；QA 策略丢非 QA 叶子；无过滤原因记录 |
| P0-03 | indexer.py:1500 + retrieval/retrievers.py:866 | chunk_id 被覆盖为 {doc_id}_{i}，parent_chunk_id 仍指内容派生 id → Parent 扩展 $in 查询永远落空 |
| P0-04 | docx_parser.py:122-145、pdf_parser.py:250-257 | 表格解析与段落流分离，全部挂最后 section，不按阅读顺序 |
| P0-05 | chunking.py Step/Legal/QA/_attach_virtual_parents/_split_table_node fallback | 多条路径无 token 上限检查，超长步骤/条款/答案直接成单一 Leaf |
| B2 | csv_parser.py:36 pandas 无 dtype=str | 前导零 SKU（"00123"）被数值推断破坏 |
| B2 | pdf_parser.py:290 OCR min(page_count, RAG_OCR_MAX_PAGES) | 超限页面静默不处理，无覆盖展示 |
| B4 | indexer.py:1166 full_text="\n\n".join(chunks) | Parent(章节全量)+Leaf(子集) 拼接 = 大量重复文本充当文档全文 |
| B4 | indexer.py:238 _sample_for_summary | 超长文档只取头尾 20k+20k 字符声称代表全文 |
| B4 | question_gen.py:248 chunks_text[:24] | 模拟问题只生成前 24 chunk，长文档尾部零覆盖 |
| B3 | structure_analyzer.py completeness<0.7 | 强结构信号（qa 节点=1.0 除外）整篇降级 Recursive |

## 约束

- 复用 UnifiedMetadata / DecisionEnvelope / taxonomy / IndexRun / generation / candidate publish / 质量报告 / Trace。
- 不删除现有元数据字段或切分策略，除非有调用方证据。
- Semantic 切分保持默认关闭。
- 隔离测试 KB 验收，不污染正式知识库。

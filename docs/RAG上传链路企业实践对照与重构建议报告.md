# RAG 文件上传链路：逐环节实现分析 × 企业实践对照 × 重构建议

> 范围：`POST /upload`（`backend/app/api/routes/rag_upload.py`）→ `IncrementalIndexer`（`backend/rag/indexing/indexer.py`）→ 预处理流水线（`backend/rag/preprocessing/`）→ 多存储落库 → SSE 进度收尾。
> 结论先行：**整体架构已达到"准企业级"水位**（流式落盘、原子替换、四层去重、先写后删、内容派生 chunk_id、全链路 trace 都是真功夫）。可借鉴的企业做法主要集中在：**任务持久化编排、跨实例一致性、上传通道工程、embedding 模型生命周期、安全扫描** 五个方向。

---

## 一、逐环节分析

### 1. 上传接收：流式写 tmp + 双保险限流 + 魔数校验 + 原子落盘

**现状实现**（`sync_upload_impl`）
- 分块 `await file.read()` 流式写临时文件，边写边字节计数，超限立即删 tmp（防 tmp_dir 泄漏）；
- Content-Length 预检带 16KB multipart 余量（F10），精确上限由流式计数兜底（双保险）；
- 落盘前魔数校验：PDF `%PDF-`、docx/xlsx `PK\x03\x04`、文本格式查 NUL/UTF-16 BOM；
- 中文文件名 latin-1→utf-8 乱码修复；realpath 归一 + `commonpath` 防目录穿越；
- 覆盖同名先 `copy2` 备份 `.bak`，再 `os.replace` 原子替换。

**企业做法**
- 对象存储直传（预签名 URL + multipart upload），服务器只收元数据不中转字节；
- 分片上传 / 断点续传（tus 协议、S3 multipart），大文件失败只补传分片；
- Content-Disposition/RFC 5987 编码处理文件名，而不是手工多编码猜测；
- 病毒扫描（ClamAV/沙箱）+ 文件类型深度嗅探（libmagic）作为独立安全层。

**好处**：服务器内存与带宽解耦（直传）、大文件上传成功率显著提升、安全责任边界清晰。

**可学习 / 重构建议**
- ✅ 现状对 50MB 上限 + 内网场景完全够用，**不建议为直传而直传**。
- 值得借鉴的两点：
  1. **文件名解析改用标准库 `email.utils`/`werkzeug.utils.secure_filename` 思路或 RFC 5987 解析**，替代当前 latin-1/cp1252/iso-8859-1 猜测链（现在三个编码按序试，遇到生僻字仍会失败）；
  2. 上传接口统一用 **HTTP 状态码 + 错误码体系**（现在所有业务错误都是 `200 + {"ok": False, "error": ...}`），企业前端网关/监控按 4xx/5xx 统计告警，200+error 会让错误率被稀释、无法接告警。

---

### 2. 并发控制：文件锁 + 信号量 + 心跳自愈

**现状实现**（`acquire_index_lock` / `_start_lock_heartbeat` / `_get_index_semaphore`）
- `.lock` sidecar 文件 + `O_CREAT|O_EXCL` 原子互斥（跨进程跨线程）；
- 锁内写 `pid + 时间戳`，持锁期间 60s 心跳线程刷新，30 分钟 TTL 自愈崩溃残留；
- 心跳先校验 pid 归属，绝不复活已易主的锁；
- 索引并发用 asyncio.Semaphore（默认 2，`RAG_MAX_CONCURRENT_INDEX`），满时发排队提示。

**企业做法**
- Redis/DB 分布式锁（Redlock、`SET NX PX` + 续期 watchdog），多实例共享锁空间；
- 任务级幂等（同一 doc_id 的索引任务天然幂等可重入）替代物理锁；
- 全局并发用消息队列 consumer 并发度 + 背压（lag 告警）控制，而非进程内信号量。

**好处**：多实例下锁语义仍成立；队列天然削峰、故障重试有界。

**可学习 / 重构建议**
- 当前实现**单实例场景已是最佳实践**（甚至比很多企业的粗暴 DB 行锁好——有心跳、有自愈、有防复活）。写注释的水准也高。
- 重构触发条件明确：**只要计划多实例/多 worker 部署，`.lock` 文件和 `_progress_queues` 内存字典必须先升级**——建议直接用现成的 Redis（进度镜像已在写 Redis，锁也迁 Redis SET NX + 续期，改造成本低）。

---

### 3. 去重体系：四层防线（这是本项目亮点）

**现状实现**
1. **文件级**：SHA256（8MB 分块流式计算）对比 registry，未变直接 `duplicate` 终态；
2. **registry 占位行去重**：`register_in_progress` 写 parsing 状态行（重启恢复依据）；
3. **chunk 级**：SimHash（64-bit，字符 3-gram，汉明距离 ≤3 判重，保留最近 1 万条）；
4. **doc 级近似**：MinHash 签名（`compute_minhash`）写入 registry，`near_dup_id` 标记近似文档（indexer.py:1130-1146，按 doc_type 查询活跃文档比对）。

**企业做法**
- 完全一致的分层思路：byte-level hash → chunk-level fingerprint → document-level near-dup（MinHash/LSH）；
- 差异在**规模工程**：SimHash 指纹库持久化 + LSH bucket 索引（O(1) 查询），而不是进程内 list 线性扫描；
- 内容寻址存储（CAS）：文件按 hash 存储天然去重。

**好处**：省 embedding 成本（重复 chunk 不再嵌入）、防检索结果被同 chunk 双命中稀释 mrr、为知识库瘦身提供数据。

**可学习 / 重构建议**
- 分层设计已对齐企业，**架构不用动**。两处小短板：
  1. `DuplicateDetector.seen_hashes` 是实例内存 list，`should_keep` 线性扫描 O(n)，且**跨请求/跨重启不共享**——文档量大时同一重复 chunk 在两次索引中都会被保留。可把指纹落 SQLite（chunk_store 顺手加一列）或 Redis set，用 LSH 分桶；
  2. `near_dup_id` 检测到了近似文档但**只是标记，没有后续策略**（保留？替换？提示用户？）。企业做法是把 near-dup 作为审核信号进入 `pending_review` 状态——好在状态枚举里已经有 `pending_review`，接线即可。

---

### 4. 解析层：parser 注册表 + AST + OCR 兜底

**现状实现**（`backend/rag/preprocessing/parser/` + `pipeline.parse_and_chunk`）
- 每种格式一个 parser（pdf/docx/xlsx/csv/md/txt），统一产出 DocumentAST；
- `PARSABLE_EXTS` 注册表是**单一事实源**：上传白名单、磁盘扫描名单都从它派生（F6）；
- PDF 无文本层时按 `RAG_OCR_PROVIDER`（默认 rapidocr，支持 dashscope/baidu）走 OCR 兜底；OCR 关闭时入口预检直接明确拒绝；
- 扫描件友好报错：页数、图片块数都告诉你（pipeline.py:32-52）。

**企业做法**
- 商用文档智能服务（Azure Document Intelligence / LlamaParse / unstructured.io）：版面分析、表格结构还原、阅读顺序还原一体解决；
- 解析失败降级链（OCR → Vision LLM → 人工审核队列）。

**好处**：复杂版面（多栏、嵌套表格）的解析质量决定 RAG 上限，企业在此投入最重。

**可学习 / 重构建议**
- 自研 AST + 注册表方向正确且可测试性好（test_pdf_parser/test_docx_parser 等成体系），**不建议换框架**。
- 可借鉴：**解析失败降级链**——当前 OCR 失败即失败；企业会在 OCR 失败后尝试 Vision LLM（你已有 dashscope provider，加一个 fallback provider 链即可，配置化）。

---

### 5. 清洗 + 质量过滤 + PII 脱敏

**现状实现**（`DocumentCleaner` / `ChunkFilter`）
- 清洗九项操作全部 config 开关化（控制字符/全角半角/HTML/PDF 页眉页脚/URL 邮箱……），`CleanResult` 带变更留痕；
- 清洗失败**不阻塞**索引，用原始文本继续（降级正确）；
- ChunkFilter 五拒一改：空/超短/纯符号/低中文占比/SimHash 重复 → 拒绝；PII → 改写不拒绝；
- PII 正则覆盖手机号/身份证/银行卡 + 财务专用（税号/社会信用代码），财务文档**强制**脱敏；
- 对 `\ufffd` replacement char 的处理很细腻（PDF 解码占位符不误杀）。

**企业做法**
- NER 模型做 PII 识别（regex 漏召回：段号、住址、邮箱旁的姓名等）；
- 内容审核（moderation API）作为独立阶段；
- 数据脱敏策略引擎（按 KB/租户配置可逆脱敏 vs 不可逆脱敏 + 原文保管策略）。

**好处**：合规是 RAG 上生产的硬门槛；脱敏策略与检索层解耦才可审计。

**可学习 / 重构建议**
- regex PII 对内网/演示够用；若文档来源不可控（用户上传任意内容），**建议加一层 LLM-based PII 复核**（只对 regex 漏检的高风险 doc_type 抽样），成本可控。
- `_masked_text` 通过 metadata 传递（`filter.py:229`）属于隐式契约，重构时值得显式化（返回值携带），避免 metadata 键漂移。

---

### 6. 分块：策略路由 + parent-child + 语义切分 + 内容派生 chunk_id

**现状实现**（`chunking.py` + `ChunkStrategyRouter`）
- `parse_and_chunk`：parser → cleaner → StructureAnalyzer → **按 doc_type + 结构完整度路由切分策略**（fixed/step/semantic/legal/financial…，各有独立测试）；
- parent-child 双粒度（PARENT/LEAF_CHUNK_TOKENS），检索命中 leaf、溯源用 parent；
- 语义切分：中文句界 + 相邻句向量余弦骤降检测边界（`_detect_boundaries`）；
- **内容派生 chunk_id**：`md5(doc_id:anchor:内容hash)`——文档局部微变后未受影响 chunk 的 id 稳定（幂等重索引），这是生产级细节；
- `_enrich` 截断防护：超 `MAX_CHUNKS_PER_DOC` 截断 + 全链路 `chunks_truncated` 标记，写进 registry `quality_issues` 可追溯。

**企业做法**
- Layout-aware chunking（按版面元素切块而非纯文本）——你们的 AST 方案本质就是这个；
- Late chunking（先长文本嵌入再切）、contextual retrieval（Anthropic：每 chunk 前 prepend LLM 生成的上下文）——你们用「模拟问题前缀 + 章节标题注入」达到了类似效果；
- chunk 策略 A/B：企业会对不同 doc_type 跑评测对比 chunk 策略收益（你们有黄金集评测，具备条件）。

**好处**：chunk 质量直接决定召回上限；策略可路由意味着新增文档类型不用改老代码。

**可学习 / 重构建议**
- **这是全链路最不该动的部分**，设计已超前于多数开源方案。
- 一个量化建议：用现有评测集对 `LEAF_CHUNK_TOKENS=512`/overlap=50 做一次**参数扫描实验**（chunk_size × 策略 的 mrr/recall 矩阵），把默认值从经验值变成实验值。

---

### 7. 元数据：LLM 标注内联在索引关键路径

**现状实现**（`_build_doc_metadata`，indexer.py:1067）
- 异步并发执行：摘要、关键词（规则+DeepSeek）、doc_type 分类、复杂度、实体、模拟问题（按 chunk 对齐 `questions_by_chunk`）、章节归属注入；
- LLM 失败 → 降级默认值继续（"元数据构建失败（使用默认值）"）；
- Qwen chunk 级关键词已关闭（文档级覆盖足够，省本地推理耗时）——有「按需开关」的取舍记录。

**企业做法**
- **元数据 enrichment 异步化**：先完成最小可检索集（chunk+向量）立即入库，metadata 由独立 worker 异步补齐后增量更新——索引延迟与 LLM 延迟解耦；
- metadata 版本化：策略升级后批量回填（re-enrichment job）。

**好处**：上传→可检索的首字节延迟从「LLM 延迟 + 索引延迟」降为「索引延迟」；LLM 抖动不影响入库成功率。

**可学习 / 重构建议**
- 现状「同步标注 + 失败降级」是保守正确的选择（保 metadata 完整性）；若用户反馈上传慢（LLM 标注通常占索引总时长的大头），**异步 enrichment 是首选重构**：向量先入库，metadata 完成后 `update_metadata` 增量补写。改造点集中在 indexer metadata 段落与 registry 的状态机（可复用 pending_review 中间态）。

---

### 8. Embedding：批处理 + 指数退避 + 整批降级逐条 + 预嵌入直传

**现状实现**（`_embed_with_retry`，indexer.py:1000）
- 按 `embed_batch_size`（尊重实现声明的最优批，对齐 DashScope 上限）批推理；
- 每批 `EMBED_RETRY_MAX` 次重试，指数退避 + 随机抖动（防 thundering herd）；
- 整批耗尽 → **降级逐条**隔离失败点（坏的 chunk 不拖累整批）；
- 成功向量**直传** `vectordb.add_documents(embeddings=...)`，避免 langchain 内部二次嵌入（成本省一半）；
- 每个 chunk 前拼模拟问题前缀（Document Expansion，注释标注召回 +10-15%）。

**企业做法**
- 完全同构：批处理、退避重试、失败隔离是标配；额外有 embedding 结果缓存（同文本 hash 不重复嵌入，跨文档/跨版本复用）和**模型版本迁移流水线**。

**好处**：嵌入是索引链路最贵的计算，批+缓存+直传三件事都是真金白银。

**可学习 / 重构建议**
- 批+重试+降级+直传已是企业水准，**不需要重构**。
- 两个可加项：
  1. **embedding 结果缓存**：chunk 内容 hash → 向量，落 Redis/SQLite。收益场景：只改了文档一部分时，未变 chunk 全部免嵌入（你们的稳定 chunk_id 正好可以做缓存键，天然契合）；
  2. **embedding 模型迁移任务**：registry 已记录 `embedding_model`，但换模型后没有「存量文档批量重嵌」的调度。企业做法是后台 re-embed 队列 + 新旧双 collection 灰度切换 + 完成后别名切换。当前换模型只能全量重建。

---

### 9. 多存储写入一致性：四写 + 先写后删 + 版本快照 + 补偿删除

**现状实现**（`reindex_file` / `_index_file_inner` / `_remove_document`）
- 一次索引落四个存储：向量库（chunk 级）、doc_db（doc 级全文截 16K）、chunk_store（SQLite 原文）、BM25；
- 重索引策略：
  - 普通文档**先写后删**：新索引成功后按旧 chunk_id 精确清理，消除检索空窗；失败时旧数据完整保留（严格优于先删后写）；
  - 财务文档走**版本快照**：旧 chunk 标 `is_latest=False` 保留历史版本向量（快照失败兜底删除）；
  - dedup 命中（skipped）时不动旧数据（防凭空丢文档）；
- 任一步失败触发补偿删除（`_remove_document` 清向量库+doc_db+chunk_store+BM25 四处）；
- 写入 doc_db 前 metadata 深拷贝展平（ChromaDB 不支持嵌套 dict）、超长全文截断打标 `doc_level_truncated`。

**企业做法**
- **Outbox / 两阶段提交**：写 staging（shadow collection / 临时命名空间）→ 校验 → 一次原子切换（alias/版本指针）；
- 变更日志（event sourcing）：所有写操作先落事件表，消费者保证至少一次应用 + 幂等去重。

**好处**：分布式多存储下没有真事务，企业用「可验证的中间态 + 原子指针切换」把不一致窗口压到秒级，且任何一步失败都可重放。

**可学习 / 重构建议**
- 现状补偿逻辑覆盖面已经很好，**单实例下不建议大改**。
- 但要注意一个现存缺口：**四写之间没有版本一致性校验**——补偿删除失败只 `logger.warning`（`_remove_document` 全部 swallow），长期运行可能积累「registry 说删了、向量库还在」的幽灵 chunk。企业会加**定期对账 job**（registry chunk_ids ↔ 向量库实际 diff）。你们的 `consistency.py` / `test_index_consistency.py` 方向已经在做，建议把它从测试工具升级为**周期任务**（低频每日跑，差异告警）。

---

### 10. 任务编排与崩溃恢复：asyncio task + registry 状态机

**现状实现**
- `asyncio.create_task(_run_index_background)`，任务引用集防 GC（`_background_index_tasks`）；
- registry 状态机：`uploading → parsing → embedding → active/failed/deleted/pending_review`；
- 索引开始写 parsing 占位行，失败标 failed（防启动恢复无限重试），崩溃后启动 `sync()` 按 INTERRUPTED_STATUSES 恢复；
- 失败差异化：`ChunkingEmptyError`（业务失败）保留源文件；其他异常清理（覆盖场景除外——P0-X 防丢用户源文件）。

**企业做法**
- **持久化任务队列**：任务表/消息队列记录任务全量参数 + 进度 + 重试次数，worker 无状态，崩溃重启**从断点续跑**而不是重新索引整个文件；
- 重试策略显式化（退避 + 最大次数 + 死信队列 + 人工介入）。

**好处**：大文件索引 10 分钟、进程在 9 分钟崩——企业做法能从「解析完成」续跑，你们要整文件重来（好在有 SHA256 去重兜底不会重复入库，但计算白费）。

**可学习 / 重构建议**
- registry 状态机 + 启动恢复已经是「半个持久化队列」，方向对。
- 最小改造：**把 trace 的 stage 级进度（parse/chunk/embed 已完成的 chunk 数）落 registry 或任务表**，恢复时支持「已完成阶段跳过」。比引入消息队列便宜一个量级，解决 80% 的痛点。
- 暂不建议上 Kafka/RabbitMQ——单机部署引入 MQ 是负资产；等真的多实例再说。

---

### 11. 进度与观测：SSE + Redis 镜像 + 全链路 trace + 操作日志

**现状实现**
- SSE：`GET /upload/{id}/stream`，事件流 uploading→parsing→chunking→embedding→done/duplicate/error；15s keepalive 防代理断连；断连 finally 清队列；
- 哨兵模式：终态放 None 哨兵而非 pop 队列——**晚到的订阅者（小文件索引先完成）仍能拿到终态**，这是很容易踩的坑被提前想到了；
- Redis Hash 镜像进度（600s TTL，跨实例可查），写盘放线程池防阻塞事件循环；
- 每次索引一棵 trace（6 个标准 span），每阶段真实耗时回传前端 `stage_elapsed`；
- 操作日志（`_safe_log_op`）含 chunk_count/file_hash/doc_type/llm_tokens 全量 detail。

**企业做法**
- 进度通道与实例解耦：Redis pub/sub 或 WebSocket 网关，任意实例可订阅任意任务；
- OpenTelemetry 标准化导出 + 指标（索引时长分布、失败率、队列深度）+ 告警。

**好处**：多实例部署不挑订阅实例；指标化才能做 SLO。

**可学习 / 重构建议**
- 单实例下当前设计很完整。**多实例的第一道裂缝就在这**：`_progress_queues` 是内存 dict，上传 POST 打到实例 A、SSE 订阅打到实例 B → 永远收不到事件。升级路径现成：SSE handler 改从 Redis 读取（镜像已在写）或加 Redis pub/sub 转发，改动量小。

---

### 12. 缓存一致性：答案缓存原子失效

**现状实现**：`invalidate_kb` 用 **Redis 版本号原子递增**（不是删 key），本地版本缓存同步更新；索引成功后失效对应 KB。
**企业做法**：同构（版本号/epoch 失效是标准手法，避免大量 DEL 的雪崩）。
**结论**：已是最佳实践，无需动。一个细节：失效粒度是 KB 级——单文档更新使整个 KB 缓存失效，命中率有损失；企业按 doc_version 细粒度失效，可作为后续优化。

---

## 二、汇总对照表

| 环节 | 现状水位 | 与企业差距 | 重构优先级 |
|---|---|---|---|
| 流式上传+原子落盘 | ★★★★★ | 缺断点续传（50MB 内不痛） | P3 不动 |
| 错误返回风格 | ★★☆ | 200+ok:False → HTTP 状态码+错误码 | **P1**（改接口契约，前端联调） |
| 文件锁/并发控制 | ★★★★★ | 单实例最佳；多实例需迁 Redis | 条件触发（多实例时 P0） |
| 去重体系（4 层） | ★★★★☆ | SimHash 指纹不持久、near_dup 无后续策略 | **P2** |
| 解析+OCR | ★★★★☆ | OCR 失败无二级降级 | P2 |
| 清洗+PII | ★★★★ | regex PII 漏召回；无内容审核层 | P2（来源不可控时升级） |
| 分块策略 | ★★★★★ | 仅缺参数实验数据 | P3 不动 |
| LLM 元数据 | ★★★★ | 内联关键路径，拖慢可检索时间 | **P1**（异步 enrichment） |
| Embedding | ★★★★★ | 缺结果缓存、模型迁移流水线 | **P1**（缓存）/ P2（迁移） |
| 多存储一致性 | ★★★★ | 补偿失败静默；缺周期对账 | **P1**（对账 job） |
| 任务编排/恢复 | ★★★☆ | 阶段级断点续跑缺失 | P1（最小改造）/ P3（MQ） |
| 进度/观测 | ★★★★☆ | SSE 绑定单实例内存 | 条件触发（多实例时 P0） |
| 缓存失效 | ★★★★★ | KB 级粒度粗 | P3 |

---

## 三、建议的重构清单（按性价比排序）

**第一梯队（低风险高收益，单实例即可做）**
1. **embedding 结果缓存**：以稳定 chunk_id/内容 hash 为键落 Redis，重索引免重复嵌入。与现有内容派生 chunk_id 天然契合，改动集中在 `_embed_with_retry` 前后。
2. **周期性索引对账 job**：registry chunk_ids ↔ 向量库/doc_db/BM25 实际内容 diff，差异告警。把 `consistency.py` 从测试工具升级为运行时守护（低频每日）。
3. **错误码体系**：上传接口改 HTTP 状态码 + 结构化错误码（保留 error message），为网关监控告警铺路。
4. **near_dup_id 接入 pending_review 流程**：近似文档不再静默标记，进入审核队列。

**第二梯队（有明确收益，改造成本中等）**
5. **元数据异步 enrichment**：最小可检索集先入库，LLM 标注后增量补写。索引延迟与 LLM 延迟解耦。
6. **阶段级断点续跑**：stage 进度落库，崩溃恢复跳过已完成阶段。
7. **SimHash 指纹持久化 + LSH**：去重跨请求/跨重启生效。
8. **OCR 二级降级链**：rapidocr 失败 → dashscope → 明确报错，配置化。

**条件触发（多实例部署时才做，现在做是过度设计）**
9. `.lock` 文件 → Redis 分布式锁（SET NX + watchdog 续期，心跳逻辑可平移）。
10. `_progress_queues` → Redis pub/sub / 从进度镜像读。
11. 任务编排 → 持久化队列/任务表。

**不建议动的**：分块策略体系、四层去重架构、先写后删 + 版本快照、SSE 哨兵模式、原子落盘流程——这些是项目里最接近或达到企业水准的部分。

**重构风险提示**（结合本仓现状）：
- 上传链路有成熟的测试矩阵（`test_rag_upload_*`、`test_rag/` 约 30 个测试文件），重构前先跑基线（记得 `--no-cov`）；
- 链路上有多个并发会话在动的可能（本仓存在并发提交先例），动手前先 `git status` 确认没人在改同批文件；
- 错误码体系改动会破坏现有前端解析逻辑，需要前后端同一批改。

---

# 补充篇一：切分策略深挖（2026-09-14 第三轮分析）

> 范围：`chunking.py`（8 策略 + Router）、`structure_analyzer.py`、参数体系（LEAF=500 / PARENT=2000 / OVERLAP=50 / SEMANTIC 阈值 0.45）。总评：**策略分层和资格校验（legal 需真有条款、faq 需真有 QA 节点）是企业水准的防误路由设计**，以下按影响排序。

## C1. 🟠 三类 doc_type 的 parent-child 检索实际失效

Step（sop/training）、Legal（legal/contract_template）、QA（faq）三条策略产出的 leaf **`parent_chunk_id` 恒为空串**（`_make_leaf_chunk` / `QAChunkStrategy` 均不建 parent）：
- Legal 一份 50 条的合同 = 50 个孤儿 leaf，无表级/文档级 parent 可溯源；
- Step 每章节一个 leaf 同样无 parent；
- 检索层「命中 leaf → 取 parent 扩上下文」的机制对这三类文档**静默退化**。

修法：Legal 用「文档标题+条款区间」造虚拟 parent（如每 5-8 条一个 parent）；Step 用文档标题做单 parent；QA 用文档级 parent。改动集中在三个策略内部，Router 不动。

## C2. 🟠 表格双层切分只给 financial 用了，其他类型硬切风险

`FinancialTableChunkStrategy` 的「表级摘要(parent) + 行级 kv(leaf) + numeric_values 元数据」是全项目最亮的切分设计——但 `STRUCTURE_STRATEGIES` 里 6 个走 `StructureChunkStrategy` 的类型（policy/product_spec/listing…）遇到表格 leaf 超长时，`_emit_leaf` 会拿 `RecursiveCharacterTextSplitter` **按字符硬切，把表格行从中间切断**（与财务策略注释里批判的旧行为完全一致）。参数表/配置表在非财务文档里很常见。

修法：把表格双层切分抽成共享 helper（`_split_table_leaf`），`StructureChunkStrategy._emit_leaf` 遇 `node.type == "table"` 时走它。经验证财务策略代码结构允许直接抽取（`_table_nl.py` 的 normalize/summary/kv 函数本就是独立的）。

## C3. 🟡 Fixed/Recursive/Semantic 丢弃 section 上下文 + indexer 脆弱映射

- 三个无结构策略产出 `section_title=""`（AST 里明明有 section 归属，`_merge_small_texts` 合并时丢了）；
- indexer 补救方式是 `full_text.find(ch.page_content[:80])` 首次出现位置映射章节（indexer.py:702）——文本重复出现（模板化措辞）或清洗后文本变化都会**错配章节**；
- 叠加一个真 bug 味道的细节：pipeline 里清洗过一次（`parse_and_chunk` 内 `node.text = cleaner.clean(...)`），indexer 的 clean span **又对 chunk 清洗第二次**（indexer.py:489-498）——重复劳动 + 二次变更会让 find() 映射进一步失真。

修法：`_merge_small_texts` 返回 `(merged_text, 来源 section 列表)`，段落级携带 section_title；indexer 的 find() 映射降级为「chunk 无标题时才用」的兜底；二次清洗删除（pipeline 已清洗，indexer 的 clean span 只保留统计）。

## C4. 🟡 Semantic 策略：无 parent、无碎片合并、重组丢标点

- 无 parent（同 C1）；
- 不做 `_merge_small_texts`——小叶子逐一成 chunk，Fixed/Recursive 都修了的碎片化问题 Semantic 没修；
- 句子按 `[。！？；]` 切、重组只用 `"。".join()`——原文的 ！？；全部变句号（信息损失小但无理由）；
- `_detect_boundaries` 后的 end 查找最坏 O(n²)（叶内句子数有限，影响小，顺手修）。

## C5. 🟢 chunk_id 的 anchor 稳定性 trade-off（记录不动）

`_chunk_id = md5(doc_id:anchor:内容hash)`：Structure 的 anchor 含 section 标题——**标题改名后未变内容的 chunk_id 也会漂移**（跨版本追踪断链）。换成「层级+序号」anchor 可解，但会让"同标题同内容"的不同章节撞 id。属于已文档化的 trade-off（注释写得很清楚），**保持现状**，仅记录。

## C6. 🟢 死配置与空壳分支清理

- `GENERAL_CHUNK_OVERLAP=100`、`CHUNK_SIZE`、`POLICY_MAX_CHUNK_SIZE`、`PROJECT_CHUNK_SIZE` 等导出常量疑似无消费方（待 grep 确认后清理）；
- Router 的 LLM Assisted 分支是空壳（`ENABLE_LLM_CHUNKING` 默认关且无实现体）——要么实现要么删。

## C7. 建议的参数实验（承接正文 P3）

现有默认值（LEAF=500/OVERLAP=50/SEMANTIC 阈值 0.45）全是经验值。用黄金集跑「LEAF × 策略 × 阈值」网格（≈15 组），每组记录 mrr/recall/chunk 数。产出一张参数-效果表贴进 docs，之后调参有据可依。

---

# 补充篇二：同类内容优化机会总表（三轮分析合并）

> 编号溯源：S0-S3 见补充篇三（元数据深挖，S0 为 P0 修复项）；C1-C7 见补充篇一（切分深挖）。实施顺序以《RAG优化实施计划.md》为准。

| 类别 | 项目 | 成本 | 优先级 |
|---|---|---|---|
| 修复 | S0 模拟问题链路恢复 | 中 | P0 |
| 切分 | C1 三策略 parent 补齐 | 中 | P1 |
| 切分 | C2 表格双层切分通用化 | 中 | P1 |
| 增强 | contextual prefix（摘要+章节拼嵌入文本） | 低 | P1（与 S0 同改造点） |
| 增强 | doc_db 文本增强（summary/sections 头拼） | 低 | P1 |
| 增强 | 表格 chunk LLM 描述（仅 table chunk） | 低 | P2 |
| 增强 | 实体元数据接检索 filter | 中 | P2 |
| 增强 | time_refs 时效衰减 | 中 | P2 |
| 切分 | C3 上下文携带 + 二次清洗删除 | 低 | P2 |
| 切分 | C4 Semantic 三项修补 | 低 | P2 |
| 基建 | embedding 结果缓存（键=chunk_id） | 中 | P1 |
| 基建 | 索引对账 job | 中 | P1 |
| 基建 | 摘要缓存 Redis 化 | 低 | P2 |
| 工程 | 错误码体系（前后端联动） | 中 | P2 |
| 工程 | 元数据异步 enrichment | 高 | P3 |
| 工程 | near_dup 接 pending_review | 低 | P2 |
| 实验 | C7 参数网格实验 | 中 | P2 |

---

# 补充篇三：元数据生成深挖 + 内容增强类优化全景（2026-09-14 二次分析）

> 针对「元数据生成还能怎么优化、还有哪些同类内容优化」的深挖。这一轮在 `_build_doc_metadata`（indexer.py:1067-1365）、`llm_enrichment.py`、`keyword.py`、`metadata.py` 里发现了一个**静默失效的召回特性**，比所有"优化"都优先。

## S0. 🔴 先修这个：模拟问题（Document Expansion）已静默失效

**证据链**（三处代码互相印证）：
1. `_build_doc_metadata` 中 `enriched: dict | None = None` 且**无任何赋值路径**（indexer.py:1228，F1 注释自认"合并分支为死分支"）；
2. `enrich_metadata_llm`（`questions_by_chunk` 的唯一生产者）全仓**无有效调用方**——只剩 indexer 的 import（为 gather 分支预留）和定义本身；
3. 于是 `questions_by_chunk` 恒为 `[]` → chunk metadata 永远不写 `simulated_questions` → `_embed_text_for` 的「【相关问题】前缀」**永远不触发**（indexer.py:944-949）。

**后果**：
- `_embed_with_retry` 注释宣称的「Document Expansion 召回率 +10-15%」**实际是死的**；
- chunk_store 的 `simulated_questions` 列（v2→v3 迁移专门加的）恒存 `'[]'`；
- 前端 `rag_documents.py:539` 仍在读取该字段（恒空）。
- 这就是 F1 重构（并发化 summary/keywords/entities）时**只迁移了三个任务、漏了第四个（问题生成）**的回归。测试没兜住：`test_embed_batching.py` 是手工塞 metadata 测前缀拼接，不测真实生产链路。

**修复方案（三选一，按推荐排序）**：
1. **轻量恢复（推荐）**：新写一个 `generate_chunk_questions(chunks_text)`，只做问题生成这一件事，作为 gather 的第四个并发任务。prompt 只带 chunk 预览（原合并调用要带全文+全部 chunk，长文档 prompt 巨大、长度不符就整单 fallback——这大概率是它当初被摘掉的真实原因），每 chunk 独立校验，坏一个不影响其他。成本：每文档 1 次 LLM 调用。
2. **零 LLM 恢复**：用规则造伪问题（「{section_title}是怎么规定的？」「什么是{top_keyword}？」）。零成本零延迟，质量低于 LLM 版但立即可用，可以做成第一档、LLM 版做成可配置的第二档。
3. **不恢复，删干净**：如果决策就是不要这个特性，把 `_embed_text_for` 前缀逻辑、chunk_store 列、前端读取一起清掉，别留三处死代码迷惑后人。

**配套补一个回归测试**：断言「索引完成后 chunk_store.simulated_questions 非空（LLM 可 mock）」，防再次静默失效。

## S1. 元数据生成本身的优化空间

现状盘点：`_build_doc_metadata` 已做对的事——三路并发（摘要/关键词/实体）、qwen 关闭思考模式（8.5s→1.3s）、<1KB/2KB 门槛跳过 LLM、LLM 失败逐级降级、质量门禁、MinHash 指纹持久化、metadata_fingerprint 预留 schema 迁移。在此基础上还有：

| # | 优化点 | 现状问题 | 做法 | 收益 |
|---|---|---|---|---|
| S1-1 | **摘要缓存跨进程持久化** | `_summary_cache` 是进程内 FIFO dict，重启即失、多 worker 不共享 | 缓存键已有（text_hash），落 Redis 即可（代码里 `_write_progress_redis` 的模式现成） | 重索引/批量重建时省重复 LLM 调用 |
| S1-2 | **doc_type LLM 复验挪出关键路径** | 规则分类 confidence<0.3 时**串行**调 LLM 复验（indexer.py:1174-1197），低置信文档索引时长被 LLM 延迟直接拉长 | 复验并入 gather 并发组；或并入元数据异步化（见 S1-4） | 低置信文档索引提速数秒 |
| S1-3 | **doc_db 文本增强** | doc 级入库是"全文头 16K 字符"裸文本；LLM 花钱生成的 summary/sections/keywords **没参与** doc 级向量 | doc_db 文本改为 `summary + "\n\n章节：" + sections + "\n\n" + 全文头 N 字` | Stage1 文档定位质量提升，成本为零 |
| S1-4 | **元数据异步 enrichment**（承接正文 P1） | LLM 标注内联在「上传→可检索」关键路径 | 最小可检索集先入库，metadata 完成后 `registry.update` + 向量库 `update_metadata_where` 增量补写；可复用 `pending_review → active` 状态迁移表达"待补齐" | 可检索延迟与 LLM 延迟解耦 |
| S1-5 | **metadata_fingerprint 驱动的批量回填** | 字段已入库但无人消费 | schema 升级时按 fingerprint 差异批量 re-enrich 旧文档（后台任务、限速） | 元数据策略升级不再需要全量重建 |
| S1-6 | **classify 规则表与 LLM 复验的评测** | 分类准确率无数据支撑 | 黄金集评测加 doc_type 维度，规则 vs LLM 复验对比 | 决定复验阈值 0.3 是否合理 |

## S2. 其他「内容增强类」优化（同一族的机会点）

按「已有雏形 → 只差一步」优先：

1. **Contextual Prefix（Anthropic contextual retrieval 的零 LLM 版）**：你已有文档级 summary + 每 chunk 的 `section_title` + `chunk_keywords`，但**只存 metadata 不进 embedding 文本**。给每个 chunk 拼 `「{文档摘要}｜{章节路径}」\n{正文}` 再嵌入，相当于穷人版 contextual embeddings——成本为零（复用现有字段），对标方案（Anthropic）要每 chunk 一次 LLM 调用。**与 S0 恢复模拟问题是同一改造点**（都在 `_embed_text_for`），建议一次做完，用评测集对比三种前缀（无前缀 / 仅模拟问题 / 摘要+章节+模拟问题）的召回差异。
2. **实体元数据用于检索**：`entities_nested`（person/org/regulation 结构化实体）已抽取入库，但检索层没人消费。最小落地：检索时 query 里命中的实体名作为 metadata filter 的加分项/过滤项（你已有 `kb_filter` 体系，加一个维度即可）。再往上是 GraphRAG-lite（实体共现边），不建议现在做。
3. **时间元数据用于时效性**：`time_refs` 已抽取。政策/合规类文档可加"时效衰减"：检索 rerank 阶段对过期文档降权（需要 `time_refs` + 当前时间 + doc_type 规则表）。你的主场景是政策知识库，这条收益直接。
4. **表格语义增强**：cleaner 刻意跳过 table 节点，表格以结构化形式进 chunk——但 LLM 从不生成"这个表在说什么"的描述。财务报表类 chunk 的嵌入质量是已知短板（数字序列语义稀疏）。做法：对 `chunk_type` 为 table 的 chunk 用一次 LLM 生成一句表格描述拼进 embedding 文本（只对表格 chunk 花 LLM 钱，量小）。
5. **文档级 FAQ 入库**：模拟问题是 chunk 级；policy/sop/faq 类文档可再生成 3-5 条文档级 FAQ 存 doc_db，命中 FAQ 问题的 query 在 Stage1 就能精准定位文档。

**不建议做的**：GraphRAG 全套（实体图构建+社区检测，维护成本远超当前规模收益）、late chunking（需要换 embedding 用法，动了 `_embed_text_for` 的前提）、HyDE（属检索侧改造，与上传链路解耦，另行立项）。

## S3. 动手顺序建议（结合正文清单合并排序）

1. **S0 修复模拟问题链路**（回归修复，最优先）+ 配套回归测试
2. **S2-1 Contextual Prefix**（与 S0 同一改造点，一次改完跑评测对比）
3. 正文 1-1 embedding 结果缓存（键=稳定 chunk_id，与 S1-1 摘要缓存共用 Redis 基建）
4. **S1-3 doc_db 文本增强**（零成本）
5. 正文 1-2 索引对账 job
6. 正文 1-3 错误码体系（涉及前端联调，单独排期）
7. S2-2 实体检索 → S2-3 时间衰减 → 正文 1-4 near_dup 接 pending_review
8. 元数据异步 enrichment（S1-4）作为中期项

1-4 每项做完跑一次黄金集评测（`test_eval_golden` 之外建议单建上传链路冒烟），用数据决定下一项。

---

*生成于 2026-09-14，基于当日代码实测阅读（rag_upload.py / indexer.py / pipeline.py / chunking.py / filter.py / cleaner.py / doc_registry.py / bm25_store.py / answer_cache.py / ocr.py / llm_enrichment.py / metadata.py / keyword.py）。*

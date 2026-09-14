# RAG 优化实施计划

> 配套文档：《RAG上传链路企业实践对照与重构建议报告.md》（分析依据：S0-S3 = 元数据深挖，C1-C7 = 切分深挖）。
> 原则：每阶段独立可交付、可回滚；每阶段结束跑测试基线 + 评测对比；一次只动一条链路。
> 执行约定：测试一律 `--no-cov`（pytest.ini 55% 覆盖率门槛 + 沙箱 safe-delete 冲突）；提交前 `git status` 确认无并发会话改动；commit 后 `git log --oneline -3` 复核。

---

## 阶段 0：基线与防护网（约半天）

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| 0.1 | 跑全量测试基线：`./.venv/Scripts/python.exe -m pytest backend/tests/ -q -p no:randomly --no-cov` | 基线通过数记录（已知 3274 passed / 104 failed，失败全部在 eval_golden 属环境依赖） | 基线数字入档，后续阶段对比用 |
| 0.2 | 跑黄金集评测基线（S0 修复前后要对比召回） | mrr/recall 基线数字 | 数字入档 |
| 0.3 | 新增 S0 回归测试骨架：索引完成后断言 chunk_store.simulated_questions 非空（LLM mock） | `tests/rag/test_simulated_questions_pipeline.py` | 测试当前状态应为**失败**（证明 bug 存在），修复后转绿 |

## 阶段 1：S0 修复 + Contextual Prefix（同一改造点，核心阶段）

**1.1 恢复模拟问题生成链路**（方案：轻量 LLM 版，规则版做降级）
- 新增 `backend/rag/preprocessing/question_gen.py`：`generate_chunk_questions(chunks_text, doc_type) -> list[list[str]]`
  - LLM 路径：一次调用，prompt 只带 chunk 预览（300 字/chunk，沿用 `enrich_metadata_llm` 的编号格式与清洗/校验/兜底逻辑，复用 `_fallback_questions`）；复用 `invoke_metadata_llm`（qwen 关思考模式）
  - 规则降级路径：LLM 失败/未配 → `「{section_title}是怎么规定的？」/「什么是{keyword}？」`（从 chunk metadata 取，零 LLM 成本）
- 接线点：`_build_doc_metadata` 的 gather 并发组加入第四任务 `task_questions(chunks_text)`（与 summary/keywords/entities 真正并行）
- 清理：`_build_doc_metadata` 中 `enriched` 死分支与 `enrich_metadata_llm` import 一并删除（F1 遗留）
- 配置：`ENABLE_SIMULATED_QUESTIONS`（默认 on）+ `QUESTION_GEN_MAX_CHUNKS`（超长文档只对前 N chunk 生成，控成本）
- 改动文件：`indexer.py`、新 `question_gen.py`、`config/rag.py`
- 验收：0.3 测试转绿；上传一份测试文档，chunk_store 中 simulated_questions 非空；SSE done 事件正常

**1.2 Contextual Prefix（embedding 文本前缀）**
- `_embed_text_for` 改为三级前缀拼接：`【文档】{summary 前 100 字}｜【章节】{section_title}\n【相关问题】{...}\n\n{正文}`（字段缺失自动跳过对应段）
- doc_db 的 doc 级文本同步增强（对应报告 S1-3）：`summary + "\n\n章节：" + sections + "\n\n" + 全文头 N 字`（改 `_index_file_inner` 的 doc_level_text 构造处）
- 验收：黄金集评测对比（无前缀 / 仅模拟问题 / 全前缀三组数据），mrr/recall 有据可查；结果记入 docs
- 注意：前缀改变向量 → **已索引文档需重索引才生效**，提供批量重索引脚本入口（复用现有 `reset_rag_index.py` 思路，不自动全量重建，由用户决定）

**1.3 Token 计量补齐**（2026-09-14 增补；分析结论：设施已完备，只补三处缺口）

现有计量盘点（不用重建）：
- ✅ LLM chat：proxy `_record_tokens` 自动落 llm_usage_store + Prometheus，且带 `current_trace_context()` 的 trace_id——上传链路走 proxy 的调用天然可按索引任务聚合
- ✅ Embedding：`_TrackedEmbedding` 包装单例（indexer/semantic 切分用的 `get_embedding()` 都已计量，估算法 3 chars/token）
- ✅ OCR：`parser/ocr.py` 已写 llm_usage_store（component="ocr"）
- ✅ 看板：`/observability/tokens` 读 llm_usage 明细

待补缺口（本任务范围）：
- a) **S0 question_gen 出生即计量**：新模块必须走 `backend.infra.llm` proxy（1.1 的实现约束，不是独立工作）；
- b) **trace/registry 的 llm_tokens 口径补全**：`index_metadata` span 的 metrics 现在只含关键词路径 tokens；`build_llm_summary` 与 question_gen 的 tokens 汇总进同一 metrics（Store 层已有，只补 trace 视图），registry `llm_tokens` 同步；
- c) **ChatOllama 直连路径统一**：doc_type LLM 复验的 `ChatOllama` 直连不走 proxy（本地模型零成本，计量价值低）——统一收敛到 `invoke_metadata_llm` 入口，保证未来切 cloud 模型时不漏记；
- d)（可选，P3）`_TrackedEmbedding` cloud 模式改读 DashScope 真实 usage 替代估算。

验收：上传一份文档 → llm_usage 表出现 metadata/summary/questions 各一行、trace_id 与 indexer trace 一致；index_metadata span metrics 含三路 tokens 合计。

## 阶段 2：切分策略修补（C1/C2/C3/C4，每项独立提交）

**2.1 C2 表格双层切分通用化**（P1，影响面最大）
- 从 `FinancialTableChunkStrategy._split_with_tables` 抽出 `_split_table_leaf(table_node, sec, path, file_path) -> list[Document]` 共享 helper（放 `chunking.py` 模块级）
- `StructureChunkStrategy._emit_leaf`：`leaf.type == "table"` 且有 rows → 走 helper；financial 策略改为调用同一 helper（行为不变，消除重复）
- 验收：新增 `test_table_split_general.py`——policy 类型文档含超长表格时断言：行不被切断、有 table_summary parent、行级 leaf 有 numeric_values；跑 `test_excel_parser.py`/`test_financial*` 确认无回归

**2.2 C1 三策略 parent 补齐**
- Legal：每连续 N 条款（默认 5，配置 `LEGAL_CLAUSES_PER_PARENT`）造一个虚拟 parent（文本=该区间条款拼接），leaf.parent_chunk_id 指向它
- Step：文档级单 parent（文本=各章节标题列表 + 文档标题）
- QA：文档级单 parent（文本=全部问题列表）
- 验收：三类样例文档索引后断言 parent_chunk_id 非空且可在 chunk_store 查到 parent 行

**2.3 C3 上下文携带 + 去二次清洗**
- `_merge_small_texts` 返回 `(merged_text, [来源 section_title...])`；Fixed/Recursive 策略给 chunk 填 `section_title`（多来源时取首个 + `section_mixed=true`）
- indexer 的 find() 章节映射改为兜底（chunk 已有 section_title 时跳过）
- 删除 indexer `_index_file_inner` 的第二次 `DocumentCleaner` 全量清洗（pipeline 已清洗；clean span 保留、只记统计不再改文本）——**需先 grep 确认无其他调用方依赖二次清洗的副作用**
- 验收：切分测试全绿；上传样例文档 section_title 正确率抽查

**2.4 C4 Semantic 修补**
- 补 `_merge_small_texts`（复用 2.3 返回结构）；句子重组保留原标点（记录切分标点随句存储）；end 查找改 O(n) 预计算下一边界数组
- 验收：`test_semantic_chunking.py` 全绿 + 新增小叶子合并断言

## 阶段 3：基建项（缓存 + 对账）

**3.1 embedding 结果缓存**（P1）
- Redis 键 `rag:emb:{model}:{sha256(embed_text)}`（embed_text 即含前缀的最终文本，TTL 30 天），读写在 `_embed_with_retry` 批循环内（批量 mget 风格，pipeline 化）
- 命中率打 trace metrics（`cache_hit` 计数）
- 验收：同文档重索引，第二次 embedding 调用次数为 0；`test_embed_batching.py` 适配

**3.2 索引对账 job**（P1）
- 新 `backend/rag/indexing/reconcile.py`：按 registry 逐文档 diff chunk_ids ↔ 向量库 where doc_id 实际 ↔ chunk_store ↔ BM25，差异写 operation_log（result="reconcile_diff"）+ warning
- 入口：复用启动时 `cleanup_stale_upload_artifacts` 的调度模式，每日低频跑一次（环境变量开关，默认 on，单批限 200 文档防占资源）
- 验收：人工制造一处不一致（手动删向量），job 能发现并留痕

**3.3 摘要缓存 Redis 化**（P2，顺手）
- `build_llm_summary` 的进程内 dict 缓存改 Redis（键含 text_hash），保留内存层做 L1

## 阶段 4：工程化收尾（按需排期）

- **4.1 near_dup → pending_review**：`near_dup_id` 非空时 registry 状态置 `pending_review`（而非 active），检索层过滤该状态；前端文档列表已有状态展示可直接复用
- **4.2 错误码体系**：`/upload` 等业务错误改 HTTP 状态码 + `{"code": "RAG_XXXX", "message": ...}`；**需与前端同一批改**，单独排期 + 前端回归（tsc/vitest/curl 冒烟）
- **4.3 实体检索 filter / time_refs 时效衰减 / 表格 chunk LLM 描述**：P2 增强项，逐项小步交付，每项带评测对比
- **4.4 元数据异步 enrichment**：P3，等 1-3 阶段稳定后单独立项（涉及状态机语义变化：先 active 后补 metadata vs 先 pending_review 后 active，需讨论）
- **4.5 死代码清理**：`ENABLE_LLM_CHUNKING` 空壳分支、疑似死配置（`GENERAL_CHUNK_OVERLAP` 等，先 grep 确认）、`enrich_metadata_llm`（阶段 1 已删）

## 阶段 5：企业级模型用量审计与统一上报（2026-09-14 增补，依赖阶段 1.3）

> 需求：本地与 Cloud 两类模型调用统一统计——Token 计量、调用日志、日志上传/上报；企业侧经统一日志中心归集，完成用量统计、成本核算与审计留存；前端提供企业级审计（按模型来源、租户/用户、时间、调用类型查询/聚合/导出/告警）。

**设计基线（最大化复用现有设施，不重建）**
- `llm_usage_store` 已是"每次调用一行"的明细表，含 `provider`（local/cloud 区分来源）、`component`（调用类型雏形）、`trace_id/session_id`、tokens/cost/duration；
- `user_id` 已有解析链路（请求体优先、信任网关注头次之，`app/api/schemas.py`）；
- TokenTracker JSONL 可作归档兜底通道；
- 注意：当前观测栈仅 Postgres（无 Prometheus/Alertmanager），告警做在应用内。

### 5.1 Schema 扩展与身份上下文贯通
- `llm_usage` 表加列：`tenant_id`、`user_id`、`call_type`（标准枚举：chat / embedding / rerank / ocr / metadata / questions / summary / eval…）、`request_id`、`env_mode`；SQLite 迁移沿用 chunk_store v2→v3 的模式（带版本号幂等）；
- 调用上下文贯通：扩展 `current_trace_context` 或新增 usage ContextVar 携带 (tenant_id, user_id, call_type)；各入口注入——chat API（已有 user_id）、上传链路（call_type=metadata/questions/summary，user_id 从上传请求取）、evaluation；
- **网关侧扩展点（api-gateway，Java Spring Cloud Gateway）**：网关在计量链路中的角色是**可信身份源，不是计量点**（token 用量在 LLM 响应体/流式帧里，网关侧计量既脆弱又会漏掉不经网关的内部调用）。具体动作：`UserHeaderGlobalFilter` 增加 `X-Tenant-Id` 可信头注入（与现有 `X-User-Id` 同模式，注意与 `gateway.auth.enabled` 短路逻辑的互斥语义），Python 侧 `TRUST_TENANT_HEADER` 对应消费——为 5.1 的 tenant_id 贯通提供可信来源；
- 本地模型成本口径：定价配置表（model → 单价），local 调用也产出"虚拟成本"，与企业云成本同一张报表可加总。

### 5.2 计量覆盖收口（与 1.3 合并实施验收）
- 全部调用路径统一走 proxy `_record_tokens` 或 TokenTracker；ChatOllama 直连收敛到 `invoke_metadata_llm`；本地调用同样写明细行（cost=虚拟成本）。

### 5.3 统一上报与审计留存（日志上传核心）
- **LogShipper**：从 llm_usage_store 增量读取（watermark = 已上报最大行 id），批量上报企业日志中心。传输端点可配置三选一：HTTP webhook（JSON 批）/ OpenTelemetry exporter / Kafka；无远端时降级为按小时落归档 JSONL（离线收取）；
- 可靠性：批量 + 重试 + 指数退避；**失败不动 watermark**；幂等键 = usage 行主键（日志中心侧去重）；频控（限速）防打爆；
- 留存策略：本地 retention 默认 90 天（可配）+ 按月归档导出（CSV/JSONL）；删除前必须归档成功；
- 降级追补：日志中心不可达 → 本地继续积累，恢复后自动追补，不丢不重。

### 5.4 聚合统计与成本核算 API
- `GET /observability/usage/aggregate`：按 provider(local/cloud) / model / tenant / user / call_type / 时间窗 聚合 tokens、cost、调用次数（SQL group by + 环比）；
- 月度成本核算报表：按租户/模型汇总，CSV 导出（本地与云成本分列 + 合计）。

### 5.5 审计明细查询 API
- 明细查询：时间范围 × 模型来源 × 租户 × 用户 × 调用类型 × trace_id 组合过滤 + 游标分页（复用 cs_admin 的分页模式）；
- CSV 流式导出（防大结果集撑内存）；
- 管理端接口挂 admin 鉴权（审计数据含用量敏感信息）。

### 5.6 前端审计页面（Next.js，企业级）
- 用量看板：tokens/成本趋势图（local 与 cloud 分色）、Top 租户/模型/调用类型排行表；
- 审计明细页：多维筛选器 + 结果表 + CSV 导出按钮；
- 告警规则页：规则 CRUD（维度 × 窗口 × 阈值，如「租户 X 单日 cloud tokens > N」/「月度成本 > $N」）；
- 校验三件套：`npx tsc --noEmit` / `npx vitest run` / 全路由 curl 200；dev 启动记得 `NEXT_DIST_DIR` 指向新空目录。

### 5.7 应用内告警引擎
- 规则表（维度、窗口、阈值、启停）+ 定时评估（复用 `progress_queue_gc_loop` 的后台任务模式）；
- 触发写 alert 记录（审计）+ 通知（先 webhook，邮件后置）；
- 未来接入 Prometheus/Alertmanager 后，`token_usage_total` 指标规则作为基础设施层补充，两者不冲突。

**验收**
- 同一次上传 + 一轮对话后：llm_usage 明细含 local/cloud 两类行，tenant/user/call_type 字段齐全，可按 trace_id 串起单任务全链路用量；
- 模拟日志中心宕机 → 本地积累 → 恢复 → 追补完成且无重复（幂等键生效）；
- 前端「cloud/local × 租户 × 周维度」聚合数字与 DB group by 完全一致；导出 CSV 可正常打开；
- 告警规则能触发、留痕、通知。

**决策点与风险**
1. **SQLite vs Postgres**：明细量 > 百万行/月或聚合并发高时迁 PG（PG 已在观测栈内）。先 SQLite + 复合索引顶住；LogShipper 屏蔽存储差异，迁移不影响上报架构；
2. **租户体系对接**：user_id 已走「请求体 > 网关 X-User-Id 可信头」链路（api-gateway `UserHeaderGlobalFilter` 注入）；tenant_id 随 5.1 在网关同步注入 `X-Tenant-Id`，企业 SSO/租户体系接入时只改注入源；
3. **上报协议**：先 webhook/OTel 二选一，不要一上来绑 Kafka——协议抽象成 transport 接口，后换无损。

### 5.8 网关访问日志审计（API 层，与 token 明细分表）
- **定位**：调用审计（谁在何时调了哪个 API、状态码、耗时），与 token 用量明细**分表分语义**——审计答"谁调了什么"，计量答"花了多少 token/成本"，通过 request_id / trace_id 关联但不混表；
- 采集：api-gateway（Spring Cloud Gateway）加 AccessLog GlobalFilter（复用 `AuthenticationGlobalFilter` 的 order 模式），记录 method/path/status/耗时/X-User-Id/X-Tenant-Id/request_id，写 gateway 本地 JSONL；
- 上报：复用 5.3 的 LogShipper（新增一个 transport 通道/schema 类型），gateway 日志由其自身上报任务或由 Python 侧代收转发（实施时二选一，优先 gateway 自报，避免 Java↔Python 耦合）；
- 保留策略与 5.3 一致（retention + 归档先行）；
- 前端：审计页加"API 调用"tab（5.6 范围内，非本期必做可后置）。

## C7 参数网格实验（阶段 2 完成后做）

- 维度：LEAF_CHUNK_TOKENS ∈ {384, 500, 768} × 策略（结构性文档为主）× SEMANTIC_SIMILARITY_THRESHOLD ∈ {0.40, 0.45, 0.50}
- 跑黄金集，产出参数-效果表（mrr/recall/chunk 均长）存 docs；若默认值非最优，改 `.env` 默认并记录

---

## 里程碑与风险

| 里程碑 | 内容 | 回滚策略 |
|---|---|---|
| M1（阶段 0-1） | S0 修复 + 前缀体系 + 计量补齐 + 评测数据 | `ENABLE_SIMULATED_QUESTIONS=off` + `_embed_text_for` 单函数还原；向量库无破坏性变更 |
| M2（阶段 2） | 切分修补 | 每项独立提交，revert 单提交即可；chunk_id 稳定性不受影响（anchor 协议不变） |
| M3（阶段 3） | 缓存与对账 | 环境变量开关逐项关闭；缓存 TTL 自然过期 |
| M4（阶段 4） | 工程化 | 4.2 涉及前端契约，前后端同一 PR 回滚 |
| M5（阶段 5） | 企业级用量审计与统一上报 | Shipper/告警/前端各有开关，逐项关闭；schema 加列向后兼容（新列可空），不需回滚迁移 |

**关键风险**：
1. 阶段 1.2 改变 embedding 输入 → 新旧向量并存期检索行为不一致。对策：前缀改动后**必须**触发受影响 KB 重索引再对比评测，不做"新旧混合"评测；
2. C3 删除二次清洗前必须确认 `test_e2e_pdf_docx_index.py` 等端到端用例对清洗效果的断言，防止统计口径变化导致假失败；
3. 本仓存在并发会话，每个任务动手前 `git status` + 看 mtime，避免覆盖他人改动；
4. eval_golden 的 104 个既有失败与本次无关，对比基线时只看增量。

---

*计划制定于 2026-09-14。执行时按阶段推进，每阶段完成更新本文件的进度标记（[ ] → [x]）。*

## 进度跟踪

- [~] 阶段 0：基线与防护网 —— 0.3 回归测试已落地并确认红（6 failed / 2 passed，红=bug 证实）✅；0.1 全量基线运行中；0.2 黄金集 mrr/recall 基线待 0.1 完成后适配处理
- [ ] 阶段 1：S0 修复 + Contextual Prefix + Token 计量补齐 1.3（M1）
- [ ] 阶段 2：切分策略修补 C1/C2/C3/C4（M2）
- [ ] 阶段 3：缓存 + 对账（M3）
- [ ] 阶段 4：工程化收尾（M4）
- [ ] 阶段 5：企业级用量审计与统一上报 5.1-5.8（M5）——建议 M3 后与 M4 并行
- [ ] C7 参数网格实验

### 执行日志
- 2026-09-15 00:07-03:05：遭遇两轮工作区批量删除事件（997/427 文件，memory 已有 4 波记录的再现）。并发会话自行恢复 + 本会话 git restore 双路径处置，3 个 M 文件备份于 .workbuddy/backup_20260915/。第二轮删除时间点与后台 pytest 启动（02:55:18 mtime 批量刷新）强相关，**根因待查**：全量测试运行可能触发删除，基线完成后需核对 git status。
- 2026-09-15 03:05：S0 事实链在恢复后代码上复核成立；0.3 测试 6 红 2 绿符合预期，bug 证实。0.1 基线启动。
- [ ] 阶段 2：切分策略修补 C1/C2/C3/C4（M2）
- [ ] 阶段 3：缓存 + 对账（M3）
- [ ] 阶段 4：工程化收尾（M4）
- [ ] 阶段 5：企业级用量审计与统一上报 5.1-5.7（M5）——建议 M3 后与 M4 并行
- [ ] C7 参数网格实验

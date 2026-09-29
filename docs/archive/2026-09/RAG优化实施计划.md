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

- [x] 阶段 0：基线与防护网 ✅（2026-09-15 03:45 完成）
  - 0.1 基线：**3716 collected / 29 failed / ~3687 passed**，失败构成：evidence_gate×11、0.3 预期红×6（现已转绿）、evaluation 系列×7、零星×5。与旧基线（104 failed eval_golden）差异源于并发会话提交 60b96f5/c1561ae 对测试集的变更
  - 0.3 回归测试：`tests/rag/test_simulated_questions_pipeline.py` 红转绿全程留痕（6 红 → 8 绿）✅
  - 0.2 适配：黄金集 mrr/recall 对比顺延至 1.2 前缀改完的验收环节执行（需要 RAG 全栈运行，与 1.2 验收合并避免重复起栈）
- [~] 阶段 1：S0 修复 + Contextual Prefix + Token 计量补齐 1.3（M1）——**代码全部交付**（2026-09-15 04:00-04:10）
  - [x] **1.1 S0 修复**：新增 `question_gen.py`（LLM 主路径 + 规则降级 + 成本护栏 + proxy 计量）、config 开关、indexer gather 第四任务接线 + tokens 双路汇总 + enriched 死分支清理。验收：0.3 测试 8/8 绿；相关回归 84 passed
  - [x] **1.2 Contextual Prefix**：`_embed_text_for` 三级前缀（【文档】summary前100字/【章节】/【相关问题】，缺段自动跳过）+ `_embed_with_retry(doc_summary=)` 传参 + doc_db 文本增强（新纯函数 `_build_doc_level_text`：summary/章节拼头部，总长约束 16K、正文最少保 2000）。新增 `test_contextual_prefix.py` 12 项契约
  - [x] **1.3 计量补齐**：a) question_gen 走 proxy ✅（随 1.1）；b) llm_tokens 双路汇总进 trace/registry 口径 ✅；c) ChatOllama 直连确认为**伪缺口**（本地模型无 usage/无成本），已加注释固化"切 cloud 必须走 proxy"约束；d) embedding 真实 usage 读取可选后置
  - [ ] **1.2 运行时验收（待用户环境）**：黄金集三组对比（无前缀/仅问题/全前缀）需 RAG 全栈 + 评测集重索引，与 0.2 的 mrr/recall 基线合并执行；已索引文档需重索引前缀才生效
  - 验收汇总：contextual_prefix 12 + simulated_questions 8 + embed_batching 6 + chunking/semantic 回归 → **37 passed 全绿**
- [~] 阶段 2：切分策略修补 C1/C2/C3/C4（M2）
  - [x] **2.1 C2 表格双层切分通用化**（2026-09-15 04:20）：抽模块级 `_split_table_node` 共享 helper；`StructureChunkStrategy._emit_leaf` 表格 leaf 走双层切分（policy 等六类型不再硬切断行）；financial 改调 helper + 补 reporting_period/fiscal_year/is_latest 特有元数据（行为不变，chunk_id 与旧实现一致）。验收：test_table_split_general 8/8 绿 + 切分系回归 47 passed
  - [x] **2.2 C1 三策略 parent 补齐**（2026-09-15 04:35）：新 helper `_attach_virtual_parents`（group_size=None 文档级单 parent / N 条款分组）；Step 文档级单 parent、Legal 每 5 条款一 parent（`LEGAL_CLAUSES_PER_PARENT`）、QA 文档级单 parent；config/__init__ 导出补齐。旧测试总数断言更新（chunk 总数 = leaf+1）。验收：test_virtual_parents + legal/step/qa/router 回归 29 passed
  - [x] **2.3 C3 上下文携带 + 去二次清洗**（2026-09-15 04:45）：新 helper `_iter_leaves_with_section` + `_merge_small_with_section`；Fixed/Recursive 抽公共循环 `_split_unstructured`，chunk 注入 section_title/section_path（跨 section 合并打 `section_mixed`）；indexer find() 章节映射降级为兜底（策略注入的标题权威优先）；**删除 indexer 二次清洗**（pipeline 已清洗，span 保留标记 skipped）。验收：test_section_context 8 项 + 回归全绿
  - [x] **2.4 C4 Semantic 修补**（2026-09-15 04:50）：碎片合并（小叶子先按预算合并再做语义切分）、`_split_sentences_keep_punct` 保留原标点（！？；不再被 "。" 覆盖，fake embedding 映射同步适配）、边界查找 O(n) 预计算（原 next() 最坏 O(n²)）、Semantic chunk 注入 section_title
  - [x] **阶段 2 最终回归**：backend/tests/rag/ 全目录 **418 passed / 0 failed**（含 test_faq_routing 旧总数断言更新 2 处）
- [x] 阶段 3：缓存 + 对账（M3）✅（2026-09-15 05:15）
  - [x] **3.1 embedding 结果缓存**：新 `embed_cache.py`（Redis 键 `rag:emb:{model}:{sha256(embed_text)}`，TTL 30 天，mget/pipeline 批量，软失败全降级）；`_embed_with_retry` 缓存优先——只对 miss 子集真实嵌入+回填，逐条路径同步接入；命中率写 span metrics + 日志。config：`RAG_EMBED_CACHE_ENABLED`/`RAG_EMBED_CACHE_TTL_SECONDS`。验收：test_embed_cache 7 项（roundtrip/损坏值/软失败/二次调用零嵌入/前缀变更新键/metrics）
  - [x] **3.2 索引对账 job：取消实施**——`consistency_sweep_loop`（consistency.py，6h 周期 check+repair，孤儿向量/BM25 幽灵清扫）**已挂载 server 运行时**，功能覆盖计划目标。分析报告正文"缺周期对账"结论**有误**（当时未发现 server 挂载点），以此修正。重复造轮子是负债，已删除试写的 reconcile.py
  - [x] **3.3 摘要缓存 Redis 化**：`build_llm_summary` 升级 L1 内存 + L2 Redis 双层（键改 sha256——原内置 hash 受 PYTHONHASHSEED 影响跨进程不稳定；TTL 7 天，软失败）。验收：test_summary_cache 4 项（键稳定/L2 命中跳 LLM/回写/故障降级）
  - 验收汇总：**24 passed**（embed_cache 7 + summary_cache 4 + embed_batching 6 + indexer_pipeline 回归）
- [~] 阶段 4：工程化收尾（M4）
  - [x] **4.1（状态置位）near_dup → pending_review**（2026-09-15 05:35）：`register()` 带 `near_dup_id` 时 status 置 `pending_review`（原静默 active 入库）；chunk metadata 注入 `review_status`（供检索 where 过滤与前端展示）。验收：test_review_status 3 项
    - [x] **4.1b 检索层软过滤**（2026-09-15 05:45，**运行时验收通过** 06:45）：
      - 实现注意：只包 hybrid_retrieve 不够——rag_search/retrieve_knowledge 直连 chunk_retriever；已在 CustomRetriever.retrieve 公共出口统一过滤（幂等）
      - 运行时验证：上传原版+副本采购合同 → MinHash sim=1.00 触发 near_dup → registry pending_review → 检索"采购合同违约责任"仅返回 active 旧版 1 条，两条待审文档被过滤（ReviewFilter 日志确认）`hybrid_retrieve` 拆包装层（覆盖 enhanced/fallback/SQL bypass 全部出口），返回前剔除 `pending_review` 文档。**关键设计：不用向量库 where $ne 过滤**——存量 chunk 无 review_status 字段会被 $ne 全部误杀；改用 registry pending_review doc_id 集合（60s 进程内缓存，空集合同样缓存零开销，registry 不可用跳过过滤保可用性）。验收：test_review_filter 7 项 + hybrid 回归
  - [x] **4.5 死代码清理**（2026-09-15 05:35）：Router 空壳 LLM Assisted 分支删除；4 个零消费死配置删除（grep 实证 0 消费）+ ENABLE_LLM_CHUNKING/LLM_CHUNK_MIN_CHARS；CHUNK_SIZE 保留（rag_documents.py 在用）。验收：config 导入正常 + 回归 23 passed
  - [ ] 4.2 错误码体系：需前端联动，单独排期
  - [x] **4.3 增强项**（2026-09-15 05:55，数据可达性优先，策略层待评测数据）：
    - 4.3c 表格行 LLM 描述：新 `table_describe.py`（行级 kv → 一句话语义，一次调用批量产出，TABLE_DESC_MAX_ROWS=20 护栏，走 proxy 计量，失败无前缀降级）；`_embed_text_for` 加【表格】前缀段；indexer 接线（table_row chunk 识别 → 批量生成 → metadata.table_desc）
    - 4.3a 实体落库：结构化 entities JSON 注入 chunk metadata（500 字截断）
    - 4.3b 时间引用落库：time_refs 注入 chunk metadata；自动过期判定/降权**后置**——extract_time_refs 为非结构化字符串，解析规则与有效期阈值需业务输入
    - 验收：test_table_describe 6 项 + 回归；**rag 全目录 445 passed / 0 failed**
  - [ ] 4.4 元数据异步 enrichment（P3，单独立项）
- [ ] 阶段 5：企业级用量审计与统一上报 5.1-5.8（M5）——建议 M3 后与 M4 并行
- [ ] C7 参数网格实验

### 执行日志
- 2026-09-15 00:07-03:05：两轮工作区批量删除事件（997/427 文件，memory 已有 4 波记录的再现）。并发会话自行恢复，3 个 M 文件备份于 .workbuddy/backup_20260915/。第二轮与后台 pytest 启动时刻相关性**未被证实**：本轮基线全程 18 分钟运行后删除数保持 0，pytest 触发假设排除，根因仍指向并发会话的 git/清理操作
- 2026-09-15 03:05：S0 事实链复核成立；0.3 测试 6 红 2 绿，bug 证实
- 2026-09-15 03:45：0.1 基线完成（3716/29），删除监控全程 0
- 2026-09-15 04:00：**1.1 S0 修复交付**——question_gen 模块 4 项契约测试绿、接线测试 2 项绿、相关回归 84 passed。修复内容：gather 第四任务 task_questions、llm_tokens 双路汇总（1.3b 前半）、enriched 死分支与 enrich_metadata_llm 引用清除
- 2026-09-15 04:10：**1.2 + 1.3 交付**——_embed_text_for 三级前缀（向后兼容：无 metadata 纯正文）、_embed_with_retry(doc_summary=) 传参、_build_doc_level_text 纯函数（doc_db 增强，单测 12 项含长度约束/正文保底）、1.3c 确认伪缺口并注释固化约束。最终回归 37 passed（含 chunking/semantic 链路）。**阶段 1 代码全部完成**，仅剩 1.2 黄金集三组对比为运行时验收项（需全栈 + 重索引）
- 2026-09-15 06:10-06:45：**git 收口（3 批提交）+ 运行时验收（部分通过）**
  - 提交：0e3fb22（核心代码 10 文件）/ 4a84ebe（测试 14 文件）/ 3e1e36c（文档）/ f755fd5（4.1b base.py 补全）
  - 运行时验收通过：S0 模拟问题（chunk_store 10/10 有内容）、C3 section_title、4.1 near_dup→pending_review（MinHash sim=1.00 触发）、4.1b 检索软过滤（检索仅返回 active 旧版）
  - 运行时验收受阻：3.1 缓存命中率——Docker Desktop 崩溃重启 2 次，Redis 容器随之离线（REDIS_ENABLED 已改 true、question_gen 批级缓存已补）；4.3c 表格描述——企业文档目录暂无财务 xlsx
  - 环境注意：:8000 后端与并发会话共用（workflow 请求 observed）；.env REDIS_ENABLED 已改 true
- 2026-09-15 04:20-04:50：**阶段 2 全部交付**——2.1 表格双层切分通用化（_split_table_node 共享 helper，六类型不再硬切断行）；2.2 三策略虚拟 parent（_attach_virtual_parents，Step/Legal/QA 检索退化修复）；2.3 上下文携带（_iter_leaves_with_section + _merge_small_with_section + _split_unstructured 抽取，indexer find() 降级兜底、二次清洗删除）；2.4 Semantic 三修补（碎片合并/标点保留/O(n) 边界）。**最终回归 backend/tests/rag/ 全目录 418 passed / 0 failed**（旧总数断言更新 5 处：step×2/legal×1/faq×2）。阶段 2 完成，下一步阶段 3（embedding 缓存 + 对账 job）
- [ ] 阶段 4：工程化收尾（M4）
- [ ] 阶段 5：企业级用量审计与统一上报 5.1-5.7（M5）——建议 M3 后与 M4 并行
- [ ] C7 参数网格实验

# 进展与续工

## 已核实

- GitHub main仍为b434138；共享工作区main为0828e58且有用户原有修改，未覆盖。
- 内置工具创建工作树成功，聊天注册因thread not found失败；attach重试也失败，继续使用返回的独立目录，未创建第二工作树。
- 现有`agent_memory`同时有客服、旅游、企业制度及测试KB，不能整库删除。只读盘点时`rag_eval_kb`有15份Active、1份pending_review，chroma169条、doc_db16条；未执行任何删除。
- 管理端真实页面缺少测试库选项；不是据此直接修改权限：原规则仅super_admin可访问test受众KB。
- 用户指定桌面临时凭据文件后，实际登录local_super_admin成功；只读确认其role=super_admin、must_change_password=true。业务接口403来自首次改密门禁。
- 管理端原登录页没有消费mustChangePassword，也没有改密页面，导致用户进入工作台后看到空列表。后端保护本身没有被绕过。

## 已实施的最小前端修复

- 沿用门户既有改密页和后端`/auth/change-password`接口，在管理端补入口。
- 临时密码登录跳到改密页；路由守卫阻止待改密用户加载其他业务页。
- 改密成功换发令牌，合并后端部分userInfo摘要，保留角色/租户；失败不清改密标记。
- 上传文件选择补CSV，并让提示列出已支持的XLSX/CSV；后端原本已登记两者，不改变上传权限。
- 未更改任何真实密码、must_change_password字段、账号角色、生产Prompt或Provider配置。

## 实际验证

- Vitest定向3文件11项通过：认证会话、改密成功/失败、待改密路由及原有RoleGate。
- 管理端TypeScript `--noEmit --pretty false --incremental false`退出0；git diff --check通过。
- 实际浏览器在修复版`http://localhost:3201/login`登录临时账号后跳到`/change-password`，新密码字段未填、未提交，已交给用户操作。
- 截图：`D:/tmp/rag-universal-phase1-20261019/first-password-change.png`。
- 复用现有Next15.5.28依赖；跨盘Junction导致Next编译失败，改为复制已缓存依赖到本工作树后HTTP200并实际浏览器通过，未重新下载。
- 曾尝试门户3000辅助入口，因旧缓存Next14与跨盘依赖问题失败，已停止该进程。临时管理端3201运行于exec session74845，保留供用户改密；其服务端API Key仅在进程环境，未输出。
- Next自动修改的frontend/tsconfig已恢复；frontend-admin/tsconfig自动生成的临时类型目录变更不纳入功能提交，预览停止后恢复。

## 免费解析/过滤证据

执行当前真实parse_and_chunk_full、_filter_index_chunks、apply_index_chunk_identity及质量审计；禁用网络外呼，未调用Provider、未连接业务库写数据、未生成向量。

| 文件 | 过滤后Leaf | Parent | 覆盖缺口/硬异常/悬空引用 |
| --- | --- | --- | --- |
| faq_only.docx | 40 | 1 | 均0 |
| preamble.docx | 6 | 4 | 均0 |
| contract_table.docx | 4 | 1 | 均0 |
| sku.csv | 501 | 1 | 均0 |
| multi_sheet.xlsx | 89 | 4 | 均0 |

Artifact：`D:/tmp/rag-universal-phase1-20261019/free-inspection.json`、`free-postfilter.json`及对应chunk明细。这里只验证免费切分/过滤，不能当作真实上传、模型元数据和向量发布通过。

## 2026-10-19 隔离上传栈建成（ragu1）

- 新增 `部署/rag-universal-phase1-20261019/docker-compose.isolated.yml`（compose 项目 `ragu1`）：独立网络/卷/容器（ragu1-*），服务=postgres(pgvector)+redis×3(缓存/认证/broker)+db-migrate+app+rag-service+rag-index-worker+apisix；网关 127.0.0.1:19080、PG 对账通道 127.0.0.1:15433；worker 并发 1+2GiB（同 rag-t2 档）。不写 container_name、不与共享栈/PR#52 栈冲突。
- 镜像 `agent-platform:ragu1-0423d69`：完整构建被 Clash fake-ip 截断（198.18.0.13/2001:2::e 对 mirrors.aliyun.com apt 大文件 500/502，宿主 curl 小响应 200 但 build 容器断），改用 `Dockerfile.overlay` 在 agent-app:latest（2026-10-13 构建，依赖声明 2026-10-10 后未变）上叠加最新代码层，GIT_COMMIT=ragu1-0423d69 注入。
- 运行代码版本证据：`GET :19080/health` → `{"status":"ok","build":{"commit":"ragu1-0423d69"}}`，迁移 applied=101；pipeline.py/filter.py/indexer.py 三文件 host=app=worker SHA256 一致。
- 隔离库初始化：init_db 全量迁移 rc=0；`init_clone_config.py`（D:\tmp\rag-universal-phase1-20261019\）复制配置面：账号 local_super_admin（哈希=用户改密后，role=super_admin/tenant=default/must_change_password=false）、llm_providers 12、凭据密文 10、llm_models 20、角色绑定 7、专项绑定 2、model_price 239（approved 123）、预算 8、prompts 74+aliases 41+runtime_state 1、keyword_rules 133、metadata 规则 133。**不复制任何业务/运行数据**：doc_registry/chunk_store/rag_vectors/rag_index_runs 全 0。
- 已知差异（记录不修复）：共享库 prompt_release_records 有 b434138 迁移没有的 model_snapshot 列（结构漂移，审计表非运行必需）未复制；sys_config 由服务自建幂等表（JWT_SESSION_GUARD_MODE 回退 env=enforce 等价）；schema_consistency 报 tasks/checkpoints 缺失属惰性建表。
- 付费前预检七项全绿：①manifest SHA256 五份源文件一致（UF-03-01/05-01/08-01/11-01/12-01）；②rag_eval_kb audience=test 仅 super_admin；③角色=embedding qwen3.7-text-embedding / rerank qwen3.7-text-rerank / chat doubao-seed-2.0-mini，approved 价格齐（embedding/rerank 0.5 CNY/1M tokens）；④预算 tenant_default|default 432 CNY/日 hard，worker bind_request_budget(upload_id) 归因；⑤隔离库 RAG 四表全 0；⑥PARSABLE_EXTS 含 docx/csv/xlsx；⑦API/Worker 代码指纹一致。网关无凭据 401（fail-closed），LLMRegistry 在 app+worker 均注入 10 份凭据（Fernet 解密成功）。
- 管理端 3201 已重启（`D:\tmp\rag-phase1-admin-isolated.py`），BFF 指向 19080 隔离网关；HTTP 200。旧 3201（指向共享 9080）进程已停。
- 等待：用户在 127.0.0.1:3201 登录（账号 local_super_admin，密码=自己改密后的新密码；隔离库哈希与共享库同源）。登录后进知识库工作台上传首批 5 份（faq_only.docx/preamble.docx/contract_table.docx/sku.csv/multi_sheet.xlsx，选「统一 RAG 评测知识库」）。

## 2026-10-19 浏览器真实上传两次失败与修复（82580ed）

- 用户已在 3201 登录（隔离库账号，哈希同源），首次上传 hit 数据卷属主问题：named volume 首挂挂载点由 docker 以 root 创建，appuser 写入 `PermissionError: Errno 1`（共享卷是历史期以正确属主创建故无此问题）。已 `docker exec -u root chown 10001:10001 /app/data /app/.cache` 修复并在 Dockerfile.overlay 预建挂载点固化。上传 HTTP 层此前已 200（auth=jwt user=279、KB 可见集正确），无脏数据残留。
- 权限修复后第二次上传：POST /rag/upload 200、tasks 表由 TaskService 幂等自建、Celery 入队成功（upload_id=dd13239a8dfe, task=1ef9a1b1），但 Worker 初始化 `RAGPipeline(mode="runtime")` 抛 `向量库没有可用于 BM25 校验的 chunk`，重试 3/3 终态 service_unavailable——**空库冷启动鸡生蛋缺陷**（main b434138 既有）：全新库被防混代校验拒绝，首个上传永远失败。
- 根因修复 `82580ed fix(rag)`：`_init_retrievers` 区分「空向量+空快照=冷启动放行（空索引，检索空结果；hybrid 对 bm25 兜底召回已有异常软降级）」与「空向量+磁盘快照非空=向量数据丢失维持拒绝」；非空库 N-1 快照校验/自愈/版本检查语义不变；只读冷启动不再回退 loader 语料（避免混代）。回归：test_pipeline_runtime_modes 新增冷启动放行+孤儿快照拒绝 2 例，定向 10 passed（原 8 例全过）。
- 修复已重建 overlay 镜像部署进 ragu1（`/health` 报 `ragu1-0423d69-fix1`），app/rag-service/worker 三服务 healthy，doc_registry=0 干净起点保留。
- 等待用户第三次重传（faq_only.docx 等 5 份）。

## 2026-10-19 faq_only.docx 真实入库质量对账 PASS（6/6 检索命中）

- 用户重传 faq_only.docx：终态 `published`（upload_id=6aae4bfec52c, trace=bb9de1034b2b, gen=2f2889790dd3），质量记录 nodes=81/chunks=41/anomalies=0。
- 结构对账与免费检查基线精确一致：40 leaf + 1 parent（rag_vectors.granularity）+ 1 文档级向量 = 42 条，vector(1024) HNSW 与专项绑定 dimensions=1024 一致；parent_doc_id 表达父子；chunk_id 41/41。
- BM25 快照发布 41 chunks 同代发布（publish 三段日志）；注册表 1 行 active；失败任务（dd13239a8dfe）无任何业务残留（doc_registry/chunk_store/rag_vectors/llm_usage 均 0），仅 rag_index_runs failed + tasks FAILED 审计行与预算科目行（used=0）。
- 元数据：section_title/keywords/doc_type/department 41/41 规则全覆盖；**llm_keywords 全空**——FAQ 全文采样 <2000 字符走首句降级（ENABLE_LLM_METADATA_EXTRACT 采样门，设计内省钱路径），FAQ 类短文档永不触发 LLM 元数据增强，是否需按 doc_type 豁免阈值待拍板。
- 检索证据：UFQ-001~006 六题经 rag-service /retrieve（system_subject 内部主体，未伪造用户身份）实测 6/6 PASS，required_fact 全命中，index_status=ok；脚本 D:\tmp\rag-universal-phase1-20261019\verify_faq_retrieval.py（坑：docker exec 需 -i 才透传 stdin 给 curl）。
- 费用：累计 10 次 embedding（4 索引批+6 查询向量化）共 0.001195 CNY，role/stage/model/trace 归因齐全；无 LLM 生成调用（短文档降级）。
- 测试集数据问题（非平台）：cases.jsonl 中 faq_only 题目 required_docs 写 `faq_only.md`，实际 fixture 为 `.docx`，评测按文件名匹配会失配，下批修测试集。

## 2026-10-19 Trace 详情模型/Token/成本展示修复（0400aa0）

- 用户指出 RAG Trace 详情页 Tokens/成本/模型恒「-」。排查：数据层无缺失——llm_usage 台账按 trace_id 归因完整，且通用 trace 端点已有 `_backfill_usage` 读时回填机制；断层在 ①回填模型名只认 `component=llm` 的行，纯 embedding 的索引 trace 模型名留空；②管理端 RAG Trace 详情概览读的是聊天链路的 span 内嵌 llm_tokens 字段，未读已回填的 `trace.usage`。
- 修复：backfill 无 llm 行时回退按 total_tokens 最大行取模型/provider；rag-traces 端点对齐通用端点补 usage 回填；前端概览改读 trace.usage（by_component 合计 tokens、cost_cny 本位币优先），模型字符串形态兼容。中途曾另写 `_llm_usage_attach` 聚合注入，识别为与既有 backfill 平行的重复实现后撤回，最终改动收敛在既有机制上。
- ragu1 实测（faq_only.docx trace bb9de1034b2b）：Tokens 2,374 / 成本 ¥0.0012（台账 6 次调用）/ 模型 qwen3.7-text-embedding 全部呈现；文档类型「常见问题」/业务域 hr 原本就有。
- 遗留待拍板：页面「质量评分偏低 (0/100)」为误报——quality span 无 score 字段时前端按 0 计并触发 <60 警告，而实际质量审计 anomalies=0；需决定「无评分不警告」还是「索引链路补写 score」。

## 2026-10-19 多模型用量聚合（c0ae665）

- 用户指出概览只显示一个模型、而一次上传会用到多个模型。回填机制此前仅按组件聚合、代表模型只取 token 最大单行。现 usage.models=每组件代表模型清单、usage.models_detail=组件×模型全量明细（calls/tokens/cost_cny），前端模型卡显示全部模型+「N 个模型 · M 次调用」，多模型时卡片内展开逐模型分布；向量嵌入 span 模型名取 embedding 组件。
- faq_only.docx 单模型（embedding）显示准确：短文档未触发 LLM 元数据（2000 字符采样门）。多模型形态待剩余文件（合同/扫描件）上传后自然验证。

## 当前等待与下一步

- 用户在3201完成首次改密后再继续浏览器；新密码不必提供给助手。旧3200尚未部署修复。
- 先确认实际运行后端代码版本及测试KB/租户隔离，再准备可恢复清理和小批上传；此前没有删除旧库或产生本轮模型费用。
- 第一阶段正式数据集注册、浏览器上传、向量/元数据质量检查尚未完成；不宣称测试集接入或RAG验收完成。

## 运行代码漂移（付费前必须处理）

- 现有app镜像未挂载源码。对三份运行文件做LF归一化SHA256后，与b434138的pipeline/filter/indexer均不一致。
- 运行中pipeline没有`structured=True`清洗调用，indexer没有`cleaner_content_loss`付费前检查；确认没有当前main的#90关键保护，不是单纯行尾不同。
- 不直接向这套旧后端付费上传来冒充当前main验收。后续需在隔离栈运行最新代码、关联同版本Worker，再通过浏览器上传。
- 用户反馈管理端没有改密提示后，已实际复现并修复上述前端闭环；修复页截图已提供，等待用户亲自设置新密码。
- 上传CSV选择改动尚未在实际文件选择器中验收；不能把这两行源码修改报告为浏览器上传通过。

## 2026-10-19 治理改造并入：基线核查结论（三路并行探查，行号以当前工作树为准）

### P0-1 切分路由现状（Explore A）
- 现状是 doc_type 主轴+结构信号豁免：pipeline.py:108 词袋分类 → pipeline.py:111 路由 → chunking.py:1808 `STRUCTURE_STRATEGIES.get(doc_type)` 主路由（v2, chunking.py:1687/1703-1821）。
- 必改点：①主路由键反转（1808）改 StructureProfile 直判（qa_pair_count≥1→QA、legal_clause_count≥3→Legal、step_heading_count≥3→Step、table→表格行级）；②豁免表 _STRONG_SIGNAL_FIELD（1671-1684）删 doc_type 查表；③结构只当否决者（1787-1806）无正向启用路径；④未映射 doc_type 即使强结构信号也递归（1815-1820，偏离最大）；⑤financial 特例需「表格+数值列」结构条件替代（1605）；⑥QA completeness 直通车（structure_analyzer.py:122-123）是 faq 偏置。
- 可复用事实源齐全：StructureProfile（structure_analyzer.py:24-44,61-81）、表格/qa/条款判定（chunking.py:231-252）、ChunkRouteDecision 审计（chunking.py:1621-1652）。生产调用 indexer.py:1269 不传 hint。
- QA 虚拟 parent：QAChunkStrategy.split 末尾无条件触发（chunking.py:1174），group_size=None 全文档一个 parent（anchor=faq）。

### P0-2 检索过滤现状（Explore B）
- 检索路径 doc_type/business_domain 过滤 **100% 来自 QueryAnalyzer 对查询文本的猜测**（query_analyzer.py:61-108 to_metadata_filter；DOMAIN_RULES 262-282、DOC_TYPE_RULES 285-294），无用户显式入口（/rag/search 只收 kb_id）。
- 流向：①pipeline.py:1060-1065 合入 metadata_filter（硬过滤下推 pgvector）；②Evidence Gate 二次硬门 DOC_TYPE_MISMATCH（hybrid.py:328-354、operations.py:257-272、开关 DOC_TYPE_COVERAGE_REQUIRED 默认 true config/rag.py:483）；③软打分 enhanced_hybrid_retrieval.py:372-381。
- 历史漏召回事故：TD-13「发票认证」被猜 security→假拒答（test_stage1_relax_doc_type.py）；「广告法」business_domain=advertising 漂移→全库空命中（selection_funnel/knowledge.py:88-99）；幽灵键白名单（test_metadata_filter_whitelist.py）。
- 改动点：to_metadata_filter 停发 doc_type/business_domain（query_analyzer.py:73-88）；放宽链简化（retrievers.py:616-698 的 domain_fallback 级失效、kb 级保留）；pipeline 四处 kb_scope 放宽语义简化；Evidence Gate 的 doc_type 覆盖门退役。**必须保留**：显式 kb_id、租户/授权 keep-set、permission_scope（permissions.py 全链）、审核可见性、KBRouter kb 维度（可保留为软信号或同退役——doc_type 停发后其影响需评估）。

### P0-3/P0-4 分类链路与消费者（Explore C）
- 级联三模块 metadata_decision/metadata_runtime/metadata_evidence **100% 只服务 doc_type/business_domain 分类**（DecisionEnvelope R0/R1/R2/fallback+缓存+证据），无权限/安全职责。metadata_classifier/shadow、tasks/metadata_shadow_tasks.py、eval/metadata_baseline/* 同属退役面。
- **doc_type 字段本身不可删**，真实消费者：财务 PII 强制脱敏（filter.py:292-335）、旅游（city_guide_service.py:69-87 travel_guide+destination）、Trace 保留 180 天（observability.py:29-36、trace_retention.py:39-90）、财务版本快照（indexer.py:2624,2784-2796、publish.py:437-449）、引用/前端展示（citation.py:259-309、SourceCard.tsx:39）。供给改由规则分类器 classify_doc_type（metadata.py:227，taxonomy.yaml+domain_data.py 链，**不可删**）。
- METADATA_CLASSIFIER_ENABLED 唯一生产消费点 metadata_decision.py:185（当前默认 false 即 R1 已关）。METADATA_CASCADE_ENABLED 部署默认 true（compose），代码默认 false。ENABLE_LLM_METADATA_EXTRACT 兼管摘要（metadata_stage.py:574-575）——保留开关但语义收窄为「摘要等增强」。
- question_gen.py 与 doc_type 仅 prompt 文案/缓存键弱耦合（:105-122,173-182）；「simulated_questions 恒空」根因=级联下只有 envelope.source==llm 才生成（metadata_stage.py:933,1012-1028 route_no_llm）+P1 后占位问题被禁（question_gen.py:59-69）——是设计行为非 bug。
- permission_scope 全链与分类零耦合，原样保留。
- domain_classify.source="metadata_llm" 语义矛盾已定位方向：rule_metadata 组包处（metadata_stage 2150+ indexer 2196 domain_detail）与实际执行路径（R0）不符，待改造收敛组包路径时一并修正。

### 施工序决定
1. P0-1 路由反转（改动最集中：chunking.py Router 一个函数+表）
2. P0-2 检索去猜测过滤（query_analyzer+放宽链+Gate 门退役+测试改）
3. P0-3 分类级联退役+组包路径收敛（rule_metadata/llm_metadata 归一，domain_classify 语义修复顺带）
4. P0-4 消费者验证（财务/旅游/Trace 保留回归）+ P1 设施清理（引用搜索后删）

## 2026-10-19 治理改造首批施工完成（P0-1/P0-2/P0-3最小步/P0-4回归）

- P0-1 路由 v3（commit「refactor(rag): 切分路由 v3 结构驱动」）：AST 信号直判（问答对>表格>条款>步骤>章节>递归），doc_type 退出切分前置，仅保留 ad_policy/financial 业务例外；误分类免疫有测试钉死。测试 11+45 passed。
- P0-2 检索停发猜测过滤（commit「refactor(rag): 检索停发…」）：QueryAnalyzer 停发 doc_type/business_domain；Evidence Gate 覆盖门输入退役；放宽链简化为唯一 kb 维度（授权收口不变）；顺手修 persons 多值裸 list 存量缺陷。测试 66 passed。
- P0-3 最小上线步：METADATA_CASCADE_ENABLED 部署默认 false（docker-compose.yml×3 + isolated compose）。级联代码退役与组包收敛挂 P1（需引用搜索+消费者迁移证据，单独施工）。
- P0-4 消费者回归：chunk_type_contract/doc_type_label/candidate_publish（财务版本）/trace_retention 共 42 passed；1 失败为存量环境项（基线对照 stash 确认与本轮无关）。
- 未完成（下一轮）：级联代码删除、metadata_taxonomy 仅分类规则清理、question_gen 解耦验证、管理端旧分类入口清理、PR CI 门禁结果。

## 2026-10-19 关键词去词表化改造完成（本地提交，待用户确认后推送）

- P0-3 检索门控移除：`_filter_docs_by_keywords` 改直通（候选全保留），`_score_by_keyword_overlap` 删除，查询侧 extract_chunk_keywords 调用移除；gate_info 契约与函数名保留供 Trace 兼容。
- P0-2 生成停发：chunk 级规则提取与 Qwen 残链删除（chunk_keywords 置空串兼容）；doc 级 task_keywords 停发（KeywordResult 空产出，字段兼容）；零新增 LLM 调用。
- P0-1 keyword.py 重写：词库热加载/DOMAIN_RULES 词臂/信号词/Jieba/LLM 全链删除；保留通用标识符识别（字母前缀+数字主体，零宽断言防 AB123 误命中 AB1234-X；显式「编号/单号/错误码」标注模式）；KeywordResult/dedupe_keywords_normalized 契约保留；extract_rule_keywords 供复杂度特征（len()）继续可用。
- P0-4 确认：BM25 语料 = chunk 原文（page_content），与关键词字段无关，未改。
- P0-5 消费者兼容：前端/Trace/GET chunks detail 读空值展示；domain_data 的财务 PII/人名/时间/分类导出全部未动；词库管理 API（keyword_routes）保留未删（独立功能，挂 P1 评估）。
- 测试：新增 test_keyword_devocabbing 10 用例全过；受影响定向（去词表 10+FAQ 证据 10+路由 11+治理 45+检索放宽 4+白名单/query_analyzer+chunking_router 2+广覆盖批 91）全绿；rag 目录广覆盖 1262 passed，39 failed+55 errors 经 stash 基线对照确认全部为存量（55 errors=本地 PG 无密码环境项，28 failed 与基线逐一对应）。
- BLOCKED：Golden Recall@5/MRR/NDCG 对比需评测 runner 真实运行（涉及付费 LLM 或长时检索），未执行；替代证据=隔离栈 faq_only 6/6 检索命中 + 定向契约。
- 按用户要求：本地提交不推送，待确认后统一 push。

## 2026-10-19 治理改造第二批（P1）：级联+影子链物理删除完成（本地提交，待确认推送）

- **组包路径收敛**：metadata_stage 1147→约 810 行。删除 rollout 级联分支（decide_metadata/DecisionEnvelope 四路 lineage/影子派发/finalize_decision/finalize_cascade 死代码）；规则六步异常兜底改「合成最小 unified → finalize_unified 同一收口」；ENABLE_LLM_METADATA_EXTRACT 成为唯一增强路径（摘要/实体，doc_type 恒由规则分类器供给）。
- **物理删除**：metadata_decision/metadata_runtime/metadata_evidence/metadata_classifier/metadata_router/metadata_shadow 六模块、tasks/metadata_shadow_tasks、eval/metadata_baseline 目录；compose 的 metadata-shadow-worker 服务；config 开关（METADATA_CASCADE_*/METADATA_CLASSIFIER_*/METADATA_SHADOW_*）与指纹清单 metadata_router 行；Celery 队列注册/任务路由/admin 端点队列映射/metrics 影子计数器；测试删除 9 个文件（cascade/shadow/rollout/classifier/decision/evidence/router/runtime/deployment）。
- **保留**：metadata_llm（LLM 增强抽取，限流内联为模块级 LLM 信号量——修复了替换时误删 metadata_llm_calls_total 导入的 UnboundLocalError）；classify_with_confidence 规则分类器（doc_type 唯一供给源）；METADATA_CACHE_TTL_SECONDS（table_describe 通用缓存消费）；permission_scope/财务 PII/旅游/Trace 保留全链未动。
- **domain_classify 语义**：级联删除后 rule_metadata 的 domain_detail 来自规则路径、llm_metadata 的 metadata_llm 标注仅出现在真实 LLM 增强时——矛盾消除。
- **测试**：rag 目录 1217 passed / 22 failed（全部为存量，数量严格对账：去词表化基线 39 − 删级联测试 12 − 本轮修复 5 = 22）；worker_topology 19 passed（删影子队列用例+compose 五 worker 断言改四 worker）；metadata_llm 11、simulated_questions 11、faq_routing 9、retrievers 全过。
- **修复的引入项**：metadata_llm 限流内联时误删 metrics 导入（UnboundLocalError）；finalize_unified 计时锚点误引 build 局部变量（统一路径必炸隐患，改方法自治计时）。

## 2026-10-19 治理 P1 收尾：taxonomy 仅级联字段清理 + 管理端入口核查

- taxonomy.yaml 的 `r0_allowlist`（级联 R0 短路规则清单，唯一业务消费者 metadata_evidence 已随级联删除）字段与 taxonomy_spec 加载/校验代码删除；get_taxonomy 加载验证通过。
- 删除 `scripts/run_metadata_rollback_v1.py`（级联回滚脚本，级联退役后无意义）。
- **词库管理链路保留（引用搜索推翻删除判断）**：keyword_store 有活消费者——classify_with_confidence（doc_type 唯一供给源）的动态规则分支与 metadata_rule_service；定位从「检索关键词词表」变为「分类动态规则库」，管理端词库 tab/keyword_routes API 保留。
- 管理端旧分类入口核查：前端无级联/影子专属入口（grep 命中均为 CSS shadow 样式）；delete_document_cascade 为文档删除功能（无关，保留）。
- 回归：rag 目录 1217 passed / 22 failed / 55 errors 与 P1 后基线完全一致（全部存量），零新增。

## 2026-10-19 审查 4 项 P2 修复完成（本地提交，待确认推送）

- P2-1 信号量跨事件循环：metadata_llm 背压恢复按事件循环管理（weakref 注册表+宽度变更重建+wait_for 获取超时），宿主机 Python 3.10 无 asyncio.timeout 改用 wait_for。验证：三轮跨 asyncio.run 各 2/2 success（审查复现场景消除）。
- P2-2 扩展查询丢 leaf：CustomRetriever 扩展多路每路 over-fetch k*2（原 k 条全 parent 时后续 leaf 进不了 RRF 候选池）。验证：ParentFirst 替身（前 5 全 parent+leaf 在第 6）下每路请求 k=10、leaf-late 进结果且 parent 被滤除。
- P2-3 冷启动混合检索抛异常：hybrid 主融合路 submit 前判 bm25_retriever=None（属性访问 None.invoke 在 submit 行同步抛、不在任何 try 内）；vector_only 兜底分支同判空；"两侧失败"判定把检索器缺位与检索失败区分。验证：bm25=None 混合检索无异常（0 docs 为审核过滤对假 doc_id 的正确行为）；enhanced 路径原有判空守卫确认无此问题。
- P2-4 模型清单：回填提前返回前独立补 usage.models（已有真值的 Trace 不再缺失清单）；usage.models 改全量「组件×模型」明细（删除每组件代表压缩，同组件多模型全呈现）。验证：替身 store 双场景（新 trace/提前返回）models 均含 embedding+llm 两模型。
- 存量失败甄别收口（22 项）：环境阻塞转绿 3（quality_gate/reindex/indexer_processing_lineage，连隔离栈 PG 即过）；过期断言挂下轮契约同步（lineage_e2e 8/keywords·级联 metrics、query_router 3/hybrid 权重、table_split 2、long_doc 2/Excel 保真、vector_degraded 2/fail-closed 交互、scorer 3/NLI 降级 UnboundLocalError 属产品健壮性问题单独记录）；其余为本地 PG 无密码环境项。git diff --check 复验无告警。

## 2026-10-19 CI 门禁真回归修复：coverage_gap 假缺口（本地提交）

- CI RAG Smoke preflight 抓到 test_rag_fixture_content_integrity 回归：baseline_structure_messy（uncovered=1）/baseline_homograph（uncovered=2）coverage_gap。本地复现确认（此前本地跑该文件被 --ignore 跳过，教训记录）。
- 根因：v3 路由让这两个低中文占比叶子文档进入 Structure 切分，叶子独立成 chunk 后被 low_chinese_ratio 显式过滤；section parent 保留文本但 coverage_audit 只对 granularity=leaf 的 chunk 对账 → 合法过滤被计为 coverage_gap（v2 递归把叶子混进大文本块恰好掩盖）。**内容未真丢失**（文本在 kept parent 中可查）。
- 修复：_filter_index_chunks 的 filtered_details 增带原文 content；coverage_audit 增 excluded_texts 豁免入参（显式规则过滤=合法排除纳入对照，静默丢失仍报 gap）；门禁测试与 indexer 付费前预检同口径透传。
- 验证：fixture 完整性 23 passed 全绿（16/16 无缺口）；git diff --check 无告警。
- 教训：本地定向跑时的 --ignore 恰好跳过了 CI 必需门禁文件——后续本地验证必须覆盖 CI workflow 的 targeted 清单。

## 2026-10-19 重传对账补漏：retrieve_knowledge BM25 腿 parent 过滤（本地提交）

- 用户重传 faq_only.docx（版本覆盖 921d5068a5e1 published，41 chunks）。对账发现 P2-2 遗漏路径：/retrieve（rag-service retrieve_knowledge）的向量腿走 CustomRetriever（过滤已生效），但 **BM25 腿（self.bm25.invoke 四处）直接召回**——BM25 语料含 parent 文本，问题清单与查询词面重合仍进 Top-8。
- 修复：pipeline.py 四处 BM25 腿（retrieve_documents 三处放宽链 + retrieve_knowledge 一处）补 granularity!=parent 过滤，与 base.py/hybrid.py 同开关同语义。
- 端到端复验：top-8 无 parent 问题清单块、FAQ-001 答案照常命中、index_status=ok。
- 对账顺带确认：重传 trace（68a6c776）page_count=0+applicable=False ✓、metadata_llm_extract 子 span 7.3s（LLM 增强真实触发）✓、读侧回填模型清单双模型（doubao 2 次+qwen 6 次，cost 0.008614）✓。
- 清理：本地 pytest 运行曾把两行假数据（doc.md/doc2.md，doc_id=d1/d2，来自 pytest 临时目录）写进隔离库 doc_registry/chunk_store——已确认为纯测试数据并清除；隔离库恢复纯净（1 文档/41 chunks/42 向量）。

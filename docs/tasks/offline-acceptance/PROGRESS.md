# PROGRESS: 平台离线验收收口（offline-acceptance）

- 状态：IN_PROGRESS
- Worktree：`D:/Program Files/workplace/agent-wt-offline-audit`
  （branch `audit/offline-acceptance-r1`，基于 origin/main=`6555433`）
- 交接基线：main=`6555433`（含 #355/#356）；主仓本地 main=`7b0bfc5`
  （behind 45，未动）；gaps 工作树禁动。

## 2026-11-07 接手核实（全部只读）

- 远程分支 `codex/travel-provider-e0-hash`=`6454801` 已推送；**PR 原不存在**
  （`gh pr list --state all --head` 返回空），证实前序会话 PR 创建未成功。
- travel 工作树 HEAD=`6454801`、与远程同步、工作区干净；本地复跑
  `backend/tests/evaluation/test_eval_preflight.py` **13 passed**（38.62s）。
- gaps 工作树：分支基于旧 main `7b0bfc5`，未提交改动 **114 个文件**
  （含 `.github/workflows/*`、`Dockerfile`），按红线禁动。
- `codex/offline-eval-matrix`、`codex/offline-paid-gate` 两分支经 `git cherry`
  证实补丁已等价合入 main（对应 `a8fe74d` / `8b2ba9a`），工作树干净——
  已完工遗留，可列为清理候选（待用户确认后再动）。
- `docs/tasks/eval-closure` 已 COMPLETE（E′/D1/F/D3/D4a + 浏览器收尾均合并），
  与本任务无关；在线采样默认关闭，长期启用前需单独核验预算账本。

## PR #357（travel-provider content_hash 锁定）——已合并 ✅

- 2026-11-07 创建：https://github.com/Jayus-07/agent/pull/357
  `fix(evaluation): lock travel provider dataset hash`，base main，
  head `codex/travel-provider-e0-hash`，单提交 `6454801`。
- 改动面：manifest 补 `content_hash=78b892835152b262`；preflight 新增 2 个守卫
  测试（manifest 必须锁 canonical 样本；漂移时 `verify_dataset_integrity`
  fail-fast）；datasets README 新增 hash 治理规则；evaluation-governance.md
  更正现状描述。
- 验证：本地复跑定向测试 13 passed（38.62s）；Codex 自动 Review 完成无发现
  （`has_any_findings: false`）；免费 CI 全绿——RAG Smoke preflight（3m37s）、
  RAG Smoke 8 案例（4s）、Evaluation V2 free contract tests（4m14s）、admin
  build、scan、Detect RAG changes 全部 pass；付费 real API job 按策略
  skipping（不计作已执行）。
- **2026-11-07 05:38 UTC squash 合并，main=`52443a6`**；审计 worktree 已
  ff 到该提交。注：本机 Docker 重建/健康验证按交接排序属第 5 步，此 PR
  不含运行时服务代码，部署侧状态记 UNVERIFIED（待第 5 步统一验收）。

## 2026-11-07 逐模块离线盘点结果（静态盘点 + 本地定向测试双口径）

### 静态盘点（origin/main=`52443a6`，Explore 只读代理，全程带 file:line 证据）

- **canonical 层健康**：9 个带 manifest 数据集（rag/sql/cs/e2e/travel/travel-provider/
  planner/sql_v2/travel_v2）content_hash 全部声明且实测匹配，case_count 全部一致。
  交接预期的「travel-provider 缺口」已由 PR #357 补齐。
- **真实缺口清单**：
  1. 顶层辅助集 10 个（planner_params / tool_selector / tool_routing /
     router_domain / context_budget_golden / rag_100_docs / rag_test_kb /
     rag_cases_20 / judge golden / memory_golden）无 manifest/hash 锁；其中
     tool_selector 评测脚本以文件路径绕过 loader 校验（run_tool_selector_eval.py:184）；
     tool_selector / tool_routing / router_domain 三者无守卫测试、不在任何 CI。
  2. suite 级：cs_q3_acceptance / cs_q3_action_pool 未声明 cases_hash（loader 校验 opt-in，loader.py:205-213）。
  3. rag 版本漂移：manifest=`6.0.0-desktop-candidate.4`，两个 suite 与 snapshot 仍 candidate.1。
  4. 双事实源：sql_v2/cases.jsonl 与 sql/golden_v2.jsonl 字节级相同，前者无代码消费者。
  5. 文档漂移：scripts/weekly_eval.py:25 自述 rag_test_kb「v1.3/54 条」，文件实为 v4.0/103 条。
  6. 孤儿资产：rag_cases_20.jsonl、travel/golden_v1.baseline.json 全仓零引用。
  7. INVALID 语义缺口：markdown 报告把 INVALID 折叠成 FAIL（report/markdown.py:451）；
     旧兼容入口 compute_metrics_gate / builder.compute_release_gate 无 INVALID 态（已声明废弃）。
- **免费 CI 有效性核实成立**：INVALID 不可能计入通过（release_gate.py:457-468 overall
  仅 PASS 为真、cli.py:147-149 退出码三态、validity.py:277-283 历史 VALID 可被推翻
  而持久化 INVALID 不降级）；rag_smoke 汇总 job 按事件组合严格校验，无「跳过即放行」路径。

### 动态验证（本地定向跑，worktree 无 .env）

- `pytest backend/tests/evaluation/`：**7 failed / 522 passed / 37 skipped**。
- 5 个失败 = 夹具过时（SimpleNamespace 缺 `actual`，见 PR #358）→ 已修。
- 2 个失败 = DB 环境阻塞（test_managed_task_controls::test_failed_cancellation_sql_guards_retry_budget_and_error_type、
  test_rag_eval_migration::test_production_authorized_kbs_exclude_unified_eval_kb）：
  默认连 `localhost:5432` 失败（项目库在 5433）；两测试不在任何 CI；
  待隔离 PG 批次验证，**未算通过**。

### 修复批 1：PR #358（gate validity 夹具 + 免费 CI 盲区）——已合并 ✅

- `test(evaluation): align release gate fixtures with actual contract`（`48ae5b6`）
- `ci(evaluation): cover managed gate validity tests in free contract job`（`e46edd3`）
- 验证：该文件 **33 passed**（修复前 5 failed）；CI 免费契约测试实跑通过
  （若新增测试 ID 无效 pytest 会因收集为空而失败）。
- 2026-11-07 squash 合并，main=`561bad7`。

### 修复批 2：PR #359（cs q3 suite hash 锁 + weekly_eval 文档口径）——已合并 ✅

- `fix(evaluation): add cases_hash locks to cs q3 suites`（`5853c28`）+
  `docs(scripts): correct rag_test_kb version note in weekly_eval`（`4017997`）
- cs_q3_acceptance / cs_q3_action_pool 补 `cases_hash=0e0ec5bde442b0c1`（实测复算
  canonical 一致）；loader 实测加载 11/38 条通过；`test_cs_eval_kb_contract.py
  + test_cs_runner_v2.py` **20 passed**。
- **rag suite dataset_version 漂移（candidate.1 vs manifest candidate.4）核实为
  测试钉住的冻结声明**（test_rag_eval_suites.py:48-51「suite 文件的
  dataset_version 是冻结声明，loader 实际版本以 manifest 为权威」），非缺陷，不改。
- weekly_eval.py 文档口径 v1.3/54 → v4.0/103（既有 E402 为存量，不顺手修）。
- 2026-11-07 squash 合并，main=`2b63030`。

### 修复批 3：PR #360（markdown INVALID 三态透传）——已合并 ✅

- `fix(evaluation): preserve INVALID verdict in markdown machine summary`（`09059a4`）
- markdown.py:451 verdict 改为逐字透传 `gate["verdict"]` 三态 + 补 `invalid_reason`
  （人类可读头部 ：55-57 一直正确；JSON 报告一直完整）；
  新增 `test_report_gate_verdict.py`：FAIL 透传 + INVALID 透传（3 条命中
  Provider 错误签名的确定性构造）。
- 验证：**2 passed**；`test_report_shanghai_timestamp.py`、
  `test_metric_label_single_source.py` 同跑通过（markdown 构建面无回归）。
- 2026-11-07 squash 合并，main=`907ae13`。

### 修复批 4：PR（cs forbidden_facts 语义裁判校准脚手架，纯离线）

- 背景核实：cs 锁版 324 条中 **85 条**声明 `forbidden_facts`（23 个语义概念）；
  runners/cs.py:405 无裁判可调，标 unverified → 用例 skip（诚实机制已在位，
  缺的是"可校准的裁判"本体）。
- 交付：
  - 裁判契约 `backend/evaluation/judge_cs_forbidden.py`：`violated: bool|None`，
    None=裁判不可用（与 judge.py 失败语义同构且更严，绝不把失败冒充合规）；
    prompt 走统一治理链（`evaluation.judge.cs_forbidden` 新模板 + registry 注册）。
  - golden 校准集 `datasets/cs/forbidden_judge_golden.jsonl`：**25 条**人工标注
    （14 violated / 11 compliant），**覆盖 cases.jsonl 全部 23 个禁说概念**（测试钉住）；
    manifest 声明 `content_hash=3fcc79a1f3b1c225`，漂移拒绝加载。
  - 校准 runner `judge_cs_forbidden_golden.py`：run_judge 池调用；阻断条件=
    任一档位翻转或任一 judge_error（exit 2）；报告落盘 `data/eval_runs/cs_forbidden_golden/`。
  - 测试 8 项全离线（stub 注入，零模型调用）：完整性/概念全覆盖/不可用不冒充合规/
    清单外过滤/prompt 注册/完美裁判通过/翻转阻断/不可用阻断/manifest 漂移拒载。
- **明确未做**：接入 runners/cs.py 的 wiring——待付费阶段用真实 eval_gen 跑
  golden 校准通过后再单独接线（校准执行属付费调用）。
- 验证：`test_judge_cs_forbidden_golden.py` **8 passed**；cs_runner_v2 +
  schema preflight + eval_preflight 回归 **36 passed**（golden 不干扰 cs 加载）；
  ruff/compile 通过。

### 隔离环境批 1（2026-11-07，一次性容器 `agent-eval-pg-test` @ 127.0.0.1:5544）——①②全部完成 ✅

**环境纪律**：`pgvector/pgvector:pg16`（与主栈同镜像）、端口 5544、init_db 幂等灌
121 迁移、kb 种子 18 行；全程零触碰共享栈与共享库 5433；零付费模型调用。

**① DB 阻塞测试验证 ✅**
- `test_managed_task_controls.py` **10 passed**：根因是测试 patch 已废弃的 `_conn`，
  而 `mark_cancelled_if_status` 走 `_pooled_conn`（task_service.py:81/637）——纯单测
  修 monkeypatch 目标即绿，无需 DB。
- `test_rag_eval_migration.py` **3 passed**（对隔离库）：`get_knowledge_bases` 的
  42P01→bootstrap 回退与 DB 直读路径均按设计工作（config/knowledge_base.py:57-74）。

**② cs_eval_kb 真实索引/检索验收 ✅（PASS 6/6）**

- **关键纠偏（用户指正）**：我先误判需要新建 Ollama——只查了隔离库（绑定空）与
  共享库旧兼容表（残留 DashScope 行），漏查权威角色绑定链 `llm_model_role_bindings`。
  实际共享栈配置早已是**本地模型**：`custom-gpu`（host :8088，billing=local）+
  `bge-m3`(embedding) / `bge-reranker-v2-m3`(rerank)，角色绑定直指；main 角色为
  豆包 doubao-seed-2.0-mini（方舟 API）。
- **服务启动方式（文档在 commands.md「本地专项模型服务」节，此前我漏读）**：
  `dev-local-models.bat start`（后台启动+双模型真实预热；PID 文件管理；端口被占
  拒绝不杀）。健康：`/healthz` → status ok、双模型 CUDA 加载。
- **隔离库镜像配置**：provider `custom-gpu`(127.0.0.1:8088, driver=openai,
  billing=local) + 模型 `bge-m3`/`bge-reranker-v2-m3`（model_kind 对应）+ 角色绑定
  embedding/rerank + 凭据密文（同机共享库复制占位键，同 `SECRETS_ENCRYPTION_KEY`
  可解；宿主进程需从 .env 注入该变量）。
- **真实索引成功**：`ingest_cs_fixtures --fixture-set baseline --apply` → 15 个
  fixture 文档 / **293 chunks** 全部经本地 bge-m3 嵌入写入隔离库（embedding_model
  =bge-m3、fingerprint 已登记），BM25 293 文档同步。
- **检索验收 PASS（脚本 d:/tmp/cs-eval-acceptance/retrieval_acceptance.py，不入库）**：
  6 个中文查询 × 授权检索出口 `retrieve_documents`，全部命中正确 fixture 文档
  （top5），并从命中 chunk 的 `doc_id/source_file/version_id/section_title` 四元组
  经 `fact_ids_for_rag_source` 派生出期望事实 ID——事实 ID 断言依赖的检索链路
  真实可用。命中示例：运费→`kb:policy-shipping-fee#承担方/#规则`；美国退货→
  `#凭证/#资格`；物流丢失→`#时效/#赔付`。
- **验收口径说明**：期望映射以「查询语义正确的文档」为准（如"退款多久能到账"
  命中 FAQ 的 `Q: 退款多久到账？` 章节而非政策文档属正确召回）；事实派生选块跳过
  H1 文档级块（其 section 与目录子章节不匹配，属预期）。

### 隔离环境批 1（2026-11-07，一次性容器 `agent-eval-pg-test` @ 127.0.0.1:5544）——①②全部完成 ✅

**环境纪律**：`pgvector/pgvector:pg16`（与主栈同镜像）、端口 5544、init_db 幂等灌
121 迁移、kb 种子 18 行；全程零触碰共享栈与共享库 5433；零付费模型调用。

**① DB 阻塞测试验证 ✅**
- `test_managed_task_controls.py` **10 passed**：根因是测试 patch 已废弃的 `_conn`，
  而 `mark_cancelled_if_status` 走 `_pooled_conn`（task_service.py:81/637）——纯单测
  修 monkeypatch 目标即绿，无需 DB。
- `test_rag_eval_migration.py` **3 passed**（对隔离库）：`get_knowledge_bases` 的
  42P01→bootstrap 回退与 DB 直读路径均按设计工作（config/knowledge_base.py:57-74）。

**② cs_eval_kb 真实索引/检索验收 ✅（PASS 6/6）**

- **关键纠偏（用户指正）**：先误判需要新建 Ollama——只查了隔离库（绑定空）与
  共享库旧兼容表（残留 DashScope 行），漏查权威角色绑定链 `llm_model_role_bindings`。
  实际共享栈配置早已是**本地模型**：`custom-gpu`（host :8088，billing=local）+
  `bge-m3`(embedding) / `bge-reranker-v2-m3`(rerank)，角色绑定直指；main 角色为
  豆包 doubao-seed-2.0-mini（方舟 API）。
- **服务启动方式（文档在 commands.md「本地专项模型服务」节）**：
  `dev-local-models.bat start`（后台启动+双模型真实预热；端口被占拒绝不杀）。
  健康：`/healthz` → status ok、双模型 CUDA 加载。
- **隔离库镜像配置**：provider `custom-gpu`(127.0.0.1:8088, driver=openai,
  billing=local) + 模型 `bge-m3`/`bge-reranker-v2-m3` + 角色绑定 embedding/rerank
  + 凭据密文（同机共享库复制占位键；宿主进程需从 .env 注入 `SECRETS_ENCRYPTION_KEY`）。
- **真实索引成功**：`ingest_cs_fixtures --fixture-set baseline --apply` → 15 个
  fixture 文档 / **293 chunks** 全部经本地 bge-m3 嵌入写入隔离库（embedding_model
  =bge-m3、fingerprint 已登记），BM25 293 文档同步。
- **检索验收 PASS（脚本 d:/tmp/cs-eval-acceptance/retrieval_acceptance.py，不入库）**：
  6 个中文查询 × 授权检索出口 `retrieve_documents`，全部命中正确 fixture 文档，
  并从命中 chunk 的 `doc_id/source_file/version_id/section_title` 四元组经
  `fact_ids_for_rag_source` 派生出期望事实 ID——事实 ID 断言依赖的检索链路真实可用。

## 2026-11-08 可靠性离线验证（11 维度盘点 + 定向补强）

**静态盘点**（只读代理，file:line 证据齐全）：4 绿（超时/重试/权限隔离/用量归因
——用量归因 8 文件已在 PR 门禁，全库最强）、6 黄（降级/幂等/断点恢复/SSE 断开/
并发调度/人工确认/BadCase 回流）、1 个「实现存在但测试为零」（ProviderBreaker）。
**共性缺口：绝大多数权威测试只在周级 full_regression，PR 门禁零覆盖。**

### 修复批 6：PR #365（纯离线补强）——已合并 ✅（main=8252f9c）

- 新增 `test_provider_breaker.py`（7 例可控时钟）：达阈开路/窗口赦免/半开清窗/
  成功清窗/审计恰一次/20 线程并发恰一次——**消灭 11 维度中唯一的零测试实现**；
- 新增 `test_compose_scheduling_contract.py`（2 例）：beat 单实例 + 四类 worker
  并发档位从「注释纪律」固化为回归契约；
- rag_smoke 定向清单登记 4 文件（新增 2 + 既有 breaker/resilience 2）→ PR 门禁
  补上降级与调度契约每次必跑；
- 验证：9 例新增 + 35 例既有全绿；ruff/YAML 校验过；CI preflight+smoke pass。

### 隔离环境批 2（D4/D5/D8 权威矩阵，PG+Redis）

- **环境插曲（如实记录）**：宿主机过夜重启 → Docker 引擎与隔离容器消失 →
  重启 Docker Desktop（29.9.2）→ `docker start agent-eval-pg-test`（数据完好，
  schema_migrations 126）+ 一次性 `agent-eval-redis-test`（127.0.0.1:16380）。
- D4 幂等：`test_idempotency_protocol + test_side_effect_idempotency` ✅
- D5 断点恢复：`test_task_pending_recovery(T1-T11) + test_task_checkpoint_recovery
  + test_task_resume` ✅ **31 passed**
- D8 并发调度：`test_task_admission`（Redis 准入，原 21 skip）✅
- 三文件合并跑 **59 passed**（修复前同批 15 passed/21 skipped/23 errors——根因
  是容器随宿主重启消失 + Redis 缺位，非代码问题）
- **操作失误记录**：续工核对时在带未提交修改状态下误跑 `reset --hard`，丢弃
  TASK/PROGRESS 两处未提交编辑；已凭原文完整恢复并改用精确锚点重写。教训：
  切换/重置前必须先 `git status` 确认工作区（pitfalls §一「绝不用 --hard」）。

### 可靠性剩余缺口（下一步输入）

1. SSE 断开：`stream_resume` 注册表级单测抽离（现有用例走 FastAPI client 依赖重），
   确认 client fixture 无 PG 依赖后纳入 PR 门禁（纯离线）；
2. BadCase 回流端到端：负反馈→candidate→approve→promote→落库（需隔离 PG，现仅
   幂等契约层）；
3. 预算对账任务 `budget_reconciliation_check` 正确性用例（需隔离 PG）；
4. AT-03/AT-05 现场 E2E（真实 worker kill 接管、provider 契约查账本）——归
   隔离全栈批次（Demo/Docker 验收时顺带）。

### Demo 半项闭环（2026-11-08）：5/5 学习 Demo 实跑证据 ✅

- 出处核实：`docs/tasks/enterprise-agent-gaps/learning-lab/`（gaps 工作树未提交
  改动内），共 **5 个** Demo（非交接所称"十几个"）：python_basics（async/装饰器/
  数据结构/异常）、llm_contracts（Token/工具 Schema/结构化输出/证据门控）、
  agent_workflow（LangGraph 人工确认+MemorySaver 恢复+真 ReAct）、api_sse_demo
  （FastAPI/SSE）、backend_io_demo（PG 临时表+Redis 幂等键）。
- 2026-11-06 记录：前 4 个已实跑 exit 0；唯一缺证据的是 backend_io_demo
  （当时隔离服务未启动）。
- **2026-11-08 全部重跑**：4 个纯本地 Demo exit 0；backend_io_demo 对本任务隔离
  容器（PG 5544 / Redis 16380，脚本内置守卫拒绝共享端口）exit 0——PG `ON COMMIT
  DROP` 临时表读写、Redis `SET NX` 幂等写入/重复拒写/随机键删除。踩坑一处：
  gaps 工作树 `.env` 的带密码 `REDIS_PASSWORD` 被 dotenv 装入，需命令级置空压制
  （隔离 Redis 无认证）。
- 结论：Demo 半项闭环，无需另立任务；gaps 工作树文件全程只读执行，未改动。

### runners 接线（PR：forbidden_facts 语义裁判三态接线）

- `runners/cs.py` full_path 的 forbidden_facts 从「无条件 unverified」改为走
  `judge_cs_forbidden` 三态：violated→problems→fail（metrics
  `forbidden_facts_respected=0`）；合规→pass（=1.0）；裁判不可用→unverified→
  **skip 语义保持不变**（eval_gen 未绑定/失败绝不冒充合规）。context 传入用户
  问题（满足依赖上下文判定类别的证据要求）。
- 新增 3 例参数化接线测试（stub 裁判，零模型调用）：violated→fail+metrics、
  合规→pass+metrics、不可用→skip+unverified_checks 记录；契约断言裁判收到
  声明清单与用户问题 context。
- 验证：`test_cs_eval_kb_contract.py` **21 passed**；ruff 通过。
- **付费阶段前置注意**：接线后真实生效需 eval_gen 绑定模型；首次付费跑之前
  先做 golden 25 条真实跑分校准（judge_cs_forbidden_golden），翻转即阻断。

## 遗留缺口（下一步输入，均未算通过）

1. **DB 环境阻塞的 2 个测试**：✅ 已解决（见「隔离环境批 1」①——test A 夹具修复
   10 passed 走 PR；test B 隔离库 3 passed）。
2. **顶层辅助集 10 个无 hash 锁**（盘点缺口清单第 1 条）：是否补锁/补守卫测试待逐项裁决。
3. **tool_selector / tool_routing / router_domain 三个评测脚本无守卫测试、不在任何 CI**。
4. **孤儿资产**（rag_cases_20.jsonl、travel/golden_v1.baseline.json）与 **sql_v2 双事实源**：
   删除属数据删除动作，需用户确认后另批处理。
5. **旧兼容入口**（compute_metrics_gate / builder.compute_release_gate）无 INVALID 态：
   已声明废弃、禁新增调用方，仅记录。

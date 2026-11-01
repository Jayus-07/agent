# 评测平台 V2 API 契约（施工版）

根路径：`/api/evaluation/v2`。所有资源携带服务端可信 tenant scope。客户端不得提交 tenant 选择值；跨 tenant 的详情请求统一返回 404。

## 资源与状态

- `CaseCandidate`: `draft → needs_evidence → in_review → approved | rejected`。每次修改递增 `revision`；审核必须提供 `expected_revision`。
- `DatasetVersion`: 仅发布状态可被 Suite 引用；发布时固定 `content_hash` 和 `(case_id, case_revision)` 列表，发布后不可更新。
- `SuiteVersion`: 固定 DatasetVersion、case selection、Evaluator/Rubric、质量阈值、安全 veto 及最小有效样本量；发布后不可更新。
- `EvalRun`: `job_status=queued|preparing|running|aggregating|completed|failed|cancelled|blocked`；`gate_verdict=null|PASS|FAIL|INVALID`。两者独立。
- `CaseResult`: `pass|fail|error|skip`；网络、Provider、预算、数据和基础设施错误不得伪装为质量 fail。

## 契约

| 方法/路径 | 作用 | 核心约束 |
| --- | --- | --- |
| `GET /datasets` | 分页列出租户数据集 | 不回传其他租户数量或对象 |
| `POST /datasets` | 创建逻辑数据集 | 租户内 `dataset_key` 唯一；`domain` 必须来自统一模块登记表；冲突返回 409 |
| `POST /datasets/imports/preview` | 预检 CSV/JSON/JSONL | 不写候选或正式资产；有行号错误与有限预览；按 manifest.module 选择 RAG 或非 RAG Golden 契约 |
| `POST /datasets/imports` | 导入候选 | 全量校验后事务写入；观察答案不进入 expected；非 RAG 结构化 input/Expected 保持原样 |
| `POST /candidates` | 创建单条候选 | 只能由审核流程创建标准答案 |
| `GET /candidates` | 分页列出候选 | 当前仅 admin 可访问；资源查询带 tenant |
| `POST /candidates/{id}/review` | 审核候选 | 证据有效、理由非空、CAS；追加 Annotation 与 Audit。回答型证据可带 `anchor_id` 精确指定片段；服务端仍核验 `quote` 属于该片段，防止重叠父子片段歧义 |
| `GET /cases/{id}/latest-revision` | 读取已审核 Case 当前修订 | 仅 admin；tenant scoped；只用于创建修订候选 |
| `POST /cases/{id}/revision-candidates` | 为已审核 Case 创建修订候选 | 仅 admin；幂等键；服务端绑定目标 Case 与基础 revision，继承当前 source_ref/metadata/标准事实；审核人需重新核验证据 |
| `GET /datasets/{id}/versions` | 查询版本及样本数 | 仅返回该租户数据集版本 |
| `GET /datasets/{id}/versions/compare` | 比较两个已发布 Golden | 同一租户/数据集，返回样本增删、修订哈希变化、未变化数量和按当前 KB 权限复核的 stale case ID；不返回样本正文 |
| `GET /datasets/{id}/versions/{version_id}/coverage` | 查看已发布 Golden 覆盖分布 | 仅统计 category/scenario/risk_level/difficulty 四个受限标签，返回覆盖率与有限分布；不读取或回传题目、答案、证据正文 |
| `GET /datasets/{id}/versions/{version_id}/export` | 下载已发布 Golden JSONL | 仅 admin/super_admin；租户与数据集双重过滤；复算不可变清单哈希、按当前 KB 权限重验回答证据；最多 5000 条、预计 8 MiB、最终文件 10 MiB；只追加导出元数据审计，不保存正文 |
| `POST /datasets/{id}/versions` | 原子发布 Golden | 只选已审核样本；稳定 hash；已存在版本不覆盖 |
| `GET/POST /evaluators` | 查询/创建评分器草稿 | 配置脱敏检查；版本号唯一 |
| `POST /evaluator-versions/{id}/publish` | 发布评分器版本 | 仅草稿可发布；发布后不可变 |
| `GET/POST /suites` | 查询/创建方案草稿 | 仅允许已适配的 Runner 模块；数据集域、Suite 模块和评分器绑定由服务端交叉校验 |
| `POST /suites/{id}/versions` | 为现有方案追加草稿版本 | 只能选择绑定 Golden 版本内的唯一 case UUID |
| `POST /suite-versions/{id}/publish` | 发布 SuiteVersion | 固定 evaluator、阈值、样本选择及安全 veto |
| `POST /runs` | 创建并排队 Run | 仅 default tenant 的 super_admin；固定 Suite/Golden/Corpus/Prompt 和脱敏运行环境；Worker 发现检索配置漂移时以 INVALID 终止 |
| `POST /experiments` / `POST /experiments/{id}/runs` | 创建实验/异步运行 | 不在 HTTP 中同步执行模型；快照与幂等键必填 |
| `GET /runs/{id}` / `GET /runs/{id}/results` | 查询状态和逐例结果 | 每个读请求 tenant scoped，敏感产物须另外授权；旧 RAG Runner 能捕获的答案生成输入/输出 Token 作为数值指标写入 `scores`，不代表检索/评分等所有阶段的完整用量 |
| `POST /runs/{id}/cancel` | 请求取消 | 返回已接受/不可取消状态；保留已发生费用 |
| `POST /runs/{id}/retry` | 显式重试失败运行 | 仅失败终态可重试；沿用相同 Run/Task ID 和租约 |
| `GET /runs/{id}/results/{case_id}` | 查询逐题实际回答、期望、检索证据和错因 | 仅 super_admin；先校验 V2 逐例索引，再读取旧报告并在展示层脱敏 |
| `GET/POST /runs/{id}/results/{case_id}/human-reviews` | 查询/追加逐例人工复核 | 仅 super_admin；运行 completed 且结果为 pass/fail；必须使用 Run 快照中绑定的已发布 Human Evaluator，分数键与冻结 Rubric 完全一致；租户内 request ID 幂等并追加审计；不回写机器分数或 Gate |
| `GET/POST /experiments` | 列出/创建实验 | 实验绑定一个已发布 Suite 版本 |
| `POST /experiments/{id}/runs` | 将 Run 绑定 baseline/candidate 槽位 | Run 必须属于相同 tenant 与 Suite 版本 |
| `GET /experiments/compare` | 对比两个运行 | 快照或规则不兼容时返回原因；可比时返回逐例变化、有限脱敏标签的切片汇总和总体配对检验；切片有效样本至少 20 才计算双侧精确 McNemar，并对全部可检验切片使用 Holm 校正；错误/跳过样本不参加推断，结果不参与 Gate |
| `GET /gates/{run_id}` / `GET /audit-events` | 查询裁决、审批历史和审计 | Gate 决策及人工审批只追加；拒绝不泄露资源存在性 |
| `POST /gates/{run_id}/approvals` | 记录人工发布意见 | 仅 super_admin；运行必须 completed 且最新 Gate 为 PASS；请求绑定确切 decision ID，租户内 request ID 幂等；追加审核记录与审计，不触发 CI 或部署 |

## Golden 导入与模块边界

非 RAG manifest 使用 `schema_version: "evaluation-v2/1.0"`，并声明登记的 `module`、目标 `dataset_key`、名称和版本。用例可用 `question`，或用 `input` 保存结构化输入；客服输入支持 `input.turns`，保留多轮消息，`input.query` 必须与最后一条 user 消息一致。用例可以省略 `module`；如提供，必须与 manifest 模块相同。CSV 结构化字段使用 JSON 单元格。

`sql`、`cs`、`planner`、`e2e`、`travel`、`travel-provider` 已有离线 Expected 结构校验，可创建待审核候选并发布相应 Golden；审核和发布均拒绝知识库证据锚点。SQL 只复用旧 Runner 的离线白名单和安全校验，不执行查询。RAG 继续执行当前文档与原文证据核验。（2026-11-01 收编重构：`travel-commerce` / `travel-booking` 随两域图壳退役。）

Suite/Run 按服务端登记的 Runner 能力接受模块，客户端声明 `module` 不会授权或触发业务执行。RAG 使用受控检索 Runner；`travel`、`travel-provider` 复用现有离线质量 Runner 和 fake Provider。`sql`、`cs`、`e2e` 当前只运行离线契约检查，Run 快照固定 `execution_semantics="contract_only"`，最终 Gate 必须是 `INVALID`（`runner_semantics_not_quality_evaluation`），不能形成质量 PASS/FAIL，也不能进行人工质量评分。`planner` 尚未接入 V2 Runner，不能创建 Suite/Run。旧 Golden 可导入并不等于对应模块已具备质量评测能力。

## 错误语义

响应使用 `{ "code": "...", "message": "...", "request_id": "..." }`。稳定代码包括 `EVIDENCE_REQUIRED`、`EVIDENCE_STALE`、`REVIEW_CONFLICT`、`VERSION_HASH_MISMATCH`、`IDEMPOTENCY_CONFLICT`、`BUDGET_BLOCKED`、`EVAL_INVALID_PROVIDER`、`EVAL_INVALID_SNAPSHOT`。并发审核冲突为 409；不可见或不存在资源统一 404；可信身份或角色缺失 401/403。

Run 固定语料前若目标知识库没有已发布文档，返回 422 `INVALID_PARAM`，并提示先完成文档发布和索引；不会创建 Run、Task 或触发 Embedding。`corpus_hash` 是稳定的语料语义指纹，覆盖可见文档、全文块、索引映射与向量清单，并排除快照 UUID、操作者、时间戳及存储分配 ID；`integrity_hash` 单独校验包含快照身份的完整落盘记录。这样同一冻结语料可跨 Run 比较，同时仍能检测快照记录被改写；旧快照保留兼容校验。

## 当前实现状态与限制

V2 Run、逐例结果、异步任务适配、取消/显式重试、Experiment 槽位、兼容性对比、Gate 查询与 Audit 查询已经落地。Run 通过确定性 `run_id` 复用现有 Task/Lease/恢复机制；运行输入固定 Suite、Golden、知识库 Corpus、Prompt 与可选人工 Rubric 版本，逐例结果按 `(tenant, run, case)` 唯一追加。只有完整结果集才能完成；最终 Gate 与 `completed` 状态在同一数据库事务中提交，失败时不会留下“Run 已完成但没有 Gate”的中间状态，相同 Gate 重放幂等。运行中有 `error` / `skip` 或无效归因时返回 INVALID，不把 Provider 故障算作答案错误；失败的 `permission_denied` 拒答样本触发 `security_hard_veto`，不受总体通过率阈值放宽影响。逐例 `scores` 现包含旧 Runner 实际捕获的答案生成阶段输入、输出与合计 Token；这不代表全链路 Token 归因，模型费用仍标记为未报告。migration 110 新增运行逐例人工复核，按冻结 Rubric 校验维度和权重，记录评分、标签、理由、复核人和时间；人工分数仅是独立校准证据，不自动改机器结果或 Gate。人工发布审批由 migration 109 增加，要求超级管理员对最新 PASS Gate 的确切 decision ID 追加批准/拒绝意见，记录发布引用、理由、操作者和时间；租户内 request ID 提供幂等，审批行受 RLS 和不可变触发器保护，并写入 Audit。该记录不代表 CI/Git 发布门禁，也不会触发部署。尚未实现 Judge 与人工一致率/分歧分析。Compare 仅对同一 Suite、Golden 和 Corpus、均完成且结果 case 集相同的 Run 汇总数值指标；总体二分类 pass/fail 差新增配对 95% Wilson 近似区间与双侧精确 McNemar 检验，错误/跳过状态不纳入统计。脱敏 category/scenario/risk/difficulty 切片按标签组汇总；有效样本量至少 20 的组对双方均为 pass/fail 的配对样本执行双侧精确 McNemar 检验，所有可检验切片统一进行 Holm 多重比较校正。error/skip 排除；切片结果仅作探索，不参与 Gate。

修订候选审核通过后，API 返回 `promoted_case_revision`。若其他候选已基于同一 revision 先行通过，旧候选的基础 revision 校验会失败并返回 409；发布 v2 时可引用新 revision，已发布 v1 的 manifest、hash 和既有 Run 引用保持原值。管理端只展示每个 Case 当前最新的已审核 revision，避免误把旧 revision 重新发布。

Runner 是受控兼容适配，不代表目标态全部能力已经完成：当前只允许可信 `default` tenant 的 `super_admin` 创建运行。RAG 支持 deterministic `answer_quality`、evidence `evidence_quality` 和独立留痕的 Human Rubric；`travel`、`travel-provider` 使用现有离线质量 Runner，不调用模型或真实 Provider。`sql`、`cs`、`e2e` 只做输入/Expected 契约检查，Gate 固定 `INVALID`，且禁止人工质量复核；这三个模块不能用于评估业务质量。`planner` 尚未接入 V2 Runner。LLM Judge、Judge 与人工一致率/分歧分析、完整逐题 Token/费用归因、直接 CI 发布审批尚未迁移。RAG 费用未由旧 Runner 归因时明确标记 `not_reported_by_legacy_runner`，不显示为零；离线非 RAG Runner 标记 `not_applicable_no_model_call`。其余 tenant 的运行创建会返回 403；现有旧任务存储与报告仍按单租户适配，不得据此宣称完成多租户运行迁移。

Run 的 `snapshot_json.environment` 固定 runner、Suite/Golden/Corpus 哈希、评分器定义、Prompt 版本和 RAG 检索运行环境；`env_hash` 对整个摘要做内容哈希。运行环境只保留模式、模型/供应商标识、策略和本地模型路径指纹，不保存凭据、URL 或机器绝对路径。Worker 启动及逐例边界复核运行环境，漂移或缺少新快照时失败并写入 `INVALID` Gate；旧 Run 详情可读，但缺环境指纹的旧 queued/failed Run 不会按新配置继续执行。

Ragas 合成 Worker 通过既有任务系统把输出写入 V2 待审候选，模型生成事实不会写入 `expected`；旧导入记录仍作为兼容镜像保留。Trace 候选只从当前身份可见的 Trace 建立。候选审核和 Golden 发布会按可信 Principal 的 KB scope，重新匹配当前 active 文档版本、原文锚点和源文件哈希；不接受客户端提供的校验哈希。RAG 文档注册表与评测数据库分属不同存储，发布前核验与数据库事务无法跨库原子提交，因此并发下架仍有短暂 TOCTOU 窗口，正式上线前需增加跨库版本租约或统一快照机制。迁移 108 已在隔离 PostgreSQL 容器执行，并验证 tenant RLS 与审计不可变；尚未迁入本机共享数据库。原型中的 Mock 记录、成功 toast 和分数不得作为 API 数据。

前端运行、对比、Gate、数据集目录/差异/导出与概览已改接 V2 API。Golden JSONL 导出会在服务端复算 manifest hash、按当前知识库访问范围核对回答证据，并记录不含正文的审计。隔离 Compose 中已实际创建数据集/候选、审核 CAS、发布无证据拒答 Golden、下载 JSONL、核对导出审计并验证 viewer 被拒绝；使用的是 loopback 测试身份头，不是网关/JWT 登录验收。现有管理端开发页仍连接旧后端，概览请求返回 404；登录态下的完整浏览器/API/任务闭环尚未验收。共享 Compose 不得重建。

V2 数据集中心可为已审核 Case 创建新修订候选并跳转到既有审核队列；审核通过后发布 Golden 可选择最新 Case revision。隔离 API 已验证同一 Case 从 r1 修订到 r2、v1 hash 保持不变、版本差异识别一个 revision 变化，以及同一基础 revision 的旧候选在并发推进后返回 409。此实测不包含引用旧版的真实 Run，也不构成 Gateway/JWT 浏览器验收。

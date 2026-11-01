# CI 与评测环境运行规范

> **文档类型**：现行跨模块运维/CI 契约。**实现状态快照日期：2026-10-30（每周 Full Regression / PR #30 合入后状态刷新，main 基线 `2517e98`）；其它状态保留各自标注的核对日期。**
> **职责**：规定 CI 如何准备隔离数据库、DB API 模型绑定、评测语料和向量索引，以及 Secrets、费用与证据闭环。**不**复制测试分层（[testing-guide](../development/testing-guide.md)）、质量阈值与 Golden 规则（[evaluation-governance](../evaluation/evaluation-governance.md)）、计费公式（[llm-budget-billing](../governance/llm-budget-billing.md)）、RBAC 规则（[authorization-standard](../auth/authorization-standard.md)）或数据库 Schema（[DATABASE](../DATABASE.md)）。
>
> **实现状态图例**（本文所有条款按下表标注，状态不得凭文档存在认定）：
>
> | 标记 | 含义 |
> | --- | --- |
> | ✅ 已实现 | main 分支已有代码/配置，且有测试或实跑证据 |
> | ❌ 尚未实现 | 目标契约，当前无对应实现；不得当作用例已通过 |
> | ⏳ 环境待验 | 实现已提交，但需要外部条件（Secrets、真实 Run）才能核验 |

## 1. 既有权威与边界

- 测试层级 T0–T3：[testing-guide](../development/testing-guide.md)；发布门禁：[release-quality](release-quality.md)。
- Golden、Suite、Runner、指标与有效性 E0–E3：[evaluation-governance](../evaluation/evaluation-governance.md)。本文不另立阈值。
- 迁移与目标库：[DATABASE](../DATABASE.md)、[`scripts/init_db.py`](../../scripts/init_db.py)；数据生命周期：[data-lifecycle](../governance/data-lifecycle.md)。
- 模型/凭据唯一运行配置源：[model-provider-runtime](../governance/model-provider-runtime.md)；计费与账本：[llm-budget-billing](../governance/llm-budget-billing.md)。
- 测试 KB 授权：[authorization-standard](../auth/authorization-standard.md)、[`backend/config/knowledge_base.py`](../../backend/config/knowledge_base.py)、[`backend/rag/authz.py`](../../backend/rag/authz.py)。

**核心原则**：GitHub 只保存版本化的非敏感评测资产与初始化说明；每次真实集成评测使用隔离存储与数据库；模型运行时只从 CI 数据库内的注册表/绑定解析；外部云 API 是真实调用、真实计费。CI **不**读取本机或生产 PostgreSQL，不复用本机既有向量。

## 2. 四种资产（禁止概念混用）

| 资产 | 权威事实源 | 状态与边界 |
| --- | --- | --- |
| 测试知识库（KB） | [`backend/config/knowledge_base.py`](../../backend/config/knowledge_base.py) | ✅ 已实现：`rag_eval_kb`、`cs_eval_kb`、`travel_eval_kb` 注册为 `audience=test`；旧 `rag_test_kb`、`rag_100_docs` 为 deprecated 兼容别名（`alias_for=rag_eval_kb`）。测试 KB 不是测试数据库实例。 |
| 评测模块 | [`backend/evaluation/models.py`](../../backend/evaluation/models.py) 的 `MODULE_KINDS` | ✅ 已实现：`planner, rag, cs, sql, e2e, travel, travel-provider, travel-booking`（2026-11-01 收编重构后；`travel-commerce` 探针随原商务域图壳退役）。模块不与向量 KB 一一绑定。 |
| Golden / Suite / Fixture | [`backend/evaluation/datasets/`](../../backend/evaluation/datasets/)、[`backend/evaluation/fixtures/`](../../backend/evaluation/fixtures/)、`data/docs/` | ✅ RAG 已实现（manifest/hash/suites）；❌ `travel-booking` 仅 `cases.jsonl` 无 manifest 锁版（evaluation-governance 已登记的资产缺口）。 |
| CI 数据库/向量索引 | GitHub Actions 临时 PostgreSQL/pgvector + 迁移 + 既有入库器 | ✅ 已实现：每次运行隔离重建（`rag_regression.yml`/`rag_smoke.yml` 全套 DB env + 兼容库预建）；实现细节见 §4。 |

### 2.1 超级管理员测试库权限（✅ 已实现）

- 管理面：`readable_kb_ids(include_test=)` 仅 `super_admin` 放开 `audience=test`（[`backend/rag/authz.py`](../../backend/rag/authz.py)）；普通 admin（`rag.admin` 权限点）可见全部非 test 库但**不可见** test 库。
- 检索面：`backend/rag/authz.py` 的 `retrieval_authorized_kbs()` 对 `super_admin` 传入 `include_test=True`，因此超管可检索 test 库用于管理与评测验证；admin 及以下主体不可检索 test 库，customer 主体仅见 `audience=customer`。
- 跨库兜底：`audience=test` 由属性推导进入兜底禁入名单（`CROSS_KB_FALLBACK_EXCLUDE_TEST`，默认 true），虚构语料不得经放宽路径对客召回。
- 前端上传 KB 列表来自 `GET /api/rag/knowledge-bases`，按 `RagAuthorization.can_search_kb` 后端裁决收敛；上传走 `can_upload_to` 同一授权视图。权限由服务端负责，前端隐藏不是边界。
- **Runner 接线警示（❌ 尚未实现）**：`cs_eval_kb` / `travel_eval_kb` 已登记，但 `backend/evaluation/` 下当前无任何 Runner 引用这两个 KB（全仓零引用，2026-10-11 核对）——登记不等于评测已消费。SQL/Planner/E2E 不绑定 RAG KB。

### 2.2 CI 身份与用户登录分离（✅ 架构上已分离）

CI 通过脚本直连隔离数据库导入版本化 fixtures（`import_fixture`），不模拟超管登录上传页面；CI 的内部任务身份与用户登录权限是两条链路，不存在"内部调用"绕过生产库隔离的路径——CI 库与生产库物理隔离（§4）。

## 3. 三种环境与模型口径

| 环境 | 存储 | 模型配置来源 | 可声明的结果 | 状态 |
| --- | --- | --- | --- | --- |
| 本地真实评测 | 独立测试库/容器（如 `agent_memory_eval_ci`），不得清洗共享 `agent_memory` | DB 注册 API 模型，`ENV_MODE=cloud` | 仅代表本机对应 commit/模型/数据集版本的结果 | ✅ 流程已实跑验证（29 用例 96.5% PASS，随 PR #30 合入 main） |
| GitHub 静态/单元 CI | 无需真实向量库，可用确定性 fake | 不调用付费模型 | E0/E1 契约与 Mock 测试，不得标为真实 API 质量 PASS | ✅ [`evaluation-v2.yml`](../../.github/workflows/evaluation-v2.yml) 已合入 main 并实跑：后端编译后先跑无模型 `pr_smoke` E0 suite/manifest/hash/case 预检，再跑 V2 确定性契约与离线 SQL/客服/旅游兼容、pgvector SQL 翻译和任务错误分类用例；管理端在 Node 22 上跑评测中心 Vitest、TypeScript 和生产构建，不注入 Provider Secrets。它不是全仓通用后端测试 workflow，全量口径由每周 Full Regression 承担（§8）。 |
| GitHub 受信任真实集成 CI | **每次隔离**的 PostgreSQL/pgvector、索引目录 | 初始化后从 CI DB 的 Provider/Model/Role/Specialized Binding 加载真实 API 模型 | E2 真实 Provider/检索质量，附成本与指纹 | ✅ 已合入 main：`rag_regression.yml`/`rag_smoke.yml` 均为 `ENV_MODE=cloud` + DB API specialized binding（本地 BGE/Ollama/旧 env 凭据回退被显式禁止，维度默认 1024） |

`ENV_MODE=cloud` 是真实评测的**模型运行策略**，不表示连接生产 DB。真实评测禁止静默回退 Ollama/BGE、旧环境凭据或未声明模型。

### 3.1 配置同步契约

- **Secrets → 加密入库 → 运行时读 DB（✅ 已实现骨架 / 🚧 扩展中）**：[`backend/scripts/seed_prompt_eval_db.py`](../../backend/scripts/seed_prompt_eval_db.py)（main 已有）从受保护 Environment Secrets 读显式配置，按既有 Fernet 加密链写入 CI 临时 DB，运行时经 `registry_store.refresh_registry()` 从 DB 解析；rerank 绑定扩展与维度 env 化已随 PR #30 合入。**禁止旧 env 凭据回退**（`credentials.py`：DB 是唯一来源，未登记即未配置）。
- **版本化脱敏模型快照文件（❌ 尚未实现）**："从管理端 DB 导出快照并版本化提交、CI 对比快照与注入绑定差异"为目标契约，当前无对应实现与快照文件。
- **维度口径（✅ 探针已接入 CI / 值非长期标准）**：当前实例值 Embedding 维度 **1024**（qwen3.7-text-embedding）。实际维度必须由绑定声明、真实 API 探针（`scripts/seed_eval_model_bindings.py --probe`，✅ `rag_regression.yml` 已接入 `--verify --probe`；`rag_smoke.yml` 仅 `--verify`）、pgvector 写入后 `vector_dims` 断言（✅ 已实现）三方一致确认；换模型或维度必须重建不兼容索引（[model-provider-runtime MP-09](../governance/model-provider-runtime.md)）。

## 4. GitHub Actions 真实 RAG 评测流水线

> 目标契约共 10 步。**main 现状（2026-10-30）**：`rag_regression.yml`/`rag_smoke.yml` 已覆盖其中大部分（隔离 DB、seed/verify/probe、cloud 口径、预算闸、费用对账硬 gate）；下表逐条标注。

| # | 步骤 | 状态 | 事实源/说明 |
| --- | --- | --- | --- |
| 1 | 入口与信任检查（SHA、事件、路径、Environment 保护） | ✅ 已实现 | `environment: prompt-eval` 保护 ✅（prompt_eval、rag_smoke/rag_regression 复用）；fork PR 拿不到 Environment Secrets 为平台行为 ✅；**调用级预算闸 ✅ 已实现**（`rag_smoke.yml` `LLM_BUDGET_MODE=enforce` + 调用次数/token/费用上限；另保留 `timeout-minutes` 墙钟） |
| 2 | 资产预检（suite/manifest/hash/空 case → INVALID） | ✅ 已实现 | [`backend/evaluation/preflight.py`](../../backend/evaluation/preflight.py)（E0），workflow 已接入 |
| 3 | 临时资源就绪（隔离 PG/pgvector + 迁移 + 全套 DB env） | ✅ 已实现 | pgvector service 容器 ✅；`agent_business` 兼容库预建 + 全套 `*_PGDATABASE` env 指向隔离库 ✅（`rag_smoke.yml`/`rag_regression.yml`，缺 env 回落共享库名的红线已由显式 env 消除） |
| 4 | 模型 Seed（Secrets→加密入库→refresh→逐 role 校验） | ✅ 已实现 | seed 脚本 + `--verify` 逐 role 校验 + rerank 绑定扩展均已接入 workflow |
| 5 | 真实 Provider 探针（连通性/实际维度，失败 BLOCKED 不降级） | ✅ 已实现 | `seed_eval_model_bindings.py --probe`（`rag_regression.yml` 已接入；隔离环境实跑通过 1024=1024） |
| 6 | 入库建索引（`import_fixture` → `ingest_eval_fixtures` → 既有索引管线） | ✅ 已实现 | 导入器 + 语料范围裁剪（剔除业务 KB 目录防 near-dup 交叉）均已实现 |
| 7 | 索引验收（数量/维度/KB/权限/fixture_set + **可检索已知样本**） | 🚧 部分 | 数量与维度断言 ✅；**已知样本检索探针 ❌ 未实现**（当前仅断言非空与维度） |
| 8 | 按 Suite 运行（`backend.evaluation.cli` + Runner，检索与生成评测分开报告） | ✅ 已实现 | CLI 与 Runner、检索评测（`--no-ragas`）与生成/Judge 评测（`--judge/--ragas` 显式 opt-in）天然分离 ✅ |
| 9 | 成本与证据（commit_sha/eval_run_id/指纹/`ci_evaluation` 归因/费用/脱敏 Artifact） | 🚧 部分 | 费用证据 ✅：workflow 采集 llm_usage/budget_reservations 基线快照并执行 "Require reconciled cost ledger" 硬 gate（费用须 VERIFIED/ESTIMATED、账本须 VERIFIED）；`call_origin` 字段在 Billing V2 中仍不存在（§6）；**索引指纹机制 ❌ 未实现**（当前每次从 fixtures 重建，无可复用缓存——符合"默认重建"底线，但指纹/校验缓存缺失） |
| 10 | 终态与清理（原始退出码决定 Job、失败保留证据、清理临时资源） | ✅ 已实现 | service 容器随 Job 自动清理 ✅；原始退出码直捕（`set -o pipefail` + `exit $?`）✅ |

**索引指纹**（语料 hash + 切分版本 + Embedding 模型/维度 + KB/fixture_set/索引版本）为 ❌ 未实现的目标契约；指纹一致的已验证缓存才可复用，默认每次隔离运行从 fixtures 重建——当前实现满足"默认重建"，不满足"指纹与缓存校验"。

## 5. SQL、Planner、E2E 等模块的 CI 依赖

| 模块 | Runner | 数据依赖 | CI 集成状态 |
| --- | --- | --- | --- |
| SQL | [`runners/sql.py`](../../backend/evaluation/runners/sql.py) | 隔离业务库 Schema/测试行/授权（不依赖任何向量 KB） | ❌ 尚未实现：main 无 SQL CI workflow，评测在本地/评测中心执行 |
| Planner / Router | [`runners/planner.py`](../../backend/evaluation/runners/planner.py) | 固定意图/候选/规划用例；模型调用按 E1/E2 口径声明 | ❌ 尚未实现：无 CI workflow |
| E2E | [`runners/e2e.py`](../../backend/evaluation/runners/e2e.py) | Graph 全链路（进程内 Router→Skill→Reporter），非网关级 | ❌ 尚未实现：无 CI workflow |
| CS / Travel | [`runners/cs.py`](../../backend/evaluation/runners/cs.py)、[`runners/travel.py`](../../backend/evaluation/runners/travel.py) | `cs_eval_kb`/`travel_eval_kb` **已登记未接线**（§2.1） | ❌ 无 CI workflow，且 KB 消费链需先审计接入 |

各模块质量阈值以现行 Runner、Suite 与 [evaluation-governance](../evaluation/evaluation-governance.md) 为准；CI 环境只提供依赖与隔离，不定义第二套质量规则。

## 6. Secrets、权限、费用的强制红线

- **Fork/不可信 PR 隔离（✅ 已实现）**：仓库 workflow 无 `pull_request_target`（2026-10-11 全量核对）；付费 Job 使用 `environment: prompt-eval` 受保护环境；fork PR 按平台规则拿不到 Environment Secrets。
- **Secrets 不落库/不落日志（✅ 机制已有，持续执行）**：Secrets 只经 GitHub Actions 注入 seed 脚本加密入库；GitHub 自动 mask 注入值（run 38142127992 日志中 `PROMPT_EVAL_TEST_RERANK_BASE_URL: ***` 实证）。数据库导出/Artifact/快照不得含明文或可复用密文。
- **预算闸（✅ 调用级预算闸已实现）**：`rag_smoke.yml` 真实评测启用 `LLM_BUDGET_MODE=enforce`，配 `LLM_REQUEST_MAX_CALLS/TOKENS/COST` 做调用级预算闸；并发/整体预算上限仍未细分。**超时不能替代调用前预算闸**（[llm-budget-billing](../governance/llm-budget-billing.md) BG 系列口径）。
- **RAG 真实评测价格来源（PR #77）**：`rag_smoke` 和 `rag_regression` 在任何 Provider Probe 前，必须从受保护 Environment Secret `PROMPT_EVAL_PRICE_REGISTRY_DATABASE_URL` 指向的**经批准、隔离的 PostgreSQL 只读副本**读取现行价格。Seed 必须关联 `model_price_versions` 且仅接受 `status='active'`、来源与版本一致的记录；待审、canary、expired、rejected 和未关联版本的价格都不能进入付费评测。价格行和原版本发布/审核证据复制到本次临时评测库，保持版本、来源、币种、有效期、双人审核和维度不变。只读副本必须处于 PostgreSQL recovery 状态并使用只读事务；Secret 仅注入 Seed 步骤。测试库写入以 `PROMPT_EVAL_TEST_PGDATABASE` 锁定目标，并优先使用对应的 `PROMPT_EVAL_TEST_PGHOST/PORT/USER/PASSWORD`；未单独提供的连接字段可使用 workflow 专门配置的 `PGHOST/PORT/USER/PASSWORD`。Seed 在同一连接首个写入前校验目标库、连接角色、主机、端口和可写主库身份；`MEMORY_PGDATABASE` 与测试库不一致时失败关闭，不修改环境变量切换目标。可选变量 `PROMPT_EVAL_PRICE_REGISTRY_TABLE_PREFIX` 用于既有表前缀。缺少有效 active 版本、必需维度、来源或审批证据时，付费评测保持 BLOCKED，不得以零成本或未定价结果代替。Registry 连接只读，不接收评测数据、模型凭据或账本写入。
- **`ci_evaluation` 费用归因（🚧 部分实现）**：Billing V2（迁移 `079`、[`llm_usage_store_pg.py`](../../backend/observability/llm_usage_store_pg.py)）**无 `call_origin` 字段**，跨环境归因口径缺失；但 `rag_smoke.yml` 已开启 `OBS_ANALYTICS_ENABLED`，评测调用进入隔离库 `llm_usage`，workflow 在评测后采集 llm_usage/budget_reservations 基线快照并执行 "Require reconciled cost ledger" 硬 gate（费用须 VERIFIED/ESTIMATED、账本须 VERIFIED、超限即 fail）。费用账本链路已闭环于隔离库；`call_origin` 归因与跨环境对账（❌ 未实现）仍挂账。
- **CI 账本与生产看板（✅ 天然隔离）**：CI 隔离库账本不会自动写入本机/生产看板；跨环境聚合需受控对账机制（❌ 未实现）。

## 7. 失败与发布语义

| 情况 | 结论与动作 | 状态 |
| --- | --- | --- |
| 数据/绑定/密钥/维度/索引验证失败 | `INVALID`/`BLOCKED`，Job 非成功；禁止伪造质量 FAIL 或改用本地模型 | ✅ 三态裁决已实现（[`release_gate.py`](../../backend/evaluation/release_gate.py) + `validity.py`）；CI 步骤级 BLOCKED ✅（run 38142127992 实证缺 RERANK_API_KEY 时显式 RuntimeError） |
| 真实评测运行但指标低于强制阈值 | `FAIL`，保留失败 case_id，不删用例、不降阈值 | ✅ 三态与阈值裁决已实现（pass_rate 键错位与 GATE-12 样本量死锁已修复） |
| 全部通过 | `PASS` 仅针对本次 Suite/commit/模型快照，不等于 Production READY | ✅ 语义已实现 |
| 路径不相关跳过付费评测 | `SKIP` + 原因，不得写成质量 PASS | ✅ 已实现（rag_smoke 路径检测，job 显示 skipped） |
| 调用未进费用账本 | 质量另行判断，费用归因 `UNVERIFIED`，不报 0 元 | ✅ 隔离库账本已接入并作为硬 gate（§6）；`call_origin` 归因仍缺失，跨环境对账 ❌ 未实现 |

**Regression 命名语义（✅ 已声明）**：定时 `Regression` 必须区分"固定 Suite 阈值检测"与"历史 baseline 指标对比"。当前未启用历史对比（`--compare`/`--promote-baseline` 机制存在于 [`gate/regression.py`](../../backend/evaluation/gate/regression.py) 但未接入 workflow），`rag_regression.yml` 注释中已明示"固定样本质量门禁，不含历史基线对比"，不得宣称已检测相对回退。

## 8. 变更触发、核对与验收

- 低风险文档/纯静态：T0，默认不触发付费评测（✅ 现有路径过滤已实现）。
- RAG 上传/切分/检索/Embedding/Rerank/MCP 入口/模型/Fixture/Suite，以及 LLM 预算、用量账本、价格算法变更：触发对应静态契约 + 受信任 RAG E2 检索评测。SQL migration 使用精确清单；费用账本基础迁移 `013_obs_analytics_pg.sql` 与模型价格、LLM usage、预算及 RAG 迁移均在清单内。客服、旅游和价格巡检专用迁移不触发付费 RAG 评测；任何 migration PR 仍运行免费路径/工作流契约检查。`backend/infra/llm/price_page_monitor.py` 与 `backend/sql/migrations/099_model_price_source_monitor.sql` 是只读巡检路径，PR/main-push 均按不相关路径 skip RAG E2；若同一变更还修改了 RAG 相关路径，仍会触发评测。
- SQL/Planner/E2E/鉴权/RBAC/租户变更：按对应 Runner 与数据集选择；不机械全域重建向量（❌ CI 集成未实现，见 §5）。
- 改模型/维度/索引 Schema/阈值：版本相容性验证、失配反例、真实 API 探针、必要重索引及可比性说明（🚧 探针已实现；版本化快照与失配反例 ❌ 未实现）。

**真实评测触发矩阵（用户基准：2026-10-11；路径过滤核验：2026-10-16）**：

| 触发场景 | 真实 API | 测试范围 | 落地方式 |
| --- | --- | --- | --- |
| 与 RAG 无关的普通代码 PR | 不需要 | T0/T1、受影响范围定向测试 | ✅ 路径过滤 skip RAG jobs；相关路径 PR 才运行免费预检（真实评测 job 对 PR 事件 skipped） |
| 纯文档、页面样式 PR | 不需要 | 静态检查 | ✅ 路径过滤 skip |
| 模型价格页巡检解析器及其专用迁移（`price_page_monitor.py` / `099_model_price_source_monitor.sql`） | 不需要 | 价格解析、巡检 API/Worker 与迁移定向测试 | ✅ PR/main-push skip RAG E2；同一变更中的真实 RAG 路径仍触发 |
| 客服或旅游独立 SQL migration | 不需要 | 对应领域迁移契约 | ✅ 不触发付费 RAG E2；PR 运行免费门禁契约检查 |
| LLM usage、预算、计价或 `013_obs_analytics_pg.sql` | 需要 | 费用路径定向测试 + RAG Smoke | ✅ 精确分类后触发；客服/旅游/巡检路径排除 |
| RAG 检索算法、切分策略修改 | 需要 | RAG Smoke + 必要的 Regression | 合并 main 后自动 Smoke；Regression 审核后 workflow_dispatch |
| Embedding、Rerank 模型切换 | 必须 | 索引重建 + Golden 回归 | 每次运行强制索引重建；probe 维度三方校验失配即 BLOCKED；Golden 回归 dispatch selection=regression |
| SQL Agent、Planner 修改 | 按风险运行 | 对应领域真实模型评测 | Runner 已有，CI workflow 未建（❌ 见 §5） |
| 每日定时回归 | **付费 RAG Golden 不在 GitHub 定时运行** | 固定 Golden，监测质量 | 🚧 `rag_regression.yml` 仅 workflow_dispatch（无 schedule）；定时回归由管理端定时任务承担（配置入口：管理端） |
| 每周全量回归（Full Regression） | 不需要 | 全量 pytest（约 12500 用例，13 suite matrix 分治，各自隔离 DB） | ✅ [`full_regression.yml`](../../.github/workflows/full_regression.yml) 每周一 03:00 UTC 定时运行 + workflow_dispatch（`suites` 参数按 suite 重跑）；失败即红但不属任何 PR 门禁，不挡合并 |
| 重要版本发布前 | 需要 | 受影响领域真实 API 和 E2E | 审核后 workflow_dispatch 按域运行；E2E 属发布门禁 |

RAG Smoke 的 `workflow_dispatch` 提供 `multiquery` 布尔参数，默认 `true`（生产检索链默认启用，`false` 仅用于对照）；同一 `pr_smoke` 集会额外经过 MultiQuery 路径。PR 事件不会调用付费 API；main push 强制按生产口径启用 MultiQuery。

真实 Smoke 在 Probe、索引和评测调用之前执行 `seed_prompt_eval_db --verify-pricing`。
`prompt-eval` 环境变量 `PROMPT_EVAL_APPROVED_PRICES_JSON` 只接收源 Registry
已批准的价格行快照，字段为 `model_name/component/dimension/price_per_unit/unit/currency`、
`price_table_version/source/effective_from/effective_to/approval_status/reviewer_1/reviewer_2`。
快照必须覆盖本次绑定模型的必需计价维度，保留原始来源、版本和两位不同审查人；
缺价、失效、未批准或同版本内容冲突均阻止付费调用。导入只写隔离测试库，不能生成审批、
修改源价格或用自拟单价补齐账本。启用 MultiQuery 时，seed 同时将改写角色绑定到本次显式测试的聊天模型。

embedding 与 rerank 复用统一请求预算和预占账本；缓存命中不计出站，rerank 每次 HTTP
重试分别预占。embedding 的字符 token 估算必须标记 `estimated`；未知价格、缺少用量和
结果不明保留 `needs_review`。质量结果通过后，费用状态仍须为 `VERIFIED/ESTIMATED`、
预算账本须为 `VERIFIED`；任何身份缺失、未决预占或结算差额都阻止真实 Smoke 通过。

**验收矩阵 CI-01～CI-10（实现状态逐条标注）**：

| ID | 要求（摘要） | 状态 |
| --- | --- | --- |
| CI-01 | 固定 Suite/Manifest/hash/样本数可复核 | ✅ 已实现（preflight + suites + cases_hash） |
| CI-02 | 测试 KB 仅 super_admin 可操作；检索/兜底/跨库全路径隔离 | ✅ 已实现（§2.1；全链路系统性穿透测试未跑，属 T2 范围按需执行） |
| CI-03 | 隔离 DB/目录/索引不含共享数据；缺省配置拒绝危险写入 | ✅ 已实现（全套 `*_PGDATABASE` env 指向隔离库 + 兼容库预建 + 语料裁剪）；"缺省即拒绝"逻辑部分依赖 backend 默认值行为，**未做专项守卫测试** |
| CI-04 | 绑定/凭据从 CI DB 读取；真实调用、维度三方一致；缺失不本地回退 | ✅ 已实现（verify+probe+断言；`rag_regression.yml` 实跑通过；`rag_smoke.yml` 仅 verify，probe 待补 ⏳） |
| CI-05 | 既有导入器生成完整索引 + 检索探针成功 | 🚧 部分（导入/数量/维度断言 ✅；已知样本检索探针 ❌ 未实现） |
| CI-06 | 各模块按官方 Suite 判定；0 样本/全 SKIP 不 PASS | ✅ 判定语义已实现（gate 三态 + SKIP 不计成功）；各模块 CI 集成 ❌（§5） |
| CI-07 | Fork/不可信 PR 无付费 Secrets；Environment 有检查证据 | ✅ 已实现（无 pull_request_target + environment 保护；分支限制规则需仓库设置核对 ⏳） |
| CI-08 | 预算/并发/重试/取消终态可核对；费用归因可查或 UNVERIFIED | 🚧 调用级预算闸 ✅ + 隔离库账本硬 gate ✅；`call_origin` 归因缺失，跨环境对账 ❌（§6） |
| CI-09 | 真实退出码、Job 终态、脱敏 Artifact、SHA/指纹一致 | ✅ 已实现（退出码直捕 + Artifact + SHA）；**索引指纹 ❌ 未实现** |
| CI-10 | 未实现/未验证项记录在审查包 | ✅ 执行中（PR #30 审查包 + 本文状态标注） |

## 9. 文档维护与交付

- 本文件按 [documentation-standard](../development/documentation-standard.md) 维护：T0 检查链接、路径、重复事实源与敏感信息；导航入口在 [docs/README.md](../README.md)。
- **实现状态是本文的一部分**：CI 代码/配置变化必须同 PR 更新 §2–§8 的状态标注与快照日期；状态只有四个合法值（✅/🚧/❌/⏳），禁止删除标注只留目标描述。
- 纯文档 PR 只做 T0；不自动重跑付费模型或全量测试。
- 交付必须列：PR URL、完整 HEAD SHA、变更文件、规范↔源码↔证据对应、实际 CI Run/Artifact、PASS/FAIL/SKIP、BLOCKED/UNVERIFIED，以及不能宣称的范围。未经明确批准不合并、不操作共享库、不配置或读取生产凭据。

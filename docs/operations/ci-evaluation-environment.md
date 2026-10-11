# CI 与评测环境运行规范

> **文档类型**：现行跨模块运维/CI 契约。**实现状态快照日期：2026-10-11，基线 commit `69ccde5`（main）+ PR #30 施工分支。**
> **职责**：规定 CI 如何准备隔离数据库、DB API 模型绑定、评测语料和向量索引，以及 Secrets、费用与证据闭环。**不**复制测试分层（[testing-guide](../development/testing-guide.md)）、质量阈值与 Golden 规则（[evaluation-governance](../evaluation/evaluation-governance.md)）、计费公式（[llm-budget-billing](../governance/llm-budget-billing.md)）、RBAC 规则（[authorization-standard](../auth/authorization-standard.md)）或数据库 Schema（[DATABASE](../DATABASE.md)）。
>
> **实现状态图例**（本文所有条款按下表标注，状态不得凭文档存在认定）：
>
> | 标记 | 含义 |
> | --- | --- |
> | ✅ 已实现 | main 分支已有代码/配置，且有测试或实跑证据 |
> | 🚧 PR #30 施工中 | 代码已在 [`task/ci-eval-gate-20261011`](https://github.com/Jayus-07/agent/pull/30) 提交，未合入 main |
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
| 评测模块 | [`backend/evaluation/models.py`](../../backend/evaluation/models.py) 的 `MODULE_KINDS` | ✅ 已实现：`planner, rag, cs, sql, e2e, travel, travel-provider, travel-commerce, travel-booking`。模块不与向量 KB 一一绑定。 |
| Golden / Suite / Fixture | [`backend/evaluation/datasets/`](../../backend/evaluation/datasets/)、[`backend/evaluation/fixtures/`](../../backend/evaluation/fixtures/)、`data/docs/` | ✅ RAG 已实现（manifest/hash/suites）；❌ `travel-commerce`、`travel-booking` 仅 `cases.jsonl` 无 manifest 锁版（evaluation-governance 已登记的资产缺口）。 |
| CI 数据库/向量索引 | GitHub Actions 临时 PostgreSQL/pgvector + 迁移 + 既有入库器 | 🚧 PR #30 施工中：每次运行隔离重建；main 现状见 §4。 |

### 2.1 超级管理员测试库权限（✅ 已实现）

- 管理面：`readable_kb_ids(include_test=)` 仅 `super_admin` 放开 `audience=test`（[`backend/rag/authz.py`](../../backend/rag/authz.py)）；普通 admin（`rag.admin` 权限点）可见全部非 test 库但**不可见** test 库。
- 检索面：`authorized_kbs()` 对任何检索主体隐藏 test 库；customer 主体仅见 `audience=customer`。
- 跨库兜底：`audience=test` 由属性推导进入兜底禁入名单（`CROSS_KB_FALLBACK_EXCLUDE_TEST`，默认 true），虚构语料不得经放宽路径对客召回。
- 前端上传 KB 列表来自 `GET /api/rag/knowledge-bases`，按 `RagAuthorization.can_search_kb` 后端裁决收敛；上传走 `can_upload_to` 同一授权视图。权限由服务端负责，前端隐藏不是边界。
- **Runner 接线警示（❌ 尚未实现）**：`cs_eval_kb` / `travel_eval_kb` 已登记，但 `backend/evaluation/` 下当前无任何 Runner 引用这两个 KB（全仓零引用，2026-10-11 核对）——登记不等于评测已消费。SQL/Planner/E2E 不绑定 RAG KB。

### 2.2 CI 身份与用户登录分离（✅ 架构上已分离）

CI 通过脚本直连隔离数据库导入版本化 fixtures（`import_fixture`），不模拟超管登录上传页面；CI 的内部任务身份与用户登录权限是两条链路，不存在"内部调用"绕过生产库隔离的路径——CI 库与生产库物理隔离（§4）。

## 3. 三种环境与模型口径

| 环境 | 存储 | 模型配置来源 | 可声明的结果 | 状态 |
| --- | --- | --- | --- | --- |
| 本地真实评测 | 独立测试库/容器（如 `agent_memory_eval_ci`），不得清洗共享 `agent_memory` | DB 注册 API 模型，`ENV_MODE=cloud` | 仅代表本机对应 commit/模型/数据集版本的结果 | 🚧 流程已在 PR #30 实跑验证（29 用例 96.5% PASS），脚本随 PR #30 |
| GitHub 静态/单元 CI | 无需真实向量库，可用确定性 fake | 不调用付费模型 | E0/E1 契约与 Mock 测试，不得标为真实 API 质量 PASS | ❌ 尚未实现：main 当前无通用定向测试/类型检查 workflow（release-quality 已登记该缺口） |
| GitHub 受信任真实集成 CI | **每次隔离**的 PostgreSQL/pgvector、索引目录 | 初始化后从 CI DB 的 Provider/Model/Role/Specialized Binding 加载真实 API 模型 | E2 真实 Provider/检索质量，附成本与指纹 | 🚧 PR #30 施工中；main 现状：`rag_regression.yml` 仍为本地 BGE 512 维口径（与生产脱节，已知问题） |

`ENV_MODE=cloud` 是真实评测的**模型运行策略**，不表示连接生产 DB。真实评测禁止静默回退 Ollama/BGE、旧环境凭据或未声明模型。

### 3.1 配置同步契约

- **Secrets → 加密入库 → 运行时读 DB（✅ 已实现骨架 / 🚧 扩展中）**：[`backend/scripts/seed_prompt_eval_db.py`](../../backend/scripts/seed_prompt_eval_db.py)（main 已有）从受保护 Environment Secrets 读显式配置，按既有 Fernet 加密链写入 CI 临时 DB，运行时经 `registry_store.refresh_registry()` 从 DB 解析；PR #30 扩展可选 rerank 绑定并将维度 env 化。**禁止旧 env 凭据回退**（`credentials.py`：DB 是唯一来源，未登记即未配置）。
- **版本化脱敏模型快照文件（❌ 尚未实现）**："从管理端 DB 导出快照并版本化提交、CI 对比快照与注入绑定差异"为目标契约，当前无对应实现与快照文件。
- **维度口径（✅ 探针已实现 / 值非长期标准）**：当前实例值 Embedding 维度 **1024**（qwen3.7-text-embedding）。实际维度必须由绑定声明、真实 API 探针（`scripts/seed_eval_model_bindings.py --probe`，🚧 PR #30）、pgvector 写入后 `vector_dims` 断言（🚧 PR #30）三方一致确认；换模型或维度必须重建不兼容索引（[model-provider-runtime MP-09](../governance/model-provider-runtime.md)）。

## 4. GitHub Actions 真实 RAG 评测流水线

> 目标契约共 10 步。**main 现状**：`rag_regression.yml`/`rag_smoke.yml` 仅覆盖其中 1/2/6/8 的旧形态（本地模型口径）；下表逐条标注。

| # | 步骤 | 状态 | 事实源/说明 |
| --- | --- | --- | --- |
| 1 | 入口与信任检查（SHA、事件、路径、Environment 保护） | 🚧 部分 | `environment: prompt-eval` 保护 ✅（main，prompt_eval 在用；PR #30 复用于 RAG）；fork PR 拿不到 Environment Secrets 为平台行为 ✅；**费用上限检查 ❌ 未实现**（仅 `timeout-minutes` 墙钟） |
| 2 | 资产预检（suite/manifest/hash/空 case → INVALID） | ✅ 已实现 | [`backend/evaluation/preflight.py`](../../backend/evaluation/preflight.py)（E0），workflow 已接入 |
| 3 | 临时资源就绪（隔离 PG/pgvector + 迁移 + 全套 DB env） | 🚧 PR #30 | pgvector service 容器 ✅（main 已有）；`agent_business` 预建 + 全套 DB env 指向隔离库 🚧（缺 env 会回落共享库名，见 §6 红线） |
| 4 | 模型 Seed（Secrets→加密入库→refresh→逐 role 校验） | 🚧 PR #30 | seed 脚本 main 已有（prompt_eval 用）；rerank 扩展与逐 role verify 🚧 |
| 5 | 真实 Provider 探针（连通性/实际维度，失败 BLOCKED 不降级） | 🚧 PR #30 | `seed_eval_model_bindings.py --probe`；隔离环境已实跑通过（1024=1024） |
| 6 | 入库建索引（`import_fixture` → `ingest_eval_fixtures` → 既有索引管线） | ✅ 已实现（入口）/ 🚧（CI 接线） | 导入器 main 已有；语料范围裁剪（剔除业务 KB 目录防 near-dup 交叉）🚧 PR #30 |
| 7 | 索引验收（数量/维度/KB/权限/fixture_set + **可检索已知样本**） | 🚧 部分 | 数量与维度断言 🚧 PR #30；**已知样本检索探针 ❌ 未实现**（当前仅断言非空与维度） |
| 8 | 按 Suite 运行（`backend.evaluation.cli` + Runner，检索与生成评测分开报告） | ✅ 已实现（运行）/ 🚧（CI 口径切换） | CLI 与 Runner main 已有；检索评测（`--no-ragas`）与生成/Judge 评测（`--judge/--ragas` 显式 opt-in）天然分离 ✅ |
| 9 | 成本与证据（commit_sha/eval_run_id/指纹/`ci_evaluation` 归因/费用/脱敏 Artifact） | ❌ 大部分未实现 | 见 §6 费用红线：`call_origin` 字段在 Billing V2 中不存在；当前评测直调调用未进 `llm_usage`；workflow 级 llm_usage 汇总导出 🚧 PR #30；**索引指纹机制 ❌ 未实现**（当前每次从 fixtures 重建，无可复用缓存——符合"默认重建"底线，但指纹/校验缓存缺失） |
| 10 | 终态与清理（原始退出码决定 Job、失败保留证据、清理临时资源） | ✅ 已实现（平台）/ 🚧（退出码口径） | service 容器随 Job 自动清理 ✅；原始退出码直捕（`set -o pipefail` + `exit $?`）🚧 PR #30 |

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
- **预算闸（❌ 尚未实现）**：真实 API CI 的调用次数/并发/单次费用/总预算上限未实现；当前仅 `timeout-minutes` 墙钟上限。**超时不能替代调用前预算闸**（[llm-budget-billing](../governance/llm-budget-billing.md) BG 系列口径）。
- **`ci_evaluation` 费用归因（❌ 尚未实现）**：Billing V2（迁移 `079`、[`llm_usage_store_pg.py`](../../backend/observability/llm_usage_store_pg.py)）**无 `call_origin` 字段**；Embedding/Rerank 直调（langchain `OpenAIEmbeddings` / reranker 客户端）不经 LLM proxy 记账出口，实测 `llm_usage` 0 记录（PR #30 审查包已披露）。费用归因按 **UNVERIFIED** 挂账，不得报 0 元或宣称已验收；是否升 P0 由批准范围决定。
- **CI 账本与生产看板（✅ 天然隔离）**：CI 隔离库账本不会自动写入本机/生产看板；跨环境聚合需受控对账机制（❌ 未实现）。

## 7. 失败与发布语义

| 情况 | 结论与动作 | 状态 |
| --- | --- | --- |
| 数据/绑定/密钥/维度/索引验证失败 | `INVALID`/`BLOCKED`，Job 非成功；禁止伪造质量 FAIL 或改用本地模型 | ✅ 三态裁决已实现（[`release_gate.py`](../../backend/evaluation/release_gate.py) + `validity.py`）；CI 步骤级 BLOCKED 🚧 PR #30（run 38142127992 实证缺 RERANK_API_KEY 时显式 RuntimeError） |
| 真实评测运行但指标低于强制阈值 | `FAIL`，保留失败 case_id，不删用例、不降阈值 | ✅ 三态与阈值裁决已实现；裁决准确性依赖 🚧 PR #30 的 pass_rate 键错位与 GATE-12 样本量死锁修复（main 现状：RAG gate 的 pass_rate 必需项恒判缺失） |
| 全部通过 | `PASS` 仅针对本次 Suite/commit/模型快照，不等于 Production READY | ✅ 语义已实现 |
| 路径不相关跳过付费评测 | `SKIP` + 原因，不得写成质量 PASS | ✅ 已实现（rag_smoke 路径检测，job 显示 skipped） |
| 调用未进费用账本 | 质量另行判断，费用归因 `UNVERIFIED`，不报 0 元 | ✅ 口径已执行（PR #30 审查包）；账本接入 ❌ 未实现 |

**Regression 命名语义（🚧 PR #30 已声明 / main 未声明）**：定时 `Regression` 必须区分"固定 Suite 阈值检测"与"历史 baseline 指标对比"。当前未启用历史对比（`--compare`/`--promote-baseline` 机制存在于 [`gate/regression.py`](../../backend/evaluation/gate/regression.py) 但未接入 workflow），因此 PR #30 在 workflow 注释中明示"固定样本质量门禁，不含历史基线对比"，不得宣称已检测相对回退。

## 8. 变更触发、核对与验收

- 低风险文档/纯静态：T0，默认不触发付费评测（✅ 现有路径过滤已实现）。
- RAG 上传/切分/检索/Embedding/Rerank/MCP 入口/模型/Fixture/Suite 变更：触发对应静态契约 + 受信任 RAG E2 检索评测（🚧 PR #30 的路径检测已补 RAG HTTP 面、`rag_server.py`/`mcp_client.py`、模型绑定面、评测初始化脚本）。
- SQL/Planner/E2E/鉴权/RBAC/租户变更：按对应 Runner 与数据集选择；不机械全域重建向量（❌ CI 集成未实现，见 §5）。
- 改模型/维度/索引 Schema/阈值：版本相容性验证、失配反例、真实 API 探针、必要重索引及可比性说明（🚧 探针已实现；版本化快照与失配反例 ❌ 未实现）。

**真实评测触发矩阵（2026-10-11 用户拍板）**：

| 触发场景 | 真实 API | 测试范围 | 落地方式 |
| --- | --- | --- | --- |
| 普通代码 PR | 不需要 | T0/T1、定向单元测试 | ✅ PR 免费预检（真实评测 job 对 PR 事件 skipped） |
| 纯文档、页面样式 PR | 不需要 | 静态检查 | ✅ 路径过滤 skip |
| RAG 检索算法、切分策略修改 | 需要 | RAG Smoke + 必要的 Regression | 合并 main 后自动 Smoke；Regression 审核后 workflow_dispatch |
| Embedding、Rerank 模型切换 | 必须 | 索引重建 + Golden 回归 | 每次运行强制索引重建；probe 维度三方校验失配即 BLOCKED；Golden 回归 dispatch selection=regression |
| SQL Agent、Planner 修改 | 按风险运行 | 对应领域真实模型评测 | Runner 已有，CI workflow 未建（❌ 见 §5） |
| 每日定时回归 | **不在 GitHub 定时运行** | 固定 Golden，监测质量 | 🚧 `rag_regression.yml` 已移除 schedule（PR #30）；定时回归由管理端定时任务承担（配置入口：管理端） |
| 重要版本发布前 | 需要 | 受影响领域真实 API 和 E2E | 审核后 workflow_dispatch 按域运行；E2E 属发布门禁 |

**验收矩阵 CI-01～CI-10（实现状态逐条标注）**：

| ID | 要求（摘要） | 状态 |
| --- | --- | --- |
| CI-01 | 固定 Suite/Manifest/hash/样本数可复核 | ✅ 已实现（preflight + suites + cases_hash） |
| CI-02 | 测试 KB 仅 super_admin 可操作；检索/兜底/跨库全路径隔离 | ✅ 已实现（§2.1；全链路系统性穿透测试未跑，属 T2 范围按需执行） |
| CI-03 | 隔离 DB/目录/索引不含共享数据；缺省配置拒绝危险写入 | 🚧 PR #30（全套 DB env + 语料裁剪）；"缺省即拒绝"逻辑部分依赖 backend 默认值行为，**未做专项守卫测试** |
| CI-04 | 绑定/凭据从 CI DB 读取；真实调用、维度三方一致；缺失不本地回退 | 🚧 PR #30（verify+probe+断言；`agent_memory_test` 上真跑 ⏳ 待 RERANK_API_KEY） |
| CI-05 | 既有导入器生成完整索引 + 检索探针成功 | 🚧 部分（导入/数量/维度断言 PR #30；已知样本检索探针 ❌ 未实现） |
| CI-06 | 各模块按官方 Suite 判定；0 样本/全 SKIP 不 PASS | ✅ 判定语义已实现（gate 三态 + SKIP 不计成功）；各模块 CI 集成 ❌（§5） |
| CI-07 | Fork/不可信 PR 无付费 Secrets；Environment 有检查证据 | ✅ 已实现（无 pull_request_target + environment 保护；分支限制规则需仓库设置核对 ⏳） |
| CI-08 | 预算/并发/重试/取消终态可核对；费用归因可查或 UNVERIFIED | ❌ 预算闸未实现；费用归因 UNVERIFIED（§6） |
| CI-09 | 真实退出码、Job 终态、脱敏 Artifact、SHA/指纹一致 | 🚧 PR #30（退出码直捕 + Artifact + SHA）；**索引指纹 ❌ 未实现** |
| CI-10 | 未实现/未验证项记录在审查包 | ✅ 执行中（PR #30 审查包 + 本文状态标注） |

## 9. 文档维护与交付

- 本文件按 [documentation-standard](../development/documentation-standard.md) 维护：T0 检查链接、路径、重复事实源与敏感信息；导航入口在 [docs/README.md](../README.md)。
- **实现状态是本文的一部分**：CI 代码/配置变化必须同 PR 更新 §2–§8 的状态标注与快照日期；状态只有四个合法值（✅/🚧/❌/⏳），禁止删除标注只留目标描述。
- 纯文档 PR 只做 T0；不自动重跑付费模型或全量测试。
- 交付必须列：PR URL、完整 HEAD SHA、变更文件、规范↔源码↔证据对应、实际 CI Run/Artifact、PASS/FAIL/SKIP、BLOCKED/UNVERIFIED，以及不能宣称的范围。未经明确批准不合并、不操作共享库、不配置或读取生产凭据。

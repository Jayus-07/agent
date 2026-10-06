# AGENTS.md

> 项目级约束与架构知识，随代码一起演进。个人偏好（语言/环境路径/工具）见全局 ~/.Codex/AGENTS.md
> 本文只留硬约束与索引；细节以 `docs/` 下对应文档为准。

## Project

电商 RAG + Multi-Agent 平台｜Backend: FastAPI + LangGraph｜Frontend: Next.js 14 + React
AI: 模型角色化治理、DB 唯一来源（`config/model_roles.py` 12 角色，解析链 DB 覆盖→inherit→代码默认，env 不参与取值；当前 main/context_compactor/eval_gen=doubao-seed-2.0-mini，fallback=qwen3.8-flash，embedding/rerank=qwen3.7-text-*，备选池 17 模型 8 provider，管理端「供应商」页维护、15s 热刷新）
DB: PostgreSQL `agent_business`（业务仓库）+ `agent_memory`（元数据库）

## Architecture

```
POST /chat/stream → GraphRunner（Input Guard 门禁 → memory.start_session → graph.stream）
START → router ─┬─ 客服域锁（domain_hint=cs，跳过判域/灰度/prefilter）→ 客服域图 cs_graph_node → END
                ├─ CS 预过滤命中（CS_ENABLED + 灰度放量）──────→ 客服域图 cs_graph_node → END
                ├─ 旅游预过滤命中（TRAVEL_ENABLED） → 旅游域图 travel_graph_node → END
                ├─ 选品预过滤命中（SELECTION_FUNNEL_ENABLED）→ 选品漏斗域图 → END
                └─ RoutingEngine（domain → intent → capability → policy → RouteDecision）→ route_selector
                      ├─ direct   → skill_executor（跳过 Planner 直调 skill）→ reporter → END
                      ├─ workflow → workflow_executor → reporter → END
                      └─ plan     → planner → critique → supervisor（Send 并行）→ reporter → END
```

- **域图两条入口，勿混为一谈**：①**客服窗口锁域** —— 前端客服抽屉 `CSDrawer`（`useCSChat.ts`）每条消息带 `domain_hint=customer_service`，`router_node` 置 `cs_forced` 后**跳过域检测门/灰度/旅游与选品 prefilter** 直进 CS 管线（仍受 `CS_ENABLED` 总闸，关闭则降级主路由）；②**全局入口**（`domain_hint` 空）—— 走 CS 廉价规则预判 → 旅游正则 → 选品正则 → CS 完整检测，CS 命中后再过 `CS_ROLLOUT_PERCENT` 灰度。锁域**非绝对**：无客服规则信号且命中旅游/选品强信号时仍走 `redirect_main` 转出（LLM 仲裁阶段默认 OFF = `CS_REDIRECT_MAIN_LLM_ENABLED`）。守护用例 `tests/orchestration/graph/test_router_prefilter_order.py`。
- 域开关代码默认**全关**（`CS_ENABLED`/`TRAVEL_ENABLED`/`SELECTION_FUNNEL_ENABLED` 均 `false`），由根 `.env` 决定实际取值；三个 prefilter 均已接线（选品漏斗 2026-09-17 与旅游同层），无「待接线」项。
- **域入口模式与 handoff（多域隔离收官 2026-10-06）**：三开关 `CS/TRAVEL/SELECTION_GLOBAL_ENTRY_MODE`（`execute|guide`，**默认 execute=行为零变化**）作用于全局入口 prefilter 命中后的分派——`execute` 照旧进域图，`guide` 则主图短路（router→reporter）发 **handoff 引导卡**（SSE AUX 帧 `handoff`，契约 `orchestration/contracts/handoff.py::HandoffPayloadV1`＝目标域+参数包+原因，前端 `HandoffCard` 三入口带参跳转+点击埋点 `POST /observability/handoff/click`，指标 `agent_handoff_total{target_domain,phase}`）。一次性查询（「福州有什么景点」类）passthrough 落回主路由不受 guide 影响；**客服窗口锁域不受模式开关影响**。「四扇门」= 主聊天 + 旅游页 + 客服抽屉 + `/selection-funnel` 选品专属页（第四扇门；选品漏斗=用域图载体实现的固定工作流范式）。验收：`docs/reports/2026-10-06-多域隔离收官验收报告.md`。
- **RoutingEngine（2026-10-05 收口）**：主图唯一生产路由引擎（`orchestration/router/engine.py`），六阶段固定序 `entry_gate → domain → intent → capability → policy → route_decision`（`routing_meta.stage_order` 用例锁定）；DomainRouter／IntentRouter（`intent_router.py`，结构化分类阶段不新增 LLM 调用）／CapabilityRouter／ExecutionModeResolver 各只出一类决策，规则/向量/LLM 只是证据提供者、不各自拍板；旧三层 `route_legacy()` 与 `ROUTING_ARCHITECTURE`/`ROUTING_SHADOW_MODE` 架构开关已删除，`Router` 仅是兼容 facade——禁止新增调用方、禁止再引架构开关；`route_mode` 补齐 `clarify`/`general_chat`（域图显式 `domain_graph` 归宿），未知/低置信意图→clarify，不把空候选伪装成 plan；故障结构化四分类 `domain_classifier_degraded / vector_index_mismatch / vector_unavailable / llm_failure`，向量故障不在请求内重建索引。设计稿与验收清单：`docs/superpowers/specs/2026-10-05-routing-engine-design.md`、`docs/reports/2026-10-05-路由层企业级改造验收清单.md`。
- **Runtime 语义收口（2026-10-06，STOP A–G 全 PASS）**：①`RouteDecisionV2`（`orchestration/router/types.py` + `orchestration/runtime_types.py::RuntimeType/RuntimeTarget`）是路由事实源，旧 `route_mode`/`route_decision`/平铺字段只由唯一 Projection `orchestration/router/projection.py` 生成（含 route_decision_v2 state 键），AST 守卫 `test_legacy_route_writer.py` 禁止新增生产写入口；②`DomainGraphRegistry` 扩展 runtime descriptor（注册期校验 runtime_id 唯一/alias 冲突，声明 supports_checkpoint/interrupt/streaming、entry_modes、result_contract_version、continuation_policy），Router 域元数据（域族/入口模式/prefilter/subflow）从 Registry 活视图派生（`resolve_alias/route_mode_to_runtime_target/family/entry_mode`），域字面量静态守卫 `test_router_domain_literal_guard.py`——新增域不改 Router 文件；③域图出口 `orchestration/runtime_result_adapter.py::attach_runtime_result()` 归一 RuntimeResult 进 state `runtime_result`（final_answer/sources/clarification/handoff/tool metadata 不丢，reporter 不做二次 LLM 生成）；④`orchestration/state_projection.py` 提供 canonical readers（decision/params/clarification），unknown state key 守卫为 0；⑤Trace 13 字段 runtime 归因（domain/subflow/runtime_type/runtime_id/interaction_mode/execution_mode/workflow_id/capability/skill_id/tool_id/prompt_version/confidence/source），Skill/Tool 归因装饰器埋 `record_runtime_attribution`。机械验收门 `python -m backend.scripts.verify_runtime_arch_v2 --stage G --final`：阶段门+node_id/SSE/checkpoint/前端映射兼容门+全局回归全部由真实测试退出码计算，禁止手写 PASS（终态 447 passed，`AGENT_RUNTIME_ARCH_V2_READY=true`）。设计稿与实施方案：`docs/superpowers/specs/2026-10-06-runtime-semantic-closure-design.md`、`docs/superpowers/plans/2026-10-06-runtime-semantic-closure.md`。

- 主图核心节点固定 9 个（含 general_chat，2026-09-25 口径对齐 builder.py:140-151），顺序与命名不得随意改动（`builder.py`）；Skill 节点与域图节点由自动发现加入，**不得手写进 builder**。
- planner→critique→supervisor 是 plan 支线专属；direct/workflow/三个域图均绕过。预过滤优先级：客服 > 旅游（"订单里的行程单"属客服诉求）。
- 客服子图：state_loader → pending_handler → cs_supervisor（handoff 拦截/循环上限/LLM 兜底）→ 5 子 Agent（代码名 Expert）→ 回 supervisor → cs_reporter
- 旅游子图（**11 节点**，2026-10-07 `4ddc3c3` 起含局部重规划）：travel_slot_filler → travel_supervisor（纯规则）→ poi/transit/budget/risk/weather 五子 Agent → travel_validator →（未通过）travel_repair →（已有行程的逐条改单）travel_partial_replan → 回 supervisor → travel_reporter
- RAG 子链路：改写 → MultiQuery → 混合检索（向量+BM25）→ 同文档扩展 → Rerank → EvidenceGate → 带引用生成 → META 尾拒答判定
- 流式：节点 status/log + LLM stream_sink delta 汇入 merged_q；SSE 帧序 meta → status/log/delta → done/error（AUX 辅助帧 todo/usage/file/clarification/context/thinking/handoff 可在中段任意位置任意次出现，ping 不计帧序——权威口径与回归门见 `orchestration/graph/event_schema.py` 与 `tests/test_sse_event_schema.py`）

### 网关与异步层

- **APISIX(9080) 是唯一入口**；主链路 `/chat/stream` 同步执行、不经队列，SSE 直返。
- **Celery**（`backend/tasks/`，Redis 兼 broker/result backend）：双队列 `agent`｜`rag_index`（`celery_app.py::task_routes` 固定路由）；状态权威在 PG（agent_memory.tasks）；payload 仅 task_id、acks_late+prefetch=1、软/硬双层超时；重试 = 从最近 LangGraph checkpoint 自愈式续跑（业务终态异常不重试）。细节见 `docs/OPTIMIZATION_P3_ASYNC_QUEUE_ARCHITECTURE.md`。

### 治理平面（Governance Plane，2026-09-30 M1-M7 落地）

治理是**平面不是层**：不进请求执行路径（仅旁路埋点），不新增 Agent，载体复用 PG/Redis/Prometheus/自研 Trace。台账与设计方案见 `docs/2026-09-30-企业级治理技术债修复台账.md`。

- **契约 lock（M1）**：`backend/tool_contracts.lock.json` = 39 Tool 契约派生快照（args/必填性/output_type/hash），**禁手编**；改任何 Tool 签名必须重新生成（`python -m backend.scripts.gen_tool_contract_lock`）并随变更提交——lock 与代码漂移会被 `test_tool_contract_lock` 与 `--check` 拦截，diff 自动分类 BREAKING/DEGRADED/COMPATIBLE。
- **错误统一口径（M3）**：`observability/error_taxonomy.py::unify_*` 是三套既有词表（模型层 5 类/任务层 10 类/ToolStatus 8 值）→ 七分类（timeout/network_error/permission_denied/validation_error/business_error/contract_error/provider_error）的**唯一映射出口**；管理端失败分布与新指标 `agent_tool_error_class_total` 只用此口径，禁止再造第四套词表。
- **成本归因（M5）**：`observability/llm_context.py`（ContextVar，叠加语义）+ `llm_usage.skill_id/tool_id/agent_domain` 三列；注入点三处（skill execute 装饰器/tool executor 装饰器/builder 域图布线 `with_domain_attribution`），新增调用链记得在入口包 scope。
- **资产一致性（M6）**：`GET /api/consistency/report` 七段对账矩阵全部实时派生（禁手抄数字）；管理端/巡检消费此端点，不另建清单。
- **Tool 统计（M2）**：`GET /api/admin/tools(/stats|/changes)` 进程内 Prometheus 直读 + 契约变更历史（`ai.tool_contract_changes`，生成器检测到变更自动落库）；`skill_failure_total` 已埋点（skill 失败出口，error_type=七分类）。
- **Tool Governance Runtime（2026-10-06 落地，提交 b6b7973）**：`core/tool_governance/` 是**所有正式 capability 执行的唯一确定性门**——Skill/tool_selector 执行前必须经 `GovernanceRuntime.evaluate()`（守卫序：ToolSpec 存在 → 候选集合 → 域匹配 → intent_fit 硬门 → JSON Schema 参数校验 → 确认/审批 → 请求预算 → 限流熔断 → 去重），再进 SafeToolExecutor；LLM 输出只是 `ToolCallRequest` 的输入，不得绕行。ToolSpec 从 `orchestration/router/capabilities.yaml` 的 `governance:` 段派生（version/operation/risk_level R0-R3/confirmation_policy，`operation_by/risk_by/confirmation_by` 按 action 参数动态定风险）；**新增 Tool/改签名必须在 capabilities.yaml 补/改 governance 段**（未声明 = 静态门禁 fail）。行为变化三条：①tool_selector **首候选分数兜底已删除**——FC 失败/模型拒绝/单候选无明确 fit 一律 clarify，不再按分数自动执行；②auto 参数（运行时注入）不可由模型提供，模型参数先剥离 auto 再校验；③副作用 Tool 走 `OperationPreview` 确认（confirmation_id 一次性消费 + args_hash 指纹防重放）。静态生产门禁 `python -m backend.scripts.tool_governance_check`：全量 Tool 必须有 ToolSpec + additionalProperties:false + 编排层裸调用扫描（orchestration/skills/travel 禁 `*.func(`/命名 invoke 绕行）。回归测试 `backend/tests/tool_governance/`。
- **评测台账（M7）**：`ai.eval_run_records` 是**索引非替代**（明细仍在 `data/eval_runs/`）；评测跑完自动 upsert；`prompt_versions` 口径 = PG 权威（`snapshot_prompt_versions()`）优先、yaml 扫描兜底；CLI `--triggered-by` 记录触发者；`GET /api/evaluation/prompt-version-runs?key&version` 反查某 prompt 版本关联的评测（JSONB 包含，值口径 str）。
- **评测运行生命周期与门禁（2026-10-04 验收收敛）**：run 带生命周期状态文件 + stale 心跳判定（`EVAL_RUN_HEARTBEAT_STALE_SECONDS` 默认 600s）；终态拒绝隐式重跑（`--force` 走审计）、协作式取消端点（`POST /evaluation/runs/{id}/cancel` + `/operations` 审计，admin）；发布链 GATE 门禁族（GATE-11 基线回归门 / GATE-12 最低样本量门 / GATE-03 RAGAS 双门禁，`PROMPT_RELEASE_REGRESSION_GATE_ENABLED`/`PROMPT_RELEASE_RAGAS_GATE_ENABLED` 三态 off/audit/enforce 默认 audit，enforce 后门禁失败拒绝发布）+ 审批对比端点（`GET /prompts/{key}/releases/{id}/comparison`）；数据集不可变指纹（manifest/cases hash，原地修改 fail-fast，变更必须走新版本目录）+ 版本删除保护守卫；发布评测强制严格字段校验（`EVAL_STRICT_FIELDS`）；评测/定时任务写端点已收管理员门禁；`/evaluation` 读端点挂 `X-Tenant-Id` 租户钩子（单租户 default，其他值显式 403）、报告响应展示层 PII 脱敏；评测表 074（生命周期列+范围 CHECK）/075（样本表 UNIQUE+RESTRICT）。端点明细以 `backend/app/api/routes/evaluation*.py` 为准。
- **Prompt 版本语义与指针（M4）**：`prompt_versions.change_kind ∈ major/minor/patch`（存量 NULL）；`prompt_aliases` 中 **production 与 active_version 恒同步**（切 production=完整发布语义，走 publish），staging 只动指针不影响运行时读路径（staging 运行时消费属 Phase 2 灰度）。
- **Prompt 发布镜像 lock（第三批）**：`backend/prompts.lock.json` = DB 发布状态的仓库对照物（active_version+模板哈希+change_kind），**发布/回滚后必须 `python -m backend.scripts.gen_prompt_contract_lock` 重新生成并随变更提交**——lock 与 DB 漂移 = 有发布没留痕，被 `test_prompt_contract_lock` 与 `--check` 拦截；同版本 template_hash 变化 = prompt_versions 被手工 DML 的信号。
- **Prompt 治理收口（2026-10-06）**：registry 45→50、lock 48 行——①收编 4 个裸字符串 prompt（`router.tool_selector`/`general_chat.system`/`context.followup_rewrite`/`travel.llm_intent`，消费点经 `render_sync` 纳入版本/trace/pin，模块常量保留为逐字降级兜底，`tests/prompts/test_bare_prompt_collection.py` 锁漂移）；②修复 `customer_service.chat_fallback` 断线（YAML 存在但 registry 漏注册，运行时恒走常量的存量缺陷）；③planner.system v2 事实变量化（`schema_overview` 由 `sql/data/schema_config.py` 派生，消灭硬编码「15 张表」vs 实际 18 表漂移；JSON 示例外置 `prompts/planner.py::build_output_example`，YAML 零 `{{` 转义），**v2 已建 draft 未激活，激活必须走发布门禁 + planner 评估**；④新增 `llm_json_parse_fail_total{source}`（`shared/json_extractor` 全家支持 source 参数，策略链全失败才计数）；⑤覆盖矩阵脚本 `python -m backend.scripts.gen_prompt_eval_coverage`（派生 `docs/reports/2026-10-06-提示词评测覆盖矩阵.md`，禁手抄）。**双源口径**：defaults YAML = 首次种子 + 降级兜底 + 灾备（改 YAML 不影响已 seed 环境），改运行时模板走 `python -m backend.scripts.promote_prompt_default --key <key>` 建 draft → 管理端发布门禁激活（见 `backend/prompts/defaults/README.md`）。
- **请求级 Prompt 版本 pin（第三批 #5）**：`AgentState.prompt_versions`（**必须入 schema**——LangGraph updates 剥离 schema 外键）随 runner/task_executor 开始时快照 + `trace.tags["prompt_versions"]`；发布不影响已快照的值；checkpointer 开启时随 checkpoint 持久化，Celery 断点续跑在恢复时点重新快照。排障/审计按此 tag 回答「当时用的哪版」。
- **安全事件（M9）**：`ai.security_events` 五类统一落库（`security/events.py` 旁路软失败），四埋点 = Input Guard BLOCK / deps+rbac 403 / JWT 失败四分类 / Evidence 拒答；查询 `GET /api/admin/security/events(/stats)`。新增拒绝类路径记得旁路补埋点。
- **CI 契约门禁（第三批 #8）**：`tool_quality.yml` 已重启用——Tool lock `--check` + 一致性四件套测试 + 重复定义 + prompt lock 结构校验；PR 触发路径覆盖 backend/tools|skills/两个 lock/prompts yaml。

### CI / 评测触发链路（2026-10-01）

- **PR RAG 门禁**：`.github/workflows/rag_smoke.yml` 对所有 PR 启动轻量变更检测；只有 RAG、评测、Prompt、模型配置、评测语料、依赖或迁移相关文件变更时才执行 `pr_smoke` 的固定 **8 条**案例，使用 pgvector 临时库与缓存的离线 embedding/reranker，不调用 LLM Judge。无相关变更时评测 Job 跳过并报告成功，避免 Required check 永久 Pending。Job 名为 `RAG Smoke (8 cases)`；GitHub `main` 分支已配置 required check：`RAG PR Smoke / RAG Smoke (8 cases)`，相关 PR 未通过时不能合并。
- **Prompt 发布门禁**：管理端发布 Prompt 后，后端通过 `workflow_dispatch`/`repository_dispatch` 触发 `.github/workflows/prompt_eval.yml`；GitHub 使用外部评测模型运行管理端选定的 suite（当前默认 `pr_smoke`），上传 `prompt-eval-<release_id>` artifact；后端 Celery 维护任务轮询 GitHub Actions 和 artifact 结果。评测通过才允许 Prompt 热更新，失败或超时保持原 production 版本。
- **Prompt 完整发布流程（已在 2026-10-01 实测）**：
  1. 管理端在 Prompt 详情页创建新版本（`draft`），填写模板、变更类型和变更说明；此时不改变运行中的 `active_version`。
  2. 点击「发起发布评测」创建 Release Gate，后端记录 `release_id`、候选 `prompt_key/version`、评测集和 `external_run_id`，通过 `workflow_dispatch` 或 `repository_dispatch` 触发 GitHub `prompt_eval.yml`。
  3. GitHub 使用评测模型运行候选版本，上传评测报告；维护 worker 通过 Celery 任务轮询 GitHub run 和 artifact，回写 `ai.eval_run_records`/Release Gate。管理端展示运行中、通过、失败或超时及指标。
  4. 评测通过后，管理员在管理端点击「审批通过」，记录审批人和审批时间，Release Gate 进入 `待发布`。
  5. 点击「发布到生产」走 Release Gate 发布接口：模板校验仍保留；已通过外部评测的 Release Gate 作为最终门禁，跳过候选版本必须先手工变成 `passed` 的重复状态校验；随后原子更新 `active_version` 与 `production` alias、写审计、清缓存并发布 `agent:prompt:changed` 热更新通知。
  6. 各 app/worker/rag-service 通过热加载监听或轮询刷新 Prompt snapshot；运行 epoch 变化后，新请求使用新版本，正在执行的请求继续使用开始时 pin 的版本。失败、超时或未审批不得切换 production；同一候选版本的失败 Release 不重复复用，需创建新的候选版本。
- **Prompt 发布与 Trace 验收**：请求入口快照写入 `AgentState.prompt_versions` 和 `trace.tags["prompt_versions"]`；RAG 链请求开始时额外记录实际使用的 `rag.qa`、`rag.document`、`rag.contextualize` 版本到 Trace metadata；Tool span 记录 `contract_hash`。管理端可按 Trace 追溯「当时用的 Prompt 版本、运行 epoch、Tool 契约版本」。
- **本次验收结果**：`rag.qa v12` 完成 GitHub 评测、审批和生产发布；管理端显示所有进程 v12、runtime epoch 刷新；最终提交 `b894f92` 的 `RAG PR Smoke (8 cases)` 已通过，运行地址为 `https://github.com/Jayus-07/agent/actions/runs/36879912092`。本地相关测试 47 条通过。（现状口径 2026-10-05：此后又经发布与回切，**当前 production=v13**，以 DB `prompts.active_version`/`prompt_aliases` 为权威；`prompts.lock.json` 与 DB 漂移时按 M4 规则重新生成，勿手工编辑。）
- **触发边界**：`rag_smoke.yml` 当前只响应 PR 和手动触发，PR 只跑变更相关的 8 条 Smoke；`prompt_eval.yml` 只接受后端显式触发或手动触发；`rag_regression.yml` 每日北京时间 02:00 跑完整回归集，也支持手动选择评测集。Push 到 `main` 不等于 Prompt 发布评测，发布评测必须由管理端 Release Gate 触发。
- **GitHub 警告口径**：Actions 页面出现 Node.js 20 弃用和 `ubuntu-latest` 将迁移 Ubuntu 26 的提示时，属于非阻断告警；当前 Smoke 仍能通过。生产变更应走 PR，直接推 `main` 会绕过 PR 必需检查，不能作为企业发布流程。
- **Tool 治理 CI**：`.github/workflows/tool_quality.yml` 在相关 Tool/Skill/lock/Prompt YAML 的 PR 上触发，也按 `0 1 * * *` 每天 UTC 01:00（北京时间 09:00）运行；它检查 Tool 契约、注册一致性、重复定义和 Prompt lock，不替代 RAG 评测。
- **完整评测集**：不在每个 PR 中运行。`.github/workflows/rag_regression.yml` 默认每天北京时间 02:00 运行 `regression` 集，手动触发时可选择 `regression`/`ci_golden`/`expanded_100`/`scale_20k`；`pr_baseline`/`quick_26` 等仍保留给本地或专项回归。`pr_smoke` 是快速门禁，不删除完整集。
- **项目内部定时任务不是 CI**：Celery beat 与 APScheduler 负责客服日报、任务恢复、模型健康检查、`weekly_eval` 等产品运行任务，不能因为 GitHub CI 精简而删除。
- **旧工作流处理**：历史 `rag_eval.yml.disabled` 与 `unit-tests.yml.disabled` 已删除；不再恢复旧的全量 RAG/全量覆盖率门禁，后续如需全量单测应按当前 Python/数据库/模型治理重新设计。
- **术语口径（2026-09-29 拍板）**：域内统一叫 Agent——域调度者=**域主 Agent**（代码 supervisor），域内执行节点=**子 Agent**（代码/旧文档中「专家/Expert」= 子 Agent 的代码名，代码名保留不改）；勿在新文档再用「专家」指称运行时组件。

### 节点职责与口径

- **Planner**：只做任务拆解 → Capability DAG，禁调 Tool/Skill/DB ｜ **Critique**：规则校验优先，仅 anomaly 调 LLM ｜ **Supervisor**：纯规则 DAG 调度，Send[] 并行 + 注入 previous_outputs ｜ **Skill**：业务封装不碰外部系统 ｜ **Tool**：无状态可测试 ｜ **Reporter**：step_results → Markdown
- 规模口径（2026-09-16）：12 Skill / 17 capability（3 内部 `routed:false`）/ 39 Tool（2026-10-03 对齐契约 lock；10-02 +5：高德商家检索、12306 车票/票价查询、知乎站内/知乎全网搜索）/ 4 workflow / 5 物理域图＝3 顶级业务域（travel 含 planning/commerce/booking 子流，2026-09-29 对齐）/ 主图 9 核心节点（2026-09-25 对齐 builder 实际）/ MCP 2 server 5 tool（另经 `infra/mcp_client.py` 接外部 MCP 数据源：12306、知乎官方 MCP）。勿把所有节点统称 Agent；权威口径与例外台账见 `docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md`。
- `routed: false` 只约束路由层；Planner/critique 可见集 = routed:true 全集 ∪ 兜底白名单（`travel.poi_search`/`map.lookup`），共 **14 个**——E9 已于 2026-10-06 `4ac75bc` 闭环：`email.watch`（120s 阻塞长轮询）/`competitor.watch`/`competitor.history` 退出规划面（唯一派生口 `orchestration/router/manifest.py::planner_visible_capability_names`，禁止第二份手抄）。

### Capability DAG

```json
{"nodes": {"1": {"step_id": "1", "capability": "sql.query", "params": {"question": "..."}},
           "2": {"step_id": "2", "capability": "business.analyze", "params": {}}},
 "edges": {"2": ["1"]}}
```

17 个 capability 的 capability→Skill→节点名映射以 `capabilities.yaml` 为唯一事实源（G2，禁止手抄维护第二份表）。

### 新增资产规范（Tool / Skill / Workflow / MCP / Agent）

完整模板与隐藏接线点总表见 `docs/2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md`。**规范 > 本摘要**。

| 加什么 | 改几处 | 关键动作 |
|---|---|---|
| Tool（原子操作） | 2 | `backend/tools/<域>/<mod>.py`：`@tool` + **文件底部** `tool_registry.register(my_tool, __file__)`；❌ 不得定义在 `skills/` |
| Skill + capability | 5 | ①skill.py（仅执行实现 + `name`）②`__init__.py` 自注册图节点 ③`skills/registry.py::_instances` ④`skills/__init__.py` re-export ⑤`capabilities.yaml`（唯一业务 metadata）—— **漏⑤ = 启动 fail-fast** |
| Workflow | 3 | 类实现 + `workflows/__init__.py::register_all()` + `capabilities.yaml` 的 `workflows` 段（漏第三处 = 向量路由失明） |
| MCP Server | 2 | 继承 `MCPServer` + `servers/__init__.py::register_all()`；参数一律 `langchain_tool_to_mcp_meta` 从 `args_schema` 派生，**禁止手写** |
| 域图 / 业务 Agent | 5~7 / 2 | 手册 §6/§7；域图用技能 `agent-platform-add-domain-graph`（prefilter 实现放 `orchestration/graph/<域>_prefilter.py`、插进 `routing/prefilter_chain.py::run_domain_prefilters`，否则域永不触发） |

**铁律**：**G1** 声明式注册、启动期派生、fail-fast｜**G2** 单一事实源，派生量禁止手写回去｜**G3** 谁定义谁注册，禁止集中代注册｜**G4** 例外必须登记规范 §4 台账。
**方向**：`Planner → capability → Skill → Tool → Infrastructure`，上层调下层；MCP 不是第 5 层，是 Tool 的第二出口（Tool 不得 import Skill）。**第三方向（2026-10-02 拍板）**：外部 MCP server 可作为 Tool 的数据源——平台经 `infra/mcp_client.py`（官方 mcp SDK 同步薄客户端）消费，首例=12306 车票查询（`tools/travel/train.py`，compose 服务 mcp-12306，`TRAIN_MCP_ENABLED` 默认关，非官方源仅供学习不商用，失败不阻塞主链）；第二例=知乎官方 MCP（`tools/search/zhihu.py`，`zhihu_search`/`global_search` 双 Tool，Streamable HTTP + Bearer，`ZHIHU_MCP_ENABLED` 默认关、凭据 `ZHIHU_MCP_API_KEY` 只从 .env 读，月度配额计量，双路单路降级）。

**Skill 硬约束**：`name` = 目录名（节点名 `<name>_skill` 由其推导）；Skill 类只定义执行行为，`capabilities/description/examples/params_schema` 必须只写在 `capabilities.yaml`，由 `skills.metadata.bind_manifest_metadata()` 启动期绑定兼容字段并 fail-fast；capability 恰一个点 `<域>.<动作>` 全域唯一，workflow 纯蛇形不带点。❌ Skill 层定义 `@tool`、直接写 SQL/调 HTTP；多 Tool 覆写 `_select_tool()` 分发并把 params 裁到目标 Tool 签名内。
**新 Tool 三规**：`@tool`｜底部注册｜返回 JSON 字符串（失败返 `{"error":…}`，「查不到」与「查不了」分开），统一走 `tools/map/_base.py` 的 `ok/fail/not_configured`（存量 18 个返 Markdown 是例外 E8，别参照）。副作用 Tool 必须过 `security/tool_approval.ensure_approved()`；user_id 取 `tools/session.get_tool_user_id()`，禁止硬编码。**governance 声明（2026-10-06）**：新 Tool / 改 Tool 签名还必须在 `orchestration/router/capabilities.yaml` 补/改 `governance:` 段（operation/risk_level/confirmation_policy），漏声明会被 `tool_governance_check` 静态门禁拦截。
**新 Tool 第四规（2026-10-02）**：`tools/labels.py` 登记中文名 `display_name`（单一事实源；生成器派生进契约 lock 不参与 content_hash，管理端/守护消费；漏登记被 `test_tool_stats_alignment::TestDisplayNames` fail-fast 拦截）。
**易漏接线**：新 capability 加 `direct_executor.py::_USER_CAP_LABELS`。

**验证（改完必跑）**：`cd backend && "$PY" -m pytest tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py tests/test_tool_contract_lock.py -q --no-cov`（改 Tool 签名/args 后另跑 `"$PY" -m backend.scripts.gen_tool_contract_lock` 重新生成 lock 并随变更提交）
⚠️ 局部跑**必须加 `--no-cov`**（`pytest.ini` 挂死 `--cov-fail-under=55` 且无运行范围隔离 → 用例全绿但 EXIT=1，并覆写项目级覆盖率产物）；改 `params_schema`/描述/prompt 后另跑 planner 评估（`datasets/planner_params.json`）。

### SQL 子系统与数据库

`SQLSkill → SQLAgent → Router → Generator → Validator(6层) → RowSecurity → Executor(连接池) → PostgreSQL`
6 层安全：①SELECT 校验 ②表名白名单 ③敏感列拒绝 ④函数黑名单＋全函数正向白名单 fail-closed（2026-10-06 `3f76267` 拍板，白名单外一律拒）⑤LIMIT 强制 ⑥agent_readonly 只读角色。数据协议 SQLResult / BusinessInsight，步骤间 Supervisor 注入 `previous_outputs`。
库：7 schema × 18 表（product/order/inventory/customer/crawler/finance/ai）；连接池 min=2 max=10；Migration 走 `sql/migrations/`。

### 智能客服域图（`backend/customer_service/`，2026-10-05 全量验收收官）

**对话体验改造（T5/T6，2026-10-04 收官）**：锁域反转 `CS_WINDOW_STANDALONE`（true=独立窗口不出域，出域话题由域内分诊直出接住）；分诊直出出口 `_triage_direct_reply`（supervisor v2 L4.5 层：**寒暄→chat_fallback 一次 LLM 人设（CS_CHAT_FALLBACK_ENABLED）**、**出域→固定话术零 LLM（CS_WINDOW_STANDALONE）**），reporter 按 `supervisor_decision.direct_reply` 直出。**顺序铁律精确口径**：出域/寒暄豁免用业务域词表 `CS_SIGNAL_EXEMPT_PATTERNS`（vocab v2026-10-04.4），不用全域规则命中数（「怎么」类通用疑问词会误豁免）。

**转人工逃生与序数承接（2026-10-05）**：need_info 补槽追问期用户显式转人工 → `pending_handler` 立即释放 pending 回 supervisor 重分诊（审计 `need_info_handoff_escape`，`a65a167`，紧急通道优先于补槽追问）；`context_resolver.record_recent_orders` 落 `recent_order_ids`（≤20 条）后「第N个订单」按序号解析（中文/阿拉伯数字，越界不猜不注入；列表引用不产生 `last_order_id`，`05ae6aa`）。域图侧口径见 `docs/architecture/ai-runtime.md` 客服域图行。

**LLM 调用纪律（G7）**：客服域一切 LLM 调用必须走 `from backend.infra.llm import llm` **代理**（限流/韧性/llm_usage 记账）；`get_llm()` 返回裸实例绕过全部包装——chat_fallback/query/complaint/supervisor 四路径已收编。**记账口径**：客服域轮次 component=`customer_service`（`_usage_component()` 按 cs_target tag），主图=`llm`，对账须合并。

**词表与守卫**：词表单一源 `vocab.py`（VOCAB_VERSION 2026-10-04.4，变更升版+过 `vocab_gate` 三套回放：triage 95.06%/handoff 漏转=0/confirm_cancel 100%）；确认守卫 `uq_cs_confirmations_active_biz_op` 按 (tenant,action,target_type,target_id,semantic_fingerprint) 全局判重——**补槽升级/reask 更新自身必须 exclude 自身 confirmation_id**（08ca970 缺陷修复：否则「更新自己」被误判重复提交，追问保存全挂）。

**迁移与台账**：076_cs_faq_tables（ai.cs_faq/cs_faq_query_log 迁移化，E5；faq.py 惰性建表仅为存量兜底）；缺口周检 beat=cs.faq.gap_review 每周一 06:25 UTC（A8 台账，closed_now≥10 达标）；日报 gauge 刷新器 `start_faq_gauge_refresher`（app 进程 600s 拉 qa_daily_reports，修复 G2/G3 告警数据源断链）。寒暄人设 prompt=注册表 `customer_service.chat_fallback`（常量仅为降级兜底）。

**验收资产（2026-10-05）**：M1 E2E 黄金集 **308 条九类**（`evaluation/datasets/cs/e2e_golden_v1.jsonl` + `scripts/gen_cs_e2e_golden.py` 生成 / `scripts/replay_cs_e2e_golden.py` 分层回放：Layer1 意图层组件级全量+Layer2 端到端抽样，本轮 226/226=100%）；验收清单本体在用户桌面《客服验收清单.md》，终态 ✅127/⬜16（A6 质量门=另一会话在改值；O2/O6/坐席端与前端走查=待窗口）；报告 `docs/reports/2026-10-05-客服验收清单自动模式全量收官报告.md`。

### 旅游规划域图（`backend/travel/`，P0）

接入与客服域一致：`travel/register.py` 自注册 → `domains/__init__.py` 触发 → builder 自动布线，**不改 builder.py**。契约（Pydantic）：`TravelBrief → Poi → Itinerary`；状态只存 dict（`load_*/save_*`），保证 checkpointer 可序列化。
**validator = 旅游域的 Evidence Gate**：纯规则零 LLM 零 IO；只判定不修改（修复在 `repair.py`）；error 阻塞交付并触发修复；四轴 = 时间/地理/体力/预算。**局部修复**只动被点名的天与条目，用户点名必去条目**永不被静默丢弃**（kept_required）。
`travel.plan` 不注册主图 Skill（有状态多步流程已由域图承担）；只注册无状态的 `travel.poi_search`。数据源已切实时检索（2026-10-02 `599f4c7`：`TRAVEL_POI_SOURCE` 默认 **live**＝腾讯 LBS 实时检索，种子库下线、仅显式回退且 `TRAVEL_POI_FALLBACK_SEED` 默认关；票务查询侧=12306 MCP Tool）。规划产物走**版本链**（`plan_version` 修复重排 +1、`parent_plan_version` 指针，`TRAVEL_PLAN_VERSIONS_ENABLED` 默认开、保留 20 版，存 agent_memory 库）。开关 `TRAVEL_ENABLED`，阈值集中 `config/travel.py`。

**逐条改单局部重规划（2026-10-07 `4ddc3c3`）**：已有行程时 slot_filler 经 `parse_partial_request` 纯规则解析改单意图（六操作 replace/remove/add/pace/end_time/hard_constraint），supervisor 优先分派 `travel_partial_replan`——只动被点名的天与条目并强制重验证（`validation_failed` → failed，不静默交付）；新地点缺候选只补候选不重排，用户点名必去条目不被静默丢弃。配套：`TRAVEL_ARRIVAL_BUFFER_MINUTES`（默认 30，首日活动不得早于到达时间+缓冲）；brief 契约新增 `budget_constraint`（`hard` 默认不得超预算 / `soft` 可超需说明）与 `weather_conditions`；trace 侧 `travel/trace_semantics.py` 写 `travel_semantics` 投影（planning_mode / base·active·draft 版本 / semantic_change / modified_days 等，`trace.tags["travel_*"]`）。

**跨轮契约（checkpointer 关闭时也须遵守）**
1. `new_travel_graph_input()` **只放本轮输入**，不预置产物/执行态默认值——checkpointer 把 input 当对上轮状态的**更新**合并，预置 `brief: {}` 等于每轮清空成果
2. 读状态一律 `.get()`——本轮没写过的键不在最终状态里
3. `brief_fingerprint` 变 → 只在 slot_filler 里 `planning_reset()`；不清则 supervisor 会把**上一轮行程**当新需求输出

**checkpointer**：三处 `_build_checkpointer`（主图/客服/旅游）均 postgres 优先；主图与客服 **production 默认 fail-loud**——PG 不可用直接抛 `CheckpointerUnavailable` 拒绝启动，仅显式 `CHECKPOINTER_ALLOW_DEGRADE=true`（`config/checkpointer.py::degrade_or_raise`，默认 false）才允许降级内存检查点（开发环境失败降级 MemorySaver）；**旅游域 2026-10-07 `4ddc3c3` 起同口径 fail-loud**（`travel/graph_builder.py` PG 初始化失败且未显式放行直接抛 `CheckpointerUnavailable` 拒绝启动——三处 `_build_checkpointer` 已全部收口一致）；需 psycopg **v3** + `langgraph-checkpoint-postgres`（依赖已在 pyproject.toml 与 requirements-lock.txt 声明，本地 venv 已补齐）；`config/startup.py` 只探测 import 不探测连通性，缺驱动时 warning 点名。
两个锁文件坑（**照旧装会失败**）：① `langgraph-checkpoint` 原钉 4.0.3 与 `-postgres==3.1.0` 要求的 >=4.1.0 冲突 → 已升 **4.2.0**；② Windows/无 libpq 必须装 `psycopg[binary]`，否则 `no pq wrapper available`。
TTL 清理收敛 `orchestration/graph/checkpointer_cleanup.py`（全进程单例，改 TTL 三处一起改）。

## Design Principle

必须满足: 可理解、可测试、可观测、可维护、可扩展、可控制、可靠性
禁止: Demo 跑通式开发、临时堆叠、`except Exception: pass`
Priority：P0 数据错误/安全/崩溃/Trace 丢失 ｜ P1 架构/强耦合/重复代码 ｜ P2 命名/注释

## Code Rules

- Python: snake_case、类型注解、logger 替代 print、具体异常、SQL 参数化
- 禁止: 业务代码直接 os.getenv、文件名 misc/helper/common/utils2 ｜ Tool 必须独立可测试
- **写操作审批门**：副作用工具执行前必须 `security/tool_approval.ensure_approved()`（TOOL_APPROVAL_MODE=required 默认建审批单，管理员经 `/api/approvals` 批准后同指纹放行）；表 `ai.tool_approval_requests`（migration 007）
- **工具契约兼容**：契约与消费方同仓同发布，不加版本号；只向后兼容演进（新增可选参数），破坏性变更 = 新 capability + 旧能力废弃期；契约回归门 = registry/layer/adr0001/base_output 四个一致性测试 + e2e 故障注入（`datasets/e2e/cases.jsonl` F-*）；改 params_schema/prompt 后跑 planner 评估
- **主图保护**：recursion_limit = MAIN_GRAPH_RECURSION_LIMIT（默认 80）；checkpointer 默认关（MAIN_GRAPH_CHECKPOINTER_ENABLED），开启后 request_context 以 `checkpoint_safe()` dict 进状态，thread_id 每轮唯一

## 文档体系（顶级文档与维护责任）

**入口顺序**（新会话/新人）：根 `README.md`（「系统规模」= 一切数量类口径**唯一权威**，G2 禁止第二处手抄）→ `AGENTS.md`（**唯一入口**；根 `CLAUDE.md` 是指向本文件的薄指针，2026-09-29 起不承载内容，一切变更只改本文件）→ `docs/README.md`（文档总索引）→ 按需深读。

**顶级文档 = 改代码必须同步维护的部分**（文末「最后验证」日期不得落后于其覆盖范围的结构性变更；只做口径校准则更新日期并注口径）：

| 文档 | 何时必须改 |
|---|---|
| `docs/` 顶层 8 文档：PRD / ARCHITECTURE / **DESIGN**（三端设计规范）/ RAG_DESIGN / AGENT_DESIGN / DATABASE / API / ROADMAP | 对应产品定位 / 顶层架构 / 前端 UI 与 token / RAG 链路 / 编排 / 库表 / 接口契约 / 规划变更时 |
| `docs/architecture/system-overview.md` | 部署拓扑 / 端口 / 异步层 / 网关认证变更 |
| `docs/architecture/ai-runtime.md` | 图结构 / 节点职责 / 域图契约 / 路由通路变更 |
| `docs/architecture/domain-service-map.md` | 专家依赖 / 新增第三方服务 / 凭据 / 降级策略变更（新服务必须补全：用途 / 消费方 / 凭据变量 / 开关 / 降级 五行） |

**文档纪律**：① 过程报告写 `docs/reports/`，完结归档 `docs/archive/<年-月>/`，禁止堆 docs 顶层；② 被 source code 注释 / 测试 / 本文件按路径引用的 docs 文件**移动必须同步改引用方**；③ 前端设计 token 改动必须三端 `globals.css` + `tailwind.config.ts` 同步；④ 「专家→工具」「capability→Skill」等可派生映射**以代码与 `capabilities.yaml` 为准**，任何文档不手抄明细（G2）。

## Change Flow

明确目标 → 阅读代码 → 分析影响 → 修改 → 测试 ｜ Bug 先复现、Refactor 测试通过、Feature 优先补测试
编写/修改任何测试前必须遵循铁律（强断言、只 mock 外部边界、必须运行、假阳性自审）；存量测试随 diff 增量审查，不主动全量翻修。

## Validation

Backend: `py_compile` + `pytest tests/sql/ -v` ｜ Frontend: `npx tsc --noEmit` + `npm test` ｜ E2E: `cd backend && python e2e_demo.py`
管理端对账：`GET /api/agents`、`GET /api/capabilities`
文档索引 `docs/README.md`；四层规范与新增资产手册见上文链接；记忆 用户级 `~/.Codex/projects/<project>/memory/MEMORY.md`

### 本机测试超级管理员（仅限 local）

- 用户名：`local_super_admin`
- 租户：`default`
- 角色：`super_admin`
- 登录入口：本机前端 `/login`，或经 APISIX `http://127.0.0.1:9080/api/auth/login`
- 密码不写入仓库文档或版本库；遗失时应通过受控的本机密码重置流程处理。
- 重新执行提权（幂等）：`$env:PGPORT='5433'; D:/Python/python.exe -m backend.scripts.bootstrap_super_admin --tenant default --username local_super_admin`

## 服务启停与网关边界

**唯一启停入口 = `devctl.bat` 系列**（旧的 start_py/start_frontend 等 .bat 已删除）。完整实测踩坑清单见 `命令文档.md` 与 `docs/gateway-apisix-final-report.md`。

```bash
.\devctl.bat status                          # 空参 = status
.\devctl.bat start|stop|restart [backend|admin|web|all] [/y]
.\devctl.bat all /y                          # stop+start 全量
# 短路入口 dev-start/dev-stop/dev-restart.bat 等价，底层实现 dev-svc.bat（一般不直接调）
```

- 服务：`backend` = docker compose `app`（:8000）｜`admin` = frontend-admin（:3200）｜`web` = frontend（:3100）｜网关 APISIX :9080
- **`backend` 只按服务名操作**：`stop backend` 只停 app 容器，不动 postgres/redis/apisix/rag-service/mcp-service/worker；整套栈用 `docker compose up -d` / `down`
- **脚本化/agent 调用一律加 `/y`**（stop/restart 确认是交互式，否则挂住）；仅支持 cmd/powershell——Git Bash 用 `/c/Windows/System32/cmd.exe /c "devctl.bat status"`，且当前目录须已是仓库根
- 前端每次 start 都新开一个空 `NEXT_DIST_DIR=.next-dev-<rand>`（复用非空 distDir 必启动失败），故 `frontend/.next-dev-*`、`frontend-admin/.next-dev-*` 会不断堆积（实测 16 个目录 ~300MB，`.gitignore` 用 `.next-*/` 兜住）；**脚本不清理旧 distDir，需手动删**
- 宿主机 `127.0.0.1:8000` 可能有 Docker 残留僵尸绑定 → 裸跑 uvicorn 前先重启 Docker Desktop

**已知坑（实测，详见 `命令文档.md`、`docs/gateway-apisix-final-report.md`）**：

- **.bat 必须 ASCII-only**（cmd 按 GBK 解析，中文注释会破坏控制流）；**别用 `timeout /t`**（Git Bash PATH 会解析到 GNU timeout，改用 `ping -n N 127.0.0.1 >nul`）
- **`dev-svc.bat` 未跟踪**（`?? ` 状态）：它是 `devctl`/`dev-{start,stop,restart}.bat` 四者的共享实现，删旧脚本时务必一起 `git add`，否则新提交的入口脚本会指向一个不存在的文件
- app 容器换 IP 后 APISIX 有 ~1-2min 502 窗口（`dns_resolver_valid: 5` 已缓解），急用 `docker compose restart apisix`；oa-auth-service/system 无重启策略，引擎重启后需手动 `docker start`
- oa-auth 整栈曾被反复 SIGKILL(137)：修复 = `docker start oa-auth-nacos oa-auth-mysql oa-auth-redis oa-auth-service oa-auth-system` → `docker network connect agent_agent-net <容器>` → 重启前端。vpnkit 回环不可靠，**容器名直连是首选**；本机 5432 是宿主机原生 PG，不是 agent-postgres
- 杀端口脚本都带 docker 守卫（容器占端口时跳过，防误杀 com.docker.backend）
- **py 与 Java 是两个独立项目**：Java = Enterprise_OA（备份 `.workbuddy/java-legacy-backup/`，割接清单 `docs/java-side-handover.md`）；唯一联系是 Java 客服调 py agent
- **认证 py 自建**（issuer=agent-platform，`backend/security/local_jwt.py`，migration 008）；APISIX `gateway-auth` 插件验 JWT——**Bearer 优先于 X-API-Key**，注入 X-User-Id 身份头（契约 `docs/contracts/identity-header-protocol.md`）；登录链路前端 `/login` → APISIX `/api/auth/**` → auth-service，回滚 = env 改回 8080

## 多会话协作与环境避坑纪律（2026-09-19~09-22 真实事故沉淀）

多个会话（人 + 多个 AI session）并行操作同一仓库、共享同一套容器/端口/测试环境。每条规则来自实测事故，不是理论推演。完整版（含全部排查命令与判定流程）见 `.zcode/skills/agent-repo-pitfalls/SKILL.md`。

### 提交：必须双重路径限定

- 事故：不带路径的 `git commit` 把整个暂存区一并提交——实测一次吞掉 258 个文件（含其他会话 stage 的内容）。
- 标准写法（两条都要带路径）：`git add -A -- <paths>` + `git commit -m "..." -- <paths>`；`commit -- <path>` 对未跟踪文件无效，必须先 add。
- 误提交回退：`git reset <base>`（mixed，工作区零损失），绝不用 `--hard`。

### 提交前：查引用符号是否已落库

- `git diff <file>` 看新增行引用了哪些新模块/函数/导出，逐个 `git status --short -- <模块路径>` 确认定义文件已提交——否则主干 import / tsc 直接挂（实测两次）。
- 文件混有他人未提交改动 → 整个文件此时不能提交；文件干净只引用未提交模块 → 接线行留待对方落定后补，提交信息显式标注「差一行未生效」。
- admin 门禁（`SENSITIVE_API_GUARD_MODE=enforce`）改动必须与前端改道原子提交，否则旧前端 403。
- 阶段完成**立即**路径限定提交——并行会话可能「收编」工作区未提交文件代为提交（实测发生过）。

### 并发 pytest 冻结

任何会话跑全量 pytest（约 20 分钟）期间：不改 `backend/` 代码、不动容器（改了结果不可信）。开工前先确认没有会话在跑全量测试。

### 动容器/端口前：先查归属

- 启动/重启/down/rebuild/抢占端口前必做只读核查：`docker ps`（谁在跑）、`netstat -ano | findstr :<port>`（端口被谁占）、`docker inspect <容器>`（真实启动配置）。会停掉/重建/抢占别人在用的 → 先报告「发现了什么、会影响谁」，等确认再动手；纯增量只读操作可直接做；无冲突可启动但须说明占了什么。
- **孤儿容器陷阱**：容器若靠已删除的临时 override 拉起（`docker inspect <容器> --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'` 指向不存在的文件），主干 `compose up` 后会**静默消失**（实测 beat 消失、三条周期任务停摆）。服务必须正式定义在主干 compose；Celery beat 必须单实例，worker 才可多副本。

### compose up 中途失败 → 半死容器 → 前端「认证缺 data 字段」

- `up` 报错中断 ≠ 什么都没发生：前面的服务可能已被 SIGTERM 停掉且未拉起。容器名冲突先 `docker rm <冲突容器>` 再 up，不要反复重试。
- **关键判定**：前端报「认证响应缺少 data 字段」（文案出自 `frontend/src/lib/auth.ts:96` 的 `unwrapResult`，期望 Result 包裹 `{code,message,data}`）= 网关 503 HTML / 上游挂了的典型症状——**先查后端容器，别改前端代码**。
- 处置：`docker ps -a` 找 Exited/Created 容器 → `docker start agent-app-1` → `curl -s http://localhost:8000/health` 验证恢复。

### 本机双 PostgreSQL（连错库排查极费时）

- docker `agent-postgres-1` = 宿主机映射 **5433**（agent 权威库）；宿主机原生 PG **5432** 恰好也有同名 `agent_memory` 库——库名一样数据不一样，极易误判「数据丢了/写入没生效」。
- agent 项目库连接一律**显式写 5433**；见 `localhost:5432` 先怀疑连错库。不确定就 `docker port agent-postgres-1` 确认。

### 其他仓库级坑

- 开工前必查 `git status` + `git log`；发现已有实施只做增量修改，不重写（用户常多会话并行推进同一功能）。
- refs/remotes 静默丢失（`git status` 永远 `[ahead N]/[gone]`）：`mkdir -p .git/refs/remotes/origin` 后重试 fetch。
- TaskList 不跨会话持久化：跨会话待办以 `docs/未完成功能进度汇总-*.md` + `docs/*跨会话交接报告*.md` 的并集为准。

### 通用排查顺序（运行环境类问题）

1. `docker ps -a` 看全量容器（不是 `docker ps`）→ 2. 前端报「响应格式不对」类错误先 curl 上游 :8000 确认是否 503 → 3. 数据「消失/不一致」先 `docker port` 确认连的哪个 PG → 4. 环境 OK 再看代码，反过来必走弯路。

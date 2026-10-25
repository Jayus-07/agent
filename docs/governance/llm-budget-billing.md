# 模型用量、预算、价格与计费治理规范

> 适用于统一 LLM Provider 代理、模型调用、Prompt/Agent/Skill/Tool 引发的模型消费。**标准与事实分离**：精确状态字段、数据表、限额模式及价格生效流程以源码、迁移和实际配置为准。与 [模型角色与供应商配置](../model-config-governance-design.md) 分工：模型配置文档负责选哪个模型及密钥，本规范负责怎么拦截、记录、定价、结算、审计。

## 1. 责任分工与事实源

| 责任 | 事实源 | 禁止事项 |
| --- | --- | --- |
| 请求内 budget | `backend/infra/llm/budget.py` | 各 Agent 自己计数/拦截 |
| 用户/租户额度与预占 | `backend/infra/llm/quota.py`、`backend/app/api/routes/budgets.py` | 绕过 quota 直接调用 LLM |
| 定价、版本与货币折算 | `backend/infra/llm/price_governance.py`、`backend/config/budget.py` 和实际 pricing 模块 | 用当前单价回写历史已结算记录 |
| Token 统计/估算 | `backend/infra/token_tracker.py`、`backend/infra/llm/usage_estimator.py` | 把 estimated 说成 provider exact |
| 用量持久化/归因 | `backend/observability/llm_usage_store_pg.py`、迁移 `079_llm_usage_billing_v2.sql` | 将 Grafana 曲线当作账本权威 |
| 异常预占对账 | `backend/infra/llm/reconciliation_report.py`、`backend/tasks/budget_reconciliation_tasks.py` | 让未决预占永远占额或任意删除历史 |

模型调用的授权/额度在模型真正调用前检查；primary、retry、fallback 都应纳入同一请求预算、身份和实际计费用量归因。预算功能失败的业务语义必须依据安全策略 fail-closed 或由已有明确的 observe/off 配置决定，不能在各消费点静默放开。

请求设置正数成本上限时，每次调用保守预占该请求的剩余成本额度（上限减去本请求已记录的结算成本）。已结算成本已经占用用户/租户账本的 `used_cny`，不能在后续调用中再次预占。未决调用仍由 PostgreSQL 的 `reserved_cny` 保留占额；本规则不释放 `needs_review`，也不按预计单次调用价格缩小预占。因此，剩余额度不足以同时容纳多个在途预占时，现有硬门禁仍可拒绝并发调用。

## 2. 必须区分的状态轴

- **调用状态**：请求被允许/拒绝，调用真正发起、失败/成功、取消、fallback；调用重试不等于新用户任务。
- **预算状态**：额度核验、预占、结算、释放、滞留及 `needs_review`；跨进程同一个幂等消费不得重复扣账。
- **用量事实等级**：Provider 返回 usage 的实测值、明确标为 `estimated` 的估算值、无法估算/待对账；使用估算的依据和覆盖率应可查询。
- **定价状态**：模型及供应商标识、价格版本、计价输入/缓存输入/输出拆分、币种/汇率、折算后的记账金额。
- **展示状态**：用户/租户可见余额与费用，管理员可见异常和审计；不得因模型成功就假称成本已成功入账。

目前 `backend/config/budget.py` 声明预算本位币为 CNY，Provider 原生币种与折算要分别留痕。**汇率缺失、价格缺失、无法匹配模型不可默认 1:1 或虚构价格**。模型订阅制、缓存 token、reasoning token 等遵守当前 provider/billing 契约，不得人为统一为“输入+输出×一个价格”。

## 3. 一次 LLM 调用的基本不变量

1. 从可信请求上下文取得 tenant/user/request/run/step/role 等可用归因标识；缺归属时走明确的内部账号/拒绝语义，不自动混入普通租户。
2. 额度前置判定与预占必须覆盖 retries/fallback；统一代理完成后使用本次模型与价格快照，保存唯一一次可审计结算或释放。
3. 调用超时且结果未知不能盲目补写“0 费用”，不能直接认定计费失败；后续按账本/对账策略处理。
4. Provider 无 usage 尾帧但有可估算输入/输出时，按实际实现记录 `estimated`，并可区分估算份额；没有任何可信数据时保留 `needs_review` 和排查依据。
5. 报表/Trace/管理端消费同一费用事实，汇总不能回填改变原始历史明细；历史账单必须保留价格/汇率版本以便重算差异。
6. Prometheus 仅做有限维度聚合指标，**user_id、tenant_id、trace_id、session_id 等不得作为 Metrics label**；精细归因留在受控 DB/Trace。

## 4. 新增模型调用点与改价验收

| 编号 | 场景 | 必须断言 |
| --- | --- | --- |
| BG-01 | 请求/用户/租户额度不足 | 受限请求被预期阻断，未实际发生被拒绝的模型调用 |
| BG-02 | primary + retry + fallback | 同一请求成本和调用次数正确归因，预算不被绕过 |
| BG-03 | 流式中断、Provider 响应丢失、无 usage | 实测/estimated/needs_review 分类正确，无重复扣账 |
| BG-04 | 价格版本切换及汇率变更 | 新单据使用新快照，历史单据不漂移 |
| BG-05 | 缓存输入/输出/推理 token | 计价规则与该 Provider 实际能力一致 |
| BG-06 | 并发同一幂等调用 | 预占、结算、释放与剩余额度不重复 |
| BG-07 | 无价格、无汇率、存储异常 | 按模式明确拒绝/待核对，不伪造费用 |
| BG-08 | Trace、管理端、Grafana | 费用来源一致，能识别 estimated，聚合与明细可对账 |
| BG-09 | 审计与权限 | 非管理员不能修改他人预算，租户数据不串读 |
| BG-10 | 每日对账任务 | 未决率、滞留预占、估算占比能被定位和告警 |

执行成本/配额整改先做调用链审计和账本基线，不要因“统一”新增第二套 LLM SDK 调用出口、预算表或费用聚合器。用对应 `backend/tests/infra/`、预算 API、迁移及定向测试验证，未接真实 Provider 只标隔离测试通过。

### 4.1 供应商公开价格页巡检

`model.price_page_monitor` 由 Celery Beat 每日 08:00（Asia/Shanghai）读取固定的官方价格页面，记录 Doubao Seed 2.0 mini、Qwen3.7 Text Embedding 和 Qwen3.7 Text Rerank 的北京地域刊例价。它对比前次有效观测和当前 `model_price` 已审核生效行，分别报告供应商价格变化、系统价格不一致以及页面读取/解析失败。

巡检告警复用 `backend/observability/alerts.py` 的 JSONL、Prometheus 和可选 `ALERT_WEBHOOK_URL` 通道。管理端“模型与供应商 → 价格巡检”展示运行状态、计划时间、各价格来源结果、系统与官方价格对照、官方价格变化和已触发的告警事件；告警投递是异步 best-effort，因此页面只展示触发记录，不宣称 webhook 已送达。迁移 `100_model_price_change_alert_outbox.sql` 将价格变化事件与当日观测一并持久化；Worker 在告警触发前退出时，重跑会恢复 pending 事件。告警调用返回后事件标记为 `triggered`，通知送达状态仍为 `unknown`，同日重跑不会重复处理已触发事件。可捕获的 Worker 异常记为 `failed`，Celery 撤销记为 `cancelled`；进程被强制终止时，巡检运行记录可能仍为 `running`，需与 Celery 任务终态对账。

每日观测只写入 `model_price_source_observations`、变化告警 outbox 和巡检运行历史；巡检不调用 LLM、不产生 Provider 费用，也不导入、审批或覆盖 `model_price`。Doubao Seed 2.0 mini 官方常规在线价格按输入长度分段，而当前计费表只表示 `[0, 32]` 档；在统一价格模型覆盖其他档位前，LLM Proxy 对该模型限制输入不超过 32,000 token，并在预算预占前拒绝超限请求。该限制依据[火山方舟模型价格页](https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing)的常规在线档位。发现系统价格差异后仍须按价格治理的双人审核和 24 小时 canary 流程处理。供应商页面无法访问或格式变化时必须告警，不能把缺失报价记为零。默认直连官方站点；必须通过环境代理出网时设置 `MODEL_PRICE_MONITOR_USE_ENV_PROXY=true`。

### 4.2 当前有效价格的可见性与缺价处置（2026-10-17）

系统存在两条价格写入路径，信任级别不同，不能相互冒充：

| 写入路径 | 写什么 | 信任级别 |
| --- | --- | --- |
| 模型登记页直接录价（`services/model_config._apply_model_pricing`） | `model_price` 行：`source=provider-page`、`approval_status=approved`、`reviewer_1=操作人`、`reviewer_2=system`；**不写** `model_price_versions` | 单管理员直登价：留痕可追溯，但不等同双人审核或 canary |
| 价格治理版本发布（`infra/llm/price_governance`） | 导入 pending → 两名不同审核人 → 24h canary → `model_price_versions.status=active` 并生成 approved 行 | 正式治理价 |

「当前有效价格」的唯一判定（`pricing.current_price_eligibility_sql`，在线计费与价格巡检共用）：

- 带版本行的价格：仅 `status=active` 入选，`expired`/`rejected` 永不参与计价；
- 无版本行的价格：仅 `source=provider-page` 的直登价入选，且该模型组件不存在任何 active 治理版本——**治理版本优先**，防止直登价与正式价混价；
- 同一模型组件混用版本或币种视为冲突：读取失败关闭，计费记 `price_unknown`/`unpriced`，绝不挑一条继续算。

硬预算（`enforce`）下的缺价处置取代 2026-09-22 的「缺价一律放行」：

- 价格冲突 → 拒绝调用（`price_conflict`）；
- 取不到有效价格行、且注册表（`llm_models.pricing`）也没有任何正单价 → 拒绝调用（`price_unavailable`）；
- 取不到有效价格行、但注册表仍有正单价 → 放行并按注册表估价结算，`cost_status=price_unknown`，不得展示为精确成本或零成本。

价格巡检读取「当前系统价」与在线计费使用同一谓词，因此直登价不再在巡检中显示为缺价，治理版本切换后也不会拿旧版本比对。

### 4.3 价格变更权限与生效方式（2026-10-17 决策）

价格治理不再要求「双人审核 + 24 小时 canary」。改为：

- **只有 `super_admin` 能改价**：`/admin/model-prices/imports`、`.../reviews`、`.../versions/{version}/publish`、`.../canary` 全部要求最高权限角色；`admin` 仍可读版本历史、登记模型，但不能改计费价。
- **一步直发**：`POST /admin/model-prices/versions/{version}/publish`（`source`、`reason` 必填）直接写入
  `model_price_versions(status='active')` 与 `approval_status='approved'` 价格行，旧 active 版本置 `expired`，
  同维度旧行置 `effective_to`（append-only，只关不删）。审批证据形状为
  `reviewer_1=操作人 / reviewer_2=system / imported_by=super-admin`，并在 `model_price_reviews` 落审计理由。
- **仍然保留的硬约束**：覆盖率必须 100%、缺价/价格计算错误必须为 0、币种必须为 `CNY` 或 `USD` 且同一模型组件不得混用、历史行不可改写。
- **热更新**：发布成功后立即失效进程内价格缓存（`pricing.clear_price_cache()`），后续模型登记与调用按新价计费、账本记为 `approved_price_table`。
- **CI 真实评测**：`seed_prompt_eval_db` 的只读 Registry 校验接受上述审批形状（`version_imported_by='super-admin'`），因此不需要伪造第二个审核人。

### 4.4 本地推理与嵌入重建的记账

只有已登记模型所属 Provider 明确声明 `billing=local` 时，统一预算层才免云端现金费用预占和云端单价检查；调用次数、token 上限与用量记录仍生效。未知模型不能依靠缺省 Provider 推导为免费。本地声明及恢复这类历史配置要求超级管理员用户，普通管理员不能以该声明绕过按量价格治理。

统一账本将本地推理记为 `pricing_source=local_inference`、云端按量应付费用 0 CNY，保留真实调用次数和可得 token。嵌入 SDK 未提供统一原始 usage 时按文本估算，标 `usage_source=estimated`、`cost_status=estimated`，不冒充厂商精确账单；硬件、电费及托管开销不在该零金额含义内。

嵌入切换预检和全库重建均复用正式嵌入客户端与 TokenTracker，Trace 关联任务和调用用户/租户，不另建费用账本。云端按量目标在预检前必须具有有效、一致的 embedding 价格；保存角色时后端会再次预检，因此预检及保存各可能产生一次最小调用费用。批次调用有界且仍受用户/租户额度约束，已落库且快照一致的候选在同任务恢复时复用；超时但供应商实际已执行的请求无法保证不被再次收费。

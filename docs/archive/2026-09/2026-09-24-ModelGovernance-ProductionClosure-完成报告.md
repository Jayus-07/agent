# LLM Model Governance Production Closure — 完成报告（STOP E / Freeze）

- 日期：2026-09-23
- 基线：main @ 7e21440 起，五个 STOP 独立提交（见 §2）
- 前置审计：`docs/2026-09-24-ModelGovernance-STOP-A-Audit.md`

## 1. Verdict

```text
MODEL_GOVERNANCE_PRODUCTION_CLOSURE_PASS=true
MODEL_GOVERNANCE_CORE_FROZEN=true
```

冻结条件逐项核对（任务书 §19）：见 §14。

## 2. Commits

| STOP | 提交 | 内容 |
|---|---|---|
| A | 7e21440 | docs(llm): audit model governance production chain |
| B | 42b6848 | feat(llm): establish canonical model registry |
| C | 5d86db1 | fix(llm): close runtime usage and billing identity |
| D | 063c5b0 | feat(llm): govern model capabilities and fallback |
| E | 本提交 | docs + 验收脚本（backend/scripts_e2e/verify_model_governance.py） |

## 3. Identity Model

```
role（12 枚举，model_roles.py 唯一事实源）
  → canonical（llm_models.name，PK，name==canonical_name 语义固化）
  → provider（llm_models.provider_id → llm_providers）
  → driver（llm_providers.driver：openai|anthropic|ollama|specialized，provider≠driver）
  → upstream（llm_models.upstream_model_name，实际发送给厂商的 model 字段）
  → response.model（观测值，落 llm_usage.upstream_model_id，永不覆盖 canonical）
```

单一 lookup：`models.lookup_model_entry`（name → upstream 双匹配）；
`resolve_provider` 先双匹配再启发式。归属序（`_record_tokens`）：
**显式声明 > ResolvedModelContext > response 观测值**。

## 4. Registry（数据库最终字段）

`llm_models`（045 后）：name(PK)/provider_id/display_name/description/
**capabilities JSONB**（键：tools/vision/structured_output/thinking，只认显式 true）/
context_length/**max_output_tokens**/pricing/enabled/source/时间戳/
model_kind（六值 CHECK）/upstream_model_name。
`llm_usage`（046 后）：30 列 + **requested_model / upstream_model_id /
binding_source / input_unit_price / output_unit_price / cache_input_unit_price
（NUMERIC(18,6)，price_unknown 时 NULL）**。两个迁移均已应用实机库并幂等复验。

## 5. Role Bindings（最终 role matrix，实机导出）

| Role | Primary（DB 绑定） | Fallback | 能力匹配 | 状态 |
|---|---|---|---|---|
| main | doubao-seed-2.0-mini | qwen3.8-flash（fallback 角色） | tools ✓ thinking ✓ | ✅ |
| context_compactor | qwen3.8-flash | inherit main 链 | tools ✓ | ✅ |
| fallback | qwen3.8-flash | —（单层，无环） | tools ✓ | ✅ |
| eval_gen | doubao-seed-2.0-mini | 无 | chat | ✅ |
| ocr | qwen-ocr | 解析兜底 | kind=ocr 匹配 | ✅ |
| embedding / rerank | qwen3.7-text-embedding / -rerank（专项绑定表） | 本地降级 | kind 匹配 | ✅ |
| doc/metadata_extract/question_gen/table_describe/tool_selector | （inherit main） | 角色策略表 | — | ✅ |

`llm_model_role_policy` 0 行（全部走代码默认，无 role 指向 disabled 模型、无 kind 错配）。

## 6. Capabilities

- 容器：`llm_models.capabilities` JSONB（031 建列，本轮转正）。
- 读取口径：`model_capabilities`（只认显式 true，fail-closed）+ `registry_declares`
  （显式声明判定，支撑渐进治理）。
- 消费点：D1 工具调用门（显式 false 拒绝 bind）、D4 thinking 统一判定
  （声明优先+legacy 兼容）、D7 角色绑定校验（tool_selector 显式 false 拒绝）。
- 初始值（migration 045，官方来源 checked_at=2026-09-23）：
  doubao-seed-2.0-mini={tools,vision,thinking}、qwen3.8-flash={tools}、kimi-k3={vision}。

## 7. Context / Tokenizer

- context_length：doubao=262144（256K，火山方舟文档）、qwen3.8-flash=1000000
  （1M，阿里云 Model Studio）、kimi-k3=1048576（1M，Kimi 平台/OpenRouter）；
  qwen3.7-flash / qwen-ocr / embedding / rerank 无可靠官方来源 → **保持 NULL**
  （fail-safe 回退全局更小窗口，不编造）。
- 接口：`token_counter.resolve_model_context_window = min(env, 登记值)`；
  测试锁定「登记值直接决定 Budget 有效窗口」（test_registry_window_*）。
  Context Budget 算法本体零改动（CORE_FROZEN 遵守）。
- tokenizer_id：**未实现（死链保留）**——现有标定估算路径工作正常，见 §13。
- max_output_tokens：概念已拆分（列 + resolve_output_token_cap）；
  活跃路径（driver_compat）已接 per-model 值，NULL 回退窗口值（零行为变化）。

## 8. Usage（实机豆包/Qwen 真调结果）

`STOP_E_DYNAMIC_VERIFY=PASS`（backend/scripts_e2e/verify_model_governance.py）：

```text
[E3/E9] main 豆包真调 → model=doubao-seed-2.0-mini
        provider=custom-doubao-seed-2-0-mini
        upstream_model_id='doubao-seed-2-0-mini-260428'（回传原值）
        binding=db_binding  cost=7.4e-05  status=exact  unit_in=0.200000
[E7]   response.model='doubao-seed-2-0-mini-260428' 与 canonical 同见、互不覆盖
[E3]   context_compactor qwen3.8-flash → model=qwen3.8-flash provider=custom-api（不串线）
```

对比修复前：同链路产生 `model=doubao-seed-2-0-mini-260428, provider=ollama,
cost=0`（llm_usage 存量 1174 行错位）；9-23 旁路链仍在产生错位行，
修复后归属全部正确。

## 9. Billing

- 价格权威：model_price（append-only + 双人审 + 24h 灰度，KEEP 未动）。
- 单位：per_1m_tokens（内部统一，未改口径）；币种随价格行（USD/CNY）。
- **Billing Snapshot（C6）**：调用时 input/output/cache_input 单价随 usage 行
  落库 + cost_status；改价后快照跟随新价、历史行物化不变
  （test_snapshot_unit_prices_survive_price_change）。
- price missing：exact/estimated/unpriced/price_unknown 四态收口；
  **unpriced 不冒充免费**；billing fail-open（含意外异常补口）。
- cache token：cache_read ⊆ input，billable=input−cached；缺 cache 价行按
  input 价保守估（estimated）。

## 10. Fallback

- retry（同模型瞬时错误，预算预占 primary/retry）与 fallback（换模型，
  decision='fallback'）语义分离；单层单值结构**天然无环**（测试锁定）。
- C11/C12：每个失败 attempt 落独立 usage 行（tokens=0、finish_reason=error:*、
  decision 区分 primary/retry/fallback）——provider 已计费的半途调用不再消失。
- 实机说明：E8 未对生产绑定做破坏实验（多会话共享环境），以
  test_fallback_usage（含端到端韧性链双 attempt 断言）+ 代码路径覆盖。

## 11. Observability

- 新增指标（低基数 labels，无 user/session/trace）：`llm_requests_total{model,
  provider,status}`、`llm_failures_total{model,error_type}`、
  `llm_fallback_total{primary_model,fallback_model}`。
- 流式缺 usage：`llm_usage_missing_total` + warning（C13，不再静默）。
- trace：`_last_call_meta` 携带 canonical/upstream/requested/provider/
  binding_source/单价快照；usage_parse 统一解析层（C4 消除双 parser 漂移）。
- 告警（E13）：沿用既有 budget_threshold/budget_events + PG 看板体系，
  未新建 observability 框架；LLMProviderFailureRateHigh 等可直接基于
  `llm_failures_total/llm_requests_total` 比值定义（Prometheus 规则侧后续接入）。

## 12. Regression

- STOP B/C/D 新增测试：12 文件 66 例全绿（registry/resolution/identity/
  capabilities/limits/usage_identity/usage_billing/fallback_usage/stream_usage/
  pricing_snapshot/capability_gate/usage_parse 委托）。
- E14 回归：tests/context_budget + tests/infra（除基线红外）**391 passed**；
  tests/test_llm_span_fields 21 passed；metadata_llm 4 passed。
- 存量测试跟进新契约（非偷改）：test_pricing_cost_status（breakdown 新增
  单价键）、test_llm_span_fields（消除注册表顺序耦合，自备 seed fixture）。
- **基线红登记**（clean worktree e7f6d70 复现，与本轮无关，未偷改）：
  test_llm_provider_passthrough ×3、test_llm_role_resolution ×1、
  test_model_config_runtime ×1、test_llm_bind_tools::test_valid_model_with_key_accepted
  （env mock 与 DB 凭据唯一事实源冲突）。

## 13. Remaining Risks（如实列出）

1. **CNY 计价模型成本按 1:1 记入 USD 账本（P0-2 遗留，降级 P1）**：
   qwen3.8-flash/kimi-k3 的 cost_usd 少乘汇率（~7 倍低估），预算 settle 同样
   少扣。修复需汇率来源与 settle 语义的业务决策（cost_currency 列 + 汇率配置
   + 看板分组三步），涉及 budget_ledger 独立治理域，本轮不冒进。
   Token/身份/单价快照不受影响（快照已带币种语义可追溯）。
2. 流式 usage 缺失目前是**打点+告警**，未做估算补齐（估算会污染精确数据，
   按任务书「禁止沉默」的最低要求收口）。
3. `upstream_model_name` CRUD 静默重置缺陷（STOP A P1-5）**未修**——当前
   存量无别名行不受影响；别名登记后编辑供应商仍会抹掉，需在 service
   _upsert_model 传参修复（清单在案）。
4. builtin 6 个 provider builder 的「窗口=输出上限」耦合仍在（builtin 当前
   全部 disabled；活跃 custom 路径已解耦）。
5. tokenizer_id 死链：读取方存在、数据源与实现缺失（标定估算路径正常）。
6. 旁路直建实例（keyword 本地 Ollama、eval 兼容路径）无 ResolvedModelContext，
   provider 归属依赖启发式（本地模型语义正确；云端直建须先登记）。
7. 9-22 的 1174 行存量错位数据未清洗（历史成本低估不可追溯修复，仅能标注）。
8. E8 fallback 实机破坏实验未做（见 §10）；E10 以冷启动语义验证（共享
   app 容器未重启，遵守多会话纪律——容器 bake 镜像需 rebuild 后新代码才生效）。
9. 基线红 6 例（§12）待归属会话处理。
10. `rag/chain.py::_finish` 身份修复因该文件混有他人 staged 改动暂缓提交，
    待对方落定后补（修复代码已在工作区，语义：trace 收尾优先 ResolvedModelContext）。

## 14. 冻结条件核对

| 条件 | 状态 |
|---|---|
| Canonical identity 单一来源 | ✅ lookup_model_entry 双匹配唯一入口 |
| Role binding 单一 resolver | ✅ model_roles.resolve_effective（env 已出局） |
| Provider / Driver 分离 | ✅ 无 driver→provider 推断；实机验证 |
| Upstream identity 可追踪 | ✅ upstream_model_id 落库，实机同见不覆盖 |
| Capability registry 生效 | ✅ thinking/tools 门 + 绑定校验消费 |
| Context limit 生效 | ✅ 活跃模型登记值决定 Budget 窗口（接口测试锁定） |
| Usage identity 正确 | ✅ 实机豆包/qwen 双模型归属全对 |
| Billing snapshot 正确 | ✅ 单价快照落库，改价不污染历史 |
| Fallback identity 正确 | ✅ 单层无环 + 双 attempt 落痕 |
| Observability 正确 | ✅ 3 新指标 + 流式缺量打点 |
| 真实模型验收通过 | ✅ STOP_E_DYNAMIC_VERIFY=PASS |

冻结后约束：不再按模型名堆 if/else（新能力走 capabilities）；不新增第二套
Model Registry / Usage Tracker / Billing Calculator；不凭感觉改 fallback。
新增 Provider/模型 = 注册（provider/driver/upstream/capabilities/limits/pricing）
→ 绑定 role → 跑验收脚本，不改核心代码。

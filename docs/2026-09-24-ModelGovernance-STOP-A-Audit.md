# LLM Model Governance — STOP A 全链路审计报告

- 日期：2026-09-23（文档名沿用任务书指定 2026-09-24）
- 基线：main @ 31107b0（工作区含其他会话未提交改动，本次审计零代码修改）
- 方法：四个只读分区审计（Schema/CRUD、RoleBinding/Proxy、Usage/Billing、Capability/Context/UI）+ PostgreSQL 实机核对（agent-postgres-1:5433 → `agent_memory` 库）
- 原则：只审计，不重构。所有结论带 `file:line` 或 SQL 证据。

## 0. Verdict

```text
STOP_A_PASS=true
STOP_B_ALLOWED=true
```

审计完成度：A1~A10 全部完成；豆包 provider 错位已定位到代码级 root cause（含实机 1174 行错位数据佐证，且 2026-09-23 仍在发生）。

---

## 1. 实机数据快照（2026-09-23，agent_memory 库）

### 1.1 llm_models（16 行，PK=name，无 id 列）

| name | provider_id | kind | upstream_model_name | context_length | enabled | 定价(pricing JSONB) |
|---|---|---|---|---|---|---|
| doubao-seed-2.0-mini | custom-doubao-seed-2-0-mini | chat | doubao-seed-2.0-mini | **NULL** | t | USD 0.2/2.0，无缓存价 |
| qwen3.8-flash（context_compactor 绑定） | custom-api | chat | qwen3.8-flash | **NULL** | t | CNY 0.8/2.7/0.1 |
| qwen3.7-flash | custom-api | chat | qwen3.7-flash | **NULL** | t | USD 0.2/0.8/0.04 |
| kimi-k3 | custom-api | chat | kimi-k3 | **NULL** | t | CNY 20/100 |
| qwen-ocr（ocr 绑定） | custom-api | ocr | qwen-ocr | **NULL** | t | CNY 0.5/2.0 |
| qwen3.5-ocr | custom-api | chat | qwen3.5-ocr | **NULL** | f | CNY 0.5/2.0 |
| qwen3.7-text-embedding / qwen3.7-text-rerank | specialized-api | embedding/rerank | '' | **NULL** | t | `{}` 空 |
| deepseek-v4-flash、MiniMax-M3、qwen2.5:3b、qwen3.7-plus、qwen3.7-plus@tp、Qwen/Qwen3-8B、Qwen/Qwen3-32B、Qwen/Qwen3-32B-AWQ | 各 builtin | chat | '' | **NULL** | f | 见 model_price |

要点：**context_length 16/16 全 NULL**；`capabilities` JSONB 16/16 全为 `{}`（caps_set=0）；活跃 custom 供应商 `custom-api` 的 base_url 是 `https://maas.qianwenaiapi.com/compatible-mode/v1`（第三方中转，非阿里官方）。

### 1.2 llm_model_role_bindings（7 行）

| role | model_name | updated_at |
|---|---|---|
| main | doubao-seed-2.0-mini | 2026-09-22 16:34 |
| fallback | qwen3.8-flash | 2026-09-22 04:53 |
| context_compactor | qwen3.8-flash | 2026-09-22 12:35 |
| eval_gen | doubao-seed-2.0-mini | 2026-09-22 08:49 |
| ocr | qwen-ocr | 2026-09-22 04:53 |
| embedding | qwen3.7-text-embedding | 2026-09-19 19:34 |
| rerank | qwen3.7-text-rerank | 2026-09-20 19:27 |

`llm_model_role_policy` **0 行**（超时/重试/failure_policy 全部走代码默认）。planner/sql/reporter/doc/metadata_extract/question_gen/table_describe 无显式绑定 → 走 inherit/代码默认。

### 1.3 llm_usage 中豆包身份错位实据（P0 核心证据）

```text
model                        | provider                    | rows | cost
doubao-seed-2.0-mini         | custom-doubao-seed-2-0-mini |  774 | 0.8776   ← 正确归属
doubao-seed-2-0-mini-260215  | ollama                      |  677 | 0.0000   ← 错位：upstream 回传名 + 默认 provider
doubao-seed-2-0-mini-260428  | ollama                      |  495 | 0.0000   ← 错位（上游 9-22 升级到 260428 版本）
```

按日分布：9-22 错位 1133 行 vs 正确 611 行；**9-23 仍有 39 行错位 vs 214 行正确（旁路至今在产生错位）**。错位行来源：

| role | stage | rows | 链路 |
|---|---|---|---|
| （空） | （空） | 581 | `record_llm_result` 补计量（rag/preprocessing/llm_enrichment.py:57-66，从不 set ResolvedModelContext） |
| metadata_extract | metadata_extract | 532 | `get_llm_for_role` 旁路（绕过 `_LLMProxy` 包装 → 无 ctx） |
| question_gen | question_gen | 59 | 同上 |

### 1.4 model_price（价格治理表）现状

- doubao-seed-2.0-mini：USD 0.2/2.0（approved+active）；qwen3.8-flash：**CNY** 0.8/2.7；kimi-k3：**CNY** 20/100 —— **同表混币种**，而 `llm_usage.cost_usd`/`budget_ledger.*_usd` 按 USD 语义聚合。
- `qwen3.8-flash` 在 llm_models.pricing JSONB 配了 `cached_input_price_per_1m=0.1`，但 model_price 无对应 cache_read 生效行（双写不一致样本）。
- 历史行可见「2000 元/1M」错价被后续行纠正的痕迹（append-only 设计正常工作）。

---

## 2. A1 数据库审计

**前提澄清**：`backend/infra/llm/` 下**没有任何 ORM**（无 SQLAlchemy declarative），llm 治理族全部经 `sqlalchemy.text()` 裸 SQL 读写。migration ↔ 真实库核对**一致**（\d llm_models 与 031+034+035+038+039 叠加结果完全吻合）。

### 2.1 llm_models 实际列（031 建表 + 038/039 演进）

| 列 | 来源 | 类型/约束 | 备注 |
|---|---|---|---|
| name | 031:301 | TEXT PK | **即 canonical_name，无独立 id 列** |
| provider_id | 031:302 | TEXT FK→llm_providers CASCADE | |
| display_name | 031:304 | TEXT DEFAULT '' | |
| description | 031:305 | TEXT DEFAULT '' | |
| capabilities | 031:306 | JSONB DEFAULT '{}' | **全空、零消费** |
| context_length | 031:307 | INTEGER NULL | **16/16 NULL** |
| pricing | 031:308 | JSONB | 键：input_price_per_1m / output_price_per_1m / cached_input_price_per_1m / price_currency |
| enabled | 031:309 | BOOLEAN DEFAULT true | |
| source | 031:310 | CHECK(builtin/user) | |
| created_by/created_at/updated_at | 031:312-314 | | |
| model_kind | 031:407 | CHECK 六值：chat/embedding/rerank/vision/speech/ocr（039 放开 ocr） | |
| upstream_model_name | 038:11 | TEXT DEFAULT ''（''=与 name 相同） | |

**任务书点名列的存在性**：`id`❌（PK=name）、`driver`/`base_url`❌（在 llm_providers 上，031:266-268）、`ocr_kind`❌（039 只是放开 model_kind 枚举含 ocr，无独立列）、`input_price` 等独立列❌（在 pricing JSONB + model_price 表）、`max_output_tokens`❌、`tokenizer_id`❌、`priority`❌、`supports_*` 布尔列❌。

### 2.2 其它表

- **llm_providers**（031:262）：id PK、display_name、driver CHECK(openai/anthropic/ollama + 031:378 加 specialized)、base_url、network_scope、billing(metered/subscription/local)、extra_headers、is_builtin、enabled、探测三列。
- **llm_provider_credentials**（031:285）：key_cipher + fingerprint + last4，明文永不出库。
- **llm_specialized_model_bindings**（031:382）：role CHECK(embedding,rerank)、provider_id FK RESTRICT、adapter、model_name、base_url、options、探测列。
- **llm_model_role_policy**（034:17）：role PK、fallback_model、timeout_seconds(1-600)、max_retries(0-3)、failure_policy CHECK 五值。当前 0 行。
- **llm_model_health**（034:39）、**llm_config_history**（031:350，034 放宽 object_type 加 role_policy）。
- **model_price**（031:52-83）：append-only 触发器（031:88-116）、NUMERIC(18,6)、unit(per_1m_tokens/per_call)、currency(USD/CNY)、双人审核 reviewer_1/2、effective_from/to、approval_status。配套 `model_price_versions`/`model_price_reviews`（031:222-257）。
- **llm_usage**（013:35-58 + 035 补列 + store 自愈补列）：30 列；注意 `ts`/`created_at` 是 **TEXT** 类型。

### 2.3 role 枚举（以代码为准，`config/model_roles.py:101-187`）

12 个：`main, doc, metadata_extract, question_gen, table_describe, tool_selector, context_compactor, fallback, ocr, embedding, rerank, eval_gen`。**与任务书题设差异**：不存在 planner/sql/reporter/vision role——planner/sql/reporter 共用 main（都走全局 `llm` 代理）；"vision" 只是 model_kind 不是 role。`llm_model_role_bindings.role` **无 DB CHECK**，非法值靠读侧过滤丢弃（registry_store.py:364-366）。

---

## 3. A2 Model CRUD 审计

调用链：API route（`app/api/routes/model_config.py`）→ `ModelConfigService`（`services/model_config.py`）→ 裸 SQL 直写（无独立 store 层）；每次写后 `await registry_store.refresh_registry()`（model_config.py:472、954、1258 等共 8 处）。

| 问题 | 答案 | 证据 |
|---|---|---|
| 创建必填字段 | modelName（min_length=1）+ provider_id 路径；经 create_provider 建商时另必填 baseUrl/apiKey（ollama 豁免） | 路由:109、service:1498、1807 |
| name 语义 | **登记名 = canonical**（价格/角色/账目按名引用）；上游真名独立为 upstream_model_name（038 后） | 038:3-6、service:1517 |
| 是否 canonical/upstream 混用 | schema 已分离；但存量数据 upstream=name（豆包行即如此，别名未启用） | §1.1 |
| provider 如何确定 | 用户指定或 create_provider 由 `_provider_slug(display_name, base_url hostname)` 生成 `custom-*` | service:333-338、1854-1865 |
| driver 如何确定 | 用户选（默认 openai）+ service 白名单 4 值校验（openai/anthropic/ollama/specialized） | 路由:87、service:1796-1798 |
| upstream_name 可单独配置 | 是（API 字段 upstreamModelName，缺省=登记名） | 路由:99-101、service:1565-1568 |
| context_length UI 可填 | **否**——API schema 无 contextLength 字段，INSERT/UPDATE 列清单均无此列 | 路由:60-125、service:1437-1445 |
| max_output_tokens | 不存在 | §8 |
| pricing 从哪配置 | admin API（model_prices 导入/双人审/灰度）+ ProviderModelEditor 表单（三价+币种）双写 llm_models.pricing 与 model_price | model_prices.py:42-147、service:158-175 |
| 修改后 runtime 即时生效 | 保存进程即时（refresh_registry）；其它进程（worker）≤15s（后台轮询 `_REFRESH_INTERVAL_S=15.0`）+ 快照签名变化才 `invalidate_runtime_caches()` | registry_store.py:436-471、proxy.py:139-162 |
| 缓存 | 进程内单例快照 + 签名失效，无 TTL 型 registry 缓存（探测目录缓存 300s 除外） | registry_store.py:418-451 |
| 版本/updated_at | 有 updated_at + llm_config_history 审计 | 031:314、031:350 |

**CRUD 缺陷**（P1）：
1. `update_provider`/`configure_specialized` 路径的 `_upsert_model` 不传 upstream_model_name → UPDATE 无条件写 `''`（service:1246-1254、888-894、1417-1427、1374-1389）——**编辑供应商会静默抹掉自定义上游别名**（'' 语义=同 name，当前存量恰好未受害）。
2. `_upsert_model` 无条件覆盖 `display_name = model_name`（service:1385、1423、1444）。
3. 软删复活不对称：UPDATE 路径强制 `enabled=true`（service:1379、1418），软删行被编辑动作静默复活。
4. `sys_providers._db_rows` 恒过滤 `enabled=true`（registry_store.py:69）——停用供应商在管理端不可见、不可再编辑（sys_providers.py:301-303 注释自认）。
5. `DraftProbeRequest.model_kind` 枚举缺 `ocr`（sys_providers.py:102）——草稿探测无法探 OCR 模型，与 039 方向矛盾。

---

## 4. A3 Role Binding 审计（真实 precedence）

**env 已出局**：`model_roles._env_of` 恒返回 ""（model_roles.py:369-372），env_key 仅作管理端兼容展示。真实优先序（代码为准）：

```text
request override（ContextVar，API 入口 fail-fast 校验 chat.py:106-129）
  > role DB binding（llm_model_role_bindings → 15s 刷新注入 _overrides；main 见 proxy.py:726-732）
  > factory 进程内切换（仅 main，factory._current，proxy.py:735-739）
  > 代码默认（MODEL_ROLES[].default；main 默认字面值 "MiniMax-M3"，config/llm.py:159）
```

- 非 main 角色：`get_llm_for_role`（proxy.py:681-695）= `resolve_effective(role)`（DB → inherit → 代码默认），**不看 request override**；doc/metadata_extract/question_gen/table_describe/tool_selector/context_compactor 六个 role inherit=main（空值有语义：是否启用本地 Ollama）。
- `fallback` 角色特殊：只认 `source==SOURCE_DB` 的绑定，否则回落旧常量（proxy.py:698-710）。
- 生效时延：保存方进程立即（service:467-472 set_override+refresh）；其它进程 ≤15s；实例缓存随快照签名失效。**「切换绑定无需重启」已满足（≤15s），STOP E 实测记录真实 SLA 即可。**

| role | 当前绑定 | resolver | 缓存 | fallback |
|---|---|---|---|---|
| main | doubao-seed-2.0-mini（DB） | `_resolve_active_llm` proxy:718-763 | `_default_llm`/`_override_llm_cache` | fallback 角色→qwen3.8-flash |
| tool_selector | （inherit main） | resolve_effective | `_override_llm_cache` | 角色策略 llm_model_role_policy（当前 0 行）→代码默认 |
| context_compactor | qwen3.8-flash（DB） | 同上 | 同上 | 同上 |
| fallback | qwen3.8-flash（DB） | `_configured_fallback_model` proxy:698-710 | `_fallback_llm` | —（一层，天然无环） |
| ocr | qwen-ocr（DB） | resolve_effective + ocr.py:55 特判 | 直建实例 | 失败走 parse 兜底 |
| embedding/rerank | 专项绑定表 | `llm_specialized_model_bindings`（specialized.py） | `rag/embedding_singleton` | 本地降级 |
| eval_gen | doubao（DB） | resolve_runtime_name | get_llm_for_role | 无（评测场景） |

---

## 5. A4 LLMProxy 审计

实际公开面（无 get_model/create_model/resolve_model 三个名字）：

| 步骤 | 位置 |
|---|---|
| 确定 canonical | `_resolve_call_context`（proxy.py:777-839）+ `get_active_model_name`（:743-763）调用时实时解析 |
| 确定 provider | `_get_provider_for`（proxy.py:245-256）→ `models.resolve_provider`（models.py:414-461）：注册表按 **name 精确匹配** → 名称启发式（"deepseek" in / "minimax" in / "qwen" in and ":" not in）→ **默认 `"ollama"`**（models.py:418、454-461） |
| 确定 driver | `models.get_provider_driver`（models.py:464-467）查 `_dynamic_providers` → `_build_llm_for` 分发（proxy.py:343-347） |
| canonical→upstream | `_upstream_model_name`（proxy.py:259-272）**构建时**转换（proxy.py:310-312、factory.py:129-135） |
| 创建 LangChain 实例 | `_build_llm_for`（proxy.py:296-363）provider 手写分发表 → providers/*.py；custom-* 按 driver 走 `driver_compat.build_by_driver`（driver_compat.py:33-106）；**兜底 `build_ollama`**（proxy.py:356-363） |
| response.model 处理 | 仅用于 usage 归属（proxy.py:981-998），经 `canonical_model_id` 归一后保留 upstream 原值在 `upstream_model_id`（:1094）；**无覆盖 canonical 的代码** ✅ |
| usage 身份来源 | `_record_tokens`（proxy.py:928-1149），归属序：response_metadata.model_name → 显式 model_name → ResolvedModelContext.model_id → LLM_MODEL 常量（:980-998） |

**结构性不对称（P0 根因之一）**：`canonical_model_id` 认 name+upstream 双匹配（models.py:400-406），而 `resolve_provider`/`get_model_entry` 只认 name（models.py:373-378）——**任何拿到 upstream 回传名再反查 provider 的路径必然 miss → 默认 ollama**。

**LangChain 实例创建入口共 10 处**：主链 2（proxy `_build_llm_for` / factory `_build_instance`，同型分发表双维护）、providers 底层 7 个 builder、driver_compat、provider_probe（探测独立）、**旁路 4 处直建不走 proxy**（evaluation/generation.py:126-143 显式本地分支、rag/preprocessing/keyword.py 3 组、metadata.py:631-632 doc 本地分支）、embedding_singleton（专项层）、health（探测复用）。

**「字符串猜身份」清单**（STOP B/C 收敛对象）：

| # | 位置 | 内容 |
|---|---|---|
| 1 | models.py:439-447 | resolve_provider 启发式 + 默认 ollama |
| 2 | models.py:373-378 vs 400-406 | get_model_entry 与 canonical_model_id 的 upstream 匹配不对称 |
| 3 | proxy.py:1119-1124 | usage 行 provider 在 ctx 缺失/mismatch 时回退字符串推断 |
| 4 | proxy.py:884 | turn 汇总行 provider 恒字符串推断（无视 ctx） |
| 5 | proxy.py:479/532 | 错误归因 provider 字符串推断 |
| 6 | rag/preprocessing/llm_enrichment.py:66-67 | `getattr(llm_obj,"model")` 拿到 upstream 名再 `_get_provider_for`（豆包场景恒判 ollama） |
| 7 | rag/chain.py:905-911 | trace 归属用 import 期常量 LLM_MODEL（override 请求时 trace 头陈旧） |
| 8 | infra/token_tracker.py:289 | embedding 用量 provider 硬编码 dashscope/local |
| 9 | rag/reranker.py:377 | 把协议名 RERANK_API_FORMAT 当 provider 记 |
| 10 | infra/circuit_breaker.py:352 | 全局熔断器硬编码名 "deepseek"（cosmetic） |
| 11 | proxy.py:343-363 | 构建兜底 build_ollama（provider 行 disabled 而 model 行 enabled 时真实发给 Ollama） |

---

## 6. A5 Usage 审计 + 豆包错位 root cause（代码级）

链路：请求包装（proxy:1433-1585）→ 韧性链（:572-646）→ usage parse（`_record_tokens` :928-1149）→ 分发（Prometheus llm_tokens_total :1000-1008；请求预算记账 :1066-1077→budget:342-360；per-turn 累加 :877-897；DB 落库 :1102-1144）→ trace 回填（tracer:860-915）→ 看板（observability.py:256-282）。

**llm_usage 已存**：model/provider/prompt/completion/total/cached/reasoning tokens、cost_usd+035 分项五列、cost_status、currency、role、tenant/user/session/trace/request/run/step、decision、duration、finish_reason。
**未存**：**单价快照（price_per_unit）、price_table_version**；`upstream_model_id/configured_model_id/binding_source` 只进 trace ContextVar（proxy:1094-1097），**不进 llm_usage**。

**豆包错位完整因果链**（每一环都有实机数据佐证）：

1. 豆包上游真实模型名是 `doubao-seed-2-0-mini-260215/260428`（usage 表实据），注册名/upstream 均为 `doubao-seed-2.0-mini`（llm_models 实据）。
2. `record_llm_result` 补计量链（llm_enrichment.py:57-66）与 `get_llm_for_role` 旁路（metadata_extract/question_gen）**从不 set ResolvedModelContext**。
3. `_record_tokens` 归属：response_metadata.model_name = upstream 名 → `canonical_model_id` 在**注册表未加载或条目缺失的进程**（worker/beat 不刷 registry，health.py:224-230 注释自认；首刷失败 fail-open 保空表 registry_store.py:303-328）原样放行 upstream 名 → `resolve_provider` 启发式对 "doubao-*" 永不命中 → **默认 provider="ollama"**（models.py:454-461，伴随一次性 warning `[LLMRegistry] 模型 ... 按 ollama 处理` 可作日志检索证据）。
4. provider=ollama → 估价 0 元（builtin ollama 定价为 0）→ **cost=0**（usage 表错位行 cost 全 0 实据）→ 计费系统性低估（真实 USD 2.0/1M output）。
5. 9-22 主链路修复（ResolvedModelContext 上线）后主链正确；但旁路三条链 9-23 仍产生 39 行错位。

---

## 7. A6 Billing 审计

| 问题 | 答案 | 证据 |
|---|---|---|
| price 来源 | **DB 唯一权威**（model_price，治理状态机 pending→reviewed_1→scheduled→canary 24h→active，price_governance.py:124-275）；embedding/rerank 有代码内置价兜底（models.py:271-296，CNY×0.138 硬编码汇率） | pricing.py:183-204 |
| 单位/币种 | per_1m_tokens；currency USD|CNY **混存** | 031:65-73 |
| 调用时价 or 查询时价 | **调用时算好写入行** ✅；但**无单价/版本快照列**，事后无法审计用哪版价 | proxy:1016-1064 |
| 改价污染历史 | 不会（成本已物化）；但无法复核 | §2 |
| cache token 缺失 | cached=0 全额按 input 计；缺 cache_read 价行时按 input 价保守估、status=estimated | pricing:384-392、432-434 |
| unknown usage | 软路径永不抛错（pricing:407-464 注释明确「成本统计失败不能影响主链路」）；估价>0→estimated，=0→unpriced；硬门缺价放行+注册表估价+`cost_status="price_unknown"`（**超出 m035 声明枚举 exact/estimated/unpriced**） | proxy:1044-1057 |
| fail-safe | usage 写库/预算/指标三层 try/except fail-open（store:254-256、proxy:1102-1149、budget:40-47）✅ 绝不影响聊天 | |
| 精度 | Decimal 计算（6 位舍入）→ **float 落库**（DOUBLE PRECISION） | pricing:150、proxy:1065 |

**P0 级发现——CNY 面值记入 USD 账本**：currency 跟随价格行可为 CNY，但 `cost_usd/total_cost` 直接存该币种数值（proxy:1065、1131），预算 `settle` 又把它当 USD 记入 `budget_ledger.*_usd`（budget:357-360）——kimi-k3（CNY 20/100）等 CNY 模型成本被 1:1 计入 USD 额度，看板 `SUM(cost_usd)` 混币种聚合（store:348-358）。

**P0 级发现——软/硬双轨计费口径不一致**：enforce 路径计 cache_write/reasoning/tool_call（proxy:1020-1027），默认软路径只计 input/cache_read/output（pricing:432-434）——cache_write 无落库列，漏计无法事后补算。

**双审批路径残留（P2）**：pricing.py:244-281 旧 `approve_version`（无灰度）与 price_governance 状态机并存，误用可绕过 24h 灰度。

---

## 8. A7 Capability 审计

- **不存在任何 supports_* 布尔判断**（全仓 grep 零命中）。能力治理实际由两套承载：① `model_kind` DB CHECK（六值，读写/校验链完整）；② `llm_models.capabilities` JSONB——**有存储、零消费**（无运行时读取方，API/UI 均无写入口）。
- **enable_thinking 三套口径漂移中（P1）**：auto_compact.py:397-413（名称 qw* + provider 双依据）/ llm_enrichment.py:72-77（仅 provider）/ provider_probe.py:583-593（域名+qwen3 名称）。
- **bind_tools 无能力前置检查**：tool_selector.py:335、proxy.py:1333-1355 只校验「是否在注册表」，能力不支持时靠 provider 报错后 try/except 回退。
- **运行时无 role↔kind 校验**：保存时 4 层校验完备（models.py:161-171 expected_model_kind + service:355-362 + registry_store.py:229-232 + 前端镜像 modelConfig.ts:180-189），但 `get_llm_for_role` 调用层不拦历史脏绑定。
- ocr 特判死路：ocr.py:55 只认 `model_kind=="chat"` 的绑定走云端，绑 `ocr` kind 的模型反而拿不到凭据——与 039 放开 ocr kind 的方向矛盾。
- driver 假设 OpenAI 全能：driver_compat.py:62-72 无条件按 ChatOpenAI 全参数构建；全仓无 with_structured_output/json_mode 运行时调用（structured output 需求当前不存在）。

## 9. A8 Context / Tokenizer 审计

**context_length 全 NULL 四维根因**（全部实锤）：DDL 无默认（031:307 可空）→ seed 8 条 INSERT 不含该列（031:458-505）→ API/UI 无字段（§3）→ runtime 有读但 `min()` 恒不生效。

**读取链唯一**：registry_store:75 → `token_counter.resolve_model_context_window`（token_counter.py:214-230）= `min(LLM_CONTEXT_LENGTH, entry.context_length)`，entry NULL/≤0 → 配置窗口。配置窗口 precedence：env `LLM_CONTEXT_LENGTH`（.env=8192），代码默认 **4096**（config/llm.py:161）——**任务书「min(8192, NULL)→8192」口径属实**。Context Budget 全部经 manager.get_input_budget（manager.py:38-55），无第二读取方。**未动 Context Budget 任何代码**（CORE_FROZEN 遵守）。

- tokenizer_id：字段不存在，但 token_counter.py:150-156 有读取方、`_try_native`（:185-187）恒 return None——**死链**。token 计数现状：native（死）→ tiktoken（仅字面 provider=="openai"）→ 标定系数（deepseek/qwen/siliconflow/ollama；**豆包 custom 走 calibrated 估算**）→ len//2 兜底。
- max_output_tokens：字段不存在。**8 处把 LLM_CONTEXT_LENGTH 直接当 max_tokens 传**（driver_compat.py:65、94 + 6 个 provider builder）——「窗口」与「输出上限」共用一个旋钮，属 P1 耦合。

## 10. A9 Fallback 审计

- **retry vs fallback 已分离** ✅：retry=同模型瞬时错误循环（`_call_with_resilience` proxy:572-646，`LLM_MAX_RETRIES` 默认 1 + 预算预占 primary/retry）；fallback=重试耗尽/熔断开路后切 `fallback` 角色（只认 DB 绑定，proxy:698-710、467-569），`decision='fallback'` 落库、usage 归真实接管者。
- fallback 单层单值（fallback 角色无级联）→ **天然无环**；无显式 fallback graph。错误可 fallback 性按 `_is_transient` 子串匹配（proxy:396-410）+ error_taxonomy 五类（error_taxonomy.py:19-25）。
- **缺陷**：① 失败 attempt 不落 usage 行（只有预算预占/释放，provider 已计费的半途 attempt 无处记账，proxy:583-634）；② **流式完全无 fallback/重试**（proxy:1454-1456 注释自认）；③ 熔断器全局单例不分模型（circuit_breaker.py:352）。
- requested/resolved/actual 语义：requested（override/角色绑定）在 `_resolve_call_context`；resolved 在 ResolvedModelContext；actual（response.model）只进 trace——llm_usage 表无 requested/upstream 列，**STOP C 需补列**。

## 11. A10 UI 审计（frontend-admin）

- 页面：`frontend-admin/src/app/settings/models/page.tsx`（RoleBindingsTab / ProvidersTab / DriftTab / ConfigHistoryTab / RuntimeChainCard）。
- 供应商表单（ProviderEditor.tsx:166-186）：displayName、driver(3+1 值)、baseUrl、apiKey（password + 服务端不回显）、networkScope、billing、enabled、首登模型名+kind。
- 模型表单（ProviderModelEditor.tsx:39-95）：模型名、kind（六值含 ocr）、币种、输入价、输出价、缓存价、上游名。**未暴露**：Context Length、Max Output、Capabilities、tokenizer、模型级 enabled 开关、description。
- 角色绑定页（RoleBindingsTab.tsx:332-337）：业务角色/生效模型/来源徽标(db·env·inherit·default)/健康/可用性风险/操作 + 策略弹窗（fallback/timeout/retries/failurePolicy）+ 手动探测。能力列、context window 列**无**。
- API key masked ✅ 全链路（明文永不下发，types/modelConfig.ts:548-556 maskSecret 仅 last4+指纹）。
- 小瑕疵：列表 kind 筛选下拉缺 ocr 选项（ProvidersTab.tsx:540-546）。

---

## 12. Identity Matrix

| Concept | 当前字段 | 当前问题 | 目标（STOP B/C） |
|---|---|---|---|
| canonical model | `llm_models.name`（PK） | 语义已收敛=登记名，但 lookup 不认 upstream；无独立 id（PK=name 可接受，B1 按「name==canonical_name」固化，不激进 rename） | name==canonical_name 固化 + Registry 单点双匹配 lookup |
| provider | `llm_models.provider_id`→llm_providers | 运行时 `resolve_provider` 字符串启发式 + 默认 ollama 兜底 | Registry 直查唯一来源；启发式降级为显式告警路径或删除 |
| driver | `llm_providers.driver` CHECK 四值 | 分发表双维护（proxy/factory）；custom-* 兜底 build_ollama | 单一 build_by_driver 分发 |
| upstream | `llm_models.upstream_model_name`（''=同 name） | CRUD 会静默重置 ''；不参与 get_model_entry 匹配；usage 不落库 | 修 CRUD 保字段 + lookup 认 upstream + usage 落 upstream 列 |
| response model | 不落库（仅 trace ContextVar） | `_record_tokens` 用 response.model 做归属且 provider 可错位 | usage 增 upstream/response_model 列，canonical 不被覆盖 |
| role | `llm_model_role_bindings`（role 无 CHECK） | role 合法集纯代码层；运行时无 kind 校验 | role CHECK + 绑定时强校验已有（保存层），运行时兜底校验 |
| request_model | ContextVar（进程内） | 不落库 | usage 增 requested/configured 列 |
| capabilities | `capabilities` JSONB 全空零消费 | 无写入口无读取方 | 定义 schema（tools/vision/thinking/structured），转正消费 |
| context/max_output | context_length 全 NULL；max_output_tokens 不存在 | 8 处窗口=输出上限耦合 | 活跃模型填真实窗口 + 输出上限解耦 |
| pricing | model_price（治理完善）+ pricing JSONB 双写 + embedding/rerank 代码价 | 三处来源；无调用时单价快照 | usage 增单价快照列；来源收敛 model_price |

## 13. Source of Truth Matrix

| 属性 | 当前来源 | 是否唯一 |
|---|---|---|
| provider | llm_providers 表 + resolve_provider 名称启发式（models.py:439-447） | ❌ 双源，miss 时猜 |
| driver | llm_providers.driver | ✅（但分发表两份） |
| upstream | llm_models.upstream_model_name | ✅ 存储（CRUD 会抹） |
| context window | llm_models.context_length（全 NULL）→ 实际唯一生效来源 = env LLM_CONTEXT_LENGTH | ❌ 形同虚设 |
| price | model_price（权威）+ llm_models.pricing JSONB（双写）+ EMBEDDING_RERANK_PRICING 代码价 | ❌ 三源 |
| capability | capabilities JSONB（零消费）+ enable_thinking 三处代码口径 + model_kind | ❌ 多源 |
| role binding | llm_model_role_bindings（15s 刷新） | ✅（env 已出局） |
| usage identity | ResolvedModelContext（主链）+ 字符串推断（旁路/回退） | ❌ 双轨 |
| historical cost | llm_usage.cost_usd（调用时物化） | ✅ 但无单价快照不可审计 |

## 14. 问题分级

### P0（本轮必须收口）
1. **usage provider 身份错位**：resolve_provider 默认 ollama + ctx 缺失回退 + lookup 不认 upstream（models.py:439-447/373-378、proxy.py:1119-1124/884/479）——实机 1174 行错位、cost=0、9-23 仍在发生。
2. **CNY 面值记入 USD 账本**：cost_usd/budget_ledger 混币种 1:1 聚合（pricing.py:444、budget.py:357-360）。
3. **流式 usage 静默丢失无观测**：无 usage chunk 时不落行不打点（proxy:1490-1491、1551-1552），`llm_usage_missing_total` 不覆盖流式。
4. **无 billing snapshot**：llm_usage 不存调用时单价/价格版本，改价后历史成本不可审计（m013/m035 列清单）。

### P1
5. upstream_model_name/display_name 被 CRUD 静默重置（service:1246-1254、1385、1417-1427）。
6. capabilities JSONB 零消费——能力容器未接入（含 bind_tools 无前置检查）。
7. enable_thinking 三套口径漂移（auto_compact.py:397 / llm_enrichment.py:72 / provider_probe.py:583）。
8. 软/硬计费口径不一致（cache_write/reasoning 漏计、无落库列）。
9. tokenizer_id 死链（token_counter.py:150-156、185-187）。
10. 窗口=输出上限 8 处耦合（driver_compat.py:65、94 + 6 builder）。
11. 失败 attempt 不落 usage 行（proxy:583-634）。
12. 旁路 LLM 链绕过 proxy：无 ctx、无限流、无韧性（llm_enrichment/metadata_extract/question_gen/eval/keyword/metadata 直建）。
13. 双 usage parser 漂移（proxy:940-970 vs tracer:814-838）。
14. `reset_turn_usage` 生产无调用→per-turn 用量可跨轮累计（proxy:905-907）。
15. `cost_status="price_unknown"` 超出声明枚举（proxy:1050 vs m035:5）。
16. trace 归属用 import 期常量（rag/chain.py:905-911）。
17. context_length 全 NULL（§9 四维根因）+ 无 max_output_tokens。
18. 运行时无 role↔kind 兜底校验（proxy:681-696）。
19. ocr.py:55 kind=="chat" 特判与 039 矛盾。

### P2
20. float 落库聚合（proxy:1065、store:348-393）；035 分项列只写不读；50 万行滚动删除无归档；无 llm_cost_total/llm_requests_total 指标；双审批路径残留（pricing:244-281）；031 重放收窄 model_kind CHECK（031:410-413）；停用供应商管理端不可见（registry_store.py:69）；drift() 读接口副作用（service:2284-2290）；前端 ocr 筛选缺失；circuit_breaker 硬编码 "deepseek"；token_tracker 整层死代码（tracker:260-264）；embedding token 字符估算不读真实 usage（embedding_singleton.py:242-268）；llm_usage.ts/created_at 为 TEXT 类型；llm_usage.cost 精度。

### KEEP（做得对，冻结不改）
- model_price 治理状态机（append-only + 双人审 + 24h 灰度 + canary）。
- registry 15s 轮询 + 快照签名失效 + fail-open 保旧快照。
- API key 全链路 masked（明文/密文均不下发）。
- `ResolvedModelContext`（resolved_model.py）——已有雏形（ContextVar + binding_source 枚举），STOP B/C 在其上扩展而非重写。
- 038 upstream_model_name 列、039 ocr kind、035 分项成本列（方向正确，接线不足）。
- request override 校验单点（models.py:191-238 validate_override_model，API fail-fast + 进程内宽容双态）。
- usage/billing/metrics fail-open 三层隔离（不影响聊天主链路）。
- retry/fallback/降级话术三层终止路径 + error_taxonomy 五类。

## 15. 与任务书题设的差异澄清（以代码/DB 为准）

| 题设 | 实际 |
|---|---|
| role 含 planner/sql/reporter | 不存在；三者共用 main。实际 12 role 见 §2.3 |
| llm_models 有 id 列 | 无，PK=name（B1 按 name==canonical_name 固化，最小改动） |
| input_price/output_price 独立列 | 在 pricing JSONB + model_price 表 |
| driver 含 custom | driver CHECK=openai/anthropic/ollama/specialized；"custom-*" 是 provider id 前缀 |
| LLM_CONTEXT_LENGTH 默认 8192 | 代码默认 4096，8192 来自 .env（实机取 8192 属实） |
| context_compactor→qwen3.8-flash、main→doubao | 与 DB 绑定一致 ✅ |

## 16. STOP B/C 输入（修复方向，供下阶段决策）

1. **B（Registry）**：name==canonical_name 固化；`canonical_model_id` 的 name+upstream 双匹配下沉为唯一 lookup（`get_model_entry`/`resolve_provider` 复用）；capabilities JSONB 定义 schema 并转正（tools/vision/thinking/structured）；活跃 5 模型填 context_length（须查官方来源记录 source/checked_at；无法确认的保 NULL+fail-safe 小窗口）；补 `max_output_tokens` 列解耦 8 处窗口耦合；CRUD 修复 upstream/display_name 静默重置。
2. **C（Usage/Billing）**：旁路链（record_llm_result/get_llm_for_role 消费方）补 ResolvedModelContext 或让 `_record_tokens` 回退改为「ctx.provider 优先 + Registry 双匹配直查」；llm_usage 增列：requested_model/upstream_model_id/binding_source/input_unit_price/output_unit_price/cache_input_unit_price/price_table_version（billing snapshot）；cost_status 枚举收口 price_unknown；CNY→USD 汇率口径决策（或 cost_currency 列 + 看板分组）；流式无 usage 时落 estimated 行或打点；失败 attempt 落 failed usage 行（decision=retry/fallback 区分已具备）。
3. **不重开**：Context Budget L1-L5/threshold/算法；model_price 治理状态机；registry 轮询框架；LangChain builder 体系（只收敛分发表）。

## 17. STOP A 结论

模型治理的**骨架已存在且质量不低**（031/034/035/038/039 五个迁移、model_price 治理状态机、ResolvedModelContext 雏形、15s 生效链、API key 安全），本轮收口的实质是**把已有骨架接线到闭环**：身份 lookup 双匹配、旁路 usage 归属、billing snapshot、能力字段转正、窗口数据落地。无阻断性发现。

```text
STOP_A_PASS=true
STOP_B_ALLOWED=true
```

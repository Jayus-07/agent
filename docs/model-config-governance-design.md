# 模型配置治理设计（Model Config Governance）

> 2026-09-19 起草。背景：`agent/.env` 已膨胀到 339 行 / 109 个变量，模型相关配置散落在
> 三处互不知情的事实来源上，管理端已有的模型切换器写的是进程内存态（重启即失效）。
> 本文给出收敛设计与实施分期。原文是设计阶段基线；管理端闭环已在本轮落码并完成本地验证，
> 当前实现、测试统计、迁移与上线前事项以 `docs/model-config-governance-progress-report-2026-09-19.md` §10 为准。
>
> **2026-09-20 实施补充**：附录 B 的通用 Chat BYOK 方案已落地；其中 embedding / rerank
> 专项链路已按 B.10 的独立协议适配器实现，当前状态以进度报告 §10.6 为准。

## 0. 决策记录

| # | 决策项 | 结论 |
|---|---|---|
| 1 | 模型选型是否进 DB | **接受进 DB** |
| 2 | 密钥是否允许管理端配置 | **允许**（附带加密与脱敏硬约束，见 §4） |
| 3 | 是否合并现有「模型价格」页 | **合并** |

## 1. 核心判断：扩展 `sys_config`，不新建配置层

原设计草案曾提议新建 `runtime_settings` 覆盖层。实际勘察后**推翻该方案** ——
`backend/services/sys_config.py`（2026-09-16 Lite 版，设计见
`docs/2026-09-16-动态配置lite与API-Key多Key化方案.md`）已经落地了本设计需要的
全部五个不变量：

1. 存储分层：DB `sys_config` 表是覆盖层，表内无记录回落 env 默认值
2. 免重启生效：进程内缓存 + 后台 15s `refresh_loop`，写本实例即时生效
3. 审计含旧值：`sys_config_history` 落 old→new + 操作人，回滚 = 写回旧值
4. 权限收口：写接口挂 `require_admin_user`（`app/api/deps.py:328`）
5. fail-closed：DB/值非法/键未知一律回退「上次已知值 → env 默认」

在同一套机制外再建一层会制造重复。设计阶段原计划扩展 `sys_config`；当前实现将模型治理
配置拆成专用的 `llm_providers` / `llm_provider_credentials` / `llm_model_role_bindings` 表，
由 `registry_store` 注入同一进程内覆盖层，避免把可逆密文塞进 `sys_config_history`。

### 1.1 现有实现的五处不满足（本设计的核心工作量）

| # | 现有约束 | 为什么不满足模型/密钥场景 | 扩展方向 |
|---|---|---|---|
| 1 | `_normalize()` 强制 `.strip().lower()`（`sys_config.py:103`） | 模型名**大小写敏感**：`MiniMax-M3`、`Qwen/Qwen3-8B`、`BAAI/bge-m3` 会被改写成小写从而匹配不上 | 键登记项增加 `case_sensitive`，校验与缓存均保留原大小写 |
| 2 | `_SWITCHES[*].allowed` 是静态 tuple，硬编码在代码里 | 模型角色的合法集 = `AVAILABLE_MODELS` **动态集合**，且会随代码升级变化 | 登记项支持 `validator` 回调（函数式白名单），替代静态 `allowed` |
| 3 | `get_mode()` 同步、零 IO、只回缓存 | 只能返回开关字符串；密钥需要解密+失败语义，模型需要角色解析 | 拆出三个语义入口，见 §3.3 |
| 4 | `sys_config.value VARCHAR(128)`；`sys_config_history.old_value/new_value VARCHAR(128)` | Fernet 密文长度 ≈ `enc:` + base64(1+8+16+n+16+32) 字节，一个 60 字符的 Key 加密后 **>180 字符**，`VARCHAR(128)` 直接截断/报错 | 密钥走**独立表** `provider_credentials`（TEXT 列 + 指纹列 + 版本列） |
| 5 | 审计直接打印值：`logger.warning("[SysConfigAudit] actor=%s ... old=%s new=%s", ...)`（`sys_config.py:214`） | 密钥会**明文进日志与历史表** | 密钥通道走脱敏分支：只落指纹 + 掩码尾部，见 §4.3 |

> 第 5 条不是理论风险：`sys_config_history` 会长期保留每一版值，一旦存过明文密钥，
> 此后任何一次库导出/备份/只读排查都是泄漏面。

## 2. 事实来源分层（定稿）

```
[ 消费方 ]  resolve_model(role)  ·  get_secret(provider)   ← 唯一读取入口
                 ↑ 覆盖优先            ↑ 覆盖优先
[ 覆盖层 ]  sys_config 表          provider_credentials 表   ← 管理端可写，带审计与版本
                 ↑ 兜底                ↑ 兜底
[ 底座   ]  .env（密钥 / 基础设施 / 启动期必需项）           ← 部署兜底 + 应急通道
                 ↑ 最后兜底
[ 代码   ]  AVAILABLE_MODELS 元数据（provider 能力矩阵）      ← 只读，随代码发布
```

三条不变量：

1. **env 语义不变**：`.env` 继续是部署兜底与应急通道，DB 无记录时行为与今天完全一致。
2. **读取方不感知层级**：任何消费方只调 `resolve_model(role)` / `get_secret(provider)`，
   不直接 `os.getenv`。这是收敛的标志——只要还有人在读 `os.getenv("LLM_MODEL")`，
   分层就没有真正建立。
3. **每个生效值都带来源**：读取接口返回 `{value, source: db|env|code-default}`，
   供管理端与排障展示。

## 3. 模型角色（role）抽象

### 3.1 角色表

现状是一组靠变量名后缀约定的散变量，收敛为显式角色：

| role | 语义 | 现变量 | 现状问题 |
|---|---|---|---|
| `main` | 主问答 LLM | `LLM_MODEL` | 与 `LLM_FACTORY` 内存态不一致 |
| `doc` | 入库链路文档级关键词 | `DOC_LLM_MODEL` | 留空 = 跟随 main，隐式继承无表达 |
| `tool_selector` | FC 工具选择+填参 | `TOOL_SELECTOR_MODEL` | 留空 = 跟随 main，同上 |
| `fallback` | 熔断/重试耗尽兜底 | `LLM_FALLBACK_MODEL` | 「须在 models.py 注册」纯注释维护，无强校验 |
| `ocr` | 扫描件 OCR | `RAG_OCR_DASHSCOPE_MODEL` | 与 embedding 用不同 Key，归属不清 |
| `embedding` | 向量化 | `EMBEDDING_MODEL` + `EMBEDDING_PROVIDER` | 与向量索引强绑定，切换需全量重建 |
| `rerank` | 重排 | `RERANK_MODEL` + `RERANK_PROVIDER` | 同上，另有 `RERANK_API_FORMAT` 协议开关 |
| `eval_gen` | 评测答案生成 / RAGAS | `EVAL_GEN_MODEL` 或 DB 角色绑定 | 必须绑定已登记且可用的供应商模型；空值表示停用 |

「留空 = 跟随 main」这类**继承语义显式化**：登记为 `inherit: main`，而非空字符串。
管理端据此渲染「跟随 main（当前 = xxx）」而不是让人猜空值含义。

### 3.2 provider 能力矩阵（留代码，不进 DB）

> ⚠️ **2026-09-19 修订**：本节只界定「**协议与能力**」留代码，未覆盖「**厂商实例**」。
> 后续需求（用户自建供应商 / BYOK + 连通性自测）要求厂商的可变部分进 DB。
> 边界勘定为「**驱动留代码，实例进 DB**」，见 **附录 B**。本节其余内容仍然成立。

以下三项属于「随代码发布」的事实，不能由管理端改，否则会把系统配成不可用：

- 协议类型（openai-compatible / anthropic-compatible / dashscope-native / jina / ollama）
- 是否支持 `stream_options.include_usage`（现 `.env` 注释里写「MiniMax 兼容性未验证」）
- 是否支持余额查询、是否支持 rerank 端点（现注释：「token-plan 不支持 rerank 端点」）

现状散落在 `infra/llm/providers/*.py` 与 `.env` 注释中。收敛为
`AVAILABLE_MODELS[*]` 上的显式字段（保留在 `models.py`，属代码层）。

### 3.3 三个读取入口

| 入口 | 语义 | 失败策略 |
|---|---|---|
| `resolve_model(role) -> {value, source}` | 同步、读进程内缓存、零 IO | 回退「上次已知值 → env → 代码默认」，**绝不返回未注册模型** |
| `resolve_provider(role) -> str` | 由模型名反查 provider | 同上 |
| `get_secret(provider) -> str \| None` | 解密后的明文（仅进程内使用） | **fail-loud**，见 §4.4 |

`resolve_model` 沿用 `get_mode()` 的「热路径零阻塞」不变量：守卫与问答热路径绝不能
因为 DB 抖动而被拖慢。DB 轮询仍由后台 `refresh_loop` 承担。

> 实施口径：`main`、`fallback`、`doc`、`tool_selector`、`ocr`、`rerank`、`eval_gen`
> 已由实际调用点读取 DB 覆盖；`embedding` 受单例和向量索引一致性约束，管理端保存后必须
> 配合全量索引重建，当前索引不会自动切换。

### 3.4 与 `LLMFactory` 的关系（本次必须一并修）

`LLMFactory.__init__` 里 `self._current_model = LLM_MODEL`（`factory.py:53`），
`set_current()` 只改内存（`factory.py:108-113`），**不落盘**。这导致：

- 管理端切换器（`frontend-admin/src/components/agent/LLMSwitcher.tsx`）是「会消失的开关」
- 多实例部署时各实例模型不一致

改造后：`set_current` 写入 `sys_config` 的 `role=main`（或独立的运行时 main 覆盖），
实例间靠既有 15s TTL 收敛；`LLMSwitcher` 从"改内存"变为"改配置"。

⚠️ 需明确一个语义分歧：`role=main`（持久配置）与「临时切一下试试」（运行时态）
是两件事。建议保留 `set_current` 的纯内存语义作为 debug 通道，但**管理端不再调用它**，
改调配置接口，避免再次出现"重启就变回去"的困惑。

## 4. 密钥存储设计

### 4.1 关键区分：入站 hash vs 出站可逆

项目已有的 `api_keys` 方案（`docs/2026-09-16-动态配置lite与API-Key多Key化方案.md` §2.2）
规定「`key_hash` = SHA-256 明文，库中不存明文」，这是**正确的，但只适用于入站凭据**。
两类密钥方向相反，不能套用同一策略：

| 类别 | 例子 | 我们的角色 | 存储方式 |
|---|---|---|---|
| 入站凭据 | `X-API-Key`（现有 `API_KEY`） | 校验方：只比对，不用还原 | SHA-256 hash，**不可逆** |
| 出站凭据 | `QWEN_API_KEY` / `DEEPSEEK_API_KEY` / `SILICONFLOW_API_KEY` … | 调用方：必须把明文发给第三方 | **必须可逆** → 加密存储 |

哈希对出站 Key 无解——我们得拿着明文去调 DashScope。所以「密钥允许管理端配置」
这一决策必然引入**对称加密**，主密钥必须留在 `.env`（鸡生蛋：加密密钥自身不能存在被它
加密的库里）。

### 4.2 复用现有加密先例

`backend/competitor/crypto.py` 已实现 Fernet 对称加密（`COOKIE_ENCRYPTION_KEY` +
`enc:` 前缀 + `maybe_encrypt/maybe_decrypt`），并在 `competitor/store_pg.py` 落地。
**复用该模式，不引入新密码学原语**：

- 抽公共模块 `backend/shared/crypto.py`，`competitor/crypto.py` 改为薄封装（保持其
  `COOKIE_ENCRYPTION_KEY` 与 `crawler_cookies` 行为完全不变，避免动到在跑的爬虫）
- 新增主密钥变量 `SECRETS_ENCRYPTION_KEY`（Fernet key，`.env` 唯一必须驻留的密钥）
- 统一 `enc:` 前缀，新增 `key_version` 支持多主密钥并存解密（轮换需要）

### 4.3 明文三条不变量

1. **只在写入时提交**：`PUT` 请求体带明文，落库前加密，之后**任何接口不再返回明文**
2. **读取接口只回**：`provider` + 掩码尾部（如 `sk-ws-…WEyD3`）+ SHA-256 指纹前 12 位 +
   `configured: true/false`。前端与网关日志里永远拿不到完整 Key
3. **日志/审计/异常只出指纹**：`provider_credentials_history` 存 `old_fingerprint` →
   `new_fingerprint`，不存值；异常信息里对 Key 做掩码

### 4.4 必须修掉的静默回退（否则会变成难查的 401）

`competitor/crypto.py:38-48` 的 `decrypt_cookie` 在**密钥缺失或解密失败时返回原文**：

```python
if f is None:
    return value          # 加密值但无密钥 → 返回原文
except Exception:
    return value          # 解密失败 → 返回原文
```

对 Cookie 这是优雅降级；对 LLM API Key 这是灾难——`enc:gAAAA…` 会被当作 Key 发给
provider，换回一个 `401 Invalid API key`，而真实原因（主密钥丢失/轮换错配）被埋在
provider 的错误信息之下，排查成本极高。这与仓库自己在 `config/llm.py:303` 记录的
「LLM 不可用时返回话术会冒充模型输出，真实原因被埋在 N 层语义错误之下」是同一类问题。

**密钥通道必须 fail-loud**：解密失败 → 视为「未配置」→ 记录 error 级日志（带 provider
与指纹，不带值）→ 由调用方走「未配置」分支（`factory.set_current` 已有该分支语义）。

### 4.5 密钥去重（顺带解决 401 隐患）

现状 `.env` 中同一把 Key 被写进多个变量名，改一处忘一处即 401：

| 实际同一把 Key | 现分散变量 |
|---|---|
| 阿里云百炼 Key | `DASHSCOPE_API_KEY`、`QWEN_API_KEY` |
| SiliconFlow Key | `SILICONFLOW_API_KEY`、`EMBEDDING_API_KEY`、`RERANK_API_KEY` |

新模型以 **provider 为单位**存 credential，多个 role 引用同一个 `provider`：

```
provider=aliyun_dashscope  →  credential（1 条）
   ├─ role qwen（chat, compatible-mode）
   ├─ role embedding_cloud（dashscope native）
   └─ role rerank_cloud（dashscope native）
```

注意 `reflection`：`DASHSCOPE_API_KEY` 与 `QWEN_API_KEY` 虽然同值，但**协议与端点不同**
（native `/api/v1` vs `compatible-mode/v1`）。因此 credential 收敛的是「密钥」，
协议仍由 provider 能力矩阵决定——不要顺手把端点也合并了。

## 5. 与 `.env` 的关系

### 5.1 分层归属（定稿）

| 内容 | 归属 | 理由 |
|---|---|---|
| 出站 provider 密钥（作兜底）、`SECRETS_ENCRYPTION_KEY`、`JWT_SECRET`、`API_KEY` | `.env` | 启动期必需 / 引导密钥 |
| PG / Redis / 端口 / `RAG_DATA_DIR` / `CORS_ORIGINS` | `.env` | 部署环境事实，随容器编排走 |
| `ENVIRONMENT`、`ENV_MODE` | `.env` | 启动期决定行为分支，运行期不该变 |
| 模型角色绑定（8 个 role） | DB 覆盖层 | 运行期可调，需审计 |
| provider 密钥（主存储） | DB（加密） | 本次决策 2 |
| 功能开关（`CS_ENABLED` / `TRAVEL_ENABLED` / `SELECTION_FUNNEL_ENABLED` / `ENABLE_*`） | DB 覆盖层，`.env` 保留兜底 | 现已是纯 env，纳入 `_SWITCHES` 登记即可 |
| RAG 检索参数（`CHUNK_SIZE` / `BM25_SEARCH_K` / `RERANK_TOP_K` …） | **暂缓** | 影响索引一致性，误改代价高；先只读展示，不允许写 |
| 治理模式（`LLM_BUDGET_MODE` / `GATEWAY_AUTH_MODE`） | DB 覆盖层 | 已有对应治理页（`/cost-governance/budgets`） |

⚠️ `EMBEDDING_MODEL` / `EMBEDDING_PROVIDER` 是**特例**：与向量索引强绑定，切换后必须
全量重建索引（`.env` 第 293、302 行均标注）。管理端允许改，但必须：

- 二次确认对话框，明示「需全量重建向量索引」
- 写审计时打 `requires_reindex: true` 标记
- 视图上把「当前生效 embedding 模型」与「索引实际使用的模型」并列显示 —— 后者需要新增
  一处索引元数据记录（这是本次设计外溢出的一个小需求，见 §9 P2）

### 5.2 `.env` 瘦身目标

P3 完成后 `.env` 的模型段从 ~40 行降到 ~8 行（只留 provider 级兜底与主密钥），
并把所有「回滚 = 恢复下行注释」的人肉操作改为管理端的版本回滚。

## 6. 合并模型价格页

### 6.1 价格沿用重流程，角色绑定走轻流程

现有 `PostgresPriceGovernanceRepository`（`infra/llm/price_governance.py`）实现了相当完整
的状态机，**不建议改**：

```
pending → reviewed_1 → scheduled → canary（需满 24h）→ active
              ↓                              ↑
           rejected              覆盖率须 100% / 缺价=0 / 计算错误=0
```

含双人审核（导入人不得审核、`reviewer_1 ≠ reviewer_2`）、24h 灰度、active 互斥（旧的置
`expired`）。这套流程之所以重，是因为**价格直接决定预算硬阻断的判定**，算错会误拦线上请求。

但**模型角色绑定不该照搬这个重量级**：切换模型是运维动作，需要分钟级生效，走双人审核
+24h 灰度会导致故障时无法快速回切。

**结论**：两个 tab，两套流程，同一个交互壳。

| 对象 | 流程 | 生效延迟 |
|---|---|---|
| 模型角色绑定 / 开关 | 单 admin 写 + 审计 + 一键回滚（`sys_config` 现有语义） | 本实例即时，其他实例 ≤1 TTL |
| 模型价格 | 双人审核 + 24h 灰度（现有状态机，原样保留） | 最快 24h |

### 6.2 消除两套价格（关键收益）

现状价格有两个来源，且第二套是权威的：

1. `infra/llm/models.py` 的 `AVAILABLE_MODELS[*].input_price_per_1m` —— 硬编码 USD 字面量
2. PG `model_price` 表（`price_table_version` + `approval_status`）—— 权威，带审批

本次合并后 **退役第 1 套**：`get_model_pricing()` 改为读价格表，
`compute_cost_usd()`（`models.py:170`）随之改数据源。收益：

- cost 估算与预算阻断用同一套价格，不再有「看板显示 0.014 但预算按 0.4 拦」这类不一致
- 价格变更走审批，不再需要改代码发版

> ⚠️ **合并时必须一并修的缺口**：`EMBEDDING_RERANK_PRICING`（`models.py:194`）只登记了
> DashScope 的 `qwen3-rerank` / `text-embedding-v3` / `text-embedding-v4` /
> `qwen-vl-embedding`。而 2026-09-19 已切到 SiliconFlow 的 `BAAI/bge-m3` 与
> `BAAI/bge-reranker-v2-m3` —— 这两者不在表内，`compute_embedding_cost` 返回 0.0，
> **当前 embedding/rerank 成本在估算中是被低估为 0 的**。合并价格页时须按硅基流动账单补录。

## 7. 管理端

> **实现级展开**：`docs/model-config-admin-ui-design.md`（2026-09-19，组件树 / 字段级契约 /
> 态设计 / 后端缺口清单）。本节只定「做什么」，实施细节看那份。

### 7.1 页面位置与权限

- 路由：`/settings/models`，页面名「模型与供应商」
- 导航：挂「质量与配置」组（`components/layout/navConfig.tsx:74-81`），
  与「Prompt 管理 / Agent 节点 / 能力与技能」同级
- 权限：**页级 `minRole: 'editor'` + tab 级 `admin`**（2026-09-19 修订）
- 现有页 `/cost-governance/prices` **重定向**到新页的「价格」tab；其 `navConfig` 条目
  **删除**（2026-09-19 定，避免两个入口）。重定向保留 —— 外部收藏与既有文档链接不失效

> ⚠️ **修订说明（2026-09-19）**：本节原写「`minRole: 'admin'`」，与 §7.2 的
> 「tab⑤ 体检与漂移 → editor 可见」**自相矛盾** —— 页级 admin 门禁下 editor 根本
> 进不了页面，§7.2 那条要求无法成立。
> 改为「页级 `editor` + tab 级 `admin`」，与既有先例
> `frontend-admin/src/app/cost-governance/prices/page.tsx:29,105` 的
> `RoleGate minRole="editor"` + `canAdmin = atLeast('admin')` + 顶部只读提示条**逐字一致**。
> 配套：tab② 供应商与密钥**对 editor 整 tab 隐藏**（非只读，B.6「查看供应商 = admin」），
> tab④ 的密钥类条目脱敏。完整矩阵见 UI 设计文档 §3.3。

### 7.2 五个 tab

| tab | 内容 | 权限 | 数据源 |
|---|---|---|---|
| ① 角色绑定 | 8 个 role 各选模型；显示「生效值 + 来源（db/env/默认）+ 校验结果」；未注册模型或缺 Key 标红不可选；`embedding` 改值时弹二次确认 | admin | `resolve_model` |
| ② 供应商与密钥 | provider 列表（base_url、协议、能力矩阵只读）；密钥只显示「已配置/未配置 + 掩码尾部 + 指纹」；可写入/轮换；连通性自检按钮；余额（复用 `/llm/balance`） | admin | `provider_credentials` |
| ③ 价格 | 现有价格版本的导入/审核/灰度完整流程（原样搬移） | admin | `price_governance` |
| ④ 变更历史 | 两类对象的合并时间线：谁在何时把哪个键/role 从 A 改成 B；支持一键回滚 | admin | `sys_config_history` + `provider_credentials_history` |
| ⑤ 体检与漂移 | DB 覆盖与 `.env` 不一致时点名；缺 Key / 未注册模型 / 索引模型与生效模型不一致 | editor 可见 | 聚合 |

### 7.3 后端端点（草案）

沿用 `/sys/config` 的既有分层（`require_admin_user` + `/sys` 前缀在 api_key_middleware
白名单 + service 层校验）：

```
GET    /sys/model-roles                  角色生效快照（含 source 与校验结果）
PUT    /sys/model-roles/{role}           绑定模型（case-sensitive 校验 + 审计）

GET    /sys/providers                    供应商清单 + 能力矩阵（只读）+ 配置状态
PUT    /sys/providers/{provider}/credential    写入/轮换密钥（加密落库，审计只落指纹）
POST   /sys/providers/{provider}/verify        连通性自检（用解密后的 Key 发一次最小请求）
POST   /sys/providers/verify-draft             草稿态自检（无 id，用于「保存前先测」）

GET    /sys/config/history               合并变更历史（支持 ?object= 过滤）
POST   /sys/config/history/{id}/rollback 一键回滚
GET    /sys/config/drift                 漂移与体检报告
```

**响应形态：一律裸 dict，不带 Result 壳** —— 与同前缀 `/sys/config` 一致。
理由链与决策记录见 `docs/model-config-admin-ui-design.md` §1.1.1。一句话版本：
`client.ts` 的 `request<T>` **不解包**，用壳等于每个调用点手工 `.data`（靠人记住），
且壳里的 `code` 与 HTTP 层的 `ApiError` 构成**两套冗余错误通道**。

> ⚠️ **不要把 `securityOps.updateGuardMode` 当模板。** 本节原写「复用它的写法」，
> **方向恰好相反** —— 它在 2026-09-19 之前是错的：对裸 dict 响应写了 `return res.data`，
> 造成「界面报切换失败、后端其实已写库」的假失败（`docs/model-config-admin-ui-design.md` §1.1）。
> 可复用的是它的**路径前缀**（`/api/sys/...`，网关剥 `/api`）；
> **响应解包方式不可复用**。正确模板是修复后的 `securityOps.ts` 与其共置契约测试
> `api/securityOps.test.ts`（用真实响应形状驱动，改回 `.data` 会失败）。

前端 API 层路径：`/api/sys/...`（网关剥 `/api` 前缀）。

### 7.4 交互壳复用

`LLMSwitcher.tsx` 的视觉语言（胶囊触发器 + 下拉 + 勾号反馈 + 余额徽章）建议保留，
只把数据源从 `/llm/switch` 换成新的 role 接口。它已经跑在真实页面上，重做没有收益。

## 8. 改动波及面（已实测）

| 对象 | 文件数 | 具体位置 |
|---|---|---|
| `AVAILABLE_MODELS` 消费方 | 5 | `app/api/routes/llm.py:14,60`；`infra/llm/factory.py:37,71,75,98,158`；`infra/llm/proxy.py:37,85-88,206,882-883`；`config/startup.py:245-253`；`infra/llm/models.py` 本体 |
| `config.llm` 引用 | 16 | 需逐个改为 `resolve_model(role)`，其中 8 个 role 覆盖大部分 |
| `LLMFactory` | 1 | `factory.py:53,65-113`（`set_current` 落盘改造） |
| 加密模块 | 2 | `competitor/crypto.py` 抽取；`competitor/store_pg.py` 保持接口不变 |
| 管理端 | 5 | 新页面 + `navConfig.tsx` + `api/modelRoles.ts` + `api/providers.ts` + `LLMSwitcher.tsx` |
| 价格 | 3 | `models.py:get_model_pricing` 改数据源；`proxy.py:compute_cost_usd` 调用点；`/cost-governance/prices` 迁移 |

**排雷提示**：`proxy.py` 的 5 处 `AVAILABLE_MODELS` 调用里有 2 处是**启动期/请求期校验**
（第 85-88、882-883 行），改成从 DB 读之后要保证「DB 不可用时仍能校验」——按
`sys_config` 的 fail-closed 不变量，回退到代码层 `AVAILABLE_MODELS` 即可，不要因为 DB
抖动而放行未注册模型。

## 9. 实施分期

### P0 — 契约层（零行为变化，可独立验收）

- `sys_config` 扩展：`case_sensitive` 键登记项、`validator` 回调、三个读取入口
  （`resolve_model` / `resolve_provider` / `get_secret`）
- 8 个 role 登记进 `_SWITCHES`（此时**仍全部从 env 取值**，DB 覆盖表为空）
- 唯一目标：把 16 个文件的 `os.getenv` 收敛到 `resolve_model(role)`，行为与今天逐位一致
- 验收：全量 pytest 通过 + 生效快照与 `.env` 逐项比对一致

> 这一步是整个方案的风险闸门。它不动任何行为，却把"散落在 16 个文件里的配置读取"
> 变成一个可审计的入口。此步完成前不应进入 P1。

### P1 — 密钥通道

- `backend/shared/crypto.py` 抽取（`competitor/crypto.py` 保持行为不变）
- 新表 `provider_credentials` + `provider_credentials_history`（alembic 记忆库迁移 0017）
- 密钥写入/读取/脱敏/审计/`key_version` 轮换 + fail-loud 语义
- 密钥去重：`QWEN_API_KEY`/`DASHSCOPE_API_KEY`、`SILICONFLOW_API_KEY`/`EMBEDDING_API_KEY`/`RERANK_API_KEY` 收敛为 provider 级
- 顺带处理：`.env` 第 312 行注释里的真实 SiliconFlow Key 明文（违反该文件自定规则
  「注释里一律只写占位符」）→ 轮换该 Key

### P2 — 角色绑定与管理端页面

- `set_current` 落盘改造 + `LLMSwitcher` 数据源切换
- 管理端 `/settings/models`：tab ①②④⑤ + `navConfig` 注册
- 索引元数据记录（供 §5.1 的「生效模型 vs 索引模型」对比）
- `EMBEDDING_MODEL` 改值的二次确认与 `requires_reindex` 审计标记

### P3 — 价格合并与 `.env` 瘦身

- 价格治理搬入 tab ③；`/cost-governance/prices` 重定向
- 退役 `AVAILABLE_MODELS[*].*_price_per_1m`，`get_model_pricing` 改读价格表
- **补录 SiliconFlow `bge-m3` / `bge-reranker-v2-m3` 价格**（修 §6.2 的成本低估缺口）
- `.env` 模型段瘦身 + 漂移告警接入 `config/startup.py` 现有 warning 机制

## 10. 风险与待确认

| # | 风险 / 待确认 | 说明 |
|---|---|---|
| 1 | **多实例 TTL 延迟** | `sys_config` 现为各实例 15s 轮询。模型/密钥切换存在 ≤15s 不一致窗口。当前单实例无感；若后续扩多实例，需评估是否上 Redis pub/sub 即时失效（该文档 §1.2 已预留此选项） |
| 2 | **主密钥丢失 = 全部出站 Key 不可用** | `SECRETS_ENCRYPTION_KEY` 必须在部署时持久化并可恢复。需在文档与部署脚本中显式告警，建议纳入备份清单 |
| 3 | **密钥进 DB 后的访问面** | 现在密钥只在 `.env`（文件系统权限即可保护）；进 DB 后，任何有 PG 读权限的进程理论上可拿到密文。缓解：主密钥只在 app 进程内存 + 审计只落指纹 + 只读账号 `agent_readonly`（已存在）不得有 `provider_credentials` 读权限 |
| 4 | **`role=main` 持久化后的预期变化** | 今天"重启回 `.env`"是隐性行为；改为持久后会出现「重启后模型与 `.env` 不一致」的常态。需在管理端明示来源（§7.2 tab ① 已含），否则会被当成 bug |
| 5 | **价格流程与角色流程同页不同重** | 同页两个 tab 一个即时生效、一个要等 24h，交互上必须强区分（建议价格 tab 视觉上标注「需审批生效」），否则用户会以为改了没生效 |
| 6 | **P0 的 16 文件收敛是主要工作量** | 纯重构但触及问答主链路，需在无并发会话跑全量 pytest 的窗口做（仓库约定：全量 pytest 约 20 分钟，期间不得改 `backend/`） |
| 7 | 是否把 RAG 检索参数也纳入 DB | 本设计**暂缓**（§5.1），因其影响索引一致性。待确认是否需要管理端只读展示 |

## 附：本设计引用的代码位置

| 主题 | 位置 |
|---|---|
| 现有动态配置层 | `backend/services/sys_config.py` |
| 动态配置管理路由 | `backend/app/api/routes/sys_config_admin.py` |
| 动态配置方案文档 | `docs/2026-09-16-动态配置lite与API-Key多Key化方案.md` |
| 管理员依赖 | `backend/app/api/deps.py:328` `require_admin_user` |
| 模型注册表 | `backend/infra/llm/models.py`（`AVAILABLE_MODELS` / `PROVIDERS` / `PROVIDER_API_KEY_ENV`） |
| 模型工厂 | `backend/infra/llm/factory.py:53,65-113` |
| 模型配置读取 | `backend/config/llm.py` |
| 启动校验 | `backend/config/startup.py:241-260` |
| 加密先例 | `backend/competitor/crypto.py` |
| 价格治理状态机 | `backend/infra/llm/price_governance.py`、迁移 `0016_price_governance.py` |
| 价格读取与 cost | `backend/infra/llm/pricing.py`、`models.py:129-225` |
| 管理端导航 | `frontend-admin/src/components/layout/navConfig.tsx` |
| 现有切换器 | `frontend-admin/src/components/agent/LLMSwitcher.tsx` |
| 敏感端点前端 API 写法 | `frontend-admin/src/api/securityOps.ts:96-102` |

---

# 附录 A：P0 实施记录（2026-09-19，commit `41a5df9`）

P0 已落地。以下记录与原设计不一致之处，以及实施中发现的新事实 —— **P1 开工前必读**。

## A.1 范围收窄：实际只改了 2 个 config 文件，不是 16 个

§9 原写「把 16 个文件的 `os.getenv` 收敛到 `resolve_model(role)`」。实测后收窄为：

| 分类 | 数量 | 处理 |
|---|---|---|
| 走 `from backend.config.llm import LLM_MODEL`（**导常量**） | 12 | **零改动** —— 常量求值路径改了，它们自动受益 |
| 绕过配置层直接 `os.getenv` | 4 | 其中只有 2 处是模型名（`config/rag.py`）；另 2 处是 provider/URL 枚举，归 P1 |
| 属于 P0 目标但**工作区已被其他会话改动** | 4 | `infra/llm/budget.py`、`proxy.py`、`quota.py`、`rag/chain.py` —— 暂不动，见 A.3 |

实际改动：`config/llm.py`（6 个常量）+ `config/rag.py`（2 个常量）= **8 个模型常量**。
这正是「导常量」设计的好处：收敛成本远低于预估。

## A.2 实施中发现的三个坑（已在代码里用测试锁住）

1. **`resolve_raw` 与 `resolve_effective` 必须分开。**
   `DOC_LLM_MODEL` / `TOOL_SELECTOR_MODEL` / `LLM_FALLBACK_MODEL` 的**空串在
   消费方手里有语义**（如 `if DOC_LLM_MODEL:` 判断是否启用本地 Ollama）。
   若把常量物化成「继承 main」的模型名，「未配置」会变成「配了」，行为改变。
   → legacy 常量一律用 `resolve_name`（字面值）；`resolve_effective` 只给新代码用。

2. **模型角色不能登记进 `sys_config._SWITCHES`。**
   `tests/api/test_sys_config_admin.py::test_get_config_lists_registered_switches`
   断言 `GET /sys/config` 的返回集合**恰好**是那两个守卫开关。塞进去会直接挂测试。
   而且模型名大小写敏感，`_normalize()` 的小写归一也会破坏它。
   → 模型角色注册表放在 `backend/config/model_roles.py`，`sys_config` 只加通用校验能力。

3. **注册表位置受导入链约束。**
   `config/llm.py` 处在几乎所有模块的导入链上。若把注册表放进
   `backend/services/`，而 `config/llm.py` 要 import 它，就会把
   `services/__init__`（可能含 SQLAlchemy）拖进配置导入链。
   更致命的是：**模块级 import `backend.infra.llm.models` 会先执行
   `infra/llm/__init__.py` → `factory` + `proxy` → langchain**，并与
   `proxy.py` 形成循环导入（`proxy` → `config.llm` → 本模块 → `infra.llm` → `proxy`）。
   → 注册表放 `backend/config/model_roles.py`（纯 stdlib，含 `dataclasses`）；
     `infra.llm` 的数据一律**函数内延迟 import**。
   已加两条测试锁住：子进程验证无重依赖、源码级禁止模块级 `infra.llm` 导入。

## A.3 ⚠️ 并发会话状态（P1 开工前必须重新确认）

P0 实施期间实测到另一会话正在活跃（1 小时内改过 `customer_service/maintenance.py`、
`rag/indexing/indexer.py`、`orchestration/graph/runner.py`，并在期间提交了 `89be962`）。

**当前有 4 个 P1 必需文件处于「他人未提交」状态**，改动它们会污染对方的在途工作、
且路径限定提交会把对方改动一起收编：

```
backend/infra/llm/proxy.py       ← 含 5 处 AVAILABLE_MODELS 调用点（§8）
backend/infra/llm/budget.py
backend/infra/llm/quota.py
backend/rag/chain.py
```

另：工作区约有 300 个未提交文件（`M` 200+ / `??` 60+），`.env` **不在 git 跟踪内**。

→ P1 开工前先 `git status` + 确认这 4 个文件已由对方提交或已确认归属。

## A.4 发现的两个既有缺陷（P0 只点名，未修）

### ① `LLM_FALLBACK_MODEL` 指向未注册模型 —— 熔断兜底实际不可用

实测当前 `.env`：`LLM_FALLBACK_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507`，
而 `AVAILABLE_MODELS` 里没有这个模型（可用集：`MiniMax-M3` / `Qwen/Qwen3-32B` /
`Qwen/Qwen3-32B-AWQ` / `Qwen/Qwen3-8B` / `deepseek-v4-flash` / `qwen2.5:3b` /
`qwen3.7-plus` / `qwen3.7-plus@tp`）。

`config/llm.py` 的注释写着「须在 `infra/llm/models.py` 注册」，但**没有任何强制**
—— 配错时熔断开路切备用模型会构建失败，即"以为有兜底，实际没有"。
P0 后启动校验会点名（已在真实 `.env` 下验证）：
```
[Startup 校验] 模型角色 fallback（LLM_FALLBACK_MODEL）的值
'Qwen/Qwen3-30B-A3B-Instruct-2507' 非法：未在 AVAILABLE_MODELS 注册（可用: [...]）
```

> 注意：`.env` 在 P0 实施期间被改动过（原先读到的值是 `Qwen/Qwen3-32B`，后来变成
> 上述值），疑为另一会话在途操作。**未擅自修改** —— 需与对方确认是补注册模型还是换值。

### ② `EMBEDDING_RERANK_PRICING` 缺 SiliconFlow 条目（§6.2 已记，此处重申）

当前 `EMBEDDING_MODEL=BAAI/bge-m3`、`RERANK_MODEL=BAAI/bge-reranker-v2-m3`，
均不在 `EMBEDDING_RERANK_PRICING` 内 → `compute_embedding_cost` 返回 `0.0`，
**embedding/rerank 成本在估算中被计为 0**。P3 合并价格页时按硅基流动账单补录。

## A.5 P0 验收证据

| 项 | 结果 |
|---|---|
| 8 个模型常量 vs 改造前旧表达式 | **逐项一致**（含 `source` 标注） |
| `sys_config` 既有测试 | 13 passed（守卫开关行为零变化） |
| 新增测试 `test_model_roles.py` | 37 passed |
| 新增测试 `test_sys_config_normalize.py` | 14 passed |
| 受影响面（startup_validation / tool_selector / llm_resilience / pdf_ocr / production_auth） | 105 passed, 1 skipped |
| `infra` + `config` 目录 | 51 passed |
| 真实 `.env` 下启动校验 | 正确点名 A.4① 的未注册模型 |
| 配置导入链重依赖 | 子进程验证：无 langchain / torch / SQLAlchemy / transformers |

**未做全量 pytest**（仓库约定：约 20 分钟，且期间不得有并发会话改 `backend/`；
实施期间检测到并发会话活跃，故只跑定向回归）。

## A.6 P1 前置项（按优先级）

1. 确认 A.3 的 4 个文件归属，再动 `proxy.py`。
2. 决策 A.4① 的处理方式（补注册 `Qwen/Qwen3-30B-A3B-Instruct-2507` 还是换值）。
3. `backend/shared/crypto.py` 抽取（`competitor/crypto.py` 行为保持不变）。
4. 迁移编号取 `0017`（当前最新为 `0016_price_governance.py`）。

# 附录 B：用户自建供应商（BYOK）与连通性自测（2026-09-19 追加，历史方案）

需求原文：各大厂商有 apikey 和 url，另有 coding plan 套餐也是自己输 apikey 和 url；
希望用户能自己添加/修改，输入完点测试图标验证能不能用。

## B.0 本附录改了什么前提

§3.2 定「provider 能力矩阵留代码，不进 DB」—— **该结论依然成立**，但它只回答了
「协议能不能改」，没回答「厂商能不能加」。用户要的不是改协议，是**加一家厂商**。

边界勘定：

| 层 | 内容 | 可否由管理端改 | 理由 |
|---|---|---|---|
| **驱动（driver）** | 协议适配（openai / anthropic / ollama）、能力矩阵、请求体形状 | ❌ 代码内置 | 配错会把系统配成不可用（§3.2 原论据，仍有效） |
| **实例（instance）** | base_url、API Key、模型名、计费模式、额外请求头 | ✅ DB + 管理端 | 纯数据，厂商间差异只有这些 |

## B.1 为什么可行：厂商差异不在代码里（已核实）

| 客户端类 | 覆盖家数 | 厂商 |
|---|---|---|
| `ChatOpenAI` | 5 | qwen / qwen_tp / deepseek / siliconflow / vllm |
| `ChatAnthropic` | 1 | minimax |
| `ChatOllama` | 1 | ollama |

7 个 `build_xxx()`（共 556 行）里 5 个是同一段 `ChatOpenAI` 的复制，
差别只有 `base_url` / `api_key` / `extra_body`。

**结论：3 个驱动即可覆盖今天全部 7 家**，也覆盖绝大多数新厂商与 coding plan。
今天「加一家 = 写 59 行 py + 改 2 张常量表 + 重启」，改造后「加一家 = 填 4 个框」。

## B.2 「coding plan」为什么不是新协议

coding plan（订阅套餐）与按量 API 的差别只有三点，**三点都是数据**：

| 差异 | 落到哪个字段 |
|---|---|
| 专用端点（如 `/api/coding/...`） | `base_url` |
| 订阅制计费、不走 token 计价 | `billing = subscription` |
| 模型名固定/受限，且**常不实现 `GET /models`** | `llm_models` + 测试策略 |

⚠️ 第三点直接决定测试按钮怎么设计（见 B.4）—— 这是本附录最容易被做浅的地方。

## B.3 数据模型（迁移 0017）

```
llm_providers                      -- 厂商实例
  id TEXT PK                       -- slug，如 'glm-coding'
  display_name TEXT                -- 「GLM Coding Plan」
  driver TEXT                      -- openai | anthropic | ollama（代码白名单）
  base_url TEXT
  network_scope TEXT               -- public | private（private 需显式勾选 + 审计）
  extra_headers JSONB              -- 键白名单，如 anthropic-version
  billing TEXT                     -- metered | subscription | local
  is_builtin BOOL                  -- 现有 7 家在库中也留行，便于统一展示/停用
  enabled BOOL
  created_by / created_at / updated_at

llm_provider_credentials           -- 与 provider 1:1
  provider_id PK/FK
  key_cipher TEXT                  -- Fernet 密文；必须 TEXT（VARCHAR(128) 装不下）
  key_fingerprint TEXT             -- sha256[:12]，脱敏展示 + 审计用
  key_last4 TEXT
  key_version INT                  -- 轮换计数（缓存失效靠它比对）

llm_models                         -- 用户自建模型（builtin 仍留代码）
  name TEXT PK                     -- 大小写敏感，如 'glm-4.6'
  provider_id FK
  display / capabilities JSONB / context_length / pricing JSONB / enabled / source
```

**`billing` 三态与成本口径（2026-09-19 已拍板：订阅制显示「订阅制·不计 token」）**

| billing | 成本估算 | 预算阻断 | Token 用量 | 现有归属 |
|---|---|---|---|---|
| `metered` | 按价格表算 USD | 计入 | 记录 | qwen / deepseek / minimax / siliconflow |
| `subscription` | 恒 0，但**显示为「订阅制·不计 token」，不是「免费」** | **不计入** | **仍记录**（容量规划用） | **`qwen_tp`**、新增 coding plan |
| `local` | 恒 0 | 不计入 | 仍记录 | ollama / vllm |

⚠️ **新发现：`qwen_tp` 今天已经是事实上的订阅制，却被当成「metered + 单价 0」处理**
（`models.py:73-74` 注释已写「模型包按购买量计费，不走 token 计价」）。
语义混淆的后果是**成本报表无法区分「真的没花钱」与「价格没录」** —— 这与
`EMBEDDING_RERANK_PRICING` 缺 SiliconFlow 条目（§6.2 / A.4②）被漏掉是同一类问题。
补 `billing` 字段后，`qwen_tp` 应改标 `subscription`。

**落地影响（跨模块，须与 P1a 一起做）**：
- `compute_cost_usd(model_name, ...)`（`models.py:170`）目前只按模型名查价格表，
  **拿不到 billing** → 签名需带上 provider 或先查 billing，否则无法区分「订阅制」与「免费」
- `budget.py` / `quota.py` 的成本累加需按 billing 跳过 `subscription` / `local`
- `observability/tokens` 与 `llm_usage_attribution` 的成本列要能显示「订阅制」

**关键取舍：builtin 也写一行进 DB（`is_builtin=true`），但解析链仍以代码层兜底。**
不要做「空库启动 = 没有模型」—— DB 抖动时问答会直接不可用。这与 `sys_config` 的
env 兜底是同构的，沿用既有模式。

**新增一条不变式**（P0 没有的）：`resolve_credentials(provider)` 必须是热路径零 IO，
凭据随现有 15s 刷新循环进内存缓存。**密钥不得每次调用都解密**（Fernet 是纯 Python，
且每次要读 DB），也不能让 DB 抖动拖慢问答。

## B.4 连通性测试：分级探测

不能只做一次 `GET /models` —— 大量 coding plan 与中转站点不实现该端点。
四级探测，**每级失败含义不同**：

| 级别 | 动作 | 验证 | 失败含义 | 超时 |
|---|---|---|---|---|
| L0 | `url_guard` + DNS + TCP/TLS | URL 拼写、网络、证书 | URL 写错 / 不可达 | 5s |
| L1 | `GET {base}/models` | Key 是否被接受 + **响应体是否为 OpenAI 形状** | 404 → 可能少了 `/v1`（给拼写建议）；**200 但非 OpenAI 形状 → 降级**（见 B.4.1） | 8s |
| L2 | 最小 chat 调用（`max_tokens=16`，prompt 固定） | 模型名在该 Key 下是否可用 | **401/404 分流**：404 且 body 为空 → **地址错**；带 body 的 404 → **模型名错**；401 → Key 错（见 B.4.2） | 20s |
| L3 | 试 `stream=true` 观察是否回传 usage | `stream_usage` 支持性 | 决定是否降级 | — |

**硬约束（逐条都有原因）**：

1. **L1 失败不判死，降级到 L2。** 否则用户会遇到「测试不通过但其实能用」，
   测试按钮从此没人信。
2. **L2 必须复用真实构建路径** —— 用 `build_*` 出来的实例 `invoke()` 一次，
   而不是另写一套 httpx。否则又是「测试通过、线上不通」（真实链路还带限流/
   韧性链/`stream_usage`）。
3. **探测调用必须排除在用量与预算统计之外**（打 `probe` 标记）。否则每次点测试
   都在烧预算，且污染 `/observability/tokens` 与 `llm_usage_attribution`。
4. **返回原文摘要（截断 200 字）。** 本仓库反复踩「真实原因被埋在 N 层语义错误
   之下」（`config/llm.py:303`），探测结果是排障第一现场。
5. **允许草稿态测试**（未保存即可测），否则「填完保存了才知道不能用」。
   代价是未落库的 URL 也会被探测 → 必须配合 B.6 的限制。
6. **UI 只承诺「厂商连通性」，不承诺「业务可用」。** 业务链还要过限流 / 预算 /
   工具绑定，说「可用」是过度承诺。

### B.4.1 L1 形状嗅探：200 不等于「这是 OpenAI 兼容基址」（2026-09-21 补充）

**触发场景（实测）**：用户在「阿里云百炼 · 北京 · Token Plan」新增模型，L0 / L1 通过，
L2 报 `最小调用失败：OpenAIModelNotFoundError: Error code: 404`。

根因**与模型名无关**：他把厂商**原生协议前缀**当成了 OpenAI 兼容基址。

| 请求（假 Key 探测） | 结果 |
|---|---|
| `GET  https://maas.qianwenaiapi.com/api/v1/models` | **401** —— 原生路由也存在，故 L1「通过」 |
| `POST https://maas.qianwenaiapi.com/api/v1/chat/completions` | **404，body 为空** ← 该前缀下没这条路由 |
| `POST https://maas.qianwenaiapi.com/compatible-mode/v1/chat/completions` | **401** ← 路由存在，先鉴权 |

`404` 发生在鉴权**之前**且 body 为空 = 网关没有这条路由 → **换任何模型名都无效**。

**本次新增的通用判别法**：`401` = 路由存在（网关先鉴权）；`404` = 路由不存在。
拿一个**假 Key** 打一发即可分辨，无需真凭据。

> 附：`qianwenaiapi.com` 看着像野域名，但证书 Subject 为
> `O=Alibaba (China) Technology Co., Ltd.`、SAN 覆盖 `*.cn-beijing.maas.qianwenaiapi.com`
> —— 是阿里云 MaaS 的正式域名，与 `aliyuncs.com` 同族。差别只在路径前缀。
> 另：全仓 grep `qianwenaiapi` **零命中**，说明该地址是手工填写，不是预置带出的。

**L1 的假绿灯**：百炼原生 `/api/v1/models` 同样返回 **200**，body 形状是
`{"code":null,"message":null,"success":true,"output":{"total":507,…}}` —— 这**不是** OpenAI
形状（OpenAI 的 `/models` 是 `{"object":"list","data":[…]}`）。而 L1 原本只探
「`GET {base}/models` 通不通」，于是给出了通过。

**修法**：新增 `_models_body_is_openai_shaped(body) -> bool | None`

| 判定 | 条件 | 动作 |
|---|---|---|
| `True` | `data` 是 list | 通过（原行为） |
| `False` | 无 `data`，但命中厂商原生信封特征（键含 `output` / `success` / `code`） | **降级** + 提示「疑似该厂商原生协议端点；OpenAI 协议对话会 404 —— base_url 可能需要补 `/compatible-mode` 或 `/v1`」 |
| `None` | JSON 解析失败 / 不是 dict / 形状不认识 | 通过（**保持宽容**，不降级） |

设计取向：**只降级不判死**（硬约束 1 不变）—— 形状嗅探可能误伤，故 `None` 一律放行；
只有明确命中「原生信封」才降级，且降级文案直接给出**改地址的方向**，
而不是让用户在错误的地址上反复试模型名。

### B.4.2 L2 归因：先判「地址错」，再判「模型名错」（2026-09-21 补充）

原实现只有「模型名错 / Key 错」二分，所以上面那类**地址错**要么被误归到「模型名错」，
要么被关键字表漏掉、落到兜底文案「最小调用失败」——**最没用的那一句**。

**两个把人带偏的机制**：

1. **SDK 异常名撒谎**：`langchain_openai` 把**任何**上游 404 一律重包为
   `OpenAIModelNotFoundError`（`class OpenAIModelNotFoundError(openai.NotFoundError, ModelNotFoundError)`）。
   类名里的 `ModelNotFound` **只代表「上游 404」**，不代表模型名错。
2. **关键字表漏 CamelCase**：原表只有 `"model not found"` / `"model_not_found"`，
   匹配不上异常类名拼成的 `openaaimodelnotfounderror`（无空格、无下划线）→ 掉兜底分支。

**新归因顺序（顺序即语义）**：

| 序 | 条件 | 归因文案 |
|---|---|---|
| 1 | `status_code == 404` 且原文中**不含 `{`** | **端点没有该对话路由（404 且响应体为空）：base_url 很可能不是 OpenAI 兼容基址 —— 检查是否漏了 `/v1` 或 `/compatible-mode/v1`** |
| 2 | 命中模型关键字（原表 + 新增 `modelnotfound`） | 模型名错误或该 Key 无权访问该模型 |
| 3 | 命中 Key 关键字 | Key 无效或无权访问 |
| 4 | 其余 `404`（**带 body**） | 模型名错误或该 Key 无权访问该模型 |

**为什么用「body 是否为空」区分**：上游真报「模型不存在」时会回**结构化 JSON**
（含 error code / message）；而路径不存在常是网关层的**空 body** 404。
这是当前唯一低成本可用的信号。

**验证（真实端点三组对照）**：

| 填的 base_url | 归因结果 |
|---|---|
| `…/api/v1`（用户填的） | 「端点没有该对话路由（404 且响应体为空）… 检查是否漏了 `/v1` 或 `/compatible-mode/v1`」 |
| `…/compatible-mode/v1` | 「Key 无效或无权访问」（401，阿里云标准错误体） |
| 官方 Token Plan 地址 | 同上 |

换地址后归因立刻从「地址错」跳到「Key 错」—— 判别**有效，非巧合**。

**对硬约束 4 的强化**：探测结果是排障第一现场，故归因文案**必须带上下一步动作**
（改地址 / 换模型名 / 换 Key），而不是只贴异常原文。

## B.5 必须一并改的 10 处硬冲突

| # | 冲突 | 证据 | 不修的后果 |
|---|---|---|---|
| 1 | **provider 客户端在 import 时把 KEY/BASE_URL 绑死** | `providers/qwen.py:9-16,43-44`；`siliconflow.py:11-18,44-45`；`deepseek.py:9-16,34-35`（`from backend.config import ...` 是**值拷贝**，`config.llm` 只求值一次） | 免重启 / 多实例 / 运行时轮换**全部不成立**。UI 做好了也是重启才生效 |
| 2 | **`_instance_cache` 无失效机制** | `factory.py:126-127`；`proxy.py:489-497,512-514` 还直接读私有字段绕过工厂方法 | 「测试通过、线上仍用旧 key」，**且不报任何错** —— 最难查的一类 |
| 3 | **`AVAILABLE_MODELS` 是模块级常量，4 处硬引用** | `proxy.py:37,85-88,206,882-883`；`factory.py:37,71,75,98,158`；`routes/llm.py:14,60`；`startup.py:245-253`。注意 `proxy.py:87` 对未注册模型是 **warning + 静默清空覆盖** | 用户加了模型，却「选了不生效」且无错误提示 |
| 4 | **`_get_provider` 靠模型名猜 provider，兜底 `return "ollama"`** | `factory.py:156-168`；`proxy.py:205-208` | 自建模型名（`glm-4.6` / `kimi-k2`）被判成 **ollama** → cloud 模式直接拒绝或走错端点 |
| 5 | **`set_current` 密钥校验是 8 个硬编码 if** | `factory.py:81-92` | 自建 provider 不在其中 → 校验被**静默跳过**，或切过去后调用时才 401 |
| 6 | **`LLMSwitcher` 无权限门禁** | `navConfig.tsx:74`（组 `minRole: 'editor'`）+ `ComposerToolbar.tsx:102` | editor 能切全局模型；一旦切换变成写 DB 生效，**editor 就能改线上模型** |
| 7 | **`url_guard` 与自托管直接冲突** | `url_guard.py:78-88` 拦 loopback/私网；而 `VLLM_API_BASE=http://localhost:8000/v1`（`config/llm.py:288`）、Ollama `localhost:11434` | 无脑套防护会把自托管场景打死；但不套防护就是 SSRF 面 |
| 8 | **密文长度** | §1.1 冲突 4 已定（`VARCHAR(128)` 装不下 Fernet） | 新表 `key_cipher` 必须 `TEXT`；审计**只落指纹**，历史表不得出现明文/密文 |
| 9 | **embedding/rerank 是第二条凭据链路** | `rag/embedding_singleton.py:47-65` 直接用 `EMBEDDING_API_KEY`/`EMBEDDING_API_BASE`；`config/llm.py:41` 注明与索引强绑定；`RERANK_API_FORMAT=dashscope`（`llm.py:120`）是另一套协议开关 | chat 侧能改而 embedding/rerank 还得回 `.env` 改 → 用户必问「为什么这里能改那里不能」 |
| 10 | **`.env` 密钥同源关系仍在暗处** | `DASHSCOPE_API_KEY`/`QWEN_API_KEY` 同值、`EMBEDDING_API_KEY`/`RERANK_API_KEY`/`SILICONFLOW_API_KEY` 同源（`config/llm.py:85-95,293`） | 用户改了 A 而 B 跟着变，且无从察觉。必须与 §4.5 凭据去重一起做 |

## B.6 权限、审计与新增的攻击面

自建供应商 = 管理端获得「让服务向任意 URL 发请求」的能力，这是**真实的 SSRF 面**。

| 动作 | 权限 | 审计内容 |
|---|---|---|
| 查看供应商 / 模型 | admin | — |
| 新增 / 改 base_url | admin | who / old / new（URL 可全记） |
| 写入 / 轮换密钥 | admin | **只记指纹**，不记明文与密文 |
| 点测试 | admin | who / 目标 URL / 分级结论 / 耗时，**不记 key** |
| 使用某 provider 跑会话 | editor+ | 走既有 `llm_usage_attribution` |

**测试端点必须加的四道限制**（否则它就是一个「任意 URL 探测代理」）：

1. admin only（复用现有 `require_admin_user`）
2. 目标 URL 过 `url_guard`；私网需**显式放行且放行项在管理端可见**
3. **探测报文固定**，不允许用户自定义 body；额外 header 走键白名单
4. 限流（同 admin 每分钟 N 次），防止被用来扫内网

**私网放行机制（2026-09-19 已拍板：允许 + 显式标注 + 审计）**

**不做全局环境变量白名单，改为「按实例显式勾选」。** 理由：全局白名单一开就是全站放行，
无法回答「谁允许的、为哪个厂商开的」；而按实例勾选天然可审计。

- `llm_providers.network_scope TEXT`：`public`（默认）| `private`
- 默认 `public` → 目标 URL 照常走 `url_guard` 全量检查（含私网/环回/元数据拦截）
- 管理员在新增·编辑抽屉里**显式勾选**「这是内网服务」→ 该实例才跳过 IP 段检查
- ⚠️ 跳过检查的判定**只能来自这个勾选，不能来自「解析出来是私网就自动放行」**。
  否则公网域名解析到内网 IP 的 DNS rebinding 就绕过去了
- scheme 白名单、控制字符拦截**勾选后仍然生效**（只放开网段，不放开协议）
- 勾选状态在列表以徽章显示；`created_by` / 修改 `network_scope` 的动作进审计
- 不建议复用 `SSRF_TRUSTED_DOMAINS`：它是**域名后缀**语义，装不下 `localhost:8000`
  这类 host:port，硬塞会污染竞品抓取的既有行为

## B.7 前端

`/settings/models` 的 tab② 由「只读 + 轮换」升级为 **CRUD + 测试**：

- 列表列：显示名 / 驱动 / base_url / 密钥状态（`已配置 ····a1b2 · 指纹 3f9c1d · 3 天前轮换`）
  / 模型数 / 最近一次连通性结论 + 时间
- 新增·编辑抽屉：显示名 · 驱动（下拉，来自代码）· base_url · API Key（粘贴，保存后不可读回）
  · 模型名（多行）· 计费模式 · 单价（按量时）
- **测试图标**：放在 base_url / API Key 输入框旁，草稿态可点；结果内联展开为
  分级清单（L0/L1/L2/L3 各自 ✅/❌ + 原文摘要），不是单个布尔
- 保存时若未测或未通过 → **允许保存但标红「未验证」**，并在「体检」tab 点名。
  不硬拦：用户可能先配后开网络白名单。
- 保存成功回执必须明确：「已生效，当前会话下次调用即使用新凭据」—— 直接对冲 B.5#2 的困惑

交互上两条必须做到，否则用户搞不清：
1. **区分「Key 错」与「模型名错」** —— 尤其中转 / coding plan 站点，这两种最易混。
2. **base_url 归一化提示**：去尾斜杠、缺 `/v1` 时给建议（由 L1 的 404 触发）。

## B.8 分期（接 §9，原 P1 拆为两段）

- **P1a — 凭据与注册表通道（无 UI，可独立验收，零行为变化）**
  - `shared/crypto.py` 抽取（`competitor/crypto.py` 行为不变）
  - 三张表 + 迁移 0017（含 `network_scope` / `billing` 字段，DB 空表时不影响行为）
  - 破 B.5#1（provider 改传参式）、#2（`invalidate()` + `key_version` 比对）、
    #3（`get_available_models()` 统一入口 + fail-closed 回退代码层）、
    #4（去名称启发式，猜不出显式报错）、#5（收敛 `resolve_credentials`）
  - 验收：**全部仍读 env、DB 空表**，生效快照与 `41a5df9` 逐项一致 + 定向回归
  - ⚠️ **文件归属依赖**：`billing` 传播到 `compute_cost_usd` 需要改 `proxy.py` /
    `budget.py` / `quota.py`，这三个文件当前是「他人未提交」状态（2026-09-19 13:34 核实）。
    可拆为 **P1a-1**（provider / factory / models / crypto / 迁移，全在净文件与新建文件）
    与 **P1a-2**（billing 传播，等上述三文件落定）。数字上零变化：`qwen_tp` 现在
    无论算 metered-0 还是 subscription 都是 0 成本，差别只在展示口径。
- **P1b — 探测服务**：分级探测 + `POST /sys/providers/{id}/verify`（支持草稿态）+
  `network_scope` 私网放行 + 探测流量打标排除统计
- **P2 — 管理端**：tab② CRUD + 测试图标；builtin 也可在页面停用；
  **`LLMSwitcher` 改会话级 + `chat.ts` 加 `model` 字段 + `/llm/switch` 加 admin 门禁**（B.9②）
- **P3 — 收尾**：embedding/rerank 凭据与绑定（含重建索引二次确认）、价格合并、
  `.env` 瘦身、密钥去重与轮换（含 §P1 记的第 312 行明文 Key）。其中专项凭据与绑定已在
  2026-09-20 通过迁移 `0019` 和独立适配器提前落地；价格合并与 `.env` 瘦身仍是后续工作。

**P1a 与 P1b 之间是硬闸门**：P1a 完成前做 UI，会得到一个「配了不生效」的页面。

## B.9 决策记录（2026-09-19 已拍板）

### ① 自建 provider 允许指向私网 → **允许 + 显式标注 + 审计**

落地见 B.6：按实例 `network_scope` 勾选，不做全局白名单。默认 `public`，未勾选的私网目标
一律照拦（含 DNS rebinding 情形）。

### ② editor 切模型 → **回收为会话级临时切换，全局默认只 admin 可改**

**核实结论：会话级通道已经完整存在，后端零新增。** 链路是现成的：

```
ChatRequest.model (chat.py:105,182)
  → RequestContext(model=...) (orchestration/request_context.py:55)
  → set_request_model() (core/request_context.py:173)
  → _request_model_var contextvar (proxy.py:67)
  → _resolve_active_llm() 优先取它 (proxy.py:487)
```

所以改动只在三处：

| 位置 | 改动 |
|---|---|
| `frontend-admin/src/api/chat.ts` | 加可选 `model` 字段（今天**完全没有传**） |
| `components/agent/LLMSwitcher.tsx:88` | 从调 `switchLLM()`（全局）改为写会话级状态，由 chat 请求带出 |
| `POST /llm/switch`（`routes/llm.py:74`） | 加 `require_admin_user`；保留纯内存语义作为 admin debug 通道 |

`set_current()` 的内存语义**原样保留**（它不再是管理端入口，只服务 admin 调试），
这与 §3.4 的分歧判断一致。附带收益：editor 的临时切换天然是「一次性」的，
不会出现「偷偷改了线上模型」。

⚠️ 与 B.5#3 的耦合：`_request_model_var` 在 `proxy.py:85-88` 对未注册模型是
**warning + 静默清空** → 自建模型必须先落注册表，否则会话级切换会「选了没反应且无提示」。

**2026-09-19 补充决策（用户拍板「需要后端校验」）：把静默改为 API 边界 fail-fast 400。**

需要纠正一个前提：会话级 model 的**校验早就存在**（`proxy.py:72-105` 已做注册表 /
Ollama 启用 / provider Key 三级检查），缺的是**拒绝**而非校验 —— 三条全部落到
`_request_model_var.set("")`，即「非法输入被静默吞掉，用户以为在用 A 实际在用全局默认」，
与 A.4① 的 `LLM_FALLBACK_MODEL` 未注册是同源病灶。

契约：**`POST /chat` 在 api 层校验收口（非法 → 400），`set_request_model` 自身保持宽容不变。**
理由：① 它是**上下文绑定**而非输入校验，还被非 HTTP 路径调用（评测生成、脚本），
拿不到请求上下文报错；② `tests/test_llm_bind_tools.py:117-144` 有 **4 例锁定其静默语义**，
改成抛错会直接打破并波及非 HTTP 调用方；③ 规则抽 `validate_override_model()`
供 proxy 与 api 层共用，避免两套规则漂移。
**不做模型级 ACL**（可切换的都是同一批已注册模型；成本由既有 `budget`/`quota` 兜住）。
完整契约见 `docs/model-config-admin-ui-design.md` §13.1。

### ③ 订阅制 provider → **显示「订阅制·不计 token」**

落地见 B.3 的 billing 三态表。附带把 `qwen_tp` 从「metered + 单价 0」改标 `subscription`。

---

# 附录 C：P1a-1 实施记录（2026-09-19）

**范围**：provider 传参式、凭据解析唯一入口、可用模型统一入口、实例缓存失效、
密钥通道原语抽取、迁移 0017 与 DAO。验收口径是「DB 空表 + 全读 env，行为与
`41a5df9` 逐项一致」。

## C.1 交付物

| 文件 | 状态 | 说明 |
|---|---|---|
| `backend/shared/crypto.py` | 新增 | Fernet 原语：按 env 名分桶缓存、`invalidate`、**fail-loud** 与优雅降级双语义、`fingerprint`/`last4`/`mask_secret` |
| `backend/competitor/crypto.py` | 改 | 委托 shared；保留 `_fernet` 模块变量（既有测试用它重置缓存） |
| `backend/infra/llm/models.py` | 改 | `get_available_models()` / `get_model_entry()` / `is_known_model()` / `resolve_provider()` / `ProviderResolutionError` / `set_dynamic_models()`；`PROVIDERS` 增 `driver`+`billing`；`get_provider_billing` / `get_model_billing` / `get_provider_driver` |
| `backend/infra/llm/credentials.py` | 新增 | `ProviderCredentials` + `resolve_credentials()`（调用时读 config 模块属性，非值拷贝）+ `set_db_credentials()` / `credentials_version()` / `missing_key_message()` / `check_provider_usable()` / `snapshot()` |
| `backend/infra/llm/providers/*.py` | 改 7 个 | `build_xxx(model_name, credentials=None)`、`get_xxx_balance(credentials=None)`；空字段回落 config |
| `backend/infra/llm/factory.py` | 改 | 走统一入口；`_get_provider` 委托 `resolve_provider`；`_build_instance` 传凭据；`set_current` 的 8 个硬编码 if 收敛；新增 `available_models()` / `invalidate()` / `key_version()` |
| `backend/infra/llm/registry_store.py` | 新增 | DB 三表读取 → `set_dynamic_models` / `set_db_credentials`；fail-open；凭据解密 fail-loud（单条失败只跳过该 provider） |
| `backend/sql/alembic/memory/versions/0017_llm_providers.py` | 新增 | 三表 + 索引（⚠️ 见 C.4 提交依赖） |
| `backend/tests/infra/{test_shared_crypto,test_llm_credentials,test_llm_registry_models,test_llm_provider_passthrough}.py` | 新增 | 56 例 |
| `backend/tests/infra/test_llm_siliconflow_provider.py` | 改 1 处 | monkeypatch 注入点 factory → `config.llm`（见 C.3②） |

## C.2 与设计的偏差（1 处，理由已核实）

**B.8 写的「#4 去名称启发式，猜不出显式报错」→ 只做了一半。**

实测：`_get_provider` 最后那句 `return "ollama"` **不是无意的 bug，而是唯一的本地模型
表达方式** —— Ollama 模型名不可穷举（`llama3` / `qwen2.5:7b` / 任意 pull 下来的名字），
而评测生成此前依赖 `OLLAMA_MODEL=qwen2.5:3b` 这类本地默认值。现在 `eval_gen`
要求显式绑定已登记模型，未配置或不可用时由管理端展示原因并跳过评测生成，避免
把云端模型名误送到 Ollama。

故落地为：

- `resolve_provider(name)` 宽松模式 —— 保持历史行为，但**判不出时记一次 warning**
  （历史实现完全静默，静默正是「自建模型被误判成 ollama 且无从发现」的成因）
- `resolve_provider(name, strict=True)` —— 判不出抛 `ProviderResolutionError`，
  供管理端校验（P2）与探测（P1b）使用
- 自建模型一旦登记进 DB 覆盖层，启发式自然不再触发（`get_model_entry` 先命中）

真正的 fail-closed 切换放到 P1b（那时自建模型有显式 `provider` 归属）。

## C.3 实施中发现的四件事

**① `proxy.py` 有第二条独立的构建路径 —— 运行时凭据链路在 P1a-1 无法打通。**
`proxy.py:204-231` 自带 `_get_provider_for()` 并**直接调 `build_xxx(model_name)`**，
不经过 factory。本轮的传参式改动对它是**向后兼容**的（`credentials=None` → 读 config），
所以不破坏现状；但「管理端改了密钥，会话立刻用新 Key」这条链路必须等 P1a-2
（`proxy.py` 落定后）才能闭合。**这也是 P1a → P1b 之间那道硬闸门的具体内容。**

**② 收敛 `set_current` 会破坏一个既有测试的注入点。**
`test_llm_siliconflow_provider.py::test_set_current_rejects_when_siliconflow_key_missing`
用 `monkeypatch.setattr(factory_module, "SILICONFLOW_API_KEY", "")` 注入 ——
依赖 factory 模块持有该常量。凭据收敛后 factory 不再持有它，故把注入点改为
`backend.config.llm.SILICONFLOW_API_KEY`（`resolve_credentials` 在**调用时**
读 config 模块属性，所以该注入有效）。测试意图与断言未变。

**③ 顺带发现：`proxy.py` 与 factory 的密钥判定口径不一致。**
`proxy.py` 的 `set_request_model` 用 `os.getenv(key_env)`（运行时读环境变量），
而 factory 改造前读 `config.llm` 的**导入时常量**。二者在「只改 env 不重启」时
结论可能不同。本轮的 `resolve_credentials` 统一为「读 config 模块属性」，
与 factory 历史语义等价；proxy 侧待 P1a-2 一并统一。

**④ 顺带发现：`MINIMAX_API_MASE` 与 provider 硬编码同值不同源。**
`providers/minimax.py` 用的是**字面量** `https://api.minimaxi.com/anthropic`，
而 `config.llm.MINIMAX_API_BASE` 的**代码默认**是 `https://api.minimax.chat/v1`
（OpenAI 兼容端点，另一套协议）；只是当前 `.env` 把它也设成了 Anthropic 端点，
两者恰好同值。**若「顺手」把 provider 改读 config，`.env` 缺失时会静默漂移到
另一套协议。** 本轮保留字面量（改由 `credentials.MINIMAX_ANTHROPIC_URL` 承载）。

## C.4 ⚠️ 提交依赖：0017 不能单独提交

`0014` / `0015` / `0016` 当前是**另一会话的在途工作**（`git status` 为 untracked）。
本迁移 `down_revision = "0016"`，若先于它们提交，`alembic upgrade head` 会报
`Can't locate revision identified by '0016'` —— **迁移链断裂**。
故 0017 须与 0016 一并（或在其落地之后）提交。

同理，`proxy.py` / `budget.py` / `quota.py` / `api/router.py` / `test_llm_budget.py` /
`test_llm_quota.py` 仍是他人未提交状态，本轮**未触碰**。

## C.5 验收证据

| 项 | 结果 |
|---|---|
| 8 个模型常量 vs 改造前（P0 快照） | 逐项一致（未改动 `config/llm.py`） |
| `resolve_provider` vs 旧 `_get_provider` | 25 个用例**逐位一致**（含启发式与 ollama 兜底） |
| `resolve_credentials` vs providers 实际使用的常量 | 7 个 provider 逐项一致（含 minimax 字面量端点） |
| provider 传参式（mock 客户端 kwargs） | 不传 = 改造前逐位一致；传了 = 覆盖生效；空字段 = 回落 |
| 可用性判定 vs 旧 8 个 if 链 | 不可用集合一致；文案逐字保留（含 `（sk-sp- 模型包 Key）` / `（deploy.sh 会生成）`） |
| `registry_store` 在表不存在时 | `loaded=False`、不清空动态层、凭据回落 env（日志实测 `UndefinedTable`） |
| competitor crypto 行为 | `TestCrypto` 7 passed（含 `_fernet` 重置约定） |
| 新增测试 | `tests/infra/` 74 passed |
| 配置导入链重依赖 | 无 langchain / torch / sqlalchemy（`models.py` 顶层检查） |

**未跑全量 pytest**：仓库约定约 20 分钟且期间不得有并发会话改 `backend/`，
而本轮实施期间实测另一会话活跃（pytest 缓存 13:32 被写、容器 46 分钟前重建、
`0014~0016` 新增 untracked）。按约定只跑定向回归。

## C.6 P1a-2 开工前的前置

1. `proxy.py` / `budget.py` / `quota.py` / `router.py` 落定 —— 这是运行时凭据链路的最后一段
2. `0016` 落地后提交 0017
3. 决定 `billing` 传播（`compute_cost_usd` 签名需带 provider 或先查 billing）—— 数字上零变化，
   但会碰 `budget.py` / `quota.py`

## B.10 当前实施补充：embedding / rerank 专项适配器（2026-09-20）

本节覆盖附录 B 原方案没有展开的第二条协议链路。专项模型不是通用 Chat 模型，不能复用
`GET /models` 或 Chat 最小请求作为唯一测试：Anthropic Messages、OpenAI Chat、DashScope
原生 Rerank 的 URL、请求体和成功响应字段均不同。

### B.10.1 边界与数据

- `llm_providers` 继续保存供应商实例；`driver=specialized` 只表示它由专项适配器使用，
  不进入通用 Chat 供应商列表。
- `llm_specialized_model_bindings`（迁移 `0019`）按 `embedding` / `rerank` 一角色一行保存
  `provider_id`、`adapter`、`model_name`、`base_url`、`options` 和最近探测状态。
- API Key 仍只写入 `llm_provider_credentials.key_cipher`，由 `SECRETS_ENCRYPTION_KEY`
  保护；接口只返回指纹和末四位。缺少主密钥时拒绝写入，禁止明文降级。

### B.10.2 适配器与测试契约

当前白名单：`dashscope_embedding`、`openai_embedding`、`dashscope_rerank`、`jina_rerank`。
每个适配器负责拼接端点、注入 Bearer Key、构造固定最小探测请求和判定响应结构；前端只提交
适配器名称与模型名，不拼接厂商协议。

测试保存流程：

1. 对每个已填写角色执行一次真实最小请求，记录 HTTP 状态、摘要、截断安全错误和 `elapsedMs`。
2. 任一角色失败则返回失败明细且不落库；全部通过才写入供应商、加密凭据、角色绑定和审计记录。
3. RAG 独立服务启动前加载注册表，并每 15 秒刷新；已创建的 embedding wrapper / rerank wrapper
   在下一次调用时检测配置签名，轮换 Key 或模型后切换出站客户端。
4. embedding 模型切换只解决“新请求使用哪个模型”，不改变旧向量的语义空间；仍必须全量重建
   索引，并在体检页确认索引状态。

### B.10.3 已验证事实

本地管理端真实配置并测试 `qwen3.7-text-embedding` 与 `qwen3.7-text-rerank` 均通过；RAG
运行时实际返回 1024 维向量和有效重排分数。测试耗时在页面展示，Key 只显示掩码。生产发布时
除迁移 `0019` 外，还必须把 `SECRETS_ENCRYPTION_KEY` 纳入持久化密钥托管与备份。

## B.11 当前实施补充：预置端点目录与「三选一」新增流程（2026-09-20）

B.7 把新增抽屉写成「显示名 · 驱动 · base_url · API Key · 模型名 · 计费模式」，但**驱动下拉从未
落地**（`newDraft()` 硬编码 `driver: 'openai'`），于是大量 Anthropic 兼容端点根本登记不进来；
base_url 也全靠手敲，而同一家厂商在不同计费计划下的端点**完全不同**（火山引擎按量
`/api/v3` vs Coding Plan `/api/coding/v3`），填错会产生额外费用。本次补齐这条链路。

### B.11.1 关键决策：三选一计划只映射到两个 billing

交互上先做「Token Plan / Coding Plan / 按量付费」三选一，但它**不是**三个新的 billing 值：

| 计划 | billing | 依据 |
|---|---|---|
| Token Plan | `subscription` | B.2「三点都是数据」+ B.3 的 `subscription` 归属已含 `qwen_tp` |
| Coding Plan | `subscription` | 同上 |
| 按量付费 | `metered` | 原语义不变 |

⚠️ **不要为此新增第 4 个 billing 值。** 那要连带改 DB CHECK 约束、`ModelConfigService` 三处
白名单、`get_provider_billing`、以及 `compute_cost_usd` / `budget.py` / `quota.py`（B.8 已注明
这三个文件曾是「他人未提交」状态）。计划类型的区分靠 `display_name` 与 provider id 足够。

### B.11.2 目录落点：代码内置 + 只读下发

- `backend/infra/llm/provider_presets.py` —— 49 条预置（Token Plan 16 / Coding Plan 8 /
  按量付费 25），只含静态数据与纯函数，无 IO、不读 env、**不参与任何解析链**。
  （2026-09-21 按「视觉/OCR 模型端点表」补录 6 条：MiniMax 国内 Anthropic 端点、OpenAI、
  Anthropic、Google Gemini（收录的是 OpenAI 兼容入口 `/v1beta/openai/`，非原生协议）、
  硅基流动、百度千帆国际；同时修正 MiniMax 国内域名 `api.minimax.cn` → `api.minimaxi.com`。）
- `GET /api/sys/providers/presets` —— admin only（与 B.6 的 SSRF 边界同源），返回
  `{plans, items, actor}`，条目为 camelCase，字段受白名单约束（**不得出现名为 `apiKey` 的字段**，
  `apiKeyHint` 只是「Key 长什么样」的说明）。
- 放代码而非 DB，依据 B.0 边界：**驱动与厂商能力矩阵留代码，不进 DB**。

### B.11.3 编辑态靠反查回填，不新增字段

库里没有 `preset_id`。编辑时按 `(driver, base_url)` 归一化后反查（去尾斜杠、scheme/host 小写，
**但不折叠路径语义** —— `/api/v3` 与 `/api/coding/v3` 必须仍然不同），并用实例自身的 `billing`
消歧：智谱 `open.bigmodel.cn/api/anthropic` 在 Coding Plan 与按量付费下是**同一 URL**，源数据
本身就有这个歧义。反查**不做模糊匹配**，落空即显示「未套用预置」，不把自建/内网地址
误标成某家厂商的官方端点。

### B.11.4 一并修掉的三件事

1. **显示名终于有输入入口。** 此前 `displayName` 只能由 `urlparse(base_url).hostname` 兜底，
   UI 无任何入口。预置选中时会自动填「厂商 · 地域 · 计划」，Anthropic 条目额外标注以区分同厂
   同计划的另一协议（否则 slug 会撞成 `xxx-2`）。
2. **协议下拉落地（B.7 原要求）。** 内置供应商的 driver 由后端锁定，前端同步禁用；
   `ollama` / `specialized` 也不允许被这个下拉悄悄改掉。
3. **删除靠 id 字符串匹配认计划类型的 `providerOptionLabel()`**（原实现用
   `row.id === 'qwen_tp'` / `row.id.includes('coding')` 推断），改为目录驱动。

另外补了两处**前置拦截**，用于替代原先「必然失败的探测 + 无从下手的报错」：

- **已登记模型不可改用途**：`_upsert_model` 对已登记模型一律拒绝改 `model_kind`，而前端
  `needsTest` 会因用途变化先跑探测、失败后直接中断保存 —— 后端那条精确的 409 文案永远展示不出来。
  现在该下拉对已登记模型直接锁定并给出说明。
- **模型名全局占用**：模型名唯一（`llm_models.name` PK），撞名必然 409。编辑态提前点名占用方，
  不再让用户白等一轮厂商往返。
- **`{WorkspaceId}` 占位符**：阿里云百炼按量付费已迁移到业务空间专属域名，照抄模板必然在
  L0 就挂。提交/探测前直接拦下并指名要替换的占位符。

### B.11.5 明确未做

- **探测失败仍然硬拦截保存**（B.7 建议「允许保存但标红未验证」**未采纳**）。原因是后端
  `create_provider` / `add_provider_model` 内部各自再 probe 一次、失败即 422，这是一条独立
  且有意保留的硬约束；要真正放宽必须前后端同时改，属独立变更。
- 未新增 `preset_id` 字段，未改任何解析链、计价链与 DB 结构（本次**零迁移**）。

### B.11.6 降级行为

预置目录接口不可用（后端未部署 / 请求失败）时，抽屉落回**手填 Base URL + 高级设置里选计费口径**，
即改造前的行为，并显式提示「预置厂商目录不可用」。不能因为一个参考数据接口不可用就让
「新增供应商」变成死路。

## B.12 当前实施补充：模型级移除与供应商卡片分组（2026-09-20）

需求原文：`/settings/models?tab=providers` 「只显示已配置的厂商，一个厂商下面平铺它已配置的多个模型，
未配置的厂商不显示；并且可以编辑已保存的模型，优化界面」。

### B.12.1 先说结论：列表口径本就正确，缺的是「编辑」

`GET /sys/providers` 的 `_db_rows()` **只回 DB 里真实存在的供应商**，未配置厂商不会出现；
每个供应商的 `models` 本就是数组（多模型）。所以「只显示已配置 + 一厂商多模型」**无需改动**，
本次真正的缺口是「已保存模型不能编辑」，以及列表仍是六列表格、读不出「谁被角色占用」。

### B.12.2 编辑语义收窄为「移除」（含引用检查）

对**已保存**的模型，管理端提供的动作是**移除**，不是就地改字段。理由是模型名是
`llm_models.name` 主键、用途（`model_kind`）又是 `_upsert_model` 明确拒绝变更的字段
（B.11.4 已锁），「改」实际上只有「删掉再按新名/新用途新增」一条路。因此：

- 新增服务方法 `ModelConfigService.remove_provider_model(provider_id, model_name, operator)`。
- 新增接口 `DELETE /sys/providers/{provider_id}/models`。

### B.12.3 引用红线：三类被引用一律拒绝，不留悬空引用

移除前在同一事务语义内检查，命中任一条即抛 `ModelConfigConflict`（HTTP 409）：

| 引用来源 | 表 | 拒绝文案 |
|---|---|---|
| 角色绑定 | `llm_model_role_bindings` | 列出具体角色，要求先改绑 |
| 专项绑定 | `llm_specialized_model_bindings` | 该模型被专项模型占用 |
| 价格表 | `model_price`（count > 0） | 存在计费记录，先清理价格 |

此外两条边界：

- 模型不在 `llm_models`，但**命中代码层 `AVAILABLE_MODELS`** → 拒绝并说明「代码层内置模型，
  不能从管理端移除」（与前端的 `source='builtin'` 判断同口径）。
- 模型存在但归属的 `provider` 不是当前 `provider_id` → 拒绝并**点名真实归属方**，避免跨厂商误删。

### B.12.4 审计记账：复用 `provider` 类型 + `rollbackable=false`

`llm_config_history.object_type` 的 CHECK **只允许** `role` / `provider` / `provider_credential` /
`provider_network_scope`，没有 `model`。本项**不改 CHECK**（改它需迁移且影响面大），而是：

- `object_type='provider'`，`old_value` 存 JSON `{removedModel, displayName, modelKind}`，
  `new_value=NULL`；
- `rollbackable=false` —— 模型删除**不是可重放的 UPDATE**，回滚需要重新 INSERT（含用途、归属校验），
  把它标成可一键回滚会造成「点了回滚但状态没回去」的假象。**回滚要人工重加**。
- 落库后调用 `refresh_registry()`，让运行中的注册表立刻感知模型消失。

### B.12.5 接口形态：模型名走 query 而非路径段

模型名可含 `/`（如 `Qwen/Qwen3-32B`），放进路径段会被当成分隔符、路由匹配错乱。因此：

```
DELETE /sys/providers/{provider_id}/models?modelName=<urlencoded>
```

并要求 `require_idempotency_key(request)`（与其它写接口一致）。返回
`{providerId, name, display, modelKind}`。

### B.12.6 列表响应新增两个只读装饰字段

`_decorate_models()` 给每个模型条目补：

- `source`：`user`（在 `llm_models` 里）/ `builtin`（仅代码层）；判定键是 `(provider, name)` 二元组，
  **不能只看 name** —— 同一模型名在不同厂商下语义不同。
- `usedByRoles`：从 `RegistrySnapshot.roles`（role→model 映射）反查得出，**不额外查库**。

### B.12.7 前端：卡片分组 + 就地说明为什么不能删

- 六列表格改为**供应商卡片**（`data-testid="provider-card"`）：卡片头放显示名 / 内置·自建 /
  驱动 / 网络与计费 / baseUrl / 密钥状态 / 探测状态 + 动作按钮；卡片体平铺该厂商全部模型，
  每行 `用途徽标 + 模型名 + 内置标记 + 〔移除〕`。
- 用途徽标用缩写（文本 / 向量 / 重排 / 视觉 / 语音），避免挤掉模型名。
- **不能移除的模型按钮就地禁用并写明原因**（`modelRemovalBlockReason()`）：被角色占用 →
  「正被角色 X 使用，需先改绑」；代码层内置 → 「代码层内置模型，不可移除」。
  **不隐藏按钮** —— 藏起来会让用户以为功能缺失；灰掉 + 说明才是可自助的。
- 移除走二次确认弹窗（红色），并在弹窗内提示「若仍被角色或价格表引用，后端会拒绝并说明原因」。

### B.12.8 验收证据

- 后端：`backend/tests/services/test_model_config_remove_model.py`（新增 8 例：成功+记账、
  内置拒绝、不存在、归属不符、角色占用、专项占用、价格占用、空参）+ `test_model_config_write_api.py`
  新增 3 例（幂等键缺失、query 传名断言、409 映射）+ `test_sys_providers_list_api.py` 更新 1 例、
  新增 1 例（`source`/`usedByRoles` 正确性）。
- 前端：`ProvidersTab.test.tsx` 22 例全过（含卡片分组、专项供应商并卡、移除拦截）；
  `tsc --noEmit` 零错；全量 `vitest run` 322 例全过。
- **活服务实测（重建 `app` 容器后）**：`openapi.json` 实测该路径为
  `['delete','get','post']`；缺 `Idempotency-Key` → 400；被角色占用 → 409（点名角色）；
  代码层内置 → 409；归属不符 → 409（点名真实归属方）；不存在 → 404；editor → 403；
  **200 成功路径**用一次性行做真机 E2E（插入 → 200 → 行消失 → 审计
  `object_type=provider` / `rollbackable=false` → 重复调用 404），验证数据已清理。
- 实测事实补充：治理表在 **`agent_memory`** 库（`agent_business` 只有业务表）；
  `llm_models` 的列名是 `provider_id` 而非 `provider`。

### B.12.9 明确未做

- **未提供模型改名 / 改用途**：与 B.11.4 的硬约束冲突，属独立变更。
- **未引入 `object_type='model'`**：不动 CHECK、不加迁移。
- **未做批量移除**：逐个确认，避免一次误删多个被引用模型。
- 未改列表分页 / 搜索（本次只动展示与移除链路）。

## B.13 Base URL 地址助手（2026-09-21，B1 批）

### B.13.1 起因：一个「界面无感」的静默错误

2026-09-20 的真实工单：在「新增供应商」里选完
`阿里云百炼 · 北京 · Token Plan`，Base URL 却填了 `https://maas.qianwenaiapi.com/api/v1`
（该厂商的**原生协议**前缀），随后卡在 L2「最小调用 404」。

排查后确认两件事：

1. **根因与模型名无关**。同一 host 下 `POST /api/v1/chat/completions` 返回
   **404 且响应体为空**，发生在鉴权之前 —— 网关根本没这条路由。正确前缀是
   `/compatible-mode/v1`（B.4.1/B.4.2 已补上探测侧的形状嗅探与归因顺序）。
2. **界面完全不觉得这有问题**。`applyPreset()` 回填 Base URL 后，用户若手改地址，
   `presetId` **仍然保留**：界面继续显示预置的「API Key 格式：sk- 开头」、
   计费口径与显示名，**没有任何「你已偏离预置」的标记**。`findPresetForRow()`
   只在编辑态反查回填时使用，**不参与校验**。于是「显示名写着阿里云百炼·北京·
   Token Plan、地址却是另一套前缀」这个状态，UI 无感。

本批（B1）就是补这个开口。**纯前端、零后端改动、零迁移**。

### B.13.2 硬约束：只做「据实提示」，不做「地址校验」

这是本批最重要的一条设计约束，也是与「顺手加个 URL 格式校验」的分界线：

1. **不拦截、不置灰、不拒绝提交**。自建网关、内网反代、公司统管网关都合法，
   路径可以是任意值。把合法输入判成错误，比漏报更糟。
2. **一切结论从预置目录推导，不硬编码厂商知识**。界面里不出现「阿里云必须用
   `/compatible-mode/v1`」这种写死的判断；只说「**我们收录的 43 条预置里**是
   这些值」。厂商增删预置，提示自动跟着变，不需要改前端。
3. **不替用户拍板**。同一域名常有两三个端点（火山 `/api/v3` 与 `/api/coding/v3`），
   哪个对取决于计费计划 —— 界面把候选**全部列出**让用户点，不猜「正解」。
4. **信息不足时沉默**。域名与路径都对不上、又不像原生前缀时，**不渲染任何东西**。
   宁可漏报。

### B.13.3 五种状态与判定顺序

`diagnoseBaseUrl(draft, presets, plans)` 纯函数，按序判定：

| 序 | 状态 | 触发条件 | 界面 |
|---|---|---|---|
| 1 | `empty` | 地址为空 | 不渲染 |
| 2 | `matched` | 域名+路径+**协议**与某条预置逐字一致，且计划一致 | 绿色一句「地址与预置「X」一致」 |
| 3 | `plan-mismatch` | 命中预置，但那条属于**别的计费计划** | 黄色点名「这个地址是「按量付费」的端点」+ 一键切回本计划端点 |
| 4 | `deviated` | `presetId` 非空但与当前地址不符（先选预置、又手改地址） | 黄色「地址已偏离预置」+ 预置原值 + 一键还原；并给预置的 Key 格式提示加「可能不适用」限定 |
| 5 | `suggest` | 域名在预置里、路径不是收录值 | 黄色列出该域名**全部**收录端点（最多 3 个）为可点按钮 |
| 6 | `suspect` | 域名不认识、路径形如 `/api/vN`、且目录里无此写法 | 灰底提示：给出目录中的 OpenAI 路径样本 + 「这不代表填错」+ 指向 L2 的 404 症状 |
| 7 | `custom` | 其余 | 不渲染 |

**为什么 `plan-mismatch` 要单列**（判定 3）：命中预置并不等于用对端点。
同一域名下按量付费用 `/api/v3`、Coding Plan 用 `/api/coding/v3`，**两条都是合法预置**。
若只按「URL 命中预置」就给绿灯，就会漏掉官方点名的「用错会产生额外费用」组合 ——
这正是本功能要消灭的那类事故。

**`restore` 目标按域名找，不能只在 URL 相同的预置里找**：用户「先选计划、再粘贴
地址」时 `presetId` 为空，「还原到原预置」无从谈起，只能靠「同域名 + 同协议 + 本计划」
定位。此处有一个实测抓到的缺口：初版把查找范围限在「URL 完全一致」的预置里，
结果恰好在最需要按钮的路径上按钮消失（活服务复验截图见 B.13.5）。已修正。

### B.13.4 文件与实现位置

- `frontend-admin/src/components/model-config/ProvidersTab.tsx`
  - 纯函数层：`baseUrlHost` / `baseUrlPath` / `openaiPathSamples` / `presetLabelFor` /
    `suspectForeignPath` / `diagnoseBaseUrl`（含 `BaseUrlDiagnosis` 联合类型）。
  - 展示层：`BaseUrlAdvisor` + `AdvisorButton`，挂在 Base URL 行下方。
  - 复用既有 `applyPreset(presetId)` 做「一键替换 / 还原」，**不新增写接口**。
- 类型未导出：与文件内既有纯函数（`findPresetForRow` 等）一致，全部经组件行为覆盖。

### B.13.5 验收证据

- 单测（`ProvidersTab.test.tsx` 新增 7 例，共 29 例全过；`tsc --noEmit` 零错；
  全量 `vitest run` **329 例**全过）：
  一致时只给确认不出告警、偏离后一键还原、计划不符可切回、**未选预置时同样给出可切回端点**、
  同域名列出多候选、陌生域名 `api/vN` 只提示且**不含任何按钮**、
  合法自建网关与「带业务空间的按量地址」**都不触发提示**（含取值断言防假阳性）。
- **活服务实测**（当时为真实 43 条预置目录，浏览器经 :3200 → 登录 admin → 供应商 tab；
  目录已于 2026-09-21 扩至 49 条，本次实测结论不受影响）：
  - 选 `阿里云百炼 · 北京 · OpenAI 兼容` → `matched`，`baseUrl` 确为
    `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。
  - 覆写成工单地址 `https://maas.qianwenaiapi.com/api/v1` → `deviated`，预置原值与
    `/compatible-mode` 差异并排可见；Key 提示带上「而地址已被改过」。
  - 点「还原为预置地址」→ 回到预置值并转 `matched`。
  - 切到「自定义」后填同一地址 → `suspect`，文案命中「404 且响应体为空」，
    **候选按钮数 = 0**（不给伪正解）。
  - **误报扫描**（真实地址 7 例）：合法自建网关 → 无提示；`{WorkspaceId}` 换成真实
    取值的按量地址 → 无提示；`ark…/api/v9` → `suggest`（列 3 个候选）；
    `ark…/api/v3`（当前计划 Coding Plan）→ `plan-mismatch`；`api.deepseek.com/anthropic`
    （当前协议 OpenAI）→ `suggest`。**零误报**。

### B.13.6 明确未做（留给后续批次）

- **B2**：模型名从探测拿到的目录里选（需探测响应回传 `availableModels`）+
  探测结果区重构（归因配下一步动作、失败自动滚入视野）+ 底部 4 个平级按钮收敛为 3 个。
- **B3**：列表页筛选/搜索 + 角色占用徽标 +「去改绑」跳转；editor 只读可见。
- **B4**：1003 行单文件拆成 `providers/` 子目录（纯重构，零行为变化）。
- **`apiKeyHint` 的团队版文案**（`provider_presets.py:171` 写「sk- 开头」，而官方团队版
  是 `sk-sp-` 开头）：属后端预置内容变更，随 B2 一并处理并需重建容器。
- **偏离时的「显示名」陈旧**：`applyPlan()` 会清 `presetId` 与地址，但**不清
  `displayName`**；先选预置再换计划时，显示名可能仍留着上一个计划的字样。本批未动
  （改动会波及 slug 生成），记为 B3 候选。

## B.14 分层职责重划 + 模型目录按需拉取 + 失败归因「去修」（2026-09-21，B2 批）

### B.14.1 分层职责重划：L1 移出探测链（关键决策）

**讨论起点**：「其实只要测 L2 就行了？」——对。可用性**从来只由 L2 说了算**，L1
自始就不参与判定（B.4 硬约束 1 的「降级不判死」）。顺着推下去的结论不是「L1/L2
并行」，而是**把 L1 整个挪出探测**：

| 层 | 批次前 | 批次后 | 理由 |
|---|---|---|---|
| L0 | 保留 | **保留（安全闸门）** | url_guard + TCP/TLS，唯一能拦 SSRF/内网的地方；毫秒级 |
| L1 | 探测链内，8s 超时 | **移出探测链** | 诊断价值已被修好的 L2 归因吃掉；唯一残余价值是那份模型清单 |
| L2 | 判定 | **唯一判定** | 只有真调一次才能证明「地址+Key+模型名」组合可用 |
| L3 | 完整模式 | 不变 | 与可用性无关，只关乎记账 |

**收益**：「测试连接」= L0 + L2，成功路径几百毫秒到 1 秒出结论；失败路径不再
「地址填错了还要先白等 8 秒 L1 超时才被告知」。原「L1/L2 并行」方案仍让 L1 的
8 秒不确定性留在关键路径上，故弃。

**为什么不是「探测响应加 `availableModels` 字段」**：探测 `detail` 被
`DETAIL_LIMIT=200` 截断（挡膨胀是特性不是缺陷）；若放开，每次点「测试连接」都
重传几十 KB 目录，哪怕这次根本不挑模型名。清单**不属于「探测」这件事**——判定
归判定，填料归填料。

### B.14.2 模型目录：独立只读端点 + 按需拉取

- 后端（`provider_probe.py`）：
  - 原产目录能力改写为 `fetch_model_catalog()`（草稿态：baseUrl+apiKey）与
    `fetch_provider_catalog()`（已存实例：服务端用托管密钥，密钥不回显）；
  - 只取 `id`，丢 `object`/`created`/`owned_by`/`permission`（原始响应可达
    100–300KB，瘦身后十几 KB）；条数上限 + `truncated` 标记；
  - 粗分 `kind`（chat/embedding/rerank/vision/speech），前端据此分组；
  - **进程内短 TTL 缓存**（键 = base_url + key 摘要）：同一地址的清单不会秒变，
    中转站「好看但虚假的全量名单」也照单收——所以目录只能是**输入提示**，
    选中后仍需「测试连接」通过才认；
  - 安全约束与探测同源：admin only、url_guard、限流、审计不记密钥。
- 前端（`ModelCatalogPicker`）：「模型名称」框旁「从目录选」按钮，**点了才请求**；
  输入即筛（不是滚动找）；按 kind 分组、当前用途平铺、其余折叠进 `<details>`；
  选中的名字若与当前「模型用途」不符只提示不拦截；**输入框保持自由手打**——
  大量站点不实现 `/models`，拉不到清单不影响使用。

### B.14.3 失败归因配「去修」动作

- 后端归因（`_classify_l2_failure`）升级为**结构化** `FailureAttribution`
  （`reason` ∈ base_url / model_name / api_key / … + summary + raw），前端
  `normalizeProbeResponse` 透传。
- 前端 `fixHintsFor()`：由**失败归因代号**推出可点修复动作——`base_url` →
  同域名收录端点（`use-preset`）/ 算好目标地址的**替换**（`use-url`，百炼
  `/api/v1` → `/compatible-mode/v1` 是换不是接）/ 补 `/v1`；`model_name` →
  打开目录；`api_key` → 聚焦 Key 框。**给不出可执行动作的归因（timeout 等）
  不造按钮**——点了没用的按钮比没有按钮更糟。
- **失败文案全面中文化**（用户明确要求）：结论与摘要是人话；英文异常原文
  （langchain/OpenAI SDK 包装后的 `OpenAIModelNotFoundError` 等）只进折叠的
  「技术细节（上游原文，排障用）」，不与结论混排；连网络层异常（`_tcp_tls_ok`
  的 `OSError` 等）也转述为「连接 host:port 超时/不可达」。守门测试断言
  `probe-step-summary-L2` 不含异常类名。

### B.14.4 底部按钮 4 → 3

「取消 / 测试连接 / 完整测试 / 保存」中，测试连接与完整测试是同一件事的轻重
两档，平铺太吵。收敛为「取消 / 测试连接 / 保存」；**测试深度**（快速/完整）
移入「高级设置」下拉，附一句「流式 usage 只影响记账，与能不能用无关」。
新抽屉保存按钮文案为「测试并保存」（新配置必然先测后存）。

### B.14.5 顺带

- `apiKeyHint` 团队版文案改 `sk-sp- 开头`（`provider_presets.py`，随容器重建生效；
  已被并发提交 `ae2130e` 一并收编）。
- B.13.6 中「探测响应加 availableModels」的 B2 原方案作废，以本节为准。

### B.14.6 验收证据

- 后端：`test_provider_probe.py` 重写（L1 用例转目录用例 + 中文化守门）49 例；
  新增 `test_sys_providers_catalog_api.py`；相关 5 个测试文件 124 例全绿；
  后端全量 pytest 通过。
- 前端：`ProvidersTab.test.tsx` 29 → 37 例（目录按需拉取/分组折叠/手打兜底/
  去修按钮/一键替换/无动作不造按钮/英文只进技术细节/底部 3 按钮），全量
  vitest + `tsc --noEmit` 零错。
- 实施过程注意：对同一文件**并行发多个 Edit 会互相覆盖**（本次 3 处补字段语句
  被吞，靠 vitest 红灯逐个找回）——同文件多处修改必须串行编辑 + grep 复核。

## B.15 模型清单 DB 统一控制：代码层种子退役（2026-09-21）

**决策**（源自用户：「不需要种子，只要 DB 来统一控制；DB 里没有却被引用的应该报错」）：

`AVAILABLE_MODELS` 代码种子是「合并视图」时代的残留。0017 已把 9 个厂商行迁入
`llm_providers`，本批把最后 8 条模型行迁入 `llm_models`，自此 **DB 是模型清单的
唯一事实来源**（含 `qwen3.7-plus@tp` —— 它此前只存在于代码层）。

### B.15.1 迁移 0023（`0023_seed_builtin_models_to_db.py`）

- 8 条模型（qwen/qwen3.7-plus、qwen_tp/qwen3.7-plus@tp、ollama/qwen2.5:3b、
  deepseek/deepseek-v4-flash、minimax/MiniMax-M3、vllm/Qwen3-32B-AWQ、
  siliconflow×2）`INSERT ... ON CONFLICT (name) DO NOTHING`，单价进 pricing JSONB；
- `source='builtin'`（`llm_models_source_check` 仅允许 builtin/user），
  `created_by='migration-0023'` 供 downgrade 精确回滚与溯源；
- ⚠️ 实施教训：**host 侧跑 alembic 必须 `PGHOST=127.0.0.1 PGPORT=5433` 前缀**。
  `backend/config/database.py` 顶层 `load_dotenv()` 会把 `.env` 的
  `PGHOST=localhost/PGPORT=5432` 读进来，而 **宿主 5432 是本机原生 PG**（非 agent
  容器；compose 注释已注明「本地调试/迁移脚本通道：绑 127.0.0.1:5433」）。首次
  执行时迁移被带到了原生 PG 的 agent_memory（0019→0023，种入 8 行）——该库为
  同链陈旧副本，改动幂等无害，但**处置待定**（保留 or `downgrade 0022` 回退）。

### B.15.2 代码侧变更

| 文件 | 变更 |
|---|---|
| `models.py` | `AVAILABLE_MODELS` 清空退役（空列表仅为兼容）；`get_available_models()`/`get_model_entry()` 改 **DB-only**，返回动态层拷贝；模块头「新增模型走管理端」 |
| `proxy.py` | 错误提示里的可用清单改读 `get_available_models()` |
| `sys_providers.py` | `_merged_models` → DB-only；`_decorate_models` 恒 `source='user'`（「代码层内置不可移除」判定退役）；`_builtin_rows` env 兜底分支不再产出模型 |
| `model_roles.py` | `_registered_model` 报错改「未在数据库模型注册表中登记（可用: …）」；注册表为空时给专门提示 |
| `sys_config.py` / `startup.py` / `config/llm.py` | 注释同步（合法集 = llm_models） |
| 前端 `ProvidersTab.tsx` | 删「代码层内置模型，不可移除」分支与页脚说明；合成占位行 source 改 user |

### B.15.3 三层报错（用户要求的「DB 没有就报错」）

1. **绑定时**：角色校验（`_registered_model`）未命中 → 拒绝并列出可用清单；
2. **启动时**：`validate_roles()` 对默认/备用/各角色指向的未登记模型发告警；
3. **运行时**：`validate_override_model` / proxy 对未注册模型名 fail-fast（既有）。

### B.15.4 测试与验收

- 受影响 13 个测试文件全部转 DB 注入（`SEED_MODELS` fixture 模拟 registry 已加载），
  **168+ 例通过**；基线复跑确认 4 个**既有失败**与本批无关：
  `test_missing_key_env_reported_when_key_absent`（断言已废弃的 env 分支）、
  `test_llm_siliconflow_provider::test_set_current_rejects_when_siliconflow_key_missing`
  （报错文案已改断言未跟）、`test_database_model_config_authority` 2 例（OCR/embedding
  旧 env 语义）；
- 前端 `tsc --noEmit` 零错，`ProvidersTab.test.tsx` 37/37；
- ⚠️ 全量 pytest 未在本批重跑（历史基线另有 65 failed/3 error 均为无关区域，见 B.14）。

### B.16 B3 列表体验 + B4 文件拆分（2026-09-21 续会话，提交 067aa8c / 2a30b92）

#### B.16.1 B3（提交 067aa8c，7 文件 +282/-31）

| 项 | 实现 |
|---|---|
| 列表筛选/搜索 | ProvidersTab 工具条：搜索（显示名/ID/地址/模型名）+ 用途/状态下拉 + `N/M 家` 计数 + 双空态（无供应商 vs 筛选无结果）；纯客户端过滤 |
| 角色占用徽标 | `usedByRoles` 逐角色紫色徽标，点击 `onGoToRoles(role)` → 切角色 tab 并以 URL `?role=` 带参；RoleBindingsTab 接 `highlightRole`，目标行琥珀高亮 + `scrollIntoView` 居中 |
| editor 只读可见 | **后端配套**：`GET /sys/providers` 由 `require_admin_user` 放宽为 `require_user_actor`（与 model-roles 读同档，service/API-Key 身份仍拦；写/探测/目录端点不变）；前端 tab 过滤与 query `enabled` 同步放开，写操作仍按 canAdmin 门控 |
| applyPlan 不清 displayName | 现状已满足，补回归测试固化（换计划清地址、保留手改显示名） |

#### B.16.2 B4（提交 2a30b92，纯重构，行为零改动）

`ProvidersTab.tsx` 1860 行 → 主文件 632 行（列表渲染 + 数据编排）+ `providers/` 11 子模块：
`presets.ts`（预置反查/六态地址诊断）、`draft.ts`（草稿类型/工厂）、`format.ts`、
`catalogSections.ts`、`fixHints.ts` 纯函数 + `ProbeResultDetails` / `BaseUrlAdvisor` /
`ErrorNote` / `ModelCatalogPicker` / `ProviderEditor` / `ProviderModelEditor` 组件。
import 单向无环；`tsc --noEmit` 零错；frontend-admin 348 例全绿。

#### B.16.3 既有失败处置（提交 7baab20）

- `test_missing_key_env_*` → 改写为 `test_missing_key_env_is_none_in_db_mode`
  （missingKeyEnv 在 DB 模式恒 None）；
- siliconflow 拒绝用例断言对齐新文案「未在数据库配置 API Key」；
- `test_database_model_config_authority` 2 例 **xfail(strict=False)** 显式标注：
  实现侧保留「无 DB 绑定回退旧 env」开发兼容（embedding_singleton / ocr docstring
  明示），与收口目标态断言冲突 —— **是否删除兼容路径待拍板**：删除会使无 DB
  绑定的开发环境失去 embedding/OCR 云端能力。

#### B.16.4 遗留

- ✅ 全量 pytest 已回归（2026-09-21，14m06s，PGHOST=127.0.0.1 PGPORT=5433）：
  **5217 passed / 51 failed / 3 errors / 2 xfailed**，对照历史基线 65 failed/3 error
  **净减 14 例**（含本批修复的 4 例 + 基线统计时点差异）；3 errors 仍为 rerank 顺序
  既有组；model-config 改动面（sys_providers / sys_model_roles / siliconflow /
  authority）**零失败**，51 例全部落在既有无关区域（competitor、rag_upload、
  lineage、tool_approval、email 幂等、memory_routes 503 等）；
- P0（宿主 5432 原生 PG 误迁移）已拍板：**保留不回滚**（2026-09-21）；
- ✅ env 开发兼容路径已拍板（2026-09-21）：**保留不删除**。理由：回退仅在「无 DB
  绑定」时作为末位兜底，不违反 DB 权威原则；删除会使无 DB 绑定的开发环境直接
  失去 embedding/OCR 云端能力。2 例 xfail 已改写为钉住过渡期行为的正式用例
  （`test_embedding_config_env_fallback_when_no_db_binding_transitional` /
  `test_ocr_key_env_fallback_when_no_db_binding_transitional`，后者同时钉住三级
  env 优先级），docstring 显式标注翻转条件——待 DB 绑定成为强制配置后改回
  「env 不再生效」目标态断言；
- ⏳ 活服务容器未重建，B3 读权限放行需随下次变更窗口生效（当日工作区有他会话
  合并中間态：`backend/app/api/router.py` 等处于 UU 冲突未决，禁止此时 rebuild）。

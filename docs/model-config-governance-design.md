# 模型配置治理设计（Model Config Governance）

> 2026-09-19 起草。背景：`agent/.env` 已膨胀到 339 行 / 109 个变量，模型相关配置散落在
> 三处互不知情的事实来源上，管理端已有的模型切换器写的是进程内存态（重启即失效）。
> 本文给出收敛设计与实施分期。**本轮只出设计，不含代码改动。**

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

在同一套机制外再建一层会制造重复。**本设计的实质工作是扩展 `sys_config` 以承载
「模型角色」与「密钥」两类新配置**，而不是另起炉灶。

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
| `eval_gen` | 评测生成 / RAGAS | `OLLAMA_MODEL` | 与 `OLLAMA_ENABLED` 联动 |

「留空 = 跟随 main」这类**继承语义显式化**：登记为 `inherit: main`，而非空字符串。
管理端据此渲染「跟随 main（当前 = xxx）」而不是让人猜空值含义。

### 3.2 provider 能力矩阵（留代码，不进 DB）

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

### 7.1 页面位置与权限

- 路由：`/settings/models`，页面名「模型与供应商」
- 导航：挂「质量与配置」组（`components/layout/navConfig.tsx:74-81`），
  与「Prompt 管理 / Agent 节点 / 能力与技能」同级
- 权限：`minRole: 'admin'` —— 因含密钥操作，不能给 editor
- 现有页 `/cost-governance/prices` **重定向**到新页的「价格」tab，避免两个入口

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

GET    /sys/config/history               合并变更历史（支持 ?object= 过滤）
POST   /sys/config/history/{id}/rollback 一键回滚
GET    /sys/config/drift                 漂移与体检报告
```

前端 API 层复用 `frontend-admin/src/api/securityOps.ts` 里 `updateGuardMode` →
`/api/sys/config/{key}` 的写法（网关剥 `/api` 前缀）。

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

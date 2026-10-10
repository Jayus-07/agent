# 模型配置治理设计（Model Config Governance）

> 本文定义模型配置的数据来源、密钥治理、供应商接入和模型选择规则。实现细节以 backend/infra/llm、backend/services、数据库迁移、管理端代码及对应测试为准；本文不记录进度、提交或验收历史。

## 0. 决策记录

| # | 决策项 | 结论 |
|---|---|---|
| 1 | 模型选型是否进 DB | **接受进 DB** |
| 2 | 密钥是否允许管理端配置 | **允许**（附带加密与脱敏硬约束，见 §4） |
| 3 | 是否合并现有「模型价格」页 | **合并** |

## 1. 配置边界：守卫开关与模型配置分开治理

`backend/services/sys_config.py` 管理动态守卫开关，提供进程缓存、后台刷新、审计和环境默认值回退。模型角色、供应商实例及出站凭据使用专用模型注册/数据库治理实现，不能把密钥写入 `sys_config` 或普通配置历史。

稳定边界：

- `sys_config` 只承载适合字符串覆盖的运行开关；守卫读取必须保持热路径无同步数据库 I/O，并对未知或非法值安全回退。
- 模型名需保留大小写并通过当前注册模型集合验证；角色解析、供应商解析和密钥读取走专用入口，不能让调用方各自读取环境变量。
- Provider 驱动与协议能力随代码发布；供应商实例配置、角色绑定和密钥按专用迁移与注册仓储管理。
- 密钥写入、审计和读取遵循 §4 与附录 B；不得复用会记录普通 old/new 值的开关审计路径。

### 1.1 读取与校验边界

守卫开关和模型角色虽可热更新，但其数据来源与错误策略不同。修改时分别核对 `backend/services/sys_config.py`、`backend/config/model_roles.py`、`backend/infra/llm/registry_store.py`、对应迁移和失败路径测试；不能根据旧实现说明推断当前生效值。

## 2. 事实来源分层（定稿）

```
[ 消费方 ]  resolve_model(role)  ·  get_secret(provider)   ← 唯一读取入口
[ 持久层 ]  llm_model_roles / llm_models / provider_credentials  ← 专用迁移与审计
[ 驱动层 ]  PROVIDERS、Provider drivers、模型用途适配器           ← 代码只读
[ 配置层 ]  .env 基础设施设置与代码默认值                         ← 按各配置项契约读取
```

三条不变量：

1. **模型清单以数据库为准**：`llm_models` 是运行时模型清单；代码默认值不能绕过注册与绑定校验。
2. **模型角色有唯一解析入口**：消费方使用 `backend/config/model_roles.py` 暴露的解析函数；历史 env 名称仅供兼容展示，不作为模型选择来源。
3. **Provider 凭据独立治理**：出站 Key 只由凭据解析模块读取和解密；不得通过 `.env` 兜底发送旧 Key。

## 3. 模型角色（role）抽象

### 3.1 角色表

角色名、用途、继承语义和专项模型校验由 `MODEL_ROLES`（`backend/config/model_roles.py`）唯一维护；持久绑定从数据库读取。`RoleSpec.env_key` 是历史环境变量名，仅兼容展示。新增角色必须同步配置消费方、模型用途校验和回归测试，不在本文复制动态角色清单。

### 3.2 Provider 驱动与供应商实例

协议类型、请求格式和能力适配由代码中的 Provider driver 定义；自建供应商实例的 URL、模型目录及凭据由专用配置管理。边界为「**驱动留代码，实例数据按治理入口管理**」，不得把用户输入直接变成任意网络代理。安全与探测细节见附录 B。

以下由代码驱动层维护，不能由管理端以实例配置覆盖：

- 协议类型（openai-compatible / anthropic-compatible / dashscope-native / jina / ollama）
- 是否支持 `stream_options.include_usage`（现 `.env` 注释里写「MiniMax 兼容性未验证」）
- 是否支持余额查询、是否支持 rerank 端点（现注释：「token-plan 不支持 rerank 端点」）

Provider driver 与能力适配以 `backend/infra/llm/models.py`、`backend/infra/llm/providers/` 为准；模型目录本身由数据库提供。`AVAILABLE_MODELS` 已不是可添加模型的事实源，新增或移除模型应经数据库治理接口与引用检查。

### 3.3 三个读取入口

| 入口 | 语义 | 失败策略 |
|---|---|---|
| `resolve_model(role) -> {value, source}` | 同步、读进程内缓存、零 IO | 按 DB 绑定及代码默认策略解析；**绝不返回未注册模型** |
| `resolve_provider(role) -> str` | 由模型名反查 provider | 同上 |
| `get_secret(provider) -> str \| None` | 解密后的明文（仅进程内使用） | **fail-loud**，见 §4.4 |

`resolve_model` 的热路径不得执行同步数据库 I/O；注册表后台刷新及数据库故障时的缓存保留策略以 `registry_store.py` 为准。

> 实施口径：`main`、`fallback`、`doc`、`tool_selector`、`ocr`、`rerank`、`eval_gen`
> 已由实际调用点读取 DB 覆盖；`embedding` 受单例和向量索引一致性约束，管理端保存后必须
> 配合全量索引重建，当前索引不会自动切换。

### 3.4 运行时取值

`LLMFactory`、统一代理和管理端必须使用当前模型角色解析入口。持久角色绑定与请求级临时覆盖是不同作用域；请求级覆盖不得写回全局配置。页面和 API 的权限、响应和覆盖行为见[管理端交互契约](model-config-admin-ui-design.md)，实际生效模型以解析结果及来源字段为准。

## 4. 密钥存储设计

### 4.1 关键区分：入站 hash vs 出站可逆

两类密钥方向相反，不能套用同一策略：

| 类别 | 例子 | 我们的角色 | 存储方式 |
|---|---|---|---|
| 入站凭据 | `X-API-Key` | 校验方：只比对，不用还原 | 按当前认证实现存储/比对，禁止在文档或日志暴露明文 |
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

## 5. 环境变量边界

`.env` 仍用于数据库、Redis、认证、加密主密钥和部署参数。模型角色从数据库绑定与代码默认值解析；历史模型变量名只作兼容展示，不作为运行时模型来源。Provider 出站密钥由专用加密凭据表解析，不得在配置缺失时退回旧环境变量 Key。

只有显式登记到 `sys_config` 的守卫开关走 DB 覆盖与环境默认值回退；不能据此推断所有 `*_ENABLED`、RAG 参数或治理模式都能从管理端热改。Embedding 与索引语义绑定，改模型前核对 `requires_reindex` 契约、当前索引元数据和对应迁移。

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
| 模型角色绑定 | 专用角色绑定接口写入 + 审计；请求级覆盖不改全局默认 | 以模型注册仓储刷新语义为准 |
| 动态守卫开关 | `sys_config` 登记项校验 + 审计 + 安全回退 | 以配置刷新周期为准 |
| 模型价格 | 双人审核 + 24h 灰度（现有状态机，原样保留） | 最快 24h |

### 6.2 价格事实来源

LLM 与 embedding/rerank 的计费必须使用当前统一价格治理实现，不得在模型注册表和调用路径中维护相互冲突的单价。成本估算、预算阻断和账单展示应采用一致的价格版本与币种口径；价格来源和计算以价格仓储、迁移及测试为准。
## 7. 管理端

> 管理端交互契约见 [模型与供应商管理端设计](model-config-admin-ui-design.md)。本节定义治理边界，字段和请求形态以交互契约与当前代码为准。

### 7.1 页面位置与权限

- 路由：`/settings/models`，页面名「模型与供应商」
- 导航：挂「质量与配置」组（`components/layout/navConfig.tsx:74-81`），
  与「Prompt 管理 / Agent 节点 / 能力与技能」同级
- 权限：页级 editor 只读可见；修改操作需 admin。
- 现有页 `/cost-governance/prices` **重定向**到新页的「价格」tab；其 `navConfig` 条目
  导航只保留一个主入口；外部路由继续兼容重定向。

> 页面采用页级 editor 只读可见、管理员操作门禁；供应商与密钥 tab 仅 admin 可见，体检与漂移允许 editor 查看。具体字段脱敏和 tab 矩阵见管理端交互契约 §3.3–§3.4。

### 7.2 五个 tab

| tab | 内容 | 权限 | 数据源 |
|---|---|---|---|
| ① 角色绑定 | 每个注册 role 选择已登记模型；显示生效值、来源与校验结果；缺 Key 或类型不兼容时不可保存；Embedding 变更遵循重建索引提示 | admin | `resolve_model` |
| ② 供应商与密钥 | provider 列表（base_url、协议、能力矩阵只读）；密钥只显示「已配置/未配置 + 掩码尾部 + 指纹」；可写入/轮换；连通性自检按钮；余额（复用 `/llm/balance`） | admin | `provider_credentials` |
| ③ 价格 | 现有价格版本的导入/审核/灰度完整流程（原样搬移） | admin | `price_governance` |
| ④ 变更历史 | 两类对象的合并时间线：谁在何时把哪个键/role 从 A 改成 B；支持一键回滚 | admin | `sys_config_history` + `provider_credentials_history` |
| ⑤ 体检与漂移 | DB 覆盖与 `.env` 不一致时点名；缺 Key / 未注册模型 / 索引模型与生效模型不一致 | editor 可见 | 聚合 |

### 7.3 API surface

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

新增端点按裸对象响应，与现有客户端契约一致。`request<T>` 不自动解包响应体，HTTP 错误通过 `ApiError` 表达。已有接口若使用其他结构，继续遵循其自身契约并由测试覆盖。

前端 API 层路径：`/api/sys/...`（网关剥 `/api` 前缀）。

### 7.4 交互壳复用

`LLMSwitcher.tsx` 的视觉语言（胶囊触发器 + 下拉 + 勾号反馈 + 余额徽章）建议保留，
只把数据源从 `/llm/switch` 换成新的 role 接口。它已经跑在真实页面上，重做没有收益。

## 8. 实现与安全边界

- 模型与供应商清单的权威来源由当前注册表和数据库迁移定义；不得另造并行清单。
- 配置写入必须通过受保护的管理端 API，并校验角色、供应商归属和输入。
- 模型角色解析模块保持轻依赖；需要 Provider 能力或注册表快照的数据由 API 边界组装。
- 密钥只在写入时接收明文，存储前加密；读取接口不得回传明文，日志和审计仅保留必要的脱敏信息。
- Provider 探测与模型目录读取不得变成任意 URL 代理；网络范围、限流、授权和审计规则见附录 B.4、B.6、B.14。
- 价格治理与模型角色切换属于不同风险等级，分别遵循各自审批和生效策略。

## 9. 修改与验证

修改模型注册、凭据解析、探测或角色切换时，检查对应调用路径、数据库迁移、管理端响应契约及失败测试。外部 Provider 或凭据不可用时，说明未实测范围；不得以 mock 验证冒充真实连通验收。
## 附：本设计引用的代码位置

| 主题 | 位置 |
|---|---|
| 现有动态配置层 | `backend/services/sys_config.py` |
| 动态配置管理路由 | `backend/app/api/routes/sys_config_admin.py` |
| 模型配置事实源 | `backend/infra/llm/`、数据库迁移及对应测试 |
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

# 附录 B：用户自建供应商与连通性契约

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

**`billing` 三态与成本口径**

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

**跨模块约束**：
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

### B.4.1 L1 形状嗅探：200 不等于「这是 OpenAI 兼容基址」

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

### B.4.2 L2 归因：先判「地址错」，再判「模型名错」

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

**私网放行机制**

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

## B.9 决策记录

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

**请求级模型校验决策：把静默改为 API 边界 fail-fast 400。**

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

## B.10 Embedding 与 rerank 适配器契约

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

## B.11 预置端点目录与新增流程

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

⚠️ **不要为此新增第 4 个 billing 值。** 那要连带改 DB CHECK 约束、`ModelConfigService` 白名单、
`get_provider_billing` 及成本/预算/配额计算路径，所有消费端必须同步使用同一 billing 类型。

### B.11.2 预置目录与只读接口

内置供应商端点目录由代码静态维护，不参与模型解析链。管理端按需读取白名单字段；响应不得包含 API Key 或其他秘密。新增目录条目时同步检查协议、计费类型、模型用途及安全测试。
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

## B.12 模型移除与供应商分组

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

## B.13 Base URL 输入辅助

### B.13.1 设计场景

基础地址的主机可达，不代表其 API 路径符合所选服务计划。界面可依据预置目录提示可能的路径不匹配，但不能把目录当作权威验证或拦截用户自建端点。
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

## B.14 分级探测、模型目录与失败归因

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

## B.15 数据库模型清单的唯一来源

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

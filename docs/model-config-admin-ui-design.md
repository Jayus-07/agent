# 模型与供应商管理端 UI 设计（实现级）

> **定位**：本文记录模型与供应商管理端的交互、权限和请求契约。配置治理与密钥规则见 [模型配置治理设计](model-config-governance-design.md)。
> 页面实现以 frontend-admin 中的页面、API 模块和测试为准；本文不保存实现进度或历史验收记录。

---

## 0. 交互总览

| 区域 | 约束 |
|---|---|
| 页面 | 模型与供应商配置入口按角色显示；交互分为模型角色、供应商与密钥、价格、变更历史、体检与漂移等职责。 |
| 权限 | 页级和操作级权限分别校验；只读用户不得看到或调用受限操作。 |
| 请求 | 前端按后端实际裸对象响应契约处理；不擅自增加 Result 解包。 |
| 会话模型 | 临时模型选择仅作用于当前会话；改变全局默认走管理员配置流程。 |
| 敏感字段 | 密钥写入后只返回状态或掩码，不回显明文。 |

具体 tab、字段和状态交互见下文；运行时行为以对应代码与测试为准。
## 1. 前端共用模式

设计复用管理端既有组件、权限和 API 客户端模式；新增行为仍以当前组件和测试为准。

| # | 约定 | 证据 | 对本设计的约束 |
|---|---|---|---|
| 1 | Next 14 App Router + React 18 + **react-query v5** + zustand + Tailwind，**无 UI 组件库** | `package.json:14-27`；`src/components/` 无 `ui/` | 组件手写；数据用 `useQuery`/`invalidateQueries` |
| 2 | 门禁：`<RoleGate minRole>` 包页 + `atLeast('admin')` 控写操作 | `components/auth/RoleGate.tsx`；`lib/auth.ts` | 直接复用，不新造权限钩子 |
| 3 | **只读降级先例**：整页 `RoleGate minRole="editor"`，内部 `canAdmin = atLeast('admin')`，非 admin 显示顶部只读提示条 | `cost-governance/prices/page.tsx:29,105,108` | 本页照抄这个模式（见 §3） |
| 4 | 页面壳：`<PageHeader title desc />` | `components/layout/PageHeader.tsx` | 直接复用 |
| 5 | 反馈：`useToast()` → `toast.success/error`；错误卡三段式 | `components/shared/Toast.tsx`；`ErrorCard.tsx`（UX §4.4） | 轻反馈用 toast，重错误用 ErrorCard |
| 6 | 空态：`<EmptyState kind="no_data"｜"under_construction">`，**「不写假数据」**，空态必须可导航 | `shared/EmptyState.tsx:18`；UX §4.6 | 接口未就绪时用 `under_construction`，不留白板 |
| 7 | 骨架屏：`<Skeleton rows cols />` | `shared/Skeleton.tsx` | 首屏加载态 |
| 8 | **tab 栏样式**：容器 `flex gap-1 mb-6 border-b border-border-subtle`，按钮 `border-b-2`，激活 `border-accent text-accent`，含 lucide 图标 | `app/prompts/[key]/page.tsx:249-269` | 照抄，保持视觉一致 |
| 9 | **Modal 形态**：`fixed inset-0 z-50 flex items-center justify-center bg-black/40` + 卡片 `bg-white rounded-2xl shadow-xl w-full max-w-md p-6`；**无通用 Modal 组件、无 Drawer 先例** | `observability/trace/AddToEvalModal.tsx:43-45`；`competitor/PriceHistoryModal.tsx`；`knowledge/UploadDialog.tsx` | **主文档 B.7 写的「新增·编辑抽屉」改为 Modal**（抽屉在本仓无先例；引入 Drawer 属新增模式）。宽度按字段量取 `max-w-2xl` |
| 10 | API 层：新代码 `import { request, mutationRequest, ApiError } from '@/api/client'`；`@/lib/fetcher` **是待移除的兼容层** | `api/client.ts:279-371`；`lib/fetcher.ts:4-10` | 新模块直连 `@/api/client`；**写操作走 `mutationRequest`**（幂等键 + 同指纹共享在途请求，防重复点） |
| 11 | 设计 token：`text-text-primary/secondary/muted`、`bg-accent`、`border-border-subtle`、`shadow-card`；**暗色不做、状态色 token 已砍** | `app/globals.css:11-44`；`tailwind.config.ts`；UX §七-2 | 只用现有语义色，**不新增 CSS 变量** |
| 12 | 测试：vitest 与被测代码共置；`navConfig.test.ts` 锁「六组 + 组 minRole」；`surface.test.ts` 锁「每域一个模块」 | `components/layout/navConfig.test.ts:24-29,103-108`；`api/surface.test.ts:20-45` | 新 API **只能加一个域模块**（见 §2.4）；`navConfig` 只加条目不动组 |

### 1.1.1 API 响应形态：新端点使用裸对象

管理端 request<T> 返回后端响应体，不自动解包 data。新增模型配置端点按裸对象返回；HTTP 错误通过 ApiError 表达。已有接口若采用其他响应结构，按其现有契约和测试处理，不要为了统一而破坏兼容性。

---## 2. 路由、导航与文件落点

### 2.1 路由

| 路由 | 动作 |
|---|---|
| `/settings/models` | 新建。tab 用 `useState` 本地态，**不做子路由**（理由：5 个 tab 都是同一份配置的不同视图，URL 可分享性收益低；且项目内 `prompts/[key]` 的 tab 也是本地态） |
| `/cost-governance/prices` | **改为重定向**到 `/settings/models?tab=prices`（读 `searchParams` 决定初始 tab —— 这是唯一需要 URL 传参的场景） |

> ⚠️ **与 UX 文档 §七-3 的区分**：UX 文档里「`/settings` 预留页」指的是**用户端** `frontend/` 的「我的权限 + 通知偏好」。本页在 **`frontend-admin/`**，两 app 独立（不同端口/域名），**不冲突**。但命名相近，实施时须在 README 或注释标注，避免后人合并。

### 2.2 导航注册

`components/layout/navConfig.tsx`「质量与配置」组（组 `minRole: 'editor'` **保持不动**，`navConfig.test.ts:105` 有断言）追加：

```tsx
{ label: '模型与供应商', path: '/settings/models', minRole: 'admin' },
```

只加条目、不动组与其它条目 —— `navConfig.test.ts` 的「六组齐全」「路径唯一」「minRole 同语义」三条断言均不破。

**为什么条目级是 `admin`**（而非页级 `editor`）：导航应少噪音，editor 的入口价值集中在 tab⑤ 体检，不需要在主导航常驻。editor 仍可通过直连 URL 进入（页级门禁是 `editor`），并在那里看到 §3.3 的说明条。

### 2.3 文件清单

**新增**

| 文件 | 职责 | 预估行数 |
|---|---|---|
| `src/app/settings/models/page.tsx` | 页面壳：RoleGate + PageHeader + tab 栏 + 分发 | ~180 |
| `src/components/settings/ModelRolesTab.tsx` | tab① 角色绑定表 + 行内编辑 + embedding 二次确认 | ~220 |
| `src/components/settings/ProvidersTab.tsx` | tab② 供应商列表 + 新增/编辑 Modal 入口 | ~260 |
| `src/components/settings/ProviderEditorModal.tsx` | 供应商新增/编辑表单 + 内网勾选 + 密钥三态输入 | ~300 |
| `src/components/settings/ConnectivityProbe.tsx` | 连通性测试按钮 + 四级清单渲染 + 状态机 | ~200 |
| `src/components/settings/ConfigHistoryTab.tsx` | tab④ 合并时间线 + 回滚 | ~180 |
| `src/components/settings/DriftTab.tsx` | tab⑤ 体检与漂移 | ~160 |
| `src/components/settings/prices/PriceTab.tsx` | tab③ 价格（从现有 `prices/page.tsx` **整体搬移**，逻辑不改） | 复用 ~127 |
| `src/api/modelConfig.ts` | 单域模块（见 §2.4） | ~150 |
| `src/types/modelConfig.ts` | 类型与纯函数（`gradeLabel` / `sourceBadge` / `maskSecret`） | ~120 |

**改动**

| 文件 | 改动 |
|---|---|
| `src/components/layout/navConfig.tsx` | +1 条目 |
| `src/app/cost-governance/prices/page.tsx` | 改为 `redirect('/settings/models?tab=prices')` |
| `src/components/agent/LLMSwitcher.tsx` | 全局切换 → 会话级（§13） |
| `src/api/chat.ts` | `ChatRequest` 加 `model?: string` |
| `src/hooks/useSSE.ts` | `runStream` 加第三参数并透传 |

**测试新增**：`src/types/modelConfig.test.ts`（纯函数）、`src/api/modelConfig.test.ts`（请求形状，照 `modelPrices.test.ts` 写法）。

### 2.4 域模块归属：为什么只加**一个** `modelConfig.ts`

`api/surface.test.ts:20-45` 的契约是「**每域有且仅有一个模块** `src/api/<domain>.ts`」（`DOMAINS` 列了 16 个域）。

本页涉及 4 类对象（role 绑定 / provider / history / drift），若拆成 `modelRoles.ts` + `providers.ts` + `configHistory.ts`，会把「一域一模块」破坏成「一页三模块」。故**合并为单模块 `modelConfig.ts`**，域边界定义为「**模型配置治理**」——history 与 drift 都是它的派生视图，不是独立域。

价格**不并入**（沿用既有 `api/modelPrices.ts`）：它有自己的审批状态机与端点前缀 `/admin/model-prices/*`，且 `modelPrices.test.ts` 已锁定它。tab③ 只是把页面搬过来，`PriceTab` 仍 import `@/api/modelPrices`。

---

## 3. 权限模型

### 3.1 页面和操作门禁

页级允许 editor 进入只读查看；修改模型绑定、供应商、密钥、价格或历史记录需 admin。后端仍须独立执行授权，隐藏按钮不能替代服务端检查。

### 3.2 敏感信息

密钥明文永不回传。editor 的供应商、密钥和体检信息按 §3.4 脱敏；变更历史中的供应商地址按既有审计契约展示。
### 3.3 tab 可见性矩阵

| tab | viewer | editor | admin | editor 视角的差异 |
|---|---|---|---|---|
| ① 角色绑定 | 页级拦 | **只读** | 读写 | 隐藏「修改」按钮与行内下拉；保留「生效值 + 来源」 |
| ② 供应商与密钥 | 页级拦 | **隐藏 tab** | 读写 | tab 按钮不渲染（不让 editor 知道有哪些厂商在配） |
| ③ 价格 | 页级拦 | 只读 | 读写 | 沿用现有只读提示 |
| ④ 变更历史 | 页级拦 | 只读（**密钥类条目脱敏**） | 读写 + 回滚 | 密钥条目只显示指纹与操作人，不显示任何值 |
| ⑤ 体检与漂移 | 页级拦 | **可见** | 可见 | 无差异 |

### 3.4 密钥类信息对 editor 的暴露边界

B.6 表格「查看供应商 / 模型 → admin」按此落实为**四条硬边界**：

1. tab② **整 tab 隐藏**（不是只读）—— 连「有哪些 provider」都不向 editor 展示；
2. tab⑤ 的体检结论**只出现「provider 缺失/无效」，不出现 base_url、掩码、指纹**；
3. tab④ 的历史条目**密钥类只显示 `已轮换（指纹 3f9c1d）`**，provider 类显示完整 URL（URL 不是秘密，B.6 也允许全记）；
4. 收敛到一个纯函数 `redactForRole(entry, canAdmin)`（在 `src/types/modelConfig.ts`），**可直测** —— 避免每个组件各写一遍脱敏逻辑。

---

## 4. 组件树

```
SettingsModelsPage ('use client')
├── RoleGate minRole="editor" pageName="模型与供应商"
│   ├── PageHeader title="模型与供应商" desc="<一句话说明 + 生效来源链>"
│   ├── [只读提示条]  ← !canAdmin 时
│   ├── [页级漂移 banner]  ← 有 critical 漂移时（数据来自 tab⑤ 同一 query）
│   ├── <nav 角色="tablist">  ← 照抄 prompts/[key] 的 tab 栏样式
│   │   └── TabButton × N（② 按 canAdmin 条件渲染）
│   └── <main>
│       ├── tab==='roles'    → <ModelRolesTab canEdit={canAdmin} />
│       ├── tab==='providers'→ <ProvidersTab canEdit={canAdmin} />
│       ├── tab==='prices'   → <PriceTab />                    ← 搬移自 prices/page.tsx
│       ├── tab==='history'  → <ConfigHistoryTab canEdit={canAdmin} />
│       └ tab==='drift'      → <DriftTab />
│
├── ProvidersTab
│   ├── [工具栏] 新增供应商按钮（canEdit）
│   ├── <table> ProviderRow × N
│   │   └── 列：显示名 / 驱动 / base_url / 密钥状态 / 模型数 / 最近探测结论 / 操作
│   └── ProviderEditorModal（受控：open + 初始值，null = 新建草稿）
│
├── ProviderEditorModal
│   ├── 表单区：显示名 · 驱动(select) · base_url · API Key · 模型名(多行) · 计费模式 · 单价 · 内网勾选
│   ├── ConnectivityProbe（base_url 与 API Key 字段旁，草稿态可点）
│   └── [底部] 取消 / 保存
│
└── ConnectivityProbe
    ├── 触发按钮/图标（4 态：idle / running / pass / fail）
    └── 结果清单 GradeRow × 4（L0/L1/L2/L3）
        └── 每行：级标 + 状态图标 + 一句话结论 + [原文摘要]折叠
```

**props 约定**：所有 tab 组件收 `canEdit: boolean`（而非各自 `atLeast`）—— 单一判据、便于测试注入。

---

## 5. 数据层

### 5.1 queryKey 与轮询

沿用价格页 `<频率>` 口径（`prices/page.tsx:30` 用 30s）：

| queryKey | 端点 | 轮询 | 说明 |
|---|---|---|---|
| `['model-roles']` | `GET /api/sys/model-roles` | 30s | 含 source 与校验结果 |
| `['llm-providers']` | `GET /api/sys/providers` | 30s | 含密钥配置状态（**不含任何密钥值**） |
| `['config-history', object?]` | `GET /api/sys/config/history` | **不轮询** | 历史是审计视图，手动刷新 |
| `['model-drift']` | `GET /api/sys/config/drift` | 60s | 页级 banner 与 tab⑤ 共用 |

**多实例不一致的 UI 交代**：另一实例改配置后本页最多滞后 30s。页脚须标注「数据每 30 秒刷新 · 最后更新 HH:mm:ss」（照 `security/page.tsx` 的 `lastUpdated` 写法），否则用户会以为「自己刚改的没生效」。

### 5.2 写操作

**全部走 `mutationRequest`**（`api/client.ts:143`）：同名操作在途时共享请求与幂等键，天然防「连点两次保存」。每个操作的 `operation` 命名：

```ts
operation: `model-role:${role}`                    // 角色绑定
operation: `provider:${id ?? 'new'}`               // 供应商新增/编辑
operation: `provider-credential:${id}`             // 密钥写入/轮换
operation: `config-history-rollback:${historyId}`  // 回滚
```

成功后 `queryClient.invalidateQueries` 对应的 queryKey（照 `prices/page.tsx:79,92,101` 写法）。

⚠️ **密钥写入的幂等键语义要特别小心**：`mutationRequest` 的指纹含 body（`stableSerialize`）。密钥**同一份明文重复提交会命中在途共享**（合理，防连点）；但**不同明文**是不同指纹（正确，允许连续轮换）。无需特殊处理，但**测试要覆盖「连点两次轮换不同 Key」不被误合并**。

### 5.3 错误与反馈

| 场景 | 呈现 |
|---|---|
| 页面级取数失败 | `<ErrorState>` 或 `ErrorCard`（三段式）+ 重试按钮 |
| 写操作失败 | `toast.error(err.message)`；**`ApiError.detail` 存在时展开详情**（后端会把探测原文放在 detail） |
| 权限不足（403） | 页级由 RoleGate 拦；写操作 403 → toast + 提示「需要管理员」 |
| 后端未实现（404/501） | 该 tab 渲染 `EmptyState kind="under_construction"`，**不留白板**（UX §4.6 硬约束） |

**不吞错**：本项目反复踩「静默降级」的坑（§4.4 与 §1.1 各一例）。故 `catch` **必须**至少 `toast.error` 或 `setError`，禁止 `catch {}` 空实现。

### 5.4 TS 接口（字段级）

`src/types/modelConfig.ts`（**本文档即契约，后端须对齐**）：

```ts
/** 生效来源：db 覆盖 / env 值 / 继承父 role / 代码默认 */
export type ValueSource = 'db' | 'env' | 'inherit' | 'default'

export interface RoleBinding {
  role: string                    // main | doc | tool_selector | fallback | ocr | embedding | rerank | eval_gen
  label: string                   // 中文显示名（前端常量，不从后端取）
  effectiveModel: string          // 实际生效的模型名（已展开 inherit）
  literalValue: string            // 字面配置值（可能为空串 —— 空串有语义，见主设计 §3.1）
  source: ValueSource
  inheritedFrom: string | null    // source==='inherit' 时的父 role
  provider: string | null         // 归属 provider（自建模型必填，B.5#3）
  providerLabel: string | null    // 厂商中文名（后端 `PROVIDERS[].label` 下发；db 自建供应商回落 provider 代码）
  registered: boolean             // 是否在可用模型清单内
  missingKeyEnv: string | null    // 缺哪个 Key 环境名（null = 齐备）
  requiresReindex: boolean        // embedding 改值需重建索引；rerank 不改变向量空间
  updatedBy: string | null
  updatedAt: string | null
}

export type BillingMode = 'metered' | 'subscription' | 'local'
export type NetworkScope = 'public' | 'private'

export interface ProviderRow {
  id: string                      // slug，如 'glm-coding'
  displayName: string
  driver: 'openai' | 'anthropic' | 'ollama'   // 代码白名单，不可自建
  baseUrl: string
  networkScope: NetworkScope
  billing: BillingMode
  isBuiltin: boolean              // 内置 7 家 = true（可在页面停用）
  enabled: boolean
  modelCount: number
  credential: {
    configured: boolean           // 只有布尔，**永不下发密钥**
    fingerprint: string | null    // 如 '3f9c1d'
    last4: string | null          // 如 'a1b2'
    rotatedAt: string | null
    rotatedBy: string | null
  }
  lastProbe: {
    at: string
    ok: boolean
    worstGrade: ProbeGrade | null  // 失败时哪一级挂了
  } | null
}

export type ProbeGrade = 'L0' | 'L1' | 'L2' | 'L3'
export type ProbeStatus = 'pass' | 'fail' | 'skip' | 'fail_degraded'

export interface ProbeStep {
  grade: ProbeGrade
  status: ProbeStatus
  /** 一句话结论（前端兜底文案；后端给了就用后端的） */
  summary: string
  /** 原文摘要（截断 200 字，主设计 B.4 硬约束 4） */
  raw?: string
  elapsedMs?: number
}

export interface ProbeResult {
  ok: boolean                     // 是否通过（含「L1 失败但 L2 通」= 通过）
  steps: ProbeStep[]
  suggestion?: string             // 如「可能缺少 /v1 后缀」
}

export interface ConfigHistoryEntry {
  id: string
  object: 'role' | 'provider' | 'provider_credential' | 'provider_network_scope'
  key: string                     // role 名 / provider id
  oldValue: string | null
  newValue: string | null
  operator: string
  at: string
  /** 密钥类条目后端只回指纹，前端再按角色脱敏 */
  secretFingerprint?: string | null
  rollbackable: boolean
}

export interface DriftItem {
  severity: 'critical' | 'warn' | 'info'
  kind: 'missing_key' | 'unregistered_model' | 'db_env_conflict' | 'index_model_mismatch' | 'unverified_provider'
  subject: string                 // role 名或 provider id
  message: string
  hint?: string
}
```

**纯函数（可直测，放同文件）**：

| 函数 | 职责 |
|---|---|
| `gradeLabel(grade)` | `L0`→「URL 可达」等，四级文案唯一来源 |
| `sourceLabel(source, inheritedFrom)` | `inherit` → 「跟随 main（当前 = xxx）」—— 落实主设计 §3.1 的「不让人猜空值含义」 |
| `maskSecret(last4, fingerprint)` | `····a1b2 · 指纹 3f9c1d` |
| `redactForRole(entry, canAdmin)` | §3.4 的脱敏收敛点 |
| `isModelSelectable(model)` | 所有角色共用的标红/禁选判据（未注册 / 缺 Key / 用途不匹配）；角色编辑统一从已登记模型目录选择 |

---

## 6. tab① 角色绑定

**布局**：单表格，**5 列**，按**业务链路分组**渲染（分组常量 = `frontend-admin/src/types/modelConfig.ts::ROLE_GROUPS`：
问答链路 / 入库链路 / 检索链路 / 评测链路，未登记角色落末位「其他」组）。

角色行数与分组从后端 MODEL_ROLES 派生，不在文档复制动态数量。新增角色时同步维护 ROLE_LABELS 和 ROLE_GROUPS；测试应覆盖缺少映射时的显示行为。
⚠️ 后端新增角色时，`ROLE_LABELS` 与 `ROLE_GROUPS` 必须**同批补齐** —— 漏补不报错，只会让角色列
显示英文代码（`roleLabel` 回落为 role），`types/modelConfig.test.ts` 的两条用例即为此设的护栏。

| 列 | 内容 | 态 |
|---|---|---|
| 角色 | 中文名（`ROLE_LABELS`）+ `role` 代码（`font-mono text-[10px] text-text-muted`） | — |
| 当前绑定 | 只留模型信息：**厂商中文名徽章**（`providerLabel` 前置，后端 `PROVIDERS[].label` 下发）+ `effectiveModel`（`font-mono`，非法时红字）+ 用途徽章（**仅当模型目录能查到该模型**才渲染，避免用期望用途冒充事实）；后端未下发中文名（db 自建供应商）时在模型名后回落显示 provider 代码 | 编辑时换成 `<select>` |
| 来源 | 第一行：**来源徽章** + 审计「最后由 who · 相对时间」（`updatedBy`/`updatedAt`）；`literalValue !== effectiveValue` 时第二行给「配置值」 | 编辑时显示「沿用当前来源，保存后更新」 |
| 可用性 | 图标 + 结论；不可用时直接给 `availabilityReason`；`requiresReindex` 的角色另起一行「变更需重建索引」 | 编辑时**按下拉所选值实时重算** |
| 操作 | 「修改」/「保存」+「取消」 | 非 canEdit 不渲染 |

**来源徽章分色**：`DB 覆盖`(蓝) / `环境变量`(灰) / `跟随 main`(紫) / `代码默认`(浅灰)；未知来源走保守灰，
不冒充「代码默认」（§6 原则「不让人猜空值含义」）。

**分组与筛选**：分组行显示链路名 + 一句话口径 + 该组角色数；表头右侧有「只看不可用」开关，
计数取后端 `availabilityReason` 判定为不可用的角色数，与页头「严重漂移」红条呼应。

**编辑态的可用性列**：进入编辑后，可用性列按**下拉所选值**重算（`roleVerdict`），而不是停留在当前生效值的
旧结论 —— 否则用户在下拉里换了个不可用的模型，要等保存后才知道。

**筛选空态**：「只看不可用」筛空时渲染 `EmptyState no_data`（title「所有角色当前都可用」），
出路动作 = 关闭筛选（满足 UX §4.6「空态必须可导航」）；表体不渲染任何分组行。

**页面头摘要卡**：随 tab 切换，每张卡描述当前 tab 正在看的内容，而不是全 tab 共用一套
（原实现中「供应商」「已验证」对非 admin 恒为「—」占位）：
- 角色绑定：模型角色 / 不可用角色（红绿）/ 登记模型 / 严重漂移
- 供应商与密钥：供应商 / 已验证 / 预置端点 / 严重漂移
- 体检与漂移：漂移项 / 严重漂移 / 提醒项 / 登记模型
- 变更历史：变更记录 / 模型角色 / 登记模型 / 严重漂移
- 模型价格：模型角色 / 登记模型 / 严重漂移 / 不可用角色

**行内编辑**（不弹窗，改动小）：
- 点击「修改」→ 所有角色统一变为 `<select>`，选项 = `get_available_models()` 中与角色用途匹配的已登记模型，并保留当前失效值为禁用项及原因；`eval_gen` 另提供「未配置（停用评测生成）」选项；没有对应分类模型时提示先到供应商页面新增并测试模型；
- 未注册或该 provider 缺 Key 的模型：**渲染为 `<option disabled>` + 后缀说明**（而不是过滤掉 —— 让用户看到「有但不可选」的原因，避免「我明明加了模型怎么找不到」）；
- 保存 → `PUT /sys/model-roles/{role}` → 成功 toast + invalidate。

**`requiresReindex` 的二次确认**（embedding）：改值弹确认，文案须含代价：

> 修改 embedding 模型后，**已有索引与查询向量不在同一空间**，检索结果会不可用，必须全量重建索引（耗时较长）。
> 确认修改？

—— 用 Modal 而非 `window.confirm`（密钥类操作不可逆，`window.confirm` 在浏览器里可被「不再显示」勾掉，且项目已有 Modal 先例；价格页用 `window.prompt` 是其历史写法，不复制）。

**空态**：未登记任何角色时渲染 `EmptyState no_data`（title「未登记任何模型角色」），description
提示确认 `/sys/model-roles` 是否正常返回，出路动作 = 触发重新加载。注：接口失败与真空数据在
组件层不可区分（父级传 `roles.data?.items ?? []`），错误场景由页面级错误条兜底，故用 `no_data`。

---

## 7. tab② 供应商与密钥

### 7.1 列表

| 列 | 内容 |
|---|---|
| 显示名 | + `isBuiltin` 徽章「内置」，+ `!enabled` 灰显「已停用」 |
| 驱动 | `openai` / `anthropic` / `ollama`（`font-mono`） |
| base_url | `font-mono text-[11px]` 截断 + `title` 全文；**私网实例加徽章「内网」** |
| 密钥状态 | `已配置 ····a1b2 · 指纹 3f9c1d · 3 天前轮换` / `未配置`（红） |
| 模型数 | `n` |
| 最近探测 | 结论徽章 + 相对时间；未测过 → 「未验证」（灰，tab⑤ 会点名） |
| 操作 | `编辑` / `停用|启用`（canEdit） |

### 7.2 新增 / 编辑 Modal 字段表

| 字段 | 控件 | 校验 | 备注 |
|---|---|---|---|
| 显示名 | input | 必填 | — |
| 驱动 | select | 必填，**仅 3 项**（来自代码） | **不可自建协议**（主设计 §3.2：能给用户改就能把系统配成不可用） |
| base_url | input + **测试图标** | 必填；保存时后端过 `url_guard` | 去尾斜杠；缺 `/v1` 由 L1 的 404 给建议 |
| API Key | **三态输入**（见 §7.3） | 新建必填；编辑可留空 | 保存后不可读回 |
| 模型名 | textarea（多行） | 每行一个，去空行去重 | 保存时逐行报「第 n 行重复」 |
| 计费模式 | select：`按量` / `订阅制` / `本地` | 必填 | 订阅制 → 单价区**置灰 + 显示「订阅制 · 不计 token」**（B.9③） |
| 单价 | input（数字） | 仅 `按量` 时启用 | 单位固定 `USD / 1M tokens`（与既有价格表同口径） |
| **这是内网服务** | checkbox | — | 见 §7.4；勾选后才显示「内网」徽章与风险提示 |

**保存后回执**（B.7 硬要求，直接对冲 B.5#2 的困惑）：

> 已保存并生效。**当前会话的下一次调用即使用新凭据**（无需重启）。

### 7.3 密钥输入的三态

| 状态 | 输入框表现 | 提交行为 |
|---|---|---|
| 未配置（新建 / 编辑时为空） | 空，placeholder「粘贴 API Key」 | 必填；空则表单校验拦 |
| 已配置（编辑） | 空，placeholder「已配置 ····a1b2 · 留空则保持不变」 | 空 → **不发送该字段**（后端不动密钥） |
| 轮换（编辑，用户输入了新值） | 显示新值（`type="password"`，带 👁 切换） | 发送新值 → 后端加密落库 + 记指纹到审计 |

**三条硬约束**：
1. 输入框**永不回填已存密钥**（前端从不持有明文）—— 连密文都不下发（`ProviderRow.credential` 只有指纹与 last4）；
2. 轮换按钮的二次确认文案须含「旧 Key 立即失效，正在跑的会话可能中断」；
3. 提交后**立即清空输入框**（`setKey('')`），避免用户以为页面里还留着。

### 7.4 内网勾选（B.6 决策 ①）

- 控件是**显式 checkbox**，文案：「这是内网服务（允许指向 `localhost` / 私网 IP）」
- 勾选后立即在表单内展开红色风险提示：

> ⚠️ 勾选后，本服务可向你的内网地址发起请求。**只为你信任的服务开启**。
> 该操作会记入审计（谁、何时、为哪个供应商开启）。

- 列表以徽章显示「内网」（`bg-amber-50 text-amber-700`），与 `public` 一眼可分
- **不得实现为「自动识别私网就放行」**（B.6 明令：DNS rebinding 会绕过）。前端唯一职责是把勾选值如实传给后端；判定在后端。

### 7.5 模型用途目录

供应商是协议、地址和凭据的容器，模型是供应商下可独立切换的目录条目。每个模型必须带用途：

| 用途 | 标签 | 可绑定角色 | 测试请求 |
|---|---|---|---|
| `chat` | 文本模型 | `main`、`doc`、`tool_selector`、`fallback`、`eval_gen` | 最小 Chat 请求 |
| `embedding` | 向量模型 | `embedding` | OpenAI 兼容 `/embeddings` |
| `rerank` | 重排模型 | `rerank` | Jina 兼容 `/rerank` |

`llm_models.model_kind` 是唯一类型来源，历史无类型数据按 `chat` 兼容。供应商列表逐模型显示
用途徽章；“新增模型”只填写模型名和用途，后端使用该供应商已保存的 URL/Key 测试，通过后才
写入目录。已有模型不能直接改用途，避免一个模型名在运行时被错误适配器调用；需要切换用途时
新增一个模型条目。

角色下拉框按目标用途过滤，同时保留后端的最终校验。Embedding 仍需在切换后全量重建向量索引，
因为用途标签不会改变既有向量的语义空间。向量、重排、OCR 均通过供应商页登记；若不同模型使用
不同 API Key，应分别新增供应商，不能依赖同一供应商凭据。

---

## 8. 连通性测试 UI（四级探测）

### 8.1 状态机

```
idle ──click──▶ probing ──每级完成──▶ probing(累计 steps[]) ──全完成──▶ done(ok|fail)
                    │
                    └── 取消/超时 ──▶ idle（保留已得 steps，标注「已中止」）
```

`ProbeStatus` 的展示语义（**每级失败含义不同**，主设计 B.4 —— UI 必须把这个差异表达出来，否则用户只会看到「测试失败」）：

| 级 | pass 文案 | fail 文案（关键：指向不同修法） |
|---|---|---|
| L0 | URL 可达（DNS + TLS 正常） | **地址写错或不可达**：检查协议/端口/拼写 |
| L1 | 端点响应正常（`/models` 可用） | `404 → 该站点可能不实现 /models（不影响使用）`；同时给「是否缺少 `/v1`」建议 → 状态 `fail_degraded`，**不判死** |
| L2 | 模型名可用（已用真实构建路径调用） | **模型名错误或该 Key 无权访问**：区分「Key 错」与「模型名错」（B.7 硬要求 1） |
| L3 | 流式响应回传 usage | 不回传 usage → `skip`，给降级说明（记账会缺 token 数） |

**L1 `fail_degraded` 的呈现**：黄色 ⚠ + 「该站点未实现端点列表，已跳过（不影响使用）」，**绝不显示为红色失败**（B.4 硬约束 1：否则「测试不通过但能用」，按钮从此没人信）。

### 8.2 四级清单渲染

- 触发：base_url 与 API Key 输入框旁的图标按钮（`Wifi` / `Loader2` / `CheckCircle2` / `AlertCircle`）
- 结果**内联展开在表单下方**（不是弹窗、不是单个布尔）—— 逐行 L0..L3，每行：级标 + 图标 + 一句话结论 + 「原文摘要」折叠（`<details>` 或自实现，等宽字体）
- 结果区顶部一句总结：「**厂商连通性通过**」/「**未通过（卡在 L2）**」
- **免责声明**（B.4 硬约束 6）：「以上仅验证厂商连通性，不代表业务可用（业务链还要过限流、预算与工具绑定）。」

### 8.3 草稿态测试（B.4 硬约束 5）

「填完保存了才知道不能用」必须避免，故测试**必须在未保存时可用**。

- 触发条件：`baseUrl` 与 `apiKey`（或已配置状态）齐备即可点
- 请求携带**草稿值**（不落库）
- ⚠️ **端点缺口**：主文档 §7.3 只定义了 `POST /sys/providers/{provider}/verify`（**有 id，针对已存实例**）。草稿态**没有 id**，需补：

| 端点 | 用途 | 权限 |
|---|---|---|
| `POST /sys/providers/{id}/verify` | 已保存实例的复测（不带 body） | admin，限流 |
| **`POST /sys/providers/verify-draft`** | **草稿态探测（body 含 driver/base_url/apiKey/networkScope）** | admin，**更严限流**（未落库 URL 更易被滥用） |

两者都须满足 B.6 的四道限制（admin only / 过 `url_guard` / 报文固定 / 限流）。

### 8.4 「未验证」不硬拦（B.7）

保存时若未测或未通过：**允许保存**，但
- 保存后 toast 用 `toast.info` 提示「已保存，但**连通性未验证**」
- 列表该行「最近探测」列显示红色「未验证」
- tab⑤ 体检点名（`unverified_provider`）

不硬拦的理由（B.7 原文）：用户可能先配、后开网络白名单。

---

## 9. tab③ 价格

价格数据沿用现有价格治理流程；管理页应明确标示审批与生效状态，并复用已有价格页面的数据契约。价格规则与模型角色切换规则分开维护。

旧路由如仍被书签或外部文档使用，应保留兼容重定向；导航只提供一个主入口。

---## 10. tab④ 变更历史

**数据源**：`sys_config_history` + `provider_credentials_history` 的**合并时间线**（后端合并后下发，前端不做两次请求再拼 —— 排序与分页在后端做才对）。

**布局**：竖向时间线（每行：时间 / 操作人 / 对象 / `旧值 → 新值` / 回滚按钮）。

| 对象类型 | 展示 | 脱敏（editor） |
|---|---|---|
| `role` | `main: MiniMax-M3 → Qwen/Qwen3-8B` | 原样 |
| `provider` / `provider_network_scope` | `glm-coding: base_url a → b` / `network_scope public → private`（**私网变更加醒目徽章**） | 原样 |
| `provider_credential` | `glm-coding: 密钥已轮换（指纹 3f9c1d）` | 原样（本就不含值） |

**回滚**：`POST /sys/config/history/{id}/rollback`
- `rollbackable=false` 的行（如被后续变更覆盖、或对象已删除）按钮置灰 + `title` 说明原因
- 密钥回滚**必须禁止**（旧密文已不可解或不该复活）：后端应回 `rollbackable=false`；前端**同时**硬编码拦截并提示「密钥不可回滚，请重新轮换」
- 二次确认 Modal：明确写出「将把 X 从 A 改回 B，并立即生效」

---

## 11. tab⑤ 体检与漂移

聚合视图，三类来源：

| 检查项 | 数据 | 严重度 |
|---|---|---|
| **DB 覆盖与 `.env` 不一致** | role 的 `source==='db'` 且 `.env` 里也有不同的值 | `warn`（主设计 §10 风险 4：持久化后「重启模型与 .env 不一致」是**常态**，不是 bug —— 故文案须写「当前由 DB 覆盖，`.env` 的值不再生效」而非「冲突错误」） |
| 缺 Key / 未注册模型 | `RoleBinding.missingKeyEnv` / `!registered` | `critical` |
| provider 未验证 / 探测失败 | `ProviderRow.lastProbe` | `warn` |
| 索引模型与生效 embedding 模型不一致 | 比对索引元数据与当前角色配置 | critical（检索质量会受影响） |

**呈现**：
- 每条：严重度图标 + 一句话 + `hint`（可操作建议）+ 跳转（如「去 tab①修改」）
- **全绿时不要显示空白** —— 显示一个明确的绿色结论卡：「4 项检查全部通过 · 检查于 HH:mm」
- **页级 banner**：仅 `critical` 且数量 > 0 时，在页头下方显示一条可折叠的红色条（点击 → 跳到本 tab）。`warn` 不打扰。

---

## 12. 空 / 错 / 载 / 未验证态

按 UX §4.6 的三层空态落实，**逐 tab 明确**（避免「打开即白板」的既有断点 X6）：

| tab | 加载中 | 接口未就绪（404/501） | 数据为空 | 错误 |
|---|---|---|---|---|
| ① | `<Skeleton rows={8} cols={5} />` | 见 §6 空态注记：组件层不区分「未就绪」与「真空」，统一 `EmptyState no_data`（description 提示确认 `/sys/model-roles`），错误由页级错误条兜底 | `no_data` +「未登记任何模型角色」+ 重新加载（§6） | ErrorCard |
| ② | Skeleton | 服务不可用时展示可重试提示 | `no_data` +「尚无自建供应商」+ **CTA「新增供应商」**（canEdit） | ErrorCard |
| ③ | Skeleton | 复用价格页既有空态 | 既有 | 既有 |
| ④ | Skeleton | `under_construction` | `no_data` +「暂无变更记录」 | ErrorCard |
| ⑤ | Skeleton | `under_construction` | **不适用**（全绿也要出结论卡） | ErrorCard |

**「未验证」是一个独立态**（B.7）：不是错、不是空，是**未知**。用 `toast.info` + 灰色徽章，不用红。

---

## 13. 会话级模型切换

会话级模型覆盖仅作用于当前会话请求，不改变全局默认模型。切换器的选择值应与会话状态同生命周期；重新生成沿用该会话所选模型，清除覆盖后回到全局默认。

全局默认和模型配置属于管理员操作；普通编辑者只能选择本次会话使用的已注册模型。API 边界执行模型可用性和凭据校验，错误需说明原因。权限、规则和请求契约分别由前端组件、chat API 与模型注册表测试锁定。
### 13.1 请求级模型校验

会话模型覆盖只对当前请求生效。API 边界调用共享的模型校验规则；模型未注册、Ollama 未启用或供应商凭据缺失时返回明确的 400 错误。合法模型才进入请求上下文。

上下文绑定层保留其既有宽容语义，因为评测和脚本等非 HTTP 调用方没有 HTTP 错误响应通道。规则实现见 backend/infra/llm/models.py，API 入口见 backend/app/api/routes/chat.py。

不引入独立的模型级 ACL；使用已有权限、预算和配额机制。

---

## 14. 回归契约

### 14.1 会话模型覆盖

API 校验与上下文绑定共用同一模型规则；非法覆盖应在 HTTP 边界拒绝，合法覆盖应随请求传递。更新请求协议时同步检查 chat API、SSE、重新生成和测试覆盖。

### 14.2 Provider 凭据传递

Provider 构建路径必须将解析后的凭据显式传给对应构造器。新增 Provider 分支时同时更新分发表和契约测试，避免管理端保存的密钥在真实问答链路中被静默绕过。凭据或探测行为的安全规则见模型配置治理设计 B.3–B.6、B.14–B.15。

### 14.3 最小检查入口

- 后端请求级模型校验：backend/tests/test_model_override_validation.py。
- Provider 凭据分发：backend/tests/infra/test_llm_proxy_credentials.py。
- 前端请求与会话覆盖：对应 API、SSE 和模型切换组件测试。

---
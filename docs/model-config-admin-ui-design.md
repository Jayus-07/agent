# 模型与供应商管理端 UI 设计（实现级）

> **定位**：主设计 `docs/model-config-governance-design.md` 的 §7（管理端）/ §7.3（端点草案）/ B.7（前端）/ B.9（决策）的**实现级展开**。
> 主文档定「做什么、谁能做、流程多重」；本文定「用什么组件、字段怎么摆、态怎么分、请求怎么发」。
> **冲突裁决**：权限与流程以主文档为准；命中本文 §3.1 指出的内部矛盾时，以本文的修正方案为准（已在主文档加修订批注）。
>
> **状态**：设计定稿，**未实施**。属主文档 B.8 的 **P2**。
> **前置依赖**：P1a-2（凭据链路闭合）、P1b（探测服务）。**两者未完成前做本页，会得到一个「配了不生效」的页面**（B.8 硬闸门）。
>
> 日期：2026-09-19 ｜ 关联：UX 架构 `docs/2026-09-17-UX体验架构设计.md`（§4.2 门禁 / §4.6 空态 / §五 token）

---

## 0. 一屏结论

| 项 | 结论 |
|---|---|
| 页面 | `/settings/models`「模型与供应商」，5 个 tab |
| 技术形态 | `'use client'` 单页 + `useState` 切 tab（**不引子路由**）；react-query 取数；手写 Tailwind |
| 门禁 | 页级 `RoleGate minRole="editor"` + tab 级 `atLeast('admin')`（**修正主文档 §7.1**，见 §3） |
| 新增文件 | 页面 1 + 组件 ~8 + API 模块 1 + 类型 1 |
| 改动文件 | `navConfig.tsx`（+1 条）、`cost-governance/prices/page.tsx`（改重定向）、`LLMSwitcher.tsx`、`api/chat.ts`、`hooks/useSSE.ts` |
| **后端缺口** | **6 个端点需要新写**（含 history / rollback / drift / verify-draft），前端无法独立交付 |
| 可先行 | ③ 价格搬移、④⑤ 的**只读骨架**（若后端先给 GET） |
| 硬约束 | **不引入新设计 token**（暗色与状态色 token 已于 UX §七 拍板砍掉）；Modal 而非 Drawer（项目无 Drawer 先例） |

---

## 1. 现状核对（已实测的前端约定）

设计必须落在既有约定上，不自造模式。以下 12 条均为代码实测结论。

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

### 1.1 ⚠️ 顺带发现并已修：安全运营页的开关切换是「假失败」

排查第 10 条（响应形态）时撞到的，**与本设计无关但影响同一批 API 写法**：

| 环节 | 事实 |
|---|---|
| 后端契约 | `PUT /sys/config/{key}` 返回**裸 dict** `{"key","old","new","changedBy"}` —— `sys_config_admin.py:51`，且 `services/sys_config.py:247` 是 `return {"old","new"}` |
| 契约已被测试锁定 | `tests/api/test_sys_config_admin.py:177` `assert body["old"] is None and body["new"] == "audit"`（读**顶层**字段） |
| 前端却按 Result 壳取 | `api/securityOps.ts:97-106`：`request<Result<...>>(...)` → **`return res.data`** |
| 响应体原样透传（两头都不包装） | BFF `app/api/[...path]/route.ts:98` `new Response(upstream.body)`；`api/client.ts:367` `return data as T`（不解包） |
| 消费者 | `app/security/page.tsx:201` `const { old, new: newVal } = await updateGuardMode(...)` |

**结论**：`res.data` 恒为 `undefined` → 解构抛 `TypeError` → 被 catch 显示「切换失败: Cannot destructure property 'old' of 'undefined'」，**但后端此时已写库并落审计**，且因抛出而跳过 `await load(true)`，界面不刷新。

即：**admin 在「安全运营」页切换守卫开关，看到的是「失败」，实际已生效** —— 本仓库最忌讳的「假失败」（「真实原因被埋在 N 层语义错误之下」的同类）。

**根因**：`/sys` 前缀下**两种响应形态并存** ——
- `/sys/security/*` → `{code,message,data,timestamp}`（`auth_local.py:114`）→ 前端 `res.data` ✓
- `/sys/config` → 裸 dict → 前端 `res.data` ✗

**修复（2026-09-19 已实施，改前端 1 处）**：`securityOps.updateGuardMode` 去掉 `Result` 包裹，直返 `request<GuardModeUpdateResult>`（新增具名类型，注释写明「裸 dict，无壳」）。**未改后端**：`test_sys_config_admin.py:177` 已把顶层字段锁成契约，包壳会打破它，且会连带 `/sys/security/*` 侧一起动 —— 代价大于收益。

配套新增共置契约测试 `api/securityOps.test.ts`（4 例），用**真实响应形状**（裸 dict）驱动。**有效性已验证**：把实现临时改回 `res.data` 后 **3 failed / 1 passed**（症状与原 bug 一致：`expected undefined to be defined`）；其中「请求形态」一例仍绿，说明断言分工准确 —— 坏的是读，不是写。

### 1.1.1 决策：新端点一律**裸 dict**（无 Result 壳）

这条直接决定 §5.4 与 §14 的端点契约。**已拍板，非建议**：

| # | 理由 | 依据 |
|---|---|---|
| ① | 与既成事实正交 | `client.ts:295,367` 的 `request<T>` **不解包**、`return data as T`（被 `client.test.ts` 锁定）。用壳 = 每个调用点手工 `.data`，**靠人记住** —— 本次 bug 正是「没记住」的产物 |
| ② | 类型系统能兜住 | 裸 dict 下返回值即 `T`，形状错配在类型层可见；壳下 `request<Result<T>>` 的 `.data` **类型完全合法**，TS 全程不报错，只在运行时炸 |
| ③ | 与最近邻同源 | 同前缀 `/sys/config` 已是裸 dict；新端点 `/sys/model-roles`、`/sys/providers`… 与它同级同前缀 |
| ④ | 消除第二套错误通道 | `client.ts` 已在 HTTP 层用 `ApiError` 表达失败，壳里的 `code` 是冗余通道 —— **两套通道并存本身就是问题源**（`/sys` 下两形态并存正是本次根因） |

**存量不动**：`/sys/security/*` 的 Result 壳**保持原样**（有测试契约锁定 + 多处消费，迁移收益 < 成本）。口径记为「**一域一形态；新域一律裸 dict**」，不启动全站统一化 —— 那属另一工作面，与本设计无关。

**对 P1b / P2 的硬要求**：每个新增 API 域模块**必须有共置契约测试**，且用真实响应形状驱动（照 `securityOps.test.ts` 写法）。

---

## 2. 路由、导航与文件落点

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

## 3. 权限模型（对主设计 §7.1 的修正）

### 3.1 主文档内部的矛盾（必须解决，否则无法实施）

| 位置 | 原文 | 问题 |
|---|---|---|
| §7.1 | 「权限：`minRole: 'admin'` —— 因含密钥操作，不能给 editor」 | 页级 admin |
| §7.2 tab⑤ | 「体检与漂移 ｜ 权限：**editor 可见**」 | **页级 admin 门禁下 editor 进不了页面，这条要求无法成立** |

两条自相矛盾。同时 B.6 的权限表写「查看供应商 / 模型 ｜ admin」，进一步收紧。

### 3.2 修正方案：页级 `editor` + tab 级 `admin`

**采用与 `cost-governance/prices/page.tsx` 逐字一致的模式**（`RoleGate minRole="editor"` + `canAdmin = atLeast('admin')` + 顶部只读提示条）：

```tsx
export default function SettingsModelsPage() {
  const canAdmin = atLeast('admin')
  return <RoleGate minRole="editor" pageName="模型与供应商">
    ...
    {!canAdmin && <div className="mb-6 rounded-xl border border-blue-100 bg-blue-50 p-4 text-xs text-blue-800">
      当前角色为只读模式：可查看模型生效值与配置体检结果。密钥、供应商与模型绑定需要管理员。
    </div>}
```

理由（三条，按权重）：
1. **⑧ tab⑤ 要 editor 可见是主文档自己的要求** —— 页级 admin 会让这条设计目标归零；
2. 项目**已有完全一致的先例**，不引入新模式；
3. 「查看模型生效值」是本页对 editor 的核心价值（排障时 editor 要能自证「我用的是哪个模型」），把整页锁成 admin 会把这个场景推回给管理员。

**修正后须同步主文档 §7.1**（已加修订批注）。

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
  registered: boolean             // 是否在可用模型清单内
  missingKeyEnv: string | null    // 缺哪个 Key 环境名（null = 齐备）
  requiresReindex: boolean        // embedding/rerank 类改值需重建索引
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
| `isModelSelectable(role, model, models)` | 标红/禁选的唯一判据（未注册 / 缺 Key） |

---

## 6. tab① 角色绑定

**布局**：单表格，8 行（= 8 个 role）。

| 列 | 内容 | 态 |
|---|---|---|
| 角色 | 中文名 + `role` 代码（`font-mono text-[10px] text-text-muted`） | — |
| 生效模型 | `effectiveModel`，`font-mono` | 非法时红字 + 冒号后原因 |
| 来源 | 徽章：`DB 覆盖`(蓝) / `环境变量`(灰) / `跟随 main`(紫) / `代码默认`(浅灰) | `inherit` 时后缀「（当前 = xxx）」 |
| 校验 | `registered`+`missingKeyEnv` 合成的结论 | 未注册 / 缺 Key → **红** |
| 操作 | 「修改」（canEdit） | 非 canEdit 不渲染 |

**行内编辑**（不弹窗，改动小）：
- 点击「修改」→ 该行生效模型单元格变为 `<select>`，选项 = `get_available_models()` ∩ 有 Key 的 provider；
- 未注册或该 provider 缺 Key 的模型：**渲染为 `<option disabled>` + 后缀说明**（而不是过滤掉 —— 让用户看到「有但不可选」的原因，避免「我明明加了模型怎么找不到」）；
- 保存 → `PUT /sys/model-roles/{role}` → 成功 toast + invalidate。

**`requiresReindex` 的二次确认**（embedding / rerank）：改值弹 `window.confirm` 风格确认，文案须含代价：

> 修改 embedding 模型后，**已有索引与查询向量不在同一空间**，检索结果会不可用，必须全量重建索引（耗时较长）。
> 确认修改？

—— 用 Modal 而非 `window.confirm`（密钥类操作不可逆，`window.confirm` 在浏览器里可被「不再显示」勾掉，且项目已有 Modal 先例；价格页用 `window.prompt` 是其历史写法，不复制）。

**空态**：`GET /sys/model-roles` 未就绪 → `EmptyState under_construction` + 文案指向主设计文档。

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

## 9. tab③ 价格（搬移）

- **逻辑零改动**：`prices/page.tsx` 的 127 行整体移入 `PriceTab.tsx`，仅去掉外层 `RoleGate` 与 `PageHeader`（改由父页提供）
- `canAdmin` 由 props 传入（原本是组件内 `atLeast('admin')`，统一到 §4 的 props 约定）
- **视觉标注「需审批生效」**（主设计 §10 风险 5：同页两个 tab 一个即时、一个要等 24h，不区分用户会以为「改了没生效」）：
  - tab 按钮文案后缀一枚小徽章「需审核」
  - tab 内容顶部一条说明：「本 tab 的变更需**双人审核 + 24 小时灰度**后才生效；其余 tab 即时生效。」
- 旧路由 `/cost-governance/prices` 重定向到 `?tab=prices` —— 保留它的 `navConfig` 条目还是删除？**删除**（避免两个入口），但重定向保留（外部收藏/文档链接不失效）。

---

## 10. tab④ 变更历史

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
| 索引模型与生效 embedding 模型不一致 | 需后端比对索引元数据（**后端缺口**，见 §14） | `critical`（检索会静默变差） |

**呈现**：
- 每条：严重度图标 + 一句话 + `hint`（可操作建议）+ 跳转（如「去 tab①修改」）
- **全绿时不要显示空白** —— 显示一个明确的绿色结论卡：「4 项检查全部通过 · 检查于 HH:mm」
- **页级 banner**：仅 `critical` 且数量 > 0 时，在页头下方显示一条可折叠的红色条（点击 → 跳到本 tab）。`warn` 不打扰。

---

## 12. 空 / 错 / 载 / 未验证态

按 UX §4.6 的三层空态落实，**逐 tab 明确**（避免「打开即白板」的既有断点 X6）：

| tab | 加载中 | 接口未就绪（404/501） | 数据为空 | 错误 |
|---|---|---|---|---|
| ① | `<Skeleton rows={8} cols={5} />` | `EmptyState under_construction` +「模型角色注册表尚未接通（P0 已完成契约层，等待 P1）」 | `no_data` +「未登记任何角色」（不该发生，视为缺陷） | ErrorCard |
| ② | Skeleton | `under_construction` +「供应商注册表尚未接通（等待 P1b）」 | `no_data` +「尚无自建供应商」+ **CTA「新增供应商」**（canEdit） | ErrorCard |
| ③ | Skeleton | 复用价格页既有空态 | 既有 | 既有 |
| ④ | Skeleton | `under_construction` | `no_data` +「暂无变更记录」 | ErrorCard |
| ⑤ | Skeleton | `under_construction` | **不适用**（全绿也要出结论卡） | ErrorCard |

**「未验证」是一个独立态**（B.7）：不是错、不是空，是**未知**。用 `toast.info` + 灰色徽章，不用红。

---

## 13. 会话级模型切换（B.9 决策 ②）

目标：**回收 editor 的全局切换权**，全局默认只 admin 可改；editor 保留「本次会话临时换模型」。

现状（已实测）：
- 后端通道**已经完整存在**：`ChatRequest.model` → `RequestContext` → `set_request_model()` → contextvar
- 前端**完全没用**：`api/chat.ts:10-19` 的 `ChatRequest` **没有 `model` 字段**；`LLMSwitcher` 走的是 `switchLLM()` 那条全局路径
- `useSSE.runStream(question, sessionId)` **只有两个参数**，不传 model

**三处改动**（后端零改动 —— 这是本决策最大的收益）：

```
① api/chat.ts      ChatRequest 加 `model?: string`；streamChat 原样透传（body 已整体序列化）
② hooks/useSSE.ts  runStream(question, sessionId, modelOverride?) —— 新增第三参数，
                   streamChat({..., model: modelOverride})
                   ⚠️ 三个调用方都要过：useChat.startStream / MessageBubble.regenerate / stopStream 路径
③ LLMSwitcher.tsx  受控化：从「自己 fetch 全局 + switchLLM」改为
                   - 读：全局默认（useQuery ['llm-current']，只读）
                   - 写：只写会话态（store 或父组件 state），**不调 switchLLM**
                   - 视觉：当前会话覆盖时，触发器显示后缀「· 本会话」（否则用户以为改了全局）
```

**状态放置**：会话级模型属「一次会话的临时偏好」，放 `store/chat.ts` 的 `sessionModel: string | null`（与 `sessionId` 同生命周期），而非组件本地 state —— 因为 `ChatView` / `ComposerToolbar` / `MessageBubble.regenerate` 三处都要读它。`regenerate` 必须带同一 model（否则「重新生成」会换模型，结果不可比）。

**permission**：`/llm/switch`（全局）加 `require_admin_user`；`LLMSwitcher` 对非 admin 隐藏「设为全局默认」项。保留 `set_current` 的内存语义作 admin 调试通道（主设计 §3.4）。

**待定细节**：会话级 model 是否需要后端校验（防止 editor 传一个未授权模型名）？→ **需要**，但这属后端 P2 项，见 §14。

---

## 14. 后端缺口清单（本页的阻塞项）

⚠️ **本页无法纯前端独立交付** —— 6 个端点不存在。核实于 2026-09-19：`backend/app/api/routes/sys_config_admin.py` **全文件仅 51 行、仅 2 个端点**（`GET /sys/config`、`PUT /sys/config/{key}`）；`/sys/model-roles`、`/sys/providers`、`/sys/config/history`、`/sys/config/drift` **均不存在**。

| # | 端点 | 依赖阶段 | 阻塞的 tab |
|---|---|---|---|
| 1 | `GET /sys/model-roles` | P2 | ①⑤ |
| 2 | `PUT /sys/model-roles/{role}` | P2 | ① |
| 3 | `GET /sys/providers` | P2 | ②⑤ |
| 4 | `PUT /sys/providers/{id}`（含 credential / network_scope） | P2 | ② |
| 5 | `POST /sys/providers/{id}/verify` + `POST /sys/providers/verify-draft` | **P1b** | ②（测试图标） |
| 6 | `GET /sys/config/history` + `POST /sys/config/history/{id}/rollback` | P2（**新写**，历史表已有但无读取端点） | ④ |
| 7 | `GET /sys/config/drift` | P2（含索引模型比对，见下） | ⑤ |

**两点必须与后端对齐**：
1. **响应形态逐端点写死**（§1.1 的教训：`/sys/config` 裸 dict、`/sys/security/*` 是 Result 壳，前端曾因此误用）。**决策见 §1.1.1：新端点一律裸 dict**，逐端点不得再靠猜。
2. **索引模型比对**（tab⑤ 的 `index_model_mismatch`）需要读索引元数据（embedding 模型名 + 维度）。若后端暂不提供，**该检查项须显式标注「暂不支持」而非静默缺失**。

---

## 15. 落地顺序

| 批 | 内容 | 前置 |
|---|---|---|
| **A（可先行）** | 类型与纯函数（`types/modelConfig.ts` + 测试）、`api/modelConfig.ts`（对着 mock）、③ 价格搬移 + 重定向、④⑤ 的只读骨架（若后端给了 GET） | 无（不动热路径） |
| **B** | tab① 角色绑定（只读先上，写操作随后端 `PUT` 上线） | 后端 #1 #2 |
| **C** | tab② 列表 + 编辑 Modal（先无测试图标） | 后端 #3 #4 |
| **D** | 连通性测试 UI | **P1b** 的 #5 |
| **E** | ④ 历史 + 回滚、⑤ 漂移 | 后端 #6 #7 |
| **F** | 会话级切换（§13）+ `/llm/switch` 加门禁 | 无（后端已就绪） |

**F 其实可以提到最前**：它零后端依赖、独立可验收，且**先做能立刻收掉 B.5#6 的权限洞**（editor 现在就能切全局模型 —— 那是当下正在生效的风险，不是设计债）。

**不建议先做 A 的页面骨架**：B.8 的硬闸门是明确的 —— P1a-2 / P1b 未完成前，页面「配了不生效」。只做**纯函数与 API 模块（含 mock 测试）**是安全的，因为它们不产生可点击的 UI。

---

## 16. 测试策略（vitest）

沿用「共置 + 纯函数优测」的仓库口径（`src/api/*.test.ts`、`components/knowledge/fmtMs.test.ts`）。

| 测试 | 覆盖 |
|---|---|
| `types/modelConfig.test.ts` | `gradeLabel` 四级文案；`sourceLabel` 的 inherit 展开；`redactForRole` **三类对象的脱敏矩阵（含 canAdmin=true 与 false 两侧）**；`isModelSelectable` 的未注册/缺 Key 两分支；`maskSecret` |
| `api/modelConfig.test.ts` | 请求形状（method/path/body）照 `modelPrices.test.ts` 写法；**响应形态断言（裸 dict，不是 Result 壳）** ← 防 §1.1 的同类回归 |
| `ConnectivityProbe` 组件测试 | **L1 失败但 L2 通过时整体为「通过」且 L1 渲染为黄色 ⚠ 而非红色**（B.4 硬约束 1 的守卫测试） |
| `ProviderEditorModal` 组件测试 | 密钥三态：未配置必填校验 / 已配置留空则 body **不含** key 字段 / 轮换则 body 含新值 |
| `navConfig.test.ts`（既有） | 新增条目后**既有断言须全绿**；补一条「`/settings/models` 条目 minRole=admin」 |
| `api/chat.ts` 相关 | `ChatRequest.model` 透传（若 `surface.test.ts` 有涉及则一并） |
| `useSSE` | `runStream` 带与不带 `modelOverride` 时 `streamChat` 收到的 body |

**禁止**：为了让测试通过而 mock 掉 `atLeast` —— 权限矩阵测试应通过 `sessionStorage.setItem('agent.user_info', ...)` 真实切换角色（照 `navConfig.test.ts:71-74` 的 `loginAs` 写法）。

---

## 17. 决策与待确认

### 已定（2026-09-19）

1. **§1.1 的「假失败」bug** → **已修**：改前端 1 处 + 加共置契约测试（见 §1.1）。
2. **新端点响应形态** → **裸 dict**，不带 Result 壳（理由链见 §1.1.1）。

### 待确认

1. **tab② 对 editor 隐藏（而非只读）**：本文按 B.6「查看供应商 = admin」从严。若你认为 editor 应能看到「有哪些供应商（不含 URL/密钥）」，矩阵需放宽。
2. **会话级模型是否需要后端校验**（editor 传未授权模型名）？本文标记为「需要」，但落在后端 P2。
3. **`/cost-governance/prices` 的 nav 条目是否删除**？本文建议删（避免两入口）+ 保留重定向。

/**
 * modelConfig.ts — 「模型配置治理」页的类型契约与纯函数（UI 设计 §5.4 / §8.1）
 *
 * 本文件是**前端侧唯一的事实来源**：类型对齐 `docs/model-config-admin-ui-design.md` §5.4
 * （后端已按同一契约实现 `/sys/model-roles`、`/sys/providers`），纯函数是该页所有
 * 文案与判据的收敛点 —— 组件里**不许再写第二遍**（§3.4 硬边界 4）。
 *
 * 两条与设计文档的有意偏差（均已在 §16 记录，勿当疏漏）：
 *
 * 1. `isModelSelectable` 从 `(role, model, models)` **收敛为 `(model)`**：
 *    可选性只由该模型自身的「是否注册 / 是否缺 Key」决定，`role` 与整份目录都不参与
 *    （§6 明写「标红/禁选的唯一判据 = 未注册 / 缺 Key」）。`role` 只影响保存时的
 *    `requiresReindex` 二次确认，与可选性无关；保留无用形参会让调用方误以为它有作用。
 * 2. `sourceLabel` 增加第 3 个形参 `inheritedValue`：设计文档给的两参签名**无法**产出
 *    它自己要求的文案「跟随 main（当前 = xxx）」——「当前 = xxx」必须由调用方把父角色的
 *    生效值传进来，否则又回到「让人猜空值含义」（主设计 §3.1 明确反对）。
 */
// ── 类型契约（对齐 UI 设计 §5.4；后端须按同一契约实现）────────────────────

/** 生效来源：db 覆盖 / env 值 / 继承父 role / 代码默认 */
export type ValueSource = 'db' | 'env' | 'inherit' | 'default'

export interface RoleBinding {
  role: string
  /** 中文显示名由前端常量提供（见 `ROLE_LABELS`），后端不下发 */
  label?: string
  effectiveModel: string
  /** 字面配置值（可能为空串 —— **空串有语义**，见主设计 §3.1） */
  literalValue: string
  source: ValueSource
  inheritedFrom: string | null
  provider: string | null
  registered: boolean
  missingKeyEnv: string | null
  available: boolean
  availabilityReason: string | null
  requiresReindex: boolean
  updatedBy: string | null
  updatedAt: string | null
}

export type BillingMode = 'metered' | 'subscription' | 'local'
export type NetworkScope = 'public' | 'private'
export type ModelKind = 'chat' | 'embedding' | 'rerank' | 'vision' | 'speech'

// ── 预置端点目录（后端 `GET /api/sys/providers/presets`）────────────────
//
// 计费计划「三选一」。它决定**候选端点**（以及推荐 billing），
// 但它本身不是新的 billing 值：按设计文档 B.2/B.3，Token Plan 与
// Coding Plan 都是预付/订阅制，都落 `subscription`，只有按量付费落 `metered`。
export type PlanId = 'token_plan' | 'coding_plan' | 'metered'

export interface PresetPlan {
  id: PlanId
  label: string
  billing: BillingMode
}

export interface ProviderPreset {
  id: string
  plan: PlanId
  vendor: string
  /** 地域 / 版本区分，如「北京」「个人版」；无区分时为空串。 */
  variant: string
  driver: 'openai' | 'anthropic'
  driverLabel: string
  baseUrl: string
  /** Key 长什么样的说明（如「sk- 开头」）—— **不是密钥**。 */
  apiKeyHint: string
  /** 用错端点会多花钱一类的提醒。 */
  note: string
  /** base_url 里待用户替换的占位符，如 `['WorkspaceId']`。 */
  placeholders: string[]
}

export interface ProviderPresetsResponse {
  plans: PresetPlan[]
  items: ProviderPreset[]
  actor: string
}

export function modelKindLabel(kind: ModelKind): string {
  switch (kind) {
    case 'embedding':
      return '向量模型'
    case 'rerank':
      return '重排模型'
    case 'vision':
      return '视觉模型'
    case 'speech':
      return '语音模型'
    default:
      return '文本模型'
  }
}

export function roleModelKind(role: string): ModelKind {
  if (role === 'embedding') return 'embedding'
  if (role === 'rerank') return 'rerank'
  return 'chat'
}

/**
 * 探测级别标签。
 *
 * ⚠️ **`L1` 已不再由后端产生**（2026-09-21 起：原 `GET {base}/models` 移出探测链，
 * 改为按需的模型目录接口，见 `ModelCatalogResponse`）。但 `L1` **必须保留在这个联合
 * 类型里**：`provider.lastProbe.worstGrade` 是**历史值**，库里可能还存着改动前那次
 * 探测留下的 `L1`，删掉会让老数据显示成空白。新代码不应再产出 `L1`。
 */
export type ProbeGrade = 'L0' | 'L1' | 'L2' | 'L3'
export type ProbeStatus = 'pass' | 'fail' | 'skip' | 'fail_degraded'

/** 失败归因代号 —— 后端 `provider_probe.REASON_*` 的镜像。
 *
 * 用它来决定显示哪个「去修」动作，**不要对 `summary` 做字符串匹配**：
 * 文案随时可能改，代号是契约。未知代号要能优雅退化成「不给建议」。
 */
export type ProbeReason =
  | 'blocked'          // 被安全策略拦截（私网 / 云元数据）
  | 'invalid_url'      // URL 本身不合法
  | 'unreachable'      // DNS / TCP / TLS 连不上
  | 'timeout'          // 连接或调用超时
  | 'base_url'         // 地址不是 OpenAI 兼容基址（路由不存在）
  | 'model_name'       // 模型名错 / 该 Key 无权访问该模型
  | 'api_key'          // Key 无效或无权
  | 'client_build'     // 客户端初始化失败（协议与地址不匹配）
  | 'no_model'         // 未提供模型名
  | 'unknown'

export interface ProbeStep {
  grade: ProbeGrade
  status: ProbeStatus
  /** 一句话结论（后端给了就用后端的，否则用 `probeFallbackSummary`）
   *
   *  后端保证这里**只有中文人话**：异常类名 / JSON / 厂商英文报文一律在 `raw` 里。
   *  所以 UI 可以直接展示 `summary`，但**展示 `raw` 必须标明是技术细节**。 */
  summary: string
  /** 排障原文（异常类名、上游响应体等，截断 200 字，主设计 B.4 硬约束 4） */
  raw?: string
  /** 失败归因代号（仅失败步骤有值） */
  reason?: ProbeReason
  elapsedMs?: number
}

export interface ProbeResult {
  ok: boolean
  steps: ProbeStep[]
  /** 如「可能缺少 /v1 后缀」 */
  suggestion?: string
  /** 后端返回的整体结论；失败时优先展示它，不能只显示「卡在 L2」。 */
  summary?: string
  blocked_at?: ProbeGrade | null
}

// ── 模型目录（填写辅助，**不是探测结果**）───────────────────────────────

export interface ModelCatalogItem {
  id: string
  /** 名称启发式分出的用途，仅用于分组展示；分错也不影响任何判定 */
  kind: ModelKind
}

/**
 * `POST /sys/providers/model-catalog` 的响应。
 *
 * ⚠️ 这里**没有 `blocked_at` / `steps`**，`ok` 的含义也**不是「供应商可用」**，
 * 而是「这次拿到了可用的模型名清单」。拿不到只代表用户要手打模型名 —— 千万不要把
 * `ok === false` 渲染成「供应商不可用」，那正是把原 L1 移出探测链要避免的误解。
 */
export interface ModelCatalogResponse {
  ok: boolean
  status: ProbeStatus
  /** 结论文案，后端保证是中文人话 */
  summary: string
  reason?: ProbeReason | null
  items: ModelCatalogItem[]
  count: number
  /** 厂商声称的总数（原生信封会给），没有则 null */
  total?: number | null
  truncated: boolean
  /** 响应体是否 OpenAI 形状；`false` 说明地址疑似非兼容端点（会连带提示改地址） */
  shape_ok?: boolean | null
  /** 命中服务端短 TTL 缓存 */
  cached: boolean
  provider: string | null
  target: string
  network_scope: string
  draft: boolean
}

export interface ModelCatalogInput {
  baseUrl: string
  apiKey?: string
  networkScope?: 'public' | 'private'
}

export interface ProviderRow {
  id: string
  displayName: string
  driver: 'openai' | 'anthropic' | 'ollama' | 'specialized'
  baseUrl: string
  modelName?: string | null
  modelKind?: ModelKind
  models?: Array<{
    name: string
    display?: string
    modelKind: ModelKind
    /** `user` = 落在数据库里、管理端可移除；`builtin` = 仅代码层登记，移除无意义 */
    source?: 'user' | 'builtin'
    /** 正占用该模型的角色。非空即不可移除 —— 删了会让角色指向不存在的模型 */
    usedByRoles?: string[]
  }>
  networkScope: NetworkScope
  billing: BillingMode
  isBuiltin: boolean
  enabled: boolean
  modelCount: number
  credential: {
    /** 只有布尔，**永不下发密钥**（§7.3 硬约束 1） */
    configured: boolean
    fingerprint: string | null
    last4: string | null
    rotatedAt: string | null
    rotatedBy: string | null
  }
  lastProbe: {
    at: string
    ok: boolean
    worstGrade: ProbeGrade | null
  } | null
}

export type SpecializedRole = 'embedding' | 'rerank'

export interface SpecializedProbeItem {
  role: SpecializedRole
  adapter: string
  ok: boolean
  statusCode?: number | null
  summary: string
  detail: string
  elapsedMs: number
}

export interface SpecializedModelBinding {
  role: SpecializedRole
  modelName: string
  providerId: string
  providerName: string
  adapter: string
  baseUrl: string
  options: Record<string, unknown>
  enabled: boolean
  credential: {
    configured: boolean
    fingerprint?: string | null
    last4?: string | null
  }
  lastProbe?: {
    ok: boolean
    summary?: string | null
    elapsedMs?: number | null
    at?: string | null
  } | null
  requiresReindex: boolean
}

export interface SpecializedModelResponse {
  items: SpecializedModelBinding[]
  adapters: string[]
  /** 专项供应商的根地址；向量 / 重排端点地址单独保存在各 binding 中。 */
  providerBaseUrl?: string
  source: 'db' | 'fallback'
}

export interface SpecializedModelConfigureInput {
  provider: {
    displayName: string
    providerId?: string
    baseUrl: string
    apiKey?: string
    networkScope?: NetworkScope
    billing?: BillingMode
  }
  bindings: Partial<Record<SpecializedRole, {
    modelName: string
    adapter: string
    baseUrl: string
    options?: Record<string, unknown>
  }>>
}

export interface SpecializedConfigureResponse {
  ok: boolean
  saved: boolean
  summary?: string
  provider?: {
    id: string
    displayName: string
    driver: 'specialized'
    baseUrl: string
    credential: {
      configured: boolean
      fingerprint?: string | null
      last4?: string | null
    }
  }
  tests: SpecializedProbeItem[]
  bindings?: Array<{
    role: SpecializedRole
    modelName: string
    adapter: string
    requiresReindex: boolean
  }>
}

/** 供应商清单响应（`GET /sys/providers`，裸 dict 无 Result 壳） */
export interface ProviderListResponse {
  items: ProviderRow[]
  /** `db` = 来自注册表；`builtin` = DB 未就绪时的代码层兜底 */
  source: 'db' | 'builtin'
  actor: string
}

export interface DriftItem {
  severity: 'critical' | 'warn' | 'info'
  kind:
    | 'missing_key'
    | 'unregistered_model'
    | 'db_env_conflict'
    | 'index_model_mismatch'
    | 'unverified_provider'
  subject: string
  message: string
  hint?: string
}

// ── 角色中文名（前端常量，不从后端取；与 backend/config/model_roles.py 的 desc 语义一致）──

/** 模型角色的中文显示名，与 `backend/config/model_roles.py` 的 `MODEL_ROLES` **一一对应**。
 *
 *  后端 `RoleSpec.desc` 是**长说明**，这里是**列名**，两者不混用。
 *  ⚠️ 后端新增角色时必须同批补齐此处 —— 漏补不会报错，只会让表格里直接显示英文代码
 *  （`roleLabel` 回落为 role）。2026-09-21 补齐过 `metadata_extract` / `question_gen` /
 *  `table_describe`，它们此前缺失，导致角色列三行显示英文。
 */
export const ROLE_LABELS: Record<string, string> = {
  main: '主问答模型',
  doc: '文档抽取模型',
  metadata_extract: '元数据抽取模型',
  question_gen: '问题生成模型',
  table_describe: '表格描述模型',
  tool_selector: '工具选择模型',
  fallback: '兜底模型',
  ocr: 'OCR 模型',
  embedding: '向量化模型',
  rerank: '重排模型',
  eval_gen: '评测生成模型',
}

/** 使用专项协议适配器的角色，与后端 `infra/llm/models.py::_SPECIALIZED_MODEL_ROLES` 对齐。
 *
 *  ⚠️ 只有 embedding / rerank —— 后端 `expected_model_kind()` 把 `ocr` 的期望用途判为
 *  `chat`，把它列进这里会让前端以为 ocr 需要视觉模型，与后端校验打架。
 *  角色绑定编辑仍统一从已登记模型目录选择。
 */
export const SPECIALIZED_MODEL_ROLES = new Set(['embedding', 'rerank'])

export function isSpecializedModelRole(role: string): boolean {
  return SPECIALIZED_MODEL_ROLES.has(role)
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}

// ── 角色分组（按业务链路，纯展示用，不参与任何写操作）─────────────────────

export interface RoleGroup {
  id: string
  label: string
  hint: string
  roles: string[]
}

/** 角色按**业务链路**分组；数组顺序即表格渲染顺序。
 *
 *  `roles` 没覆盖到的角色统一落到末位「其他」组 —— 后端新增角色时它不会从界面上消失，
 *  只是暂时没有分组归属（配合 `ROLE_LABELS` 同批补齐即可）。
 */
export const ROLE_GROUPS: RoleGroup[] = [
  {
    id: 'chat',
    label: '问答链路',
    hint: '用户提问到生成答案：主问答、工具选择、熔断兜底',
    roles: ['main', 'tool_selector', 'fallback'],
  },
  {
    id: 'ingest',
    label: '入库链路',
    hint: '文档解析到建索引：抽取、问题生成、表格描述、扫描件 OCR',
    roles: ['doc', 'metadata_extract', 'question_gen', 'table_describe', 'ocr'],
  },
  {
    id: 'retrieve',
    label: '检索链路',
    hint: '向量化与重排，与向量索引的语义空间强绑定',
    roles: ['embedding', 'rerank'],
  },
  {
    id: 'eval',
    label: '评测链路',
    hint: '评测答案生成与 RAGAS 打分',
    roles: ['eval_gen'],
  },
]

/** 未登记进 `ROLE_GROUPS` 的角色所属分组 id。 */
export const OTHER_ROLE_GROUP_ID = 'other'

/** 角色的分组 id；未登记的角色返回 `other`（页面会把它渲染在末位）。 */
export function roleGroupOf(role: string): string {
  return ROLE_GROUPS.find((group) => group.roles.includes(role))?.id ?? OTHER_ROLE_GROUP_ID
}

// ── 一、来源与密钥的展示（§6「不让人猜空值含义」）─────────────────────────

/** 生效来源 → 徽章文案。`inherit` 必须带父角色与父的当前值。 */
export function sourceLabel(
  source: string,
  inheritedFrom: string | null,
  inheritedValue?: string | null,
): string {
  switch (source) {
    case 'db':
      return 'DB 覆盖'
    case 'env':
      return '环境变量'
    case 'default':
      return '代码默认'
    case 'inherit': {
      const parent = inheritedFrom ?? 'main'
      const shown = inheritedValue ? `（当前 = ${inheritedValue}）` : ''
      return `跟随 ${parent}${shown}`
    }
    default:
      // 未知来源按最保守的展示：不猜、也不冒充「代码默认」
      return source || '未知来源'
  }
}

/** 密钥掩码：`****a1b2 · 指纹 3f9c1d`。两侧都缺时返回空串。 */
export function maskSecret(
  last4: string | null | undefined,
  fingerprint: string | null | undefined,
): string {
  const parts: string[] = []
  if (last4) parts.push(`****${last4}`)
  if (fingerprint) parts.push(`指纹 ${fingerprint}`)
  return parts.join(' · ')
}

// ── 二、模型可选性（§6 行内编辑的**唯一**判据）──────────────────────────

export interface ModelOption {
  name: string
  provider: string | null
  modelKind?: ModelKind
  /** 是否在可用模型清单内（后端 `get_available_models()` = 代码层 + DB 动态层） */
  registered: boolean
  /** 缺哪个 Key 环境名（如 `SILICONFLOW_API_KEY`）；null = 齐备 */
  missingKeyEnv: string | null
  availabilityReason?: string | null
}

export interface SelectableVerdict {
  selectable: boolean
  /** 不可选原因 —— 渲染为 `<option disabled>` 的后缀说明（**不是**把它过滤掉） */
  reason: string | null
}

/**
 * 该模型能否被选为某角色的生效值。
 *
 * ⚠️ 不可选的模型**必须仍渲染为 `disabled` 的 option + 原因**，不能过滤掉 ——
 * 否则用户会问「我明明加了模型怎么找不到」（§6 原文）。
 */
export function isModelSelectable(
  model: ModelOption | undefined,
  expectedKind?: ModelKind,
): SelectableVerdict {
  if (!model) {
    return { selectable: false, reason: '未注册' }
  }
  if (!model.registered) {
    return { selectable: false, reason: '未注册' }
  }
  if (expectedKind && model.modelKind && model.modelKind !== expectedKind) {
    return {
      selectable: false,
      reason: `用途不匹配：需要${modelKindLabel(expectedKind)}，当前是${modelKindLabel(model.modelKind)}`,
    }
  }
  if (model.availabilityReason) {
    return { selectable: false, reason: model.availabilityReason }
  }
  if (model.missingKeyEnv) {
    return { selectable: false, reason: `缺少 ${model.missingKeyEnv}` }
  }
  return { selectable: true, reason: null }
}

// ── 三、四级探测的文案（§8.1 —— 每一级失败含义不同，UI 必须把这个差异表达出来）──

/** 级标短名（四级文案的唯一来源）。 */
export function gradeLabel(grade: ProbeGrade): string {
  switch (grade) {
    case 'L0':
      return 'URL 可达'
    case 'L1':
      return '端点清单'
    case 'L2':
      return '模型调用'
    case 'L3':
      return '流式 usage'
    default:
      return grade
  }
}

/**
 * 单级结论的**前端兜底文案**（后端给了 `summary` 就用后端的，见 §5.4）。
 *
 * `fail_degraded` 与 `skip` **绝不可渲染成红色失败** —— B.4 硬约束 1：
 * 判死会让「测试不通过但能用」，按钮从此没人信。
 */
export function probeFallbackSummary(grade: ProbeGrade, status: ProbeStatus): string {
  if (status === 'pass') {
    switch (grade) {
      case 'L0':
        return 'URL 可达（DNS + TLS 正常）'
      case 'L1':
        return '端点响应正常（/models 可用）'
      case 'L2':
        return '模型名可用'
      case 'L3':
        return '流式响应回传 usage'
    }
  }
  if (status === 'fail_degraded') {
    return '该站点未实现端点列表，已跳过（不影响使用）'
  }
  if (status === 'skip') {
    return grade === 'L3'
      ? '未回传 usage，已跳过（记账会缺 token 数）'
      : '已跳过'
  }
  switch (grade) {
    case 'L0':
      return '地址写错或不可达：检查协议 / 端口 / 拼写'
    case 'L1':
      return '端点清单不可用（不影响使用）'
    case 'L2':
      return '模型名错误或该 Key 无权访问'
    case 'L3':
      return '未回传 usage（记账会缺 token 数）'
    default:
      return '未通过'
  }
}

/** 单级探测的最终展示文案：后端原文优先，缺失时按级别兜底。 */
export function probeStepSummary(step: Pick<ProbeStep, 'grade' | 'status' | 'summary'>): string {
  return step.summary.trim() || probeFallbackSummary(step.grade, step.status)
}

/** 失败探测的可读原因，供表格、toast 等入口共用。 */
export function probeFailureReason(result: Pick<ProbeResult, 'ok' | 'steps' | 'summary'>): string {
  if (result.ok) return ''
  if (result.summary?.trim()) return result.summary.trim()
  const failed = result.steps.find((step) => step.status === 'fail')
  return failed ? probeStepSummary(failed) : '探测未通过'
}

/** 结论一句话（§8.2）：通过 / 未通过（卡在哪一级）。短路级 = 第一个 `fail`。 */
export function probeOverallLabel(result: {
  ok: boolean
  steps: Array<{ grade: ProbeGrade; status: ProbeStatus }>
}): string {
  if (result.ok) return '厂商连通性通过'
  const blocked = result.steps.find((s) => s.status === 'fail')
  return blocked ? `未通过（卡在 ${blocked.grade}）` : '未通过'
}

// ── 四、变更历史的脱敏（§3.4 硬边界 3 的收敛点）─────────────────────────

export interface ConfigHistoryEntry {
  id: string
  object: 'role' | 'provider' | 'provider_credential' | 'provider_network_scope'
  /** role 名 / provider id */
  key: string
  oldValue: string | null
  newValue: string | null
  operator: string
  at: string
  /** 密钥类条目后端只回指纹 */
  secretFingerprint?: string | null
  rollbackable: boolean
}

export interface RedactedHistoryEntry {
  /** 展示用主文案（已脱敏） */
  text: string
  /** 本条是否发生了脱敏（用于渲染锁图标 / 提示） */
  redacted: boolean
}

/**
 * 历史条目的展示文案，**密钥类永不外露任何值**。
 *
 * 为什么 `canAdmin` 在当前契约下看似无用：密钥类（`provider_credential`）对 admin 与
 * editor **都是**「只给指纹」—— 数据库里本就没有可展示的值。保留 `canAdmin` 是**安全网**：
 * 将来新增带秘密的对象类型时，非 admin 分支默认隐藏值，而不是等发现泄漏再补。
 */
export function redactForRole(
  entry: ConfigHistoryEntry,
  canAdmin: boolean,
): RedactedHistoryEntry {
  switch (entry.object) {
    case 'provider_credential':
      return {
        text: `${entry.key}: 密钥已轮换（指纹 ${entry.secretFingerprint || '—'}）`,
        redacted: true,
      }
    case 'provider_network_scope':
      return {
        text: `${entry.key}: network_scope ${entry.oldValue ?? '—'} → ${entry.newValue ?? '—'}`,
        redacted: false,
      }
    case 'role':
    case 'provider':
      return {
        text: `${entry.key}: ${entry.oldValue ?? '—'} → ${entry.newValue ?? '—'}`,
        redacted: false,
      }
    default:
      // 未知对象类型：非 admin 一律不展示值
      return canAdmin
        ? {
            text: `${entry.key}: ${entry.oldValue ?? '—'} → ${entry.newValue ?? '—'}`,
            redacted: false,
          }
        : { text: `${entry.key}: 已变更`, redacted: true }
  }
}

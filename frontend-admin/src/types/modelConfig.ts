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
  requiresReindex: boolean
  updatedBy: string | null
  updatedAt: string | null
}

export type BillingMode = 'metered' | 'subscription' | 'local'
export type NetworkScope = 'public' | 'private'

export type ProbeGrade = 'L0' | 'L1' | 'L2' | 'L3'
export type ProbeStatus = 'pass' | 'fail' | 'skip' | 'fail_degraded'

export interface ProbeStep {
  grade: ProbeGrade
  status: ProbeStatus
  /** 一句话结论（后端给了就用后端的，否则用 `probeFallbackSummary`） */
  summary: string
  /** 原文摘要（截断 200 字，主设计 B.4 硬约束 4） */
  raw?: string
  elapsedMs?: number
}

export interface ProbeResult {
  ok: boolean
  steps: ProbeStep[]
  /** 如「可能缺少 /v1 后缀」 */
  suggestion?: string
}

export interface ProviderRow {
  id: string
  displayName: string
  driver: 'openai' | 'anthropic' | 'ollama'
  baseUrl: string
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

/** 8 个模型角色的中文显示名。后端 `RoleSpec.desc` 是**长说明**，这里是**列名**，两者不混用。 */
export const ROLE_LABELS: Record<string, string> = {
  main: '主问答模型',
  doc: '文档抽取模型',
  tool_selector: '工具选择模型',
  fallback: '兜底模型',
  ocr: 'OCR 模型',
  embedding: '向量化模型',
  rerank: '重排模型',
  eval_gen: '评测生成模型',
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
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

/** 密钥掩码：`····a1b2 · 指纹 3f9c1d`。两侧都缺时返回空串（调用方渲染「未配置」）。 */
export function maskSecret(
  last4: string | null | undefined,
  fingerprint: string | null | undefined,
): string {
  const parts: string[] = []
  if (last4) parts.push(`····${last4}`)
  if (fingerprint) parts.push(`指纹 ${fingerprint}`)
  return parts.join(' · ')
}

// ── 二、模型可选性（§6 行内编辑的**唯一**判据）──────────────────────────

export interface ModelOption {
  name: string
  provider: string | null
  /** 是否在可用模型清单内（后端 `get_available_models()` = 代码层 + DB 动态层） */
  registered: boolean
  /** 缺哪个 Key 环境名（如 `SILICONFLOW_API_KEY`）；null = 齐备 */
  missingKeyEnv: string | null
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
export function isModelSelectable(model: ModelOption | undefined): SelectableVerdict {
  if (!model) {
    return { selectable: false, reason: '未注册' }
  }
  if (!model.registered) {
    return { selectable: false, reason: '未注册' }
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

/** 供应商编辑草稿类型与工厂（从 ProvidersTab.tsx B4 拆分迁出）。 */
import type { ProbeResponse } from '@/api/modelConfig'
import type { ModelKind, ProviderRow } from '@/types/modelConfig'

export type Draft = {
  id: string | null
  displayName: string
  driver: ProviderRow['driver']
  baseUrl: string
  modelName: string
  modelKind: ModelKind
  networkScope: ProviderRow['networkScope']
  billing: ProviderRow['billing']
  enabled: boolean
  apiKey?: string
  clearApiKey?: boolean
  originalBaseUrl: string
  originalModelName: string
  originalModelKind: ModelKind
  credentialConfigured: boolean
  keyLast4: string | null
  /** 附加请求体的预设选择：'none' | 'thinking-off' | 'low-effort' | 'custom' */
  extraBodyPreset: ExtraBodyPreset
  /** 自定义 JSON 原文（仅 preset='custom' 时参与提交）。
   *  存字符串而非对象：非法 JSON 要就地报错，不能等到保存才炸。 */
  extraBodyText: string
  /** 打开抽屉时库里的原值（用于「未改动 = 不提交」判定，避免误覆盖） */
  originalExtraBody: Record<string, unknown>
}

/** 附加请求体预设。覆盖主流供应商的关闭思考参数名差异：
 *  火山方舟 thinking / 通义 enable_thinking / OpenAI 系 reasoning_effort。 */
export type ExtraBodyPreset = 'none' | 'thinking-off' | 'low-effort' | 'custom'

export const EXTRA_BODY_PRESETS: Array<{
  value: ExtraBodyPreset
  label: string
  hint: string
  /** 生成提交值；custom 由文本框决定，故为 null */
  build: (() => Record<string, unknown>) | null
}> = [
  {
    value: 'none',
    label: '不附加（默认）',
    hint: '按供应商默认行为调用，不添加任何额外字段。',
    build: () => ({}),
  },
  {
    value: 'thinking-off',
    label: '关闭思考（火山方舟）',
    hint: '传 {"thinking":{"type":"disabled"}}。实测思考占生成 token 的 73%，关闭后回答更快。',
    build: () => ({ thinking: { type: 'disabled' } }),
  },
  {
    value: 'low-effort',
    label: '低推理强度（OpenAI 系）',
    hint: '传 {"reasoning_effort":"low"}。保留少量推理但显著降低 token 与延迟。',
    build: () => ({ reasoning_effort: 'low' }),
  },
  {
    value: 'custom',
    label: '自定义 JSON',
    hint: '手写附加字段。保留字段（model/stream/tools/api_key 等）会被服务端拒绝。',
    build: null,
  },
]

/** 从库中原值反推预设与文本（打开抽屉时用）。 */
export function extraBodyToDraft(value: Record<string, unknown> | undefined): {
  preset: ExtraBodyPreset
  text: string
} {
  const obj = value ?? {}
  if (Object.keys(obj).length === 0) return { preset: 'none', text: '' }
  for (const item of EXTRA_BODY_PRESETS) {
    if (!item.build) continue
    if (JSON.stringify(item.build()) === JSON.stringify(obj)) {
      return { preset: item.value, text: '' }
    }
  }
  return { preset: 'custom', text: JSON.stringify(obj, null, 2) }
}

/** 把预设/文本解析为提交值。返回错误信息表示无法提交。 */
export function resolveExtraBody(
  preset: ExtraBodyPreset,
  text: string,
): { value: Record<string, unknown> } | { error: string } {
  if (preset !== 'custom') {
    const item = EXTRA_BODY_PRESETS.find((p) => p.value === preset)
    return { value: item?.build ? item.build() : {} }
  }
  const raw = text.trim()
  if (!raw) return { value: {} }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { error: '自定义附加字段不是合法 JSON' }
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { error: '自定义附加字段必须是 JSON 对象（形如 {"key": "value"}）' }
  }
  return { value: parsed as Record<string, unknown> }
}

export type ProbeStatus = ProbeResponse['steps'][number]['status']

export type ModelDraft = {
  provider: ProviderRow
  modelName: string
  modelKind: ModelKind
  error: string | null
  /** 按量计费单价（每 1M tokens），表单原始字符串；仅 metered 供应商显示 */
  inputPrice: string
  outputPrice: string
  /** 缓存命中单价（可选）：空串 = 未配置（NULL）；'0' = 明确免费 */
  cachedInputPrice: string
  priceCurrency: 'CNY' | 'USD'
  /** 上游模型名（可选）：空 = 与登记名相同；发给厂商 API 的真实名字 */
  upstreamName: string
  /** true = 从既有模型行进入（更新/改价），后端按同供应商 upsert 处理 */
  editingExisting: boolean
}

/** 移除模型前的确认态。后端对内置/被占用的模型会回 409，理由直接展示。 */
export type ModelRemoval = {
  provider: ProviderRow
  name: string
  modelKind: ModelKind
  busy: boolean
  error: string | null
}

/** 该模型能否被移除，以及不能的原因（用于就地禁用按钮并说明）。
 *
 *  §B.15 起清单 DB-only：后端恒回 `source='user'`，「代码层内置不可移除」
 *  分支已退役 —— 不能移除的唯一原因是被角色占用。
 */
export function modelRemovalBlockReason(model: {
  source?: 'user' | 'builtin'
  usedByRoles?: string[]
}): string | null {
  const roles = model.usedByRoles ?? []
  if (roles.length) return `正被角色 ${roles.join('、')} 使用，需先改绑`
  return null
}

export function draftFromRow(
  row: ProviderRow,
  defaultModels: Record<string, string>,
): Draft {
  const modelName = row.modelName || defaultModels[row.id] || ''
  const modelKind = row.modelKind || row.models?.find((item) => item.name === modelName)?.modelKind || 'chat'
  return {
    id: row.id,
    displayName: row.displayName,
    driver: row.driver,
    baseUrl: row.baseUrl,
    modelName,
    modelKind,
    networkScope: row.networkScope,
    billing: row.billing,
    enabled: row.enabled,
    originalBaseUrl: row.baseUrl,
    originalModelName: modelName,
    originalModelKind: modelKind,
    credentialConfigured: row.credential.configured,
    keyLast4: row.credential.last4,
    ...(() => {
      const { preset, text } = extraBodyToDraft(row.extraBody)
      return {
        extraBodyPreset: preset,
        extraBodyText: text,
        originalExtraBody: row.extraBody ?? {},
      }
    })(),
  }
}

export function newDraft(): Draft {
  return {
    id: null,
    displayName: '',
    driver: 'openai',
    baseUrl: '',
    modelName: '',
    modelKind: 'chat',
    networkScope: 'public',
    billing: 'metered',
    enabled: true,
    originalBaseUrl: '',
    originalModelName: '',
    originalModelKind: 'chat',
    credentialConfigured: false,
    keyLast4: null,
    extraBodyPreset: 'none',
    extraBodyText: '',
    originalExtraBody: {},
  }
}

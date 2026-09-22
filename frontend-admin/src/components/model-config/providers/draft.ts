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
  }
}

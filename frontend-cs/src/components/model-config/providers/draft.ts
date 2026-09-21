/** 供应商编辑草稿类型与工厂（从 ProvidersTab.tsx B4 拆分迁出）。 */
import type { ProbeResponse } from '@/api/modelConfig'
import type { ModelKind, PlanId, PresetPlan, ProviderPreset, ProviderRow } from '@/types/modelConfig'
import { billingForPlan, findPresetForRow } from './presets'

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
  /** 计费计划（三选一）。`''` = 未套用预置（自建 / 内网 / 目录不可用）。 */
  plan: PlanId | ''
  /** 命中的预置条目 id；`null` = 自定义或未命中。 */
  presetId: string | null
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
  presets: ProviderPreset[],
  plans: PresetPlan[],
): Draft {
  const modelName = row.modelName || defaultModels[row.id] || ''
  const modelKind = row.modelKind || row.models?.find((item) => item.name === modelName)?.modelKind || 'chat'
  // 计划与厂商由（协议, base_url）反查回填。库未就绪或地址是自建时反查落空，
  // 此时 plan='' / presetId=null，界面显示「未套用预置」，不假装它属于某家厂商。
  const matched = findPresetForRow(row, presets, plans)
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
    plan: matched?.plan ?? '',
    presetId: matched?.id ?? null,
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
    plan: '',
    presetId: null,
    originalBaseUrl: '',
    originalModelName: '',
    originalModelKind: 'chat',
    credentialConfigured: false,
    keyLast4: null,
  }
}

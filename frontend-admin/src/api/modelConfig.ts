import { mutationRequest, request } from '@/api/client'
import type {
  ConfigHistoryEntry,
  DriftItem,
  ModelKind,
  ProbeResult,
  ProviderListResponse,
  ProviderPresetsResponse,
  ProviderRow,
  RoleBinding,
  SpecializedConfigureResponse,
  SpecializedModelConfigureInput,
  SpecializedModelResponse,
} from '@/types/modelConfig'

export interface ModelRoleListResponse {
  items: RoleBinding[]
  actor?: string
}

export interface ModelCatalogEntry {
  name: string
  provider: string
  modelKind?: ModelKind
  display?: string
  description?: string
  source?: string
  available?: boolean
  availabilityReason?: string | null
}

export interface ProviderUpdateInput {
  displayName: string
  driver: ProviderRow['driver']
  baseUrl: string
  networkScope?: ProviderRow['networkScope']
  billing?: ProviderRow['billing']
  enabled?: boolean
  modelName?: string
  modelKind?: ModelKind
  extraHeaders?: Record<string, string>
  /** 不传 = 保留旧密钥；非空 = 轮换；clearApiKey = 清除。 */
  apiKey?: string
  clearApiKey?: boolean
}

export interface ProviderCreateInput {
  displayName?: string
  driver?: ProviderRow['driver']
  baseUrl: string
  modelName: string
  modelKind?: ModelKind
  networkScope?: ProviderRow['networkScope']
  billing?: ProviderRow['billing']
  enabled?: boolean
  extraHeaders?: Record<string, string>
  apiKey: string
}

export interface ProviderModelCreateInput {
  modelName: string
  modelKind: ModelKind
}

export interface DraftProbeInput {
  driver: ProviderRow['driver']
  baseUrl: string
  modelName: string
  modelKind?: ModelKind
  apiKey?: string
  networkScope?: ProviderRow['networkScope']
}

export type ProbeMode = 'fast' | 'full'

export interface ProbeOptions {
  mode?: ProbeMode
}

export interface ConfigHistoryResponse {
  items: ConfigHistoryEntry[]
}

export interface DriftResponse {
  items: DriftItem[]
  checkedAt?: string
}

export interface ProbeResponse extends ProbeResult {
  provider: string | null
  target: string
  network_scope: string
  draft: boolean
  mode?: ProbeMode
  summary?: string
}

function normalizeProbeResponse(input: Record<string, any>): ProbeResponse {
  return {
    ok: Boolean(input.ok),
    provider: input.provider ?? null,
    target: String(input.target ?? ''),
    network_scope: String(input.network_scope ?? 'public'),
    draft: Boolean(input.draft),
    mode: input.mode === 'full' || input.mode === 'fast' ? input.mode : undefined,
    summary: typeof input.summary === 'string' ? input.summary : undefined,
    blocked_at: input.blocked_at,
    steps: (Array.isArray(input.steps) ? input.steps : []).map((step: Record<string, any>) => ({
      grade: step.grade ?? step.level,
      status: step.status,
      summary: String(step.summary ?? ''),
      raw: step.raw ?? step.detail,
      elapsedMs: step.elapsedMs ?? step.elapsed_ms,
    })),
  } as ProbeResponse
}

export async function listModelRoles(): Promise<ModelRoleListResponse> {
  return request<ModelRoleListResponse>('/api/sys/model-roles')
}

export async function listModelCatalog(): Promise<{ models: ModelCatalogEntry[]; current: string }> {
  return request<{ models: ModelCatalogEntry[]; current: string }>('/api/llm/models')
}

export async function saveModelRole(role: string, modelName: string): Promise<Record<string, unknown>> {
  return mutationRequest<Record<string, unknown>>(`/api/sys/model-roles/${encodeURIComponent(role)}`, {
    operation: `model-role:${role}`,
    method: 'PUT',
    body: { modelName },
  })
}

export async function listProviders(): Promise<ProviderListResponse> {
  return request<ProviderListResponse>('/api/sys/providers')
}

/**
 * 预置端点目录（新增/编辑抽屉的「厂商 · 协议」候选）。
 *
 * 静态参考数据，只读；**不注入幂等键**（不是写操作）。
 * 返回空是合法状态（后端未部署该端点时），调用方需按「目录不可用」
 * 降级到手填 Base URL，而不是把抽屉做成死的。
 */
export async function listProviderPresets(): Promise<ProviderPresetsResponse> {
  return request<ProviderPresetsResponse>('/api/sys/providers/presets')
}

export async function saveProvider(providerId: string, body: ProviderUpdateInput): Promise<ProviderRow> {
  return mutationRequest<ProviderRow>(`/api/sys/providers/${encodeURIComponent(providerId)}`, {
    operation: `model-provider:${providerId}`,
    method: 'PUT',
    body,
  })
}

export async function createProvider(body: ProviderCreateInput): Promise<ProviderRow> {
  return mutationRequest<ProviderRow>('/api/sys/providers', {
    operation: 'model-provider-create',
    method: 'POST',
    body,
  })
}

export async function addProviderModel(
  providerId: string,
  body: ProviderModelCreateInput,
): Promise<Record<string, unknown>> {
  return mutationRequest<Record<string, unknown>>(
    `/api/sys/providers/${encodeURIComponent(providerId)}/models`,
    {
      operation: `model-provider-model-create:${providerId}:${body.modelName}`,
      method: 'POST',
      body,
      timeout: 60000,
    },
  )
}

/** 移除供应商下的自建模型条目。
 *
 * 模型名走 query 参数：`Qwen/Qwen3-32B` 这类名字自带斜杠，放进路径段会被拆开。
 * 内置模型、被角色/专项/价格占用的模型后端会返回 409，原因在错误体里，直接展示即可。
 */
export async function removeProviderModel(
  providerId: string,
  modelName: string,
): Promise<Record<string, unknown>> {
  return mutationRequest<Record<string, unknown>>(
    `/api/sys/providers/${encodeURIComponent(providerId)}/models?modelName=${encodeURIComponent(modelName)}`,
    {
      operation: `model-provider-model-remove:${providerId}:${modelName}`,
      method: 'DELETE',
      timeout: 30000,
    },
  )
}

export async function verifyProvider(providerId: string, options: ProbeOptions = {}): Promise<ProbeResponse> {
  const mode = options.mode ?? 'fast'
  const result = await mutationRequest<Record<string, any>>(`/api/sys/providers/${encodeURIComponent(providerId)}/verify?mode=${mode}`, {
    operation: `model-provider-verify:${providerId}:${mode}`,
    method: 'POST',
    timeout: mode === 'full' ? 60000 : 45000,
  })
  return normalizeProbeResponse(result)
}

export async function verifyDraftProvider(body: DraftProbeInput, options: ProbeOptions = {}): Promise<ProbeResponse> {
  const mode = options.mode ?? 'fast'
  const result = await mutationRequest<Record<string, any>>(`/api/sys/providers/verify-draft?mode=${mode}`, {
    operation: `model-provider-verify-draft:${mode}`,
    method: 'POST',
    body,
    timeout: mode === 'full' ? 60000 : 45000,
  })
  return normalizeProbeResponse(result)
}

export async function listSpecializedModels(): Promise<SpecializedModelResponse> {
  return request<SpecializedModelResponse>('/api/sys/specialized-models')
}

export async function testAndSaveSpecialized(
  body: SpecializedModelConfigureInput,
): Promise<SpecializedConfigureResponse> {
  const result = await mutationRequest<SpecializedConfigureResponse>('/api/sys/specialized-models/test-and-save', {
    operation: 'specialized-models:test-and-save',
    method: 'POST',
    body,
    timeout: 60000,
  })
  return {
    ...result,
    tests: (result.tests ?? []).map((item) => ({
      ...item,
      elapsedMs: Number(item.elapsedMs ?? 0),
    })),
  }
}

export async function listConfigHistory(object?: ConfigHistoryEntry['object']): Promise<ConfigHistoryResponse> {
  const query = object ? `?object=${encodeURIComponent(object)}` : ''
  return request<ConfigHistoryResponse>(`/api/sys/config/history${query}`)
}

export async function rollbackConfigHistory(id: string): Promise<Record<string, unknown>> {
  return mutationRequest<Record<string, unknown>>(`/api/sys/config/history/${encodeURIComponent(id)}/rollback`, {
    operation: `config-history-rollback:${id}`,
    method: 'POST',
  })
}

export async function getConfigDrift(): Promise<DriftResponse> {
  return request<DriftResponse>('/api/sys/config/drift')
}

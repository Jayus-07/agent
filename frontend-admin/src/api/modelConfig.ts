import { mutationRequest, request } from '@/api/client'
import type {
  ConfigHistoryEntry,
  DriftItem,
  ModelCatalogInput,
  ModelCatalogResponse,
  ModelHealth,
  ModelKind,
  ProbeResult,
  ProviderListResponse,
  ProviderRow,
  RoleBinding,
  RolePolicy,
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
  /** 按量计费单价（每 1M tokens）。登记即生效，同步写入计费表 */
  inputPricePer1m?: number
  outputPricePer1m?: number
  /** 缓存命中单价（每 1M tokens，可选）。不传 = 未配置（NULL），运行时判 estimated */
  cachedInputPricePer1m?: number
  priceCurrency?: 'CNY' | 'USD'
  /** 上游模型名（可选）：发给厂商 API 的真实名字，缺省 = 与登记名相同 */
  upstreamModelName?: string
}

export interface DraftProbeInput {
  driver: ProviderRow['driver']
  baseUrl: string
  modelName: string
  modelKind?: ModelKind
  apiKey?: string
  networkScope?: ProviderRow['networkScope']
  /** 已登记供应商 ID：留空 apiKey 时服务端用其托管凭据测试（明文不下发） */
  providerId?: string
}

export type ProbeMode = 'fast' | 'full'

export interface ProbeOptions {
  mode?: ProbeMode
  /** 模型级测试：只测这一个模型。后端据此解析模型用途与专项协议地址。 */
  modelName?: string
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
  /** 这次探测用的模型名（模型级测试时=点按钮的那个模型；否则为供应商第一个模型） */
  model?: string
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
    model: typeof input.model === 'string' ? input.model : undefined,
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
      reason: step.reason ?? undefined,
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

/** 管理员手动触发单模型健康探测（同步执行一次极低成本探测，结果写缓存）。
 *
 *  后端 `POST /sys/model-health/check`：模型未在注册表登记时返回 404。
 */
export async function checkModelHealth(
  modelName: string,
): Promise<{ status: string; latencyMs?: number; error?: string }> {
  return mutationRequest<{ status: string; latencyMs?: number; error?: string }>(
    '/api/sys/model-health/check',
    {
      operation: `model-health-check:${modelName}`,
      method: 'POST',
      body: { modelName },
      timeout: 30000,
    },
  )
}

/** 角色运行策略写入（治理改造 2026-09-22；后端硬校验 embedding 禁止 fallback）。 */
export async function saveModelRolePolicy(
  role: string,
  body: Omit<RolePolicy, 'role' | 'source' | 'updatedBy' | 'updatedAt'>,
): Promise<Record<string, unknown>> {
  return mutationRequest<Record<string, unknown>>(
    `/api/sys/model-roles/${encodeURIComponent(role)}/policy`,
    {
      operation: `model-role-policy:${role}`,
      method: 'PUT',
      body: {
        fallbackModel: body.fallbackModel,
        timeoutSeconds: body.timeoutSeconds,
        maxRetries: body.maxRetries,
        failurePolicy: body.failurePolicy,
      },
    },
  )
}

/** 模型健康缓存（beat 周期探测；本端点不在线探测）。 */
export async function listModelHealth(): Promise<{ items: ModelHealth[] }> {
  return request<{ items: ModelHealth[] }>('/api/sys/model-health')
}

export async function listProviders(): Promise<ProviderListResponse> {
  return request<ProviderListResponse>('/api/sys/providers')
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

/** 删除自定义供应商（名下没有被角色绑定的模型即可删，未绑定模型级联删除）。
 *
 * 内置供应商与被角色/专项/价格占用的供应商后端返回 409，原因在错误体里，直接展示即可。
 */
export async function deleteProvider(providerId: string): Promise<Record<string, unknown>> {
  return mutationRequest<Record<string, unknown>>(
    `/api/sys/providers/${encodeURIComponent(providerId)}`,
    {
      operation: `model-provider-delete:${providerId}`,
      method: 'DELETE',
      timeout: 30000,
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
  const modelNameQuery = options.modelName ? `&model_name=${encodeURIComponent(options.modelName)}` : ''
  const result = await mutationRequest<Record<string, any>>(`/api/sys/providers/${encodeURIComponent(providerId)}/verify?mode=${mode}${modelNameQuery}`, {
    operation: `model-provider-verify:${providerId}:${mode}${options.modelName ? `:${options.modelName}` : ''}`,
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

/** 拉取**草稿态**供应商的模型名清单（`GET {base}/models`，后端带短 TTL 缓存）。
 *
 *  与 `verifyDraftProvider` 的区别是本质性的：那个回答「能不能用」，这个只回答
 *  「有哪些模型名可以填」。**拿不到清单不代表供应商不可用** —— 它只意味着用户得
 *  手打模型名，所以这里失败时不要在 UI 上渲染成"测试失败"。
 */
export async function fetchModelCatalog(body: ModelCatalogInput): Promise<ModelCatalogResponse> {
  const result = await mutationRequest<Record<string, any>>('/api/sys/providers/model-catalog', {
    operation: 'model-provider-catalog-draft',
    method: 'POST',
    body,
    timeout: 30000,
  })
  return normalizeCatalogResponse(result)
}

/** 拉取**已在库实例**的模型名清单（用库里保存的地址与密钥，密钥不回显）。 */
export async function fetchProviderModelCatalog(providerId: string): Promise<ModelCatalogResponse> {
  const result = await mutationRequest<Record<string, any>>(
    `/api/sys/providers/${encodeURIComponent(providerId)}/model-catalog`,
    {
      operation: `model-provider-catalog:${providerId}`,
      method: 'POST',
      timeout: 30000,
    },
  )
  return normalizeCatalogResponse(result)
}

function normalizeCatalogResponse(input: Record<string, any>): ModelCatalogResponse {
  const items = Array.isArray(input.items) ? input.items : []
  return {
    ok: Boolean(input.ok),
    status: input.status === 'pass' ? 'pass' : input.status === 'skip' ? 'skip' : input.status === 'fail_degraded' ? 'fail_degraded' : 'fail',
    summary: String(input.summary ?? ''),
    reason: input.reason ?? null,
    items: items
      .filter((item: Record<string, any>) => item && typeof item.id === 'string' && item.id)
      .map((item: Record<string, any>) => ({ id: String(item.id), kind: (item.kind ?? 'chat') as ModelKind })),
    count: Number(input.count ?? items.length ?? 0),
    total: typeof input.total === 'number' ? input.total : null,
    truncated: Boolean(input.truncated),
    shape_ok: typeof input.shape_ok === 'boolean' ? input.shape_ok : null,
    cached: Boolean(input.cached),
    provider: input.provider ?? null,
    target: String(input.target ?? ''),
    network_scope: String(input.network_scope ?? 'public'),
    draft: Boolean(input.draft),
  }
}

/** 专项模型（embedding/rerank）供应商配置的两个端点封装。
 *
 *  ⚠️ 当前**无 UI 调用方** —— 承载它的 `SpecializedModelsCard.tsx` 已于 2026-09-21 删除，
 *  专项能力改由供应商页统一登记与测试。这里的封装与类型保留（后端端点仍在、api 测试仍覆盖），
 *  但若长期无人调用，应当连同后端端点一起决策，不要只删前端这一侧。
 */
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

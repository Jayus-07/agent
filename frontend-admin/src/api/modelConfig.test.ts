import { describe, expect, it, vi } from 'vitest'
import { mutationRequest, request } from '@/api/client'
import {
  getConfigDrift,
  listConfigHistory,
  listModelRoles,
  listProviders,
  createProvider,
  addProviderModel,
  rollbackConfigHistory,
  saveModelRole,
  saveProvider,
  verifyProvider,
  verifyDraftProvider,
  listSpecializedModels,
  testAndSaveSpecialized,
} from './modelConfig'

vi.mock('@/api/client', () => ({
  request: vi.fn(),
  mutationRequest: vi.fn(),
}))

describe('模型配置治理 API', () => {
  it('使用裸 dict 读取角色与供应商', async () => {
    vi.mocked(request).mockResolvedValueOnce({ items: [] }).mockResolvedValueOnce({ items: [], source: 'db', actor: 'u' })

    await listModelRoles()
    await listProviders()

    expect(request).toHaveBeenNthCalledWith(1, '/api/sys/model-roles')
    expect(request).toHaveBeenNthCalledWith(2, '/api/sys/providers')
  })

  it('角色与供应商写入使用 PUT、路径参数和幂等 mutation', async () => {
    vi.mocked(mutationRequest).mockResolvedValue({})
    await saveModelRole('main', 'qwen3.7-plus')
    await saveProvider('qwen', { displayName: 'Qwen', driver: 'openai', baseUrl: '' })

    expect(mutationRequest).toHaveBeenNthCalledWith(1, '/api/sys/model-roles/main', expect.objectContaining({
      operation: 'model-role:main', method: 'PUT', body: { modelName: 'qwen3.7-plus' },
    }))
    expect(mutationRequest).toHaveBeenNthCalledWith(2, '/api/sys/providers/qwen', expect.objectContaining({
      operation: 'model-provider:qwen', method: 'PUT',
    }))
  })

  it('新增供应商只提交 URL、Key 和模型名', async () => {
    vi.mocked(mutationRequest).mockResolvedValue({})
    await createProvider({
      baseUrl: 'https://example.com/v1',
      apiKey: 'sk-secret',
      modelName: 'custom-model',
    })

    expect(mutationRequest).toHaveBeenCalledWith('/api/sys/providers', expect.objectContaining({
      operation: 'model-provider-create',
      method: 'POST',
      body: {
        baseUrl: 'https://example.com/v1',
        apiKey: 'sk-secret',
        modelName: 'custom-model',
      },
    }))
  })

  it('追加模型带用途类型并走供应商模型端点', async () => {
    vi.mocked(mutationRequest).mockResolvedValue({})
    await addProviderModel('custom-api', {
      modelName: 'text-embedding-3-large',
      modelKind: 'embedding',
    })

    expect(mutationRequest).toHaveBeenCalledWith(
      '/api/sys/providers/custom-api/models',
      expect.objectContaining({
        operation: 'model-provider-model-create:custom-api:text-embedding-3-large',
        method: 'POST',
        body: { modelName: 'text-embedding-3-large', modelKind: 'embedding' },
      }),
    )
  })

  it('历史、漂移和草稿探测走设计中的路径', async () => {
    vi.mocked(request).mockResolvedValue({ items: [] })
    vi.mocked(mutationRequest).mockResolvedValue({ ok: true })

    await listConfigHistory('role')
    await getConfigDrift()
    await rollbackConfigHistory('9')
    await verifyDraftProvider({ driver: 'openai', baseUrl: 'https://example.invalid/v1', modelName: 'm' })

    expect(request).toHaveBeenCalledWith('/api/sys/config/history?object=role')
    expect(request).toHaveBeenCalledWith('/api/sys/config/drift')
    expect(mutationRequest).toHaveBeenCalledWith('/api/sys/config/history/9/rollback', expect.any(Object))
    expect(mutationRequest).toHaveBeenCalledWith('/api/sys/providers/verify-draft?mode=fast', expect.objectContaining({ method: 'POST' }))
  })

  it('探测默认走快速模式，完整模式提高前端超时上限', async () => {
    vi.mocked(mutationRequest).mockResolvedValue({ ok: true, steps: [] })

    await verifyProvider('siliconflow')
    await verifyDraftProvider(
      { driver: 'openai', baseUrl: 'https://example.invalid/v1', modelName: 'm' },
      { mode: 'full' },
    )

    expect(mutationRequest).toHaveBeenCalledWith(
      '/api/sys/providers/siliconflow/verify?mode=fast',
      expect.objectContaining({
        operation: 'model-provider-verify:siliconflow:fast',
        method: 'POST',
        timeout: 45000,
      }),
    )
    expect(mutationRequest).toHaveBeenCalledWith(
      '/api/sys/providers/verify-draft?mode=full',
      expect.objectContaining({
        operation: 'model-provider-verify-draft:full',
        method: 'POST',
        timeout: 60000,
      }),
    )
  })

  it('专项模型使用统一适配器配置接口并保留耗时', async () => {
    vi.mocked(request).mockResolvedValue({ items: [], adapters: [] })
    vi.mocked(mutationRequest).mockResolvedValue({
      ok: false,
      saved: false,
      tests: [{ role: 'rerank', ok: false, elapsedMs: 321, detail: 'Key 无效' }],
    })

    await listSpecializedModels()
    const result = await testAndSaveSpecialized({
      provider: {
        displayName: '阿里云百炼专项',
        baseUrl: 'https://dashscope.aliyuncs.com',
        apiKey: 'sk-test-placeholder',
      },
      bindings: {
        embedding: {
          modelName: 'qwen3.7-text-embedding',
          adapter: 'dashscope_embedding',
          baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
          options: { dimensions: 1024 },
        },
      },
    })

    expect(request).toHaveBeenCalledWith('/api/sys/specialized-models')
    expect(mutationRequest).toHaveBeenCalledWith(
      '/api/sys/specialized-models/test-and-save',
      expect.objectContaining({
        operation: 'specialized-models:test-and-save',
        method: 'POST',
      }),
    )
    expect(result.tests[0].elapsedMs).toBe(321)
  })
})

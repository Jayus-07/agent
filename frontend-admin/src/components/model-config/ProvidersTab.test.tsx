import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const apiMock = vi.hoisted(() => ({
  createProvider: vi.fn(),
  addProviderModel: vi.fn(),
  saveProvider: vi.fn(),
  verifyProvider: vi.fn(),
  verifyDraftProvider: vi.fn(),
}))

vi.mock('@/api/modelConfig', () => apiMock)
vi.mock('@/components/shared/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}))

import ProvidersTab from './ProvidersTab'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(configured = false, specialized = false) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(
      <ProvidersTab
        providers={[{
        id: 'qwen_tp',
        displayName: 'Qwen Token Plan',
        driver: 'openai',
        baseUrl: 'https://token-plan.example/v1',
        networkScope: 'public',
        billing: 'subscription',
        isBuiltin: true,
        enabled: true,
        modelCount: 5,
        modelName: 'qwen3.7-plus',
        modelKind: 'chat',
        models: [
          { name: 'qwen3.7-plus', modelKind: 'chat' },
          { name: 'qwen3.7-text-embedding', modelKind: 'embedding' },
          { name: 'qwen3.7-text-rerank', modelKind: 'rerank' },
          { name: 'qwen3.7-vl', modelKind: 'vision' },
          { name: 'qwen3.7-voice', modelKind: 'speech' },
        ],
        credential: {
          configured,
          fingerprint: configured ? 'abc123' : null,
          last4: configured ? 'a1b2' : null,
          rotatedAt: null,
          rotatedBy: null,
        },
         lastProbe: null,
       }, ...(specialized ? [{
         id: 'specialized-api',
         displayName: '阿里云百炼专项',
         driver: 'specialized' as const,
         baseUrl: 'https://dashscope.aliyuncs.com',
         networkScope: 'public' as const,
         billing: 'metered' as const,
         isBuiltin: false,
         enabled: true,
         modelCount: 2,
         modelName: 'qwen3.7-text-embedding',
         modelKind: 'embedding' as const,
         models: [
           { name: 'qwen3.7-text-embedding', modelKind: 'embedding' as const },
           { name: 'qwen3.7-text-rerank', modelKind: 'rerank' as const },
         ],
         credential: { configured: true, fingerprint: 'abc123', last4: '1234', rotatedAt: null, rotatedBy: null },
         lastProbe: null,
       }] : [])]}
      defaultModels={{ qwen_tp: 'qwen3.7-plus@tp' }}
      source="db"
        canAdmin
        onChanged={vi.fn(async () => undefined)}
      />,
  ))
  mounted.push({ container, root })
  return container
}

describe('ProvidersTab 探测失败详情', () => {
  it('列表只显示带星号的脱敏 Key，不回显明文', () => {
    const container = mount(true)
    expect(container.textContent).toContain('****a1b2')
    expect(container.textContent).not.toContain('sk-')
  })

  it('显示新增供应商入口并以弹窗承载配置', async () => {
    const container = mount()
    const addButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('新增供应商')) as HTMLButtonElement

    expect(addButton).toBeTruthy()
    await act(async () => {
      addButton.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('新增供应商')
    expect(container.textContent).toContain('API Key')
    expect(container.textContent).toContain('模型名称')
    expect(container.textContent).toContain('模型用途')
    expect(container.textContent).toContain('向量模型')
  })

  it('按用途显示供应商模型，并提供新增模型弹窗', async () => {
    const container = mount()
    expect(container.textContent).toContain('文本模型')
    const addModelButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('新增模型')) as HTMLButtonElement

    expect(addModelButton).toBeTruthy()
    await act(async () => {
      addModelButton.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('新增模型')
    expect(container.textContent).toContain('向量模型')
  })

  it('模型新增弹窗提供文本、向量、重排、视觉、语音五类用途', async () => {
    const container = mount()
    const addModelButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('新增模型')) as HTMLButtonElement

    await act(async () => {
      addModelButton.click()
      await Promise.resolve()
    })

    const select = container.querySelector('select') as HTMLSelectElement
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
      '文本模型（对话 / 评测 / 文档处理）',
      '向量模型（Embedding）',
      '重排模型（Rerank）',
      '视觉模型（Vision）',
      '语音模型（Speech）',
    ])
  })

  it('测试失败后在探测栏显示整体原因、失败级别和原始摘要', async () => {
    apiMock.verifyProvider.mockResolvedValueOnce({
      ok: false,
      provider: 'qwen_tp',
      target: 'https://token-plan.example/v1',
      network_scope: 'public',
      draft: false,
      summary: '未通过（卡在 L2）：HTTP 402：账户余额不足',
      steps: [
        { grade: 'L0', status: 'pass', summary: 'URL 可达' },
        { grade: 'L1', status: 'pass', summary: '端点清单正常' },
        { grade: 'L2', status: 'fail', summary: 'HTTP 402：账户余额不足', raw: '余额不足，请充值后重试' },
      ],
    })
    const container = mount()
    const testButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.trim() === '测试') as HTMLButtonElement

    await act(async () => {
      testButton.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('账户余额不足')
    expect(container.textContent).toContain('模型调用')
    expect(container.textContent).toContain('余额不足，请充值后重试')
  })

  it('HTTP 参数错误也在对应供应商行显示，不只依赖 toast', async () => {
    apiMock.verifyProvider.mockRejectedValueOnce(new Error('请求参数有误：body.model_name：不能为空'))
    const container = mount()
    const testButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.trim() === '测试') as HTMLButtonElement

    await act(async () => {
      testButton.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('请求参数有误：body.model_name：不能为空')
  })

  it('请求未返回时显示实时耗时，完成后显示总耗时', async () => {
    let resolveProbe: (value: unknown) => void = () => undefined
    apiMock.verifyProvider.mockImplementationOnce(() => new Promise((resolve) => {
      resolveProbe = resolve
    }))
    const container = mount()
    const testButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.trim() === '测试') as HTMLButtonElement

    await act(async () => {
      testButton.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('正在快速测试')
    expect(container.textContent).toContain('已耗时')

    await act(async () => {
      resolveProbe({
        ok: true,
        provider: 'qwen_tp',
        target: 'https://token-plan.example/v1',
        network_scope: 'public',
        draft: false,
        steps: [{ grade: 'L0', status: 'pass', summary: 'URL 可达', elapsedMs: 120 }],
      })
      await Promise.resolve()
    })

    expect(container.textContent).toContain('总耗时 120ms')
    expect(container.textContent).not.toContain('正在测试')
  })

  it('同一供应商行合并五类模型，且不再渲染专项模型独立卡片', () => {
    const container = mount(false, true)

    expect(container.textContent).toContain('文本模型')
    expect(container.textContent).toContain('向量模型')
    expect(container.textContent).toContain('重排模型')
    expect(container.textContent).toContain('视觉模型')
    expect(container.textContent).toContain('语音模型')
    expect(container.textContent).toContain('阿里云百炼专项')
    expect(container.querySelector('[data-testid="specialized-models-card"]')).toBeNull()
    expect(container.textContent).not.toContain('配置专项模型')
  })

  it('历史专项供应商也沿用通用测试、追加模型和编辑动作', () => {
    const container = mount(false, true)

    const row = Array.from(container.querySelectorAll('tbody tr'))
      .find((item) => item.textContent?.includes('阿里云百炼专项')) as HTMLTableRowElement
    expect(row).toBeTruthy()
    expect(row.textContent).toContain('****1234')
    expect(Array.from(row.querySelectorAll('button')).some((button) => button.textContent?.trim() === '测试' && !button.disabled)).toBe(true)
    expect(Array.from(row.querySelectorAll('button')).some((button) => button.textContent?.includes('新增模型') && !button.disabled)).toBe(true)
    expect(Array.from(row.querySelectorAll('button')).some((button) => button.textContent?.includes('编辑') && !button.disabled)).toBe(true)
    expect(row.textContent).not.toContain('历史专项配置请使用上方')
  })

  it('保存冲突时在编辑弹窗保留具体原因', async () => {
    apiMock.saveProvider.mockRejectedValueOnce(new Error(
      '模型 qwen3.7-text-embedding 已属于历史专项供应商 specialized-api，请编辑历史专项配置或确认迁移'
    ))
    const container = mount()
    const editButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('编辑')) as HTMLButtonElement

    await act(async () => {
      editButton.click()
      await Promise.resolve()
    })
    const saveButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.trim() === '保存') as HTMLButtonElement
    await act(async () => {
      saveButton.click()
      await Promise.resolve()
    })

    expect(container.textContent).toContain('历史专项供应商')
  })

  it('默认测试走快速模式，并可显式执行完整 usage 测试', async () => {
    apiMock.verifyProvider.mockResolvedValue({
      ok: true,
      provider: 'qwen_tp',
      target: 'https://token-plan.example/v1',
      network_scope: 'public',
      draft: false,
      steps: [],
    })
    const container = mount()
    const quickButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.trim() === '测试') as HTMLButtonElement
    const fullButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('完整测试')) as HTMLButtonElement

    await act(async () => {
      quickButton.click()
      await Promise.resolve()
    })
    expect(apiMock.verifyProvider).toHaveBeenNthCalledWith(1, 'qwen_tp', { mode: 'fast' })

    await act(async () => {
      fullButton.click()
      await Promise.resolve()
    })
    expect(apiMock.verifyProvider).toHaveBeenNthCalledWith(2, 'qwen_tp', { mode: 'full' })
  })
})

afterEach(() => {
  apiMock.verifyProvider.mockReset()
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { testAndSaveSpecialized } from '@/api/modelConfig'
import type { SpecializedModelResponse } from '@/types/modelConfig'
import SpecializedModelsCard from './SpecializedModelsCard'

vi.mock('@/api/modelConfig', () => ({
  testAndSaveSpecialized: vi.fn(),
}))

const response: SpecializedModelResponse = {
  source: 'db',
  adapters: ['dashscope_embedding', 'openai_embedding', 'dashscope_rerank', 'jina_rerank'],
  items: [
    {
      role: 'embedding',
      modelName: 'qwen3.7-text-embedding',
      providerId: 'dashscope-rag',
      providerName: '阿里云百炼专项',
      adapter: 'dashscope_embedding',
      baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      options: { dimensions: 1024 },
      enabled: true,
      credential: { configured: true, last4: '1234' },
      lastProbe: { ok: true, summary: '专项模型调用通过', elapsedMs: 87 },
      requiresReindex: true,
    },
    {
      role: 'rerank',
      modelName: 'qwen3.7-text-rerank',
      providerId: 'dashscope-rag',
      providerName: '阿里云百炼专项',
      adapter: 'dashscope_rerank',
      baseUrl: 'https://dashscope.aliyuncs.com/api/v1',
      options: {},
      enabled: true,
      credential: { configured: true, last4: '1234' },
      lastProbe: { ok: true, summary: '专项模型调用通过', elapsedMs: 144 },
      requiresReindex: false,
    },
  ],
}

function mount(): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div')
  const root = createRoot(container)
  act(() => root.render(<SpecializedModelsCard config={response} canAdmin onSaved={vi.fn()} />))
  return { container, root }
}

afterEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('SpecializedModelsCard', () => {
  it('展示专项模型、掩码 Key 和最近测试耗时，不展示明文 Key', () => {
    const { container, root } = mount()

    expect(container.textContent).toContain('qwen3.7-text-embedding')
    expect(container.textContent).toContain('qwen3.7-text-rerank')
    expect(container.textContent).toContain('****1234')
    expect(container.textContent).toContain('87ms')
    expect(container.textContent).not.toContain('sk-test-placeholder')

    act(() => root.unmount())
  })

  it('测试失败时保留弹窗并显示后端原因和耗时', async () => {
    vi.mocked(testAndSaveSpecialized).mockResolvedValue({
      ok: false,
      saved: false,
      summary: '专项模型测试未全部通过，配置未保存',
      tests: [
        {
          role: 'rerank',
          adapter: 'dashscope_rerank',
          ok: false,
          summary: '模型调用失败（HTTP 401）',
          detail: 'InvalidApiKey',
          elapsedMs: 321,
        },
      ],
    })
    const { container, root } = mount()
    const open = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('配置'))
    expect(open).toBeTruthy()
    act(() => open?.dispatchEvent(new MouseEvent('click', { bubbles: true })))

    const input = container.querySelector<HTMLInputElement>('input[name="apiKey"]')
    expect(input).toBeTruthy()
    act(() => {
      if (input) {
        input.value = 'sk-test-placeholder'
        input.dispatchEvent(new Event('input', { bubbles: true }))
      }
    })
    const submit = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('测试并保存'))
    await act(async () => submit?.dispatchEvent(new MouseEvent('click', { bubbles: true })))

    expect(container.textContent).toContain('InvalidApiKey')
    expect(container.textContent).toContain('321ms')
    expect(container.textContent).toContain('专项模型测试未全部通过')
    expect(container.querySelector('[role="dialog"]')).toBeTruthy()

    act(() => root.unmount())
  })

  it('编辑时供应商 Base URL 使用根地址，不误用向量端点', () => {
    const { container, root } = mount()
    const open = Array.from(container.querySelectorAll('button')).find((button) => button.textContent?.includes('配置'))
    act(() => open?.dispatchEvent(new MouseEvent('click', { bubbles: true })))

    expect(container.querySelector<HTMLInputElement>('input[name="providerBaseUrl"]')?.value)
      .toBe('https://dashscope.aliyuncs.com')

    act(() => root.unmount())
  })
})

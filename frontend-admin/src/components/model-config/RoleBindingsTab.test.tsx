import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const apiMock = vi.hoisted(() => ({
  saveModelRole: vi.fn(),
}))

vi.mock('@/api/modelConfig', () => apiMock)
vi.mock('@/components/shared/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}))

import RoleBindingsTab from './RoleBindingsTab'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(overrides: Partial<Parameters<typeof RoleBindingsTab>[0]> = {}) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(
    <RoleBindingsTab
      roles={[{
        role: 'rerank',
        effectiveModel: 'qwen3.7-text-rerank',
        literalValue: 'qwen3.7-text-rerank',
        source: 'db',
        inheritedFrom: null,
        provider: 'dashscope-rag',
        registered: true,
        missingKeyEnv: null,
        available: true,
        availabilityReason: null,
        requiresReindex: false,
        updatedBy: null,
        updatedAt: null,
      }]}
      catalog={[
        { name: 'qwen3.7-plus', provider: 'qwen', modelKind: 'chat' },
        { name: 'qwen3.7-text-embedding', provider: 'dashscope-rag', modelKind: 'embedding' },
        { name: 'qwen3.7-text-rerank', provider: 'dashscope-rag', modelKind: 'rerank' },
      ]}
      canAdmin
      onSaved={vi.fn(async () => undefined)}
      {...overrides}
    />,
  ))
  mounted.push({ container, root })
  return container
}

afterEach(() => {
  for (const item of mounted.splice(0)) {
    act(() => item.root.unmount())
    item.container.remove()
  }
  apiMock.saveModelRole.mockReset()
})

describe('RoleBindingsTab 模型目录选择', () => {
  it('专项角色编辑使用匹配用途的下拉框，不允许自由输入模型名', async () => {
    const container = mount()
    const edit = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('修改')) as HTMLButtonElement

    await act(async () => {
      edit.click()
      await Promise.resolve()
    })

    const select = container.querySelector('select') as HTMLSelectElement
    expect(select).toBeTruthy()
    expect(container.querySelector('input')).toBeNull()
    expect(Array.from(select.options).map((option) => option.value)).toContain('qwen3.7-text-rerank')
    expect(Array.from(select.options).map((option) => option.value)).not.toContain('qwen3.7-text-embedding')
    expect(Array.from(select.options).map((option) => option.value)).not.toContain('qwen3.7-plus')
  })

  it('当前模型未登记时只能重新选择目录模型，不能保存当前自由文本值', async () => {
    const container = mount({
      roles: [{
        role: 'rerank',
        effectiveModel: 'qwen3.7-text-reran',
        literalValue: 'qwen3.7-text-reran',
        source: 'db',
        inheritedFrom: null,
        provider: 'dashscope-rag',
        registered: false,
        missingKeyEnv: null,
        available: false,
        availabilityReason: '未注册',
        requiresReindex: false,
        updatedBy: null,
        updatedAt: null,
      }],
    })
    const edit = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('修改')) as HTMLButtonElement

    await act(async () => {
      edit.click()
      await Promise.resolve()
    })

    const select = container.querySelector('select') as HTMLSelectElement
    expect(select.value).toBe('qwen3.7-text-reran')
    expect(Array.from(select.options).find((option) => option.value === 'qwen3.7-text-reran')?.disabled).toBe(true)

    const save = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('保存')) as HTMLButtonElement
    await act(async () => {
      save.click()
      await Promise.resolve()
    })

    expect(apiMock.saveModelRole).not.toHaveBeenCalled()
  })
})

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
import type { RoleBinding } from '@/types/modelConfig'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

const CATALOG = [
  { name: 'qwen3.7-plus', provider: 'qwen', modelKind: 'chat' as const },
  { name: 'qwen3.7-text-embedding', provider: 'dashscope-rag', modelKind: 'embedding' as const },
  { name: 'qwen3.7-text-rerank', provider: 'dashscope-rag', modelKind: 'rerank' as const },
]

function roleRow(overrides: Partial<RoleBinding> = {}): RoleBinding {
  return {
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
    ...overrides,
  }
}

function mount(overrides: Partial<Parameters<typeof RoleBindingsTab>[0]> = {}) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(
    <RoleBindingsTab
      roles={[roleRow()]}
      catalog={CATALOG}
      canAdmin
      onSaved={vi.fn(async () => undefined)}
      {...overrides}
    />,
  ))
  mounted.push({ container, root })
  return container
}

/** 受控 select 写值：必须用原型 setter，直接 `select.value = x` 会被 React 取值跟踪器吞掉。 */
function choose(container: HTMLElement, testId: string, value: string) {
  const select = container.querySelector<HTMLSelectElement>(`[data-testid="${testId}"]`)
  expect(select).toBeTruthy()
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set
    setter?.call(select, value)
    select?.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

function buttonByText(container: HTMLElement, text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll('button'))
    .find((item) => item.textContent?.includes(text))
  expect(found).toBeTruthy()
  return found as HTMLButtonElement
}

async function startEditing(container: HTMLElement, text = '修改') {
  const button = buttonByText(container, text)
  await act(async () => {
    button.click()
    await Promise.resolve()
  })
}

function availabilityText(container: HTMLElement, role: string): string {
  return container.querySelector(`[data-testid="role-availability-${role}"]`)?.textContent ?? ''
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
    await startEditing(container)

    const select = container.querySelector<HTMLSelectElement>('[data-testid="role-model-select-rerank"]')
    expect(select).toBeTruthy()
    // 表格内不出现任何自由文本输入（「只看不可用」开关在表格外，不在此断言范围）
    expect(container.querySelector('table')?.querySelector('input')).toBeNull()
    expect(Array.from(select?.options ?? []).map((option) => option.value)).toContain('qwen3.7-text-rerank')
    expect(Array.from(select?.options ?? []).map((option) => option.value)).not.toContain('qwen3.7-text-embedding')
    expect(Array.from(select?.options ?? []).map((option) => option.value)).not.toContain('qwen3.7-plus')
  })

  it('当前模型未登记时只能重新选择目录模型，不能保存当前自由文本值', async () => {
    const container = mount({
      roles: [roleRow({
        effectiveModel: 'qwen3.7-text-reran',
        literalValue: 'qwen3.7-text-reran',
        registered: false,
        available: false,
        availabilityReason: '未注册',
      })],
    })
    await startEditing(container)

    const select = container.querySelector<HTMLSelectElement>('[data-testid="role-model-select-rerank"]')
    expect(select?.value).toBe('qwen3.7-text-reran')
    expect(Array.from(select?.options ?? []).find((option) => option.value === 'qwen3.7-text-reran')?.disabled).toBe(true)

    const save = buttonByText(container, '保存')
    await act(async () => {
      save.click()
      await Promise.resolve()
    })

    expect(apiMock.saveModelRole).not.toHaveBeenCalled()
  })

  it('编辑时可用性列跟随所选模型重算，而不是停留在旧结论', async () => {
    const container = mount({
      roles: [roleRow({
        effectiveModel: 'qwen3.7-text-reran',
        literalValue: 'qwen3.7-text-reran',
        registered: false,
        available: false,
        availabilityReason: '未注册',
      })],
    })
    await startEditing(container)

    // 未换值：沿用后端对当前生效值的结论
    expect(availabilityText(container, 'rerank')).toContain('未注册')

    choose(container, 'role-model-select-rerank', 'qwen3.7-text-rerank')
    expect(availabilityText(container, 'rerank')).toContain('可用')
    expect(availabilityText(container, 'rerank')).not.toContain('未注册')

    choose(container, 'role-model-select-rerank', 'qwen3.7-text-reran')
    expect(availabilityText(container, 'rerank')).toContain('未注册')
  })
})

describe('RoleBindingsTab 版式', () => {
  it('按业务链路分组渲染，并显示最后一次修改的人与时间', () => {
    const container = mount({
      roles: [
        roleRow({
          role: 'main',
          effectiveModel: 'qwen3.7-plus',
          literalValue: 'qwen3.7-plus',
          provider: 'qwen',
          updatedBy: 'admin',
          updatedAt: new Date(Date.now() - 2 * 86400_000).toISOString(),
        }),
        roleRow(),
      ],
    })

    expect(container.textContent).toContain('问答链路')
    expect(container.textContent).toContain('检索链路')
    expect(container.textContent).toContain('最后由 admin')
    expect(container.textContent).toContain('2 天前')

    // 分列版式回归锚点：来源/审计/字面值在独立的「来源」列，
    // 不再堆进「当前绑定」单元格（曾导致继承 + 空值行叠四层信息）。
    const sourceCell = Array.from(container.querySelectorAll('td'))
      .find((td) => td.textContent?.includes('最后由 admin'))
    expect(sourceCell).toBeTruthy()
    expect(sourceCell?.textContent).not.toContain('qwen3.7-plus')
    expect(sourceCell?.textContent).not.toContain('主问答模型')
  })

  it('「只看不可用」只留下判定不可用的角色', async () => {
    const container = mount({
      roles: [
        roleRow({ role: 'main', effectiveModel: 'qwen3.7-plus', literalValue: 'qwen3.7-plus', provider: 'qwen' }),
        roleRow({
          effectiveModel: 'qwen3.7-text-reran',
          literalValue: 'qwen3.7-text-reran',
          registered: false,
          available: false,
          availabilityReason: '未注册',
        }),
      ],
    })
    expect(container.textContent).toContain('主问答模型')

    const toggle = container.querySelector<HTMLInputElement>('[data-testid="only-problem-toggle"]')
    await act(async () => {
      toggle?.click()
      await Promise.resolve()
    })

    expect(container.textContent).not.toContain('主问答模型')
    expect(container.textContent).toContain('重排模型')
  })
})

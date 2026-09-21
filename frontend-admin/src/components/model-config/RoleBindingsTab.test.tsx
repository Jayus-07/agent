import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const apiMock = vi.hoisted(() => ({
  saveModelRole: vi.fn(),
  saveModelRolePolicy: vi.fn(),
}))

vi.mock('@/api/modelConfig', () => apiMock)
vi.mock('@/components/shared/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}))

import RoleBindingsTab from './RoleBindingsTab'
import type { RoleBinding } from '@/types/modelConfig'

/** 厂商中文名由后端下发；待 types/modelConfig.ts 落定后并入 RoleBinding 接口。 */
type RoleRow = RoleBinding & { providerLabel?: string | null }

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

const CATALOG = [
  { name: 'qwen3.7-plus', provider: 'qwen', modelKind: 'chat' as const },
  { name: 'qwen3.7-text-embedding', provider: 'dashscope-rag', modelKind: 'embedding' as const },
  { name: 'qwen3.7-text-rerank', provider: 'dashscope-rag', modelKind: 'rerank' as const },
]

function roleRow(overrides: Partial<RoleRow> = {}): RoleRow {
  return {
    role: 'rerank',
    effectiveModel: 'qwen3.7-text-rerank',
    literalValue: 'qwen3.7-text-rerank',
    source: 'db',
    inheritedFrom: null,
    provider: 'dashscope-rag',
    providerLabel: '阿里云百炼',
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
  apiMock.saveModelRolePolicy.mockReset()
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

  it('厂商中文名徽章在模型名前面；后端未下发时回落 provider 代码', () => {
    const withLabel = mount()
    const boundCell = Array.from(withLabel.querySelectorAll('td'))
      .find((td) => td.textContent?.includes('qwen3.7-text-rerank'))
    const text = boundCell?.textContent ?? ''
    expect(text.indexOf('阿里云百炼')).toBeGreaterThanOrEqual(0)
    expect(text.indexOf('阿里云百炼')).toBeLessThan(text.indexOf('qwen3.7-text-rerank'))
    // 有厂商中文名时不再显示 provider 代码（信息重复）
    expect(text).not.toContain('dashscope-rag')

    const withoutLabel = mount({ roles: [roleRow({ providerLabel: null })] })
    const fallbackCell = Array.from(withoutLabel.querySelectorAll('td'))
      .find((td) => td.textContent?.includes('qwen3.7-text-rerank'))
    expect(fallbackCell?.textContent).toContain('dashscope-rag')
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

describe('RoleBindingsTab 供应商页跳转高亮（B3）', () => {
  it('highlightRole 高亮目标角色行并滚动定位', () => {
    // jsdom 未实现 scrollIntoView，stub 掉以验证「被调用」即可
    const scrollSpy = vi.fn()
    ;(Element.prototype as unknown as Record<string, unknown>).scrollIntoView = scrollSpy
    const container = mount({ highlightRole: 'rerank' })

    const row = container.querySelector('[data-role-row="rerank"]')
    expect(row).toBeTruthy()
    expect(row?.className).toContain('bg-amber-50')
    expect(scrollSpy).toHaveBeenCalled()
  })

  it('未传 highlightRole 时无行被高亮', () => {
    const container = mount()
    expect(container.querySelector('[data-role-row="rerank"]')?.className).not.toContain('bg-amber-50')
  })
})

describe('RoleBindingsTab 运行时治理（2026-09-22 改造）', () => {
  it('继承 main 的行展示「继承主问答模型」语义，不再出现「空白有语义」措辞', () => {
    const container = mount({
      roles: [roleRow({
        role: 'metadata_extract',
        effectiveModel: 'qwen3.7-plus',
        literalValue: '',
        source: 'inherit',
        inheritedFrom: 'main',
        provider: 'qwen',
      })],
    })
    const hint = container.querySelector('[data-testid="inherit-hint-metadata_extract"]')
    expect(hint?.textContent).toContain('继承主问答模型')
    expect(hint?.textContent).toContain('qwen3.7-plus')
    expect(container.textContent).not.toContain('空白')
    expect(container.textContent).not.toContain('该角色的空值有语义')
  })

  it('健康列展示后端探测缓存的状态与延迟', () => {
    const container = mount({
      roles: [
        roleRow({ role: 'main', effectiveModel: 'qwen3.7-plus', literalValue: 'qwen3.7-plus', provider: 'qwen', health: {
          status: 'healthy', lastCheckedAt: new Date().toISOString(), lastLatencyMs: 820, lastError: null, consecutiveFailures: 0,
        } }),
        roleRow({ health: { status: 'rate_limited', lastCheckedAt: null, lastLatencyMs: null, lastError: '429', consecutiveFailures: 2 } }),
      ],
    })
    expect(container.querySelector('[data-testid="role-health-main"]')?.textContent).toContain('正常')
    expect(container.querySelector('[data-testid="role-health-main"]')?.textContent).toContain('820ms')
    expect(container.querySelector('[data-testid="role-health-rerank"]')?.textContent).toContain('限流')
    expect(container.querySelector('[data-testid="role-health-rerank"]')?.textContent).toContain('连续失败 2')

    // 无探测数据 → 「未探测」，不冒充正常
    const noHealth = mount()
    expect(noHealth.querySelector('[data-testid="role-health-rerank"]')?.textContent).toContain('未探测')
  })

  it('embedding 索引不一致时显示全局重建告警', () => {
    const container = mount({
      roles: [roleRow({
        role: 'embedding',
        effectiveModel: 'qwen3.7-text-embedding',
        literalValue: 'qwen3.7-text-embedding',
        provider: 'dashscope-rag',
        requiresReindex: true,
        indexCompat: [{
          collection: 'chroma', embeddingProvider: 'dashscope-rag', embeddingModel: 'old-emb',
          embeddingDimension: 1024, indexVersion: 1, builtAt: null, updatedAt: null,
          status: 'rebuild_required', runtimeModel: 'qwen3.7-text-embedding', mismatch: true,
        }],
      })],
    })
    const warning = container.querySelector('[data-testid="index-compat-warning"]')
    expect(warning?.textContent).toContain('请重建索引后再使用')
    expect(warning?.textContent).toContain('old-emb')
  })

  it('策略弹窗可编辑并保存 fallback/timeout/retry/failurePolicy', async () => {
    apiMock.saveModelRolePolicy.mockResolvedValue({})
    const container = mount({
      roles: [roleRow({
        policy: { role: 'rerank', fallbackModel: '', timeoutSeconds: 15, maxRetries: 1, failurePolicy: 'skip', source: 'db', updatedBy: null, updatedAt: null },
      })],
    })

    const btn = container.querySelector<HTMLButtonElement>('[data-testid="role-policy-rerank"]')
    await act(async () => {
      btn?.click()
      await Promise.resolve()
    })
    const modal = container.querySelector('[data-testid="policy-modal"]')
    expect(modal?.textContent).toContain('失败策略')
    expect(modal?.textContent).toContain('能力要求')

    const save = container.querySelector<HTMLButtonElement>('[data-testid="policy-save"]')
    await act(async () => {
      save?.click()
      await Promise.resolve()
    })
    expect(apiMock.saveModelRolePolicy).toHaveBeenCalledWith('rerank', {
      fallbackModel: '',
      timeoutSeconds: 15,
      maxRetries: 1,
      failurePolicy: 'skip',
    })
  })

  it('embedding 角色的失败策略里 fallback 选项被禁用', async () => {
    const container = mount({
      roles: [roleRow({
        role: 'embedding',
        effectiveModel: 'qwen3.7-text-embedding',
        literalValue: 'qwen3.7-text-embedding',
        provider: 'dashscope-rag',
        requiresReindex: true,
      })],
    })
    const btn = container.querySelector<HTMLButtonElement>('[data-testid="role-policy-embedding"]')
    await act(async () => {
      btn?.click()
      await Promise.resolve()
    })
    const select = container.querySelector<HTMLSelectElement>('[data-testid="policy-failure-policy"]')
    const fallbackOption = Array.from(select?.options ?? []).find((option) => option.value === 'fallback')
    expect(fallbackOption?.disabled).toBe(true)
  })
})

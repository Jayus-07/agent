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
import type { PresetPlan, ProviderPreset } from '@/types/modelConfig'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

const PLANS: PresetPlan[] = [
  { id: 'token_plan', label: 'Token Plan', billing: 'subscription' },
  { id: 'coding_plan', label: 'Coding Plan', billing: 'subscription' },
  { id: 'metered', label: '按量付费', billing: 'metered' },
]

const PRESETS: ProviderPreset[] = [
  {
    id: 'volc-coding-openai',
    plan: 'coding_plan',
    vendor: '火山引擎（方舟）',
    variant: '',
    driver: 'openai',
    driverLabel: 'OpenAI 兼容',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/coding/v3',
    apiKeyHint: 'ARK_API_KEY',
    note: '按量付费是 /api/v3，Coding Plan 是 /api/coding/v3，用错会产生额外费用',
    placeholders: [],
  },
  {
    id: 'volc-metered-openai',
    plan: 'metered',
    vendor: '火山引擎（方舟）',
    variant: '',
    driver: 'openai',
    driverLabel: 'OpenAI 兼容',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    apiKeyHint: 'ARK_API_KEY',
    note: '',
    placeholders: [],
  },
  {
    id: 'deepseek-metered-anthropic',
    plan: 'metered',
    vendor: 'DeepSeek',
    variant: '',
    driver: 'anthropic',
    driverLabel: 'Anthropic 兼容',
    baseUrl: 'https://api.deepseek.com/anthropic',
    apiKeyHint: 'DeepSeek API Key',
    note: '',
    placeholders: [],
  },
  {
    id: 'aliyun-metered-cn-openai',
    plan: 'metered',
    vendor: '阿里云百炼',
    variant: '北京',
    driver: 'openai',
    driverLabel: 'OpenAI 兼容',
    baseUrl: 'https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
    apiKeyHint: 'sk- 开头',
    note: '需把 {WorkspaceId} 换成你自己的业务空间 ID，否则无法调用',
    placeholders: ['WorkspaceId'],
  },
]

function mount(configured = false, specialized = false, catalogPresets: ProviderPreset[] = PRESETS) {
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
        plans={catalogPresets.length > 0 ? PLANS : []}
        presets={catalogPresets}
        presetsLoading={false}
      />,
  ))
  mounted.push({ container, root })
  return container
}

function findButton(container: HTMLElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll('button'))
    .find((item) => item.textContent?.includes(label))
  expect(button, `未找到按钮：${label}`).toBeTruthy()
  return button as HTMLButtonElement
}

async function click(button: HTMLElement) {
  await act(async () => {
    button.click()
    await Promise.resolve()
  })
}

function selectField(container: HTMLElement, testId: string): HTMLSelectElement {
  const select = container.querySelector<HTMLSelectElement>(`[data-testid="${testId}"]`)
  expect(select, `未找到下拉：${testId}`).toBeTruthy()
  return select as HTMLSelectElement
}

/** 受控组件的值必须用**原生 setter** 写入。
 *
 * 直接写 `node.value = x` 会命中 React 在实例上装的值跟踪器
 * （`trackValueOnNode` 覆盖了 value 属性），跟踪器把 `x` 记为「已知值」，
 * 随后的 `input`/`change` 事件被判为「没有变化」，`onChange` 根本不触发 ——
 * 而 DOM 上的值却已经是 x，于是断言 DOM 的测试会假阳性通过。
 * 走 prototype 的 setter 才能绕开跟踪器，让 React 认到这次变化。
 */
function setNativeValue(element: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype = element instanceof HTMLSelectElement
    ? HTMLSelectElement.prototype
    : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set
  setter?.call(element, value)
}

function choose(container: HTMLElement, testId: string, value: string): HTMLSelectElement {
  const select = selectField(container, testId)
  act(() => {
    setNativeValue(select, value)
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  return select
}

function typeInto(container: HTMLElement, testId: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`[data-testid="${testId}"]`)
  expect(input, `未找到输入框：${testId}`).toBeTruthy()
  act(() => {
    setNativeValue(input!, value)
    input!.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function optionLabels(container: HTMLElement, testId: string): string[] {
  return Array.from(selectField(container, testId).options).map((option) => option.textContent ?? '')
}

async function openNewProvider(container: HTMLElement) {
  await click(findButton(container, '新增供应商'))
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
    const card = container.querySelector('[data-testid="provider-card"]') as HTMLElement
    expect(card.textContent).toContain('qwen3.7-plus')
    expect(card.textContent).toContain('文本')
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

    // 同一张供应商卡片内平铺全部已登记模型（不再按用途拆成独立区块）。
    const card = Array.from(container.querySelectorAll('[data-testid="provider-card"]'))
      .find((item) => item.textContent?.includes('Qwen Token Plan')) as HTMLElement
    expect(card).toBeTruthy()
    for (const name of ['qwen3.7-plus', 'qwen3.7-text-embedding', 'qwen3.7-text-rerank', 'qwen3.7-vl', 'qwen3.7-voice']) {
      expect(card.textContent).toContain(name)
    }
    expect(container.textContent).toContain('阿里云百炼专项')
    expect(container.querySelector('[data-testid="specialized-models-card"]')).toBeNull()
    expect(container.textContent).not.toContain('配置专项模型')
  })

  it('历史专项供应商也沿用通用测试、追加模型和编辑动作', () => {
    const container = mount(false, true)

    const row = Array.from(container.querySelectorAll('[data-testid="provider-card"]'))
      .find((item) => item.textContent?.includes('阿里云百炼专项')) as HTMLElement
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

describe('ProvidersTab 计费计划驱动的厂商目录', () => {
  it('厂商下拉按所选计划过滤，不混入其他计划的端点', async () => {
    const container = mount()
    await openNewProvider(container)

    // 未选计划时不给候选，避免默认落到某个计划的端点上
    expect(optionLabels(container, 'provider-preset')).toEqual(['自定义（手填 Base URL）'])

    choose(container, 'provider-plan', 'coding_plan')
    expect(optionLabels(container, 'provider-preset')).toEqual([
      '自定义（手填 Base URL）',
      '火山引擎（方舟） · OpenAI 兼容',
    ])

    choose(container, 'provider-plan', 'metered')
    expect(optionLabels(container, 'provider-preset')).toEqual([
      '自定义（手填 Base URL）',
      '火山引擎（方舟） · OpenAI 兼容',
      'DeepSeek · Anthropic 兼容',
      '阿里云百炼 · 北京 · OpenAI 兼容',
    ])
  })

  it('选中厂商条目一次性回填地址、协议、显示名与计费口径', async () => {
    const container = mount()
    await openNewProvider(container)

    choose(container, 'provider-plan', 'metered')
    choose(container, 'provider-preset', 'deepseek-metered-anthropic')

    expect(container.querySelector<HTMLInputElement>('[data-testid="provider-base-url"]')!.value)
      .toBe('https://api.deepseek.com/anthropic')
    expect(selectField(container, 'provider-driver').value).toBe('anthropic')
    expect(container.querySelector<HTMLInputElement>('[data-testid="provider-display-name"]')!.value)
      .toContain('DeepSeek')
    // 计划派生 billing：按量付费 → metered
    expect(selectField(container, 'provider-billing').value).toBe('metered')
  })

  it('切换计划会清掉上一个计划带出的地址，避免把按量端点用在 Coding Plan 上', async () => {
    const container = mount()
    await openNewProvider(container)

    choose(container, 'provider-plan', 'coding_plan')
    choose(container, 'provider-preset', 'volc-coding-openai')
    expect(container.querySelector<HTMLInputElement>('[data-testid="provider-base-url"]')!.value)
      .toBe('https://ark.cn-beijing.volces.com/api/coding/v3')

    choose(container, 'provider-plan', 'metered')
    expect(container.querySelector<HTMLInputElement>('[data-testid="provider-base-url"]')!.value).toBe('')
  })

  it('手填的地址在切换计划时保留，不丢用户输入', async () => {
    const container = mount()
    await openNewProvider(container)

    choose(container, 'provider-plan', 'metered')
    choose(container, 'provider-preset', '__custom')
    typeInto(container, 'provider-base-url', 'https://internal.corp.local/v1')

    choose(container, 'provider-plan', 'coding_plan')
    expect(container.querySelector<HTMLInputElement>('[data-testid="provider-base-url"]')!.value)
      .toBe('https://internal.corp.local/v1')
  })

  it('地址里还有未替换的占位符时不发起探测，并给出可操作提示', async () => {
    const container = mount()
    await openNewProvider(container)

    choose(container, 'provider-plan', 'metered')
    choose(container, 'provider-preset', 'aliyun-metered-cn-openai')
    typeInto(container, 'provider-model-name', 'qwen3.7-plus')

    await click(findButton(container, '测试连接'))

    expect(apiMock.verifyDraftProvider).not.toHaveBeenCalled()
    expect(container.textContent).toContain('WorkspaceId')
    expect(container.textContent).toContain('替换')
  })

  it('预置目录不可用时降级为手填，抽屉不成为死路', async () => {
    apiMock.verifyDraftProvider.mockResolvedValue({
      ok: true,
      provider: 'draft',
      target: 'https://api.example.com/v1',
      network_scope: 'public',
      draft: true,
      steps: [],
    })
    const container = mount(false, false, [])
    await openNewProvider(container)

    expect(container.textContent).toContain('预置厂商目录不可用')
    expect(selectField(container, 'provider-plan').disabled).toBe(true)

    typeInto(container, 'provider-base-url', 'https://api.example.com/v1')
    typeInto(container, 'provider-api-key', 'sk-manual')
    typeInto(container, 'provider-model-name', 'manual-model')

    // 关键断言走探测入参而不是 DOM 值 —— 手填内容必须真的进了 state，
    // 只读 DOM 会掩盖「onChange 没触发但 DOM 已被写脏」的假阳性。
    await click(findButton(container, '测试连接'))

    expect(apiMock.verifyDraftProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        baseUrl: 'https://api.example.com/v1',
        modelName: 'manual-model',
        apiKey: 'sk-manual',
      }),
      expect.objectContaining({ mode: 'fast' }),
    )
  })
})

describe('ProvidersTab 编辑态的前置拦截', () => {
  it('已登记模型的用途下拉被锁定，不再让用户撞上必然失败的探测', async () => {
    const container = mount()
    await click(findButton(container, '编辑'))

    const kind = selectField(container, 'provider-model-kind')
    expect(kind.disabled).toBe(true)
    expect(kind.value).toBe('chat')
    expect(container.textContent).toContain('用途不可更改')
  })

  it('列出该供应商已登记的全部模型，避免只看到第一个', async () => {
    const container = mount()
    await click(findButton(container, '编辑'))

    expect(container.textContent).toContain('该供应商已登记 5 个模型')
    expect(container.textContent).toContain('qwen3.7-text-embedding')
    expect(container.textContent).toContain('qwen3.7-voice')
  })

  it('模型名已被别的供应商占用时提前点名，不等探测跑完才报 409', async () => {
    const container = mount(false, true)
    const row = Array.from(container.querySelectorAll('[data-testid="provider-card"]'))
      .find((item) => item.textContent?.includes('阿里云百炼专项')) as HTMLElement
    const editButton = Array.from(row.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('编辑')) as HTMLButtonElement

    await click(editButton)
    typeInto(container, 'provider-model-name', 'qwen3.7-plus')

    expect(container.textContent).toContain('已属于供应商')
    expect(container.textContent).toContain('Qwen Token Plan')
  })

  it('改成另一个已登记模型名时用途跟着走，不留下不可提交的组合', async () => {
    const container = mount()
    await click(findButton(container, '编辑'))

    typeInto(container, 'provider-model-name', 'qwen3.7-text-embedding')

    expect(selectField(container, 'provider-model-kind').value).toBe('embedding')
  })

  it('内置供应商的协议不可改，避免把内置驱动改坏', async () => {
    const container = mount()
    await click(findButton(container, '编辑'))

    expect(selectField(container, 'provider-driver').disabled).toBe(true)
  })
})

describe('ProvidersTab 地址助手', () => {
  function advisor(container: HTMLElement): HTMLElement | null {
    return container.querySelector<HTMLElement>('[data-testid="base-url-advisor"]')
  }

  function baseUrlValue(container: HTMLElement): string {
    return container.querySelector<HTMLInputElement>('[data-testid="provider-base-url"]')!.value
  }

  it('地址与预置一致时只给一句确认，不出告警', async () => {
    const container = mount()
    await openNewProvider(container)
    choose(container, 'provider-plan', 'coding_plan')
    choose(container, 'provider-preset', 'volc-coding-openai')

    const box = advisor(container)!
    expect(box).toBeTruthy()
    expect(box.getAttribute('data-kind')).toBeNull()
    expect(box.textContent).toContain('一致')
    expect(box.querySelectorAll('button').length).toBe(0)
  })

  it('选中预置后手改地址会点名偏离，并给一键还原', async () => {
    const container = mount()
    await openNewProvider(container)
    choose(container, 'provider-plan', 'coding_plan')
    choose(container, 'provider-preset', 'volc-coding-openai')

    typeInto(container, 'provider-base-url', 'https://my-gateway.internal/openai/v1')

    const box = advisor(container)!
    expect(box.getAttribute('data-kind')).toBe('deviated')
    expect(box.textContent).toContain('已偏离预置')
    expect(box.textContent).toContain('https://ark.cn-beijing.volces.com/api/coding/v3')
    // 预置的 Key 格式提示必须显式声明「可能不适用」，否则就是本次事故的误导来源。
    expect(container.textContent).toContain('而地址已被改过')

    await click(findButton(container, '还原为预置地址'))
    expect(baseUrlValue(container)).toBe('https://ark.cn-beijing.volces.com/api/coding/v3')
    expect(advisor(container)!.getAttribute('data-kind')).toBeNull()
  })

  it('地址落在别的计费计划端点上时点名计划不符，并可切回本计划端点', async () => {
    const container = mount()
    await openNewProvider(container)
    choose(container, 'provider-plan', 'coding_plan')
    choose(container, 'provider-preset', 'volc-coding-openai')

    // Coding Plan 下填按量付费端点 —— 两条都是合法预置，所以不能只给「已匹配」绿灯。
    typeInto(container, 'provider-base-url', 'https://ark.cn-beijing.volces.com/api/v3')

    const box = advisor(container)!
    expect(box.getAttribute('data-kind')).toBe('plan-mismatch')
    expect(box.textContent).toContain('按量付费')
    expect(box.textContent).toContain('Coding Plan')

    await click(findButton(container, '改用「Coding Plan」端点'))
    expect(baseUrlValue(container)).toBe('https://ark.cn-beijing.volces.com/api/coding/v3')
  })

  it('未选过预置、只选了计划再粘贴地址时，计划不符仍要给出可切回的端点', async () => {
    const container = mount()
    await openNewProvider(container)
    // 刻意不选预置：此时 presetId 为空，「还原到原预置」无从谈起，
    // 只能按域名找本计划的端点 —— 若实现只在 URL 相同的预置里找，这里就没了按钮。
    choose(container, 'provider-plan', 'coding_plan')
    typeInto(container, 'provider-base-url', 'https://ark.cn-beijing.volces.com/api/v3')

    const box = advisor(container)!
    expect(box.getAttribute('data-kind')).toBe('plan-mismatch')

    await click(findButton(container, '改用「Coding Plan」端点'))
    expect(baseUrlValue(container)).toBe('https://ark.cn-beijing.volces.com/api/coding/v3')
  })

  it('域名认识但路径不是收录值时，列出该域名的端点供选择而不替用户拍板', async () => {
    const container = mount()
    await openNewProvider(container)
    choose(container, 'provider-plan', 'metered')
    typeInto(container, 'provider-base-url', 'https://ark.cn-beijing.volces.com/api/v9')

    const box = advisor(container)!
    expect(box.getAttribute('data-kind')).toBe('suggest')
    expect(box.textContent).toContain('ark.cn-beijing.volces.com')
    // 同域名下两个端点都要出现，不能只给一个「正解」。
    expect(box.textContent).toContain('/api/v3')
    expect(box.textContent).toContain('/api/coding/v3')

    // 同计划的候选排在最前，故按量地址是第一个按钮。
    await click(findButton(container, '/api/v3'))
    expect(baseUrlValue(container)).toBe('https://ark.cn-beijing.volces.com/api/v3')
  })

  it('陌生域名配 /api/vN 原生前缀只提示不判死，且不提供动作按钮', async () => {
    const container = mount()
    await openNewProvider(container)
    typeInto(container, 'provider-base-url', 'https://maas.qianwenaiapi.com/api/v1')

    const box = advisor(container)!
    expect(box.getAttribute('data-kind')).toBe('suspect')
    expect(box.textContent).toContain('/api/v1')
    expect(box.textContent).toContain('没有先例')
    // 关键：自建网关可用任意路径，措辞必须留余地，且不提供「改成 X」的伪正解。
    expect(box.textContent).toContain('不代表填错')
    expect(box.textContent).toContain('404 且响应体为空')
    expect(box.querySelectorAll('button').length).toBe(0)
  })

  it('合法自建网关与带业务空间的按量地址都不触发任何提示', async () => {
    const container = mount()
    await openNewProvider(container)

    typeInto(container, 'provider-base-url', 'https://gateway.internal.example/v1')
    // 先证明值真的写进去了，否则下面的 toBeNull 会因「什么都没发生」而假阳性通过。
    expect(baseUrlValue(container)).toBe('https://gateway.internal.example/v1')
    expect(advisor(container)).toBeNull()

    // 预置里 {WorkspaceId} 是占位符域名，替换成真实取值后反查必然落空，
    // 不能因此把真实的按量付费地址误报成异常。
    typeInto(container, 'provider-base-url', 'https://ws-abc123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
    expect(baseUrlValue(container)).toBe('https://ws-abc123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
    expect(advisor(container)).toBeNull()
  })
})

afterEach(() => {
  apiMock.verifyProvider.mockReset()
  apiMock.verifyDraftProvider.mockReset()
  apiMock.saveProvider.mockReset()
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const apiMock = vi.hoisted(() => ({
  createProvider: vi.fn(),
  addProviderModel: vi.fn(),
  removeProviderModel: vi.fn(),
  saveProvider: vi.fn(),
  deleteProvider: vi.fn(),
  verifyProvider: vi.fn(),
  verifyDraftProvider: vi.fn(),
  fetchModelCatalog: vi.fn(),
  fetchProviderModelCatalog: vi.fn(),
}))

vi.mock('@/api/modelConfig', () => apiMock)
vi.mock('@/components/shared/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}))

import ProvidersTab from './ProvidersTab'
import type { ProviderRow } from '@/types/modelConfig'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(
  configured = false,
  specialized = false,
  opts: {
    usedByRoles?: string[]
    onGoToRoles?: (role: string) => void
    customProvider?: { id: string; displayName: string; usedByRoles?: string[] }
  } = {},
) {
  const custom = opts.customProvider
  const customRows: ProviderRow[] = custom
    ? [{
        id: custom.id,
        displayName: custom.displayName,
        driver: 'openai',
        baseUrl: 'https://custom.example/v1',
        networkScope: 'public',
        billing: 'metered',
        isBuiltin: false,
        enabled: true,
        modelCount: 1,
        modelName: custom.usedByRoles?.length ? 'bound-model' : 'free-model',
        modelKind: 'chat',
        models: [{
          name: custom.usedByRoles?.length ? 'bound-model' : 'free-model',
          modelKind: 'chat',
          ...(custom.usedByRoles ? { usedByRoles: custom.usedByRoles } : {}),
        }],
        credential: { configured: true, fingerprint: 'f1', last4: 'a1b2', rotatedAt: null, rotatedBy: null },
        lastProbe: null,
      }]
    : []
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
          { name: 'qwen3.7-plus', modelKind: 'chat', ...(opts.usedByRoles ? { usedByRoles: opts.usedByRoles } : {}) },
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
       }] : []), ...customRows]}
      defaultModels={{ qwen_tp: 'qwen3.7-plus@tp' }}
      source="db"
        canAdmin
        onChanged={vi.fn(async () => undefined)}
        {...(opts.onGoToRoles ? { onGoToRoles: opts.onGoToRoles } : {})}
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

  it('模型新增弹窗提供文本、向量、重排、视觉、语音、OCR 六类用途', async () => {
    const container = mount()
    const addModelButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('新增模型')) as HTMLButtonElement

    await act(async () => {
      addModelButton.click()
      await Promise.resolve()
    })

    // B3 起列表头部有筛选下拉，第一个 <select> 不再是弹窗里的「模型用途」，
    // 必须在新增模型弹窗内精确定位。
    const dialog = container.querySelector('[role="dialog"][aria-label^="新增 Qwen Token Plan 模型"]') as HTMLElement
    expect(dialog).toBeTruthy()
    const select = dialog.querySelector('select') as HTMLSelectElement
    expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
      '文本模型（对话 / 评测 / 文档处理）',
      '向量模型（Embedding）',
      '重排模型（Rerank）',
      '视觉模型（Vision）',
      '语音模型（Speech）',
      'OCR 模型（文档识别 / 视觉问答）',
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

  it('同一供应商行合并五类模型，不再引入独立的专项模型卡片', () => {
    const container = mount(false, true)

    // 同一张供应商卡片内平铺全部已登记模型（不再按用途拆成独立区块）。
    const card = Array.from(container.querySelectorAll('[data-testid="provider-card"]'))
      .find((item) => item.textContent?.includes('Qwen Token Plan')) as HTMLElement
    expect(card).toBeTruthy()
    for (const name of ['qwen3.7-plus', 'qwen3.7-text-embedding', 'qwen3.7-text-rerank', 'qwen3.7-vl', 'qwen3.7-voice']) {
      expect(card.textContent).toContain(name)
    }
    expect(container.textContent).toContain('阿里云百炼专项')
    // 「专项模型」独立卡片（SpecializedModelsCard.tsx）已于 2026-09-21 删除：
    // 供应商页统一承载五类模型的登记与测试，专项能力不再有独立入口 —— 别再往回加。
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

  it('测试按钮在模型行上：定向测该模型；供应商卡片不再有厂商级测试/完整测试', async () => {
    apiMock.verifyProvider.mockResolvedValue({
      ok: true,
      provider: 'qwen_tp',
      model: 'qwen3.7-text-embedding',
      target: 'https://token-plan.example/v1',
      network_scope: 'public',
      draft: false,
      steps: [],
    })
    const container = mount()
    // 找 embedding 模型行上的测试按钮（aria-label 精确定位）。
    const embedButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.getAttribute('aria-label') === '测试模型 qwen3.7-text-embedding') as HTMLButtonElement
    expect(embedButton).toBeTruthy()

    await act(async () => {
      embedButton.click()
      await Promise.resolve()
    })
    expect(apiMock.verifyProvider).toHaveBeenCalledWith('qwen_tp', { mode: 'fast', modelName: 'qwen3.7-text-embedding' })

    // 厂商级入口已撤下：完整测试只存在于编辑抽屉（provider-test），卡片上不再有。
    const card = Array.from(container.querySelectorAll('[data-testid="provider-card"]'))
      .find((item) => item.textContent?.includes('Qwen Token Plan')) as HTMLElement
    expect(Array.from(card.querySelectorAll('button')).some((button) => button.textContent?.includes('完整测试'))).toBe(false)
    expect(Array.from(card.querySelectorAll('button')).filter((button) => button.textContent?.trim() === '测试').length).toBe(5)
  })
})

describe('ProvidersTab 手填三要素（2026-09-22 拍板：删除预置端点目录）', () => {
  it('新增抽屉是纯手填表单：没有计费计划与厂商端点下拉', async () => {
    const container = mount()
    await openNewProvider(container)

    expect(container.querySelector('[data-testid="provider-plan"]')).toBeNull()
    expect(container.querySelector('[data-testid="provider-preset"]')).toBeNull()
    expect(container.querySelector('[data-testid="provider-base-url"]')).toBeTruthy()
    expect(container.querySelector('[data-testid="provider-api-key"]')).toBeTruthy()
    expect(container.querySelector('[data-testid="provider-model-name"]')).toBeTruthy()
    expect(container.textContent).not.toContain('预置厂商目录不可用')
  })

  it('手填地址 / Key / 模型名后测试连接，三要素必须真的进了探测请求', async () => {
    apiMock.verifyDraftProvider.mockResolvedValue({
      ok: true,
      provider: 'draft',
      target: 'https://api.example.com/v1',
      network_scope: 'public',
      draft: true,
      steps: [],
    })
    const container = mount()
    await openNewProvider(container)

    typeInto(container, 'provider-base-url', 'https://api.example.com/v1')
    typeInto(container, 'provider-api-key', 'sk-manual')
    typeInto(container, 'provider-model-name', 'manual-model')

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

  it('地址里还有未替换的占位符时不发起探测，并给出可操作提示', async () => {
    const container = mount()
    await openNewProvider(container)

    typeInto(container, 'provider-base-url', 'https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
    typeInto(container, 'provider-model-name', 'qwen3.7-plus')

    await click(findButton(container, '测试连接'))

    expect(apiMock.verifyDraftProvider).not.toHaveBeenCalled()
    expect(container.textContent).toContain('WorkspaceId')
    expect(container.textContent).toContain('替换')
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

  it('内置供应商的协议现在可改（特殊类退役 2026-09-22），特殊驱动仍锁定', async () => {
    const container = mount()
    await click(findButton(container, '编辑'))

    expect(selectField(container, 'provider-driver').disabled).toBe(false)
  })
})

describe('ProvidersTab 模型目录与「去修」动作（B2）', () => {
  const CATALOG = {
    ok: true,
    summary: '上游目录共 5 个模型',
    cached: false,
    shape_ok: true,
    truncated: false,
    items: [
      { id: 'qwen3.7-plus', kind: 'chat' },
      { id: 'qwen3.8-max', kind: 'chat' },
      { id: 'qwen3.7-text-embedding', kind: 'embedding' },
      { id: 'qwen3.7-text-rerank', kind: 'rerank' },
      { id: 'qwen3.7-vl', kind: 'vision' },
    ],
  }

  it('「从目录选」按需拉取：默认只平铺当前用途，其余折叠；选中即回填并收起', async () => {
    apiMock.fetchModelCatalog.mockResolvedValueOnce(CATALOG)
    const container = mount()
    await openNewProvider(container)
    typeInto(container, 'provider-base-url', 'https://api.example.com/v1')

    await click(container.querySelector('[data-testid="model-catalog-toggle"]') as HTMLButtonElement)
    expect(apiMock.fetchModelCatalog).toHaveBeenCalledWith({
      baseUrl: 'https://api.example.com/v1',
      apiKey: undefined,
      networkScope: 'public',
    })

    const panel = () => container.querySelector('[data-testid="model-catalog-panel"]') as HTMLElement
    expect(panel().textContent).toContain('上游目录共 5 个模型')
    // 当前用途（对话）直接平铺；别的用途收进 <details>，避免几百个名字一次铺出来
    const chatSection = panel().querySelector('[data-testid="catalog-section-chat"]') as HTMLElement
    expect(chatSection.tagName).toBe('DIV')
    expect(chatSection.textContent).toContain('qwen3.7-plus')
    const embeddingSection = panel().querySelector('[data-testid="catalog-section-embedding"]') as HTMLDetailsElement
    expect(embeddingSection.tagName).toBe('DETAILS')
    expect(embeddingSection.open).toBe(false)

    act(() => { embeddingSection.open = true })
    await click(container.querySelector('[data-testid="catalog-item-qwen3.7-text-embedding"]') as HTMLButtonElement)
    const nameInput = container.querySelector('[data-testid="provider-model-name"]') as HTMLInputElement
    expect(nameInput.value).toBe('qwen3.7-text-embedding')
    expect(container.querySelector('[data-testid="model-catalog-panel"]')).toBeNull()
  })

  it('没填地址时不去请求，给可操作提示', async () => {
    const container = mount()
    await openNewProvider(container)
    await click(container.querySelector('[data-testid="model-catalog-toggle"]') as HTMLButtonElement)
    expect(apiMock.fetchModelCatalog).not.toHaveBeenCalled()
    expect(container.textContent).toContain('请先填写 Base URL')
  })

  it('清单拉取失败不堵死路：显示原因，仍可直接手打', async () => {
    apiMock.fetchModelCatalog.mockRejectedValueOnce(new Error('上游返回 404'))
    const container = mount()
    await openNewProvider(container)
    typeInto(container, 'provider-base-url', 'https://api.example.com/v1')
    await click(container.querySelector('[data-testid="model-catalog-toggle"]') as HTMLButtonElement)
    expect(container.textContent).toContain('上游返回 404')
    // 输入框保持可自由手打（大量站点不实现 /models）
    const nameInput = container.querySelector('[data-testid="provider-model-name"]') as HTMLInputElement
    expect(nameInput).toBeTruthy()
    typeInto(container, 'provider-model-name', 'my-private-model')
    expect((container.querySelector('[data-testid="provider-model-name"]') as HTMLInputElement).value).toBe('my-private-model')
  })

  it('L2 归因为模型名时给「从清单里选」去修按钮，点击即打开目录', async () => {
    apiMock.verifyDraftProvider.mockResolvedValueOnce({
      ok: false,
      provider: null,
      target: 'https://api.example.com/v1',
      network_scope: 'public',
      draft: true,
      summary: '未通过（卡在 L2）：模型名不对',
      steps: [
        { grade: 'L0', status: 'pass', summary: 'URL 可达' },
        { grade: 'L2', status: 'fail', summary: '模型名不对，或这个 Key 无权访问该模型', reason: 'model_name' },
      ],
    })
    apiMock.fetchModelCatalog.mockResolvedValueOnce(CATALOG)
    const container = mount()
    await openNewProvider(container)
    typeInto(container, 'provider-base-url', 'https://api.example.com/v1')
    typeInto(container, 'provider-model-name', 'some-model')
    typeInto(container, 'provider-api-key', 'sk-test')
    await click(container.querySelector('[data-testid="provider-test"]') as HTMLButtonElement)

    const fixArea = container.querySelector('[data-testid="probe-fix-L2"]') as HTMLElement
    expect(fixArea.textContent).toContain('可以直接修')
    await click(Array.from(fixArea.querySelectorAll('button')).find((b) => b.textContent?.includes('从模型清单里选一个')) as HTMLButtonElement)
    expect(apiMock.fetchModelCatalog).toHaveBeenCalled()
    expect(container.querySelector('[data-testid="model-catalog-panel"]')).toBeTruthy()
  })

  it('L2 归因为地址时一键改成兼容模式地址（替换而非追加）', async () => {
    apiMock.verifyDraftProvider.mockResolvedValueOnce({
      ok: false,
      provider: null,
      target: 'https://maas.aliyuncs.com/api/v1',
      network_scope: 'public',
      draft: true,
      summary: '未通过（卡在 L2）：端点没有该对话路由',
      steps: [
        { grade: 'L0', status: 'pass', summary: 'URL 可达' },
        { grade: 'L2', status: 'fail', summary: '端点没有该对话路由（HTTP 404 且响应体为空）', reason: 'base_url' },
      ],
    })
    const container = mount()
    await openNewProvider(container)
    typeInto(container, 'provider-base-url', 'https://maas.aliyuncs.com/api/v1')
    typeInto(container, 'provider-model-name', 'some-model')
    typeInto(container, 'provider-api-key', 'sk-test')
    await click(container.querySelector('[data-testid="provider-test"]') as HTMLButtonElement)

    const fixArea = container.querySelector('[data-testid="probe-fix-L2"]') as HTMLElement
    expect(fixArea.textContent).toContain('改为兼容模式地址')
    await click(Array.from(fixArea.querySelectorAll('button')).find((b) => b.textContent?.includes('改为兼容模式地址')) as HTMLButtonElement)
    expect((container.querySelector('[data-testid="provider-base-url"]') as HTMLInputElement).value)
      .toBe('https://maas.aliyuncs.com/compatible-mode/v1')
  })

  it('归因给不出可执行动作时不造按钮（如超时）', async () => {
    apiMock.verifyDraftProvider.mockResolvedValueOnce({
      ok: false,
      provider: null,
      target: 'https://api.example.com/v1',
      network_scope: 'public',
      draft: true,
      summary: '未通过（卡在 L2）：最小调用超时',
      steps: [
        { grade: 'L0', status: 'pass', summary: 'URL 可达' },
        { grade: 'L2', status: 'fail', summary: '最小调用超时（10s）：模型可能不可用或响应过慢', reason: 'timeout' },
      ],
    })
    const container = mount()
    await openNewProvider(container)
    typeInto(container, 'provider-base-url', 'https://api.example.com/v1')
    typeInto(container, 'provider-model-name', 'some-model')
    typeInto(container, 'provider-api-key', 'sk-test')
    await click(container.querySelector('[data-testid="provider-test"]') as HTMLButtonElement)
    expect(container.querySelector('[data-testid="probe-fix-L2"]')).toBeNull()
  })

  it('用户看不懂的英文异常只收进「技术细节」，不进结论', async () => {
    apiMock.verifyDraftProvider.mockResolvedValueOnce({
      ok: false,
      provider: null,
      target: 'https://api.example.com/v1',
      network_scope: 'public',
      draft: true,
      summary: '未通过（卡在 L2）',
      steps: [
        { grade: 'L0', status: 'pass', summary: 'URL 可达' },
        {
          grade: 'L2',
          status: 'fail',
          summary: 'Key 无效或没有权限 —— 请重新粘贴 Key（注意别带多余空格）',
          reason: 'api_key',
          raw: 'openai.AuthenticationError: Error code: 401 - invalid_api_key',
        },
      ],
    })
    const container = mount()
    await openNewProvider(container)
    typeInto(container, 'provider-base-url', 'https://api.example.com/v1')
    typeInto(container, 'provider-model-name', 'some-model')
    typeInto(container, 'provider-api-key', 'sk-test')
    await click(container.querySelector('[data-testid="provider-test"]') as HTMLButtonElement)

    const summary = container.querySelector('[data-testid="probe-step-summary-L2"]') as HTMLElement
    expect(summary.textContent).toContain('Key 无效')
    expect(summary.textContent).not.toContain('AuthenticationError')
    // 英文原文保留在折叠的「技术细节」里供排障，不与结论混排
    expect(container.textContent).toContain('技术细节')
    expect(container.textContent).toContain('openai.AuthenticationError')
  })

  it('抽屉底部按钮收敛为 3 个，「完整测试」收进高级设置', async () => {
    const container = mount()
    await openNewProvider(container)
    const footer = Array.from(container.querySelectorAll('div'))
      .find((node) => node.className.includes('justify-end') && node.textContent === '取消测试连接保存')
    expect(footer, '底部应为 取消/测试连接/保存 三个按钮').toBeTruthy()
    // 深度切换存在，且没有第二个常驻测试按钮
    expect(container.querySelector('[data-testid="provider-probe-depth"]')).toBeTruthy()
    expect(container.querySelectorAll('[data-testid="provider-test"]')).toHaveLength(1)
    expect(container.textContent).not.toContain('完整测试中')
  })
})

describe('ProvidersTab 列表筛选/搜索与角色占用徽标（B3）', () => {
  it('搜索词匹配显示名与模型名，不匹配的供应商隐藏并显示计数', () => {
    const container = mount(false, true)
    expect(container.querySelector('[data-testid="provider-filter-count"]')!.textContent).toBe('2/2 家')

    // 「voice」只出现在 Qwen Token Plan 的模型名里
    typeInto(container, 'provider-search', 'voice')
    expect(container.querySelector('[data-testid="provider-filter-count"]')!.textContent).toBe('1/2 家')
    const cards = container.querySelectorAll('[data-testid="provider-card"]')
    expect(cards).toHaveLength(1)
    expect(cards[0].textContent).toContain('Qwen Token Plan')
  })

  it('用途筛选按该供应商下任一模型命中即保留', () => {
    const container = mount(false, true)
    // 两家都有 rerank 模型
    choose(container, 'provider-kind-filter', 'rerank')
    expect(container.querySelector('[data-testid="provider-filter-count"]')!.textContent).toBe('2/2 家')
    // vision 只有 Qwen Token Plan 有
    choose(container, 'provider-kind-filter', 'vision')
    expect(container.querySelectorAll('[data-testid="provider-card"]')).toHaveLength(1)
  })

  it('状态筛选区分已验证/未验证，全部未探测时「已验证」给空态', () => {
    const container = mount(false, true)
    choose(container, 'provider-status-filter', 'unverified')
    expect(container.querySelector('[data-testid="provider-filter-count"]')!.textContent).toBe('2/2 家')
    choose(container, 'provider-status-filter', 'verified')
    expect(container.querySelector('[data-testid="provider-filter-count"]')!.textContent).toBe('0/2 家')
    expect(container.textContent).toContain('没有匹配当前筛选条件')
  })

  it('角色占用徽标逐角色渲染，点击触发 onGoToRoles 前往改绑', async () => {
    const onGoToRoles = vi.fn()
    const container = mount(false, false, { usedByRoles: ['doc', 'fallback'], onGoToRoles })

    const docBadge = container.querySelector('[data-testid="used-by-role-doc"]') as HTMLButtonElement
    const fallbackBadge = container.querySelector('[data-testid="used-by-role-fallback"]') as HTMLButtonElement
    expect(docBadge).toBeTruthy()
    expect(fallbackBadge).toBeTruthy()
    expect(container.textContent).toContain('使用中，移除前需先改绑')
    // 被占用的模型仍不可移除
    const removeButton = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('移除') && button.getAttribute('aria-label')?.includes('qwen3.7-plus')) as HTMLButtonElement
    expect(removeButton?.disabled).toBe(true)

    await click(docBadge)
    expect(onGoToRoles).toHaveBeenCalledWith('doc')
  })

  it('未传 onGoToRoles 时徽标仅展示不可点', () => {
    const container = mount(false, false, { usedByRoles: ['doc'] })
    const badge = container.querySelector('[data-testid="used-by-role-doc"]') as HTMLButtonElement
    expect(badge.disabled).toBe(true)
  })
})

describe('ProvidersTab 供应商删除（2026-09-22 拍板：软删 + 关联一并停用）', () => {
  it('自建无绑定供应商显示可点删除按钮，确认后调用 deleteProvider 并刷新', async () => {
    const onChanged = vi.fn(async () => undefined)
    apiMock.deleteProvider.mockResolvedValue({
      providerId: 'custom-host',
      displayName: '自建供应商',
      removedModels: ['free-model'],
      softDeleted: true,
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    act(() => root.render(
      <ProvidersTab
        providers={[{
          id: 'custom-host',
          displayName: '自建供应商',
          driver: 'openai',
          baseUrl: 'https://custom.example/v1',
          networkScope: 'public',
          billing: 'metered',
          isBuiltin: false,
          enabled: true,
          modelCount: 1,
          modelName: 'free-model',
          modelKind: 'chat',
          models: [{ name: 'free-model', modelKind: 'chat' }],
          credential: { configured: true, fingerprint: 'f1', last4: 'a1b2', rotatedAt: null, rotatedBy: null },
          lastProbe: null,
        }]}
        defaultModels={{}}
        source="db"
        canAdmin
        onChanged={onChanged}
      />,
    ))
    mounted.push({ container, root })

    const deleteButton = container.querySelector(
      '[aria-label="删除供应商 自建供应商"]',
    ) as HTMLButtonElement
    expect(deleteButton).toBeTruthy()
    expect(deleteButton.disabled).toBe(false)

    await click(deleteButton)
    // 确认弹窗出现并展示软删范围（价格关闭/专项停用/密钥保留）
    const dialog = container.querySelector('[role="dialog"][aria-label="删除供应商 自建供应商"]')
    expect(dialog).toBeTruthy()
    expect(dialog!.textContent).toContain('软删')
    expect(dialog!.textContent).toContain('专项通道停用')

    await click(findButton(dialog as HTMLElement, '确认删除'))
    expect(apiMock.deleteProvider).toHaveBeenCalledWith('custom-host')
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('名下模型被角色占用时删除按钮禁用并提示先改绑', () => {
    const container = mount(false, false, {
      customProvider: { id: 'custom-host', displayName: '自建供应商', usedByRoles: ['doc'] },
    })
    const deleteButton = container.querySelector(
      '[aria-label="删除供应商 自建供应商"]',
    ) as HTMLButtonElement
    expect(deleteButton).toBeTruthy()
    expect(deleteButton.disabled).toBe(true)
    expect(deleteButton.title).toContain('改绑')
  })

  it('内置供应商也渲染删除按钮（特殊类退役 2026-09-22）', () => {
    const container = mount()
    const deleteButton = container.querySelector('[aria-label="删除供应商 Qwen Token Plan"]') as HTMLButtonElement | null
    expect(deleteButton).toBeTruthy()
  })

  it('后端 409 的拒绝原因直接展示在确认弹窗内', async () => {
    apiMock.deleteProvider.mockRejectedValue(new Error('供应商 custom-host 仍绑定在专项通道 embedding 上，不能删除'))
    const container = mount(false, false, {
      customProvider: { id: 'custom-host', displayName: '自建供应商' },
    })

    await click(container.querySelector('[aria-label="删除供应商 自建供应商"]') as HTMLButtonElement)
    const dialog = container.querySelector('[role="dialog"][aria-label="删除供应商 自建供应商"]') as HTMLElement
    expect(dialog).toBeTruthy()

    await click(findButton(dialog, '确认删除'))
    expect(dialog.textContent).toContain('专项通道 embedding')
  })
})

afterEach(() => {
  for (const mock of Object.values(apiMock)) mock.mockReset()
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

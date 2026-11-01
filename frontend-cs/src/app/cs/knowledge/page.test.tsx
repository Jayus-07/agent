// cs-workstation-v2 P7：知识助手（未解决聚合/展开变体/生成草稿/话术管理/检索/能力差距）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const api = vi.hoisted(() => ({
  listUnresolved: vi.fn(async () => ({
    items: [
      {
        question: '发票怎么开',
        variants: { 发票怎么开: 12, 怎么开发票: 7 },
        hits: 23,
        last_seen: '2026-10-31T10:12:00Z',
        source: 'rag_miss',
        source_display: '拒答',
      },
      {
        question: '可以发顺丰吗',
        variants: { 可以发顺丰吗: 9 },
        hits: 9,
        last_seen: '2026-10-29T08:00:00Z',
        source: 'sql_empty',
        source_display: '查询空结果',
      },
    ],
    total: 2,
  })),
  createFaqDraft: vi.fn(async (question: string) => ({
    faq_id: 123,
    faq_key: question,
    status: 'draft',
  })),
  listCanned: vi.fn(async () => [
    {
      id: 1,
      scope: 'personal',
      owner_agent_id: 'me',
      title: '开场问候',
      content: '您好，很高兴为您服务',
      category: '通用',
      enabled: true,
      updated_at: '2026-10-31T08:00:00Z',
    },
    {
      id: 2,
      scope: 'team',
      owner_agent_id: null,
      title: '退款政策',
      content: '7 天无理由',
      category: '售后',
      enabled: false,
      updated_at: '2026-10-31T08:00:00Z',
    },
  ]),
  createCanned: vi.fn(async (body: { title: string }) => ({
    id: 3,
    scope: 'personal',
    title: body.title,
    content: 'x',
    category: '通用',
    enabled: true,
  })),
  updateCanned: vi.fn(async (id: number, body: { enabled?: boolean }) => ({
    id,
    scope: 'personal',
    title: '开场问候',
    content: 'x',
    category: '通用',
    enabled: body.enabled ?? false,
  })),
  knowledgeSearch: vi.fn(async () => ({
    items: [
      {
        question: '退款时效是多久',
        answer: '退款申请 1-3 个工作日内原路退回。',
        score: 1.0,
        matched: true,
      },
      {
        question: '退款进度怎么查',
        answer: '可在我的订单查看退款状态。',
        score: 0.62,
        matched: false,
      },
    ],
    query: '退款',
  })),
}))

vi.mock('@/cs2/workspace/api', () => api)

import Cs2KnowledgePage from '@/app/cs/knowledge/page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (mounted) {
    act(() => mounted?.root.unmount())
    mounted.container.remove()
    mounted = null
  }
  vi.clearAllMocks()
})

async function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<Cs2KnowledgePage />)
  })
  mounted = { container, root }
  return container
}

function setInputValue(
  el: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

function findButton(c: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(c.querySelectorAll('button')).find((b) =>
    b.textContent?.includes(text),
  )
}

// 精确匹配（避免「检索」误中「知识检索」Tab 按钮）
function findButtonExact(
  c: HTMLElement,
  text: string,
): HTMLButtonElement | undefined {
  return Array.from(c.querySelectorAll('button')).find(
    (b) => b.textContent?.trim() === text,
  )
}

describe('Cs2KnowledgePage（P7 知识助手）', () => {
  it('未解决问题聚合渲染：来源徽章/命中数；点行展开变体；生成草稿', async () => {
    const c = await mount()
    await act(async () => {})
    expect(c.textContent).toContain('发票怎么开')
    expect(c.textContent).toContain('拒答')
    expect(c.textContent).toContain('查询空结果')
    // 点行展开原始查询聚合
    await act(async () => {
      ;(c.querySelector('tbody tr td') as HTMLElement | null)?.click()
    })
    expect(c.textContent).toContain('原始查询聚合')
    expect(c.textContent).toContain('发票怎么开 ×12')
    expect(c.textContent).toContain('怎么开发票 ×7')
    // 生成 FAQ 草稿
    await act(async () => {
      findButton(c, '生成FAQ草稿')?.click()
    })
    expect(api.createFaqDraft).toHaveBeenCalledWith('发票怎么开', [
      '发票怎么开',
      '怎么开发票',
    ])
    expect(c.textContent).toContain('草稿已生成')
    expect(c.textContent).toContain('待管理端审核发布')
  })

  it('话术管理：个人/团队 Tab 计数；新增弹窗保存；启用开关', async () => {
    const c = await mount()
    await act(async () => {})
    await act(async () => {
      findButton(c, '话术管理')?.click()
    })
    expect(api.listCanned).toHaveBeenCalled()
    expect(c.textContent).toContain('个人话术（1）')
    expect(c.textContent).toContain('团队话术（1）')
    expect(c.textContent).toContain('开场问候')

    // 切团队 Tab → 团队行启用开关（disabled=false → 打开）
    await act(async () => {
      findButton(c, '团队话术')?.click()
    })
    const toggle = c.querySelector(
      'input[aria-label="启用退款政策"]',
    ) as HTMLInputElement
    await act(async () => {
      toggle.click()
    })
    expect(api.updateCanned).toHaveBeenCalledWith(2, { enabled: true })

    // 切回个人 Tab → 新增弹窗
    await act(async () => {
      findButton(c, '个人话术')?.click()
    })
    await act(async () => {
      findButton(c, '新增话术')?.click()
    })
    const title = c.querySelector(
      'input[aria-label="话术标题"]',
    ) as HTMLInputElement
    const content = c.querySelector(
      'textarea[aria-label="话术内容"]',
    ) as HTMLTextAreaElement
    await act(async () => {
      setInputValue(title, '催单安抚')
      setInputValue(content, '非常理解您的心情，马上为您催办')
    })
    await act(async () => {
      findButtonExact(c, '保存到个人')?.click()
    })
    expect(api.createCanned).toHaveBeenCalledWith({
      title: '催单安抚',
      content: '非常理解您的心情，马上为您催办',
      category: '通用',
      scope: 'personal',
    })
  })

  it('知识检索：触发后结果带置信徽章', async () => {
    const c = await mount()
    await act(async () => {})
    await act(async () => {
      findButton(c, '知识检索')?.click()
    })
    const input = c.querySelector(
      'input[aria-label="知识检索词"]',
    ) as HTMLInputElement
    await act(async () => {
      setInputValue(input, '退款')
    })
    await act(async () => {
      findButtonExact(c, '检索')?.click()
    })
    expect(api.knowledgeSearch).toHaveBeenCalled()
    expect(c.textContent).toContain('退款时效是多久')
    expect(c.textContent).toContain('置信 100%')
    expect(c.textContent).toContain('置信 62%')
  })

  it('能力差距：静态清单渲染', async () => {
    const c = await mount()
    await act(async () => {})
    await act(async () => {
      findButton(c, '能力差距')?.click()
    })
    expect(c.textContent).toContain('用户可能问')
    expect(c.textContent).toContain('退差价')
    expect(c.textContent).toContain('合规')
  })
})

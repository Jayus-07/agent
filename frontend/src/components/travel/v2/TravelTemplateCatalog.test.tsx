import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TravelTemplateV2 } from '@/api/travelV2'

const mocks = vi.hoisted(() => ({ fetchTemplates: vi.fn() }))
vi.mock('@/api/travelV2', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/travelV2')>()),
  fetchTravelTemplatesV2: mocks.fetchTemplates,
}))

import TravelTemplateCatalog from './TravelTemplateCatalog'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function template(): TravelTemplateV2 {
  return {
    slug: 'hangzhou-slow', title: '杭州湖山慢游', destination: '杭州', summary: '湖山与人文路线',
    cover_image_url: null, tags: ['轻松', '自然'],
    content: { brief: { destination: '杭州', day_count: 3, travelers: { adults: 2, children: 1 }, budget: { amount: 2400 } } } as TravelTemplateV2['content'],
  } as TravelTemplateV2
}

let root: Root | null = null
let container: HTMLDivElement | null = null

async function renderCatalog() {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => { root?.render(createElement(TravelTemplateCatalog)) })
}

afterEach(() => {
  if (root) act(() => root?.unmount())
  container?.remove()
  root = null
  container = null
})

beforeEach(() => mocks.fetchTemplates.mockReset())

describe('旅游 V2 模板目录', () => {
  it('展示加载态后列出真实模板详情入口，并标记发现导航', async () => {
    let resolve!: (value: { templates: TravelTemplateV2[] }) => void
    mocks.fetchTemplates.mockReturnValue(new Promise((accept) => { resolve = accept }))
    await renderCatalog()
    expect(container?.textContent).toContain('正在加载旅行模板')

    await act(async () => { resolve({ templates: [template()] }) })
    expect(container?.textContent).toContain('已加载的精选模板')
    expect(container?.textContent).toContain('杭州湖山慢游')
    expect(container?.querySelector('a[href="/travel/templates/hangzhou-slow"]')).not.toBeNull()
    expect(container?.querySelector('nav[aria-label="旅游导航"] a[aria-current="page"]')?.textContent).toBe('旅行灵感')
    expect(container?.querySelector('nav[aria-label="手机导航"] a[aria-current="page"]')?.textContent).toBe('发现')
  })

  it('提供空状态，并在目录加载失败后支持重试', async () => {
    mocks.fetchTemplates.mockRejectedValueOnce(new Error('401')).mockResolvedValueOnce({ templates: [] })
    await renderCatalog()
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(container?.textContent).toContain('模板目录暂时无法加载')
    const retry = [...(container?.querySelectorAll('button') ?? [])].find((button) => button.textContent?.includes('重试'))
    await act(async () => { retry?.click(); await new Promise((resolve) => setTimeout(resolve, 0)) })
    expect(container?.textContent).toContain('暂时没有可浏览的模板')
    expect(mocks.fetchTemplates).toHaveBeenCalledTimes(2)
  })

  it('恰好返回 50 项时只报告本次加载数量和当前展示范围', async () => {
    const templates = Array.from({ length: 50 }, (_, index) => ({
      ...template(),
      slug: `template-${index + 1}`,
    }))
    mocks.fetchTemplates.mockResolvedValue({ templates })
    await renderCatalog()

    expect(container?.textContent).toContain('已加载 50 份 · 当前展示范围')
    expect(container?.textContent).not.toContain('全部精选模板')
    expect(container?.textContent).not.toContain('共 50')
  })
})

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const nav = vi.hoisted(() => ({ pathname: '/cs2/workspace' }))

vi.mock('next/navigation', () => ({ usePathname: () => nav.pathname }))

import WorkstationShell from './WorkstationShell'

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (!mounted) return
  act(() => mounted?.root.unmount())
  mounted.container.remove()
  mounted = null
})

function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<WorkstationShell><p data-testid="content">body</p></WorkstationShell>))
  mounted = { container, root }
  return container
}

describe('WorkstationShell（/cs2 新壳）', () => {
  it('渲染七个导航项且当前路由高亮', () => {
    const container = mount()
    const links = Array.from(container.querySelectorAll('nav a'))
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/cs2/workspace',
      '/cs2/conversations',
      '/cs2/tickets',
      '/cs2/refunds',
      '/cs2/qa',
      '/cs2/dashboard',
      '/cs2/knowledge',
    ])
    const active = container.querySelector('a[aria-current="page"]')
    expect(active?.getAttribute('href')).toBe('/cs2/workspace')
  })

  it('children 渲染在内容区', () => {
    const container = mount()
    expect(container.querySelector('[data-testid="content"]')?.textContent).toBe('body')
  })
})

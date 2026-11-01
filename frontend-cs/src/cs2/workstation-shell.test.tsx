import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const nav = vi.hoisted(() => ({ pathname: '/cs/workspace' }))
const api = vi.hoisted(() => ({
  getMyStatus: vi.fn(async () => ({
    agent_id: 'a1', status: 'online', accepting: true,
    presence_online: true, cs_role: 'agent',
  })),
}))

vi.mock('next/navigation', () => ({ usePathname: () => nav.pathname }))
vi.mock('./workspace/api', () => api)

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

describe('WorkstationShell（/cs 工作台壳，P8 切换）', () => {
  it('agent 角色隐藏 supervisorOnly 项（退款审核/看板），其余高亮', async () => {
    api.getMyStatus.mockResolvedValueOnce({
      agent_id: 'a1', status: 'online', accepting: true,
      presence_online: true, cs_role: 'agent',
    })
    const container = mount()
    await act(async () => {})
    const links = Array.from(container.querySelectorAll('nav a'))
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/cs/workspace',
      '/cs/conversations',
      '/cs/tickets',
      '/cs/qa',
      '/cs/knowledge',
    ])
    const active = container.querySelector('a[aria-current="page"]')
    expect(active?.getAttribute('href')).toBe('/cs/workspace')
  })

  it('supervisor 角色显示全部七项', async () => {
    api.getMyStatus.mockResolvedValueOnce({
      agent_id: 'a1', status: 'online', accepting: true,
      presence_online: true, cs_role: 'supervisor',
    })
    const container = mount()
    await act(async () => {})
    const links = Array.from(container.querySelectorAll('nav a'))
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      '/cs/workspace',
      '/cs/conversations',
      '/cs/tickets',
      '/cs/refunds',
      '/cs/qa',
      '/cs/dashboard',
      '/cs/knowledge',
    ])
  })

  it('children 渲染在内容区', () => {
    const container = mount()
    expect(container.querySelector('[data-testid="content"]')?.textContent).toBe('body')
  })
})

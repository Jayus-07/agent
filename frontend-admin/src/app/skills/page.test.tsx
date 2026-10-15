import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getCapabilities = vi.hoisted(() => vi.fn())
const getMcpServers = vi.hoisted(() => vi.fn())
const getMcpTools = vi.hoisted(() => vi.fn())
const atLeast = vi.hoisted(() => vi.fn())

vi.mock('@/api/registry', () => ({ getCapabilities, getMcpServers, getMcpTools }))
vi.mock('@/lib/auth', () => ({ atLeast }))
vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

import SkillsPage from './page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

beforeEach(() => {
  getCapabilities.mockResolvedValue({
    count: 0,
    routed_count: 0,
    capabilities: [],
    workflows: [],
    skills: [],
  })
  getMcpServers.mockResolvedValue({ servers: [] })
  getMcpTools.mockResolvedValue({ tools: [] })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.clearAllMocks()
})

async function renderPage() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted.push({ container, root })

  await act(async () => {
    root.render(<SkillsPage />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  return container
}

describe('Skill 页面 Tool 注册表入口', () => {
  it('Editor 不会看到只限管理员访问的 Tool 注册表链接', async () => {
    atLeast.mockReturnValue(false)

    const container = await renderPage()

    expect(container.querySelector('a[href="/tools"]')).toBeNull()
  })

  it('Admin 可以访问 Tool 注册表链接', async () => {
    atLeast.mockReturnValue(true)

    const container = await renderPage()

    expect(container.querySelector('a[href="/tools"]')).toBeTruthy()
  })
})

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const apiClient = vi.hoisted(() => ({ request: vi.fn() }))

vi.mock('@/api/client', () => apiClient)

import PagePlaceholder from './PagePlaceholder'

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (!mounted) return
  act(() => mounted?.root.unmount())
  mounted.container.remove()
  mounted = null
  vi.clearAllMocks()
})

async function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() =>
    root.render(
      <PagePlaceholder title="商品中心" phase="P2" description="商城三页" />,
    ),
  )
  mounted = { container, root }
  await act(async () => {})
  return container
}

describe('mall 占位页壳', () => {
  it('渲染标题、期次徽标并执行健康自检', async () => {
    apiClient.request.mockResolvedValue({ status: 'ok' })
    const container = await mount()
    expect(container.querySelector('h1')?.textContent).toBe('商品中心')
    expect(container.textContent).toContain('P2')
    expect(apiClient.request).toHaveBeenCalledWith('/api/health', { timeout: 5000 })
    expect(container.querySelector('[role="status"]')?.textContent).toBe('后端正常')
  })

  it('后端不可达时健康自检显式示警', async () => {
    apiClient.request.mockRejectedValue(new Error('down'))
    const container = await mount()
    expect(container.querySelector('[role="status"]')?.textContent).toBe('后端不可达')
  })
})

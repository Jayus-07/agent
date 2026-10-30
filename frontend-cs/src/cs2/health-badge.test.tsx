import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const apiClient = vi.hoisted(() => ({ request: vi.fn() }))

vi.mock('@/api/client', () => apiClient)

import HealthBadge from './HealthBadge'

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
  act(() => root.render(<HealthBadge />))
  mounted = { container, root }
  await act(async () => {})
  return container
}

describe('cs2 健康自检徽标', () => {
  it('探测 /api/health 成功显示后端正常', async () => {
    apiClient.request.mockResolvedValue({ status: 'ok' })
    const container = await mount()
    expect(apiClient.request).toHaveBeenCalledWith('/api/health', { timeout: 5000 })
    expect(container.querySelector('[role="status"]')?.textContent).toBe('后端正常')
  })

  it('探测失败显示后端不可达', async () => {
    apiClient.request.mockRejectedValue(new Error('down'))
    const container = await mount()
    expect(container.querySelector('[role="status"]')?.textContent).toBe('后端不可达')
  })
})

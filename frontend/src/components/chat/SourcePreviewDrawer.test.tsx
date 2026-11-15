import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import SourcePreviewDrawer from './SourcePreviewDrawer'

vi.mock('@/lib/auth', () => ({
  bearerHeaders: () => ({ Authorization: 'Bearer preview-test' }),
}))

const mounted: { container: HTMLElement; root: Root }[] = []

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('SourcePreviewDrawer', () => {
  it('shows text citations as escaped plain text through the authenticated snapshot endpoint', async () => {
    const text = '# 员工手册\n<script>alert("x")</script>'
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: vi.fn().mockResolvedValue(text),
    })
    vi.stubGlobal('fetch', fetchMock)

    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(createElement(SourcePreviewDrawer, {
        docId: 'manual-doc',
        filename: '员工手册.md',
        onClose: () => {},
      }))
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/rag/documents/manual-doc/file', {
      headers: { Authorization: 'Bearer preview-test' },
    })
    expect(container.querySelector('pre')?.textContent).toBe(text)
    expect(container.querySelector('script')).toBeNull()
  })
})

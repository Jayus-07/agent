import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { knowledgeService } from '@/api/knowledge'

vi.mock('@/components/auth/RoleGate', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-role-gate>{children}</div>,
}))

import PendingReviewPanel from './PendingReviewPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeEach(() => {
  vi.spyOn(knowledgeService, 'getPendingDocs').mockResolvedValue({
    items: [{
      id: 'pending-1',
      doc_id: 'doc-1',
      file_name: '制度文档.pdf',
      doc_type: 'policy',
      business_domain: 'general',
      confidence: 0.91,
      kb_id: 'policy_general',
      summary: '待审核制度',
      quality_score: 88,
      updated_at: '2026-10-02 15:00:00',
    }],
    total: 1,
  })
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

describe('PendingReviewPanel', () => {
  it('保留 admin RoleGate 并展示待复核文档', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }

    act(() => root.render(<PendingReviewPanel />))
    await act(async () => {
      await Promise.resolve()
    })

    expect(container.querySelector('[data-role-gate]')).not.toBeNull()
    expect(container.textContent).toContain('制度文档.pdf')
  })
})

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
      doc_type: 'travel_guide',
      business_domain: 'travel',
      confidence: 0.91,
      kb_id: 'travel',
      summary: '待审核制度',
      quality_score: 88,
      quality_status: 'pass',
      quality_issues: 'filtered_chunks:14(low_chinese_ratio=13,duplicate=1)',
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
    expect(container.textContent).toContain('旅游知识库')
    expect(container.textContent).toContain('文档类型： 旅游指南')
    expect(container.textContent).toContain('业务领域： 旅游')
    expect(container.textContent).toContain('分类置信度： 91%')
    expect(container.textContent).toContain('质量状态： 检查通过')
    expect(container.textContent).toContain('过滤了 14 个分块：中文内容占比偏低 13、内容重复 1')
    expect(container.textContent).not.toContain('travel_guide')
    expect(container.textContent).not.toContain('business_domain')
  })

  it('孤立知识库记录只允许归档，不显示批准按钮', async () => {
    vi.spyOn(knowledgeService, 'getPendingDocs').mockResolvedValue({
      items: [{
        id: 'orphan-1',
        doc_id: 'doc_a_1',
        file_name: 'doc_a.md',
        doc_type: 'general',
        business_domain: 'general',
        confidence: 0,
        kb_id: 'deleted_kb',
        quality_score: 0,
        updated_at: '2026-11-13 10:00:00',
        orphaned_kb: true,
        orphan_reason: '知识库目录中没有此标识',
        quality_status: 'unknown',
      }],
      total: 1,
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }

    act(() => root.render(<PendingReviewPanel />))
    await act(async () => { await Promise.resolve() })

    expect(container.textContent).toContain('孤立登记')
    expect(container.textContent).toContain('归档孤立记录')
    expect(Array.from(container.querySelectorAll('button')).some(button => button.textContent?.trim() === '批准')).toBe(false)
  })
})

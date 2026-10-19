import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const operationApi = vi.hoisted(() => ({ getOperations: vi.fn() }))

vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}))

vi.mock('@/api/knowledge', () => ({
  knowledgeService: { getOperations: operationApi.getOperations },
}))

vi.mock('@/components/shared/Toast', () => ({
  useToast: () => ({ error: vi.fn() }),
}))

import OperationsPanel from './OperationsPanel'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  vi.useRealTimers()
  operationApi.getOperations.mockReset()
  document.body.innerHTML = ''
})

function mount(): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<OperationsPanel />))
  return { root, container }
}

describe('OperationsPanel 活动上传', () => {
  it('显示持久任务 ID 和状态，并在活动任务期间自动刷新', async () => {
    vi.useFakeTimers()
    operationApi.getOperations.mockResolvedValue({
      items: [],
      total: 0,
      active_uploads: [{
        upload_id: 'upload-42',
        celery_task_id: 'celery-42',
        doc_name: 'faq_only.docx',
        doc_id: '',
        kb_id: 'policy_general',
        department: 'general',
        status: 'indexing',
        stage: 'metadata',
        error: '',
        created_at: '2026-10-19 01:00:00',
        updated_at: '2026-10-19 01:00:03',
      }],
    })

    const { root, container } = mount()
    await act(async () => { await Promise.resolve() })

    expect(container.textContent).toContain('faq_only.docx')
    expect(container.textContent).toContain('upload-42')
    expect(container.textContent).toContain('celery-42')
    expect(container.textContent).toContain('处理中 · metadata')

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(operationApi.getOperations).toHaveBeenCalledTimes(2)

    act(() => root.unmount())
  })
})

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

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

describe('OperationsPanel 活动上传', () => {
  it('Trace 链接携带文件、操作和处理身份参数', async () => {
    operationApi.getOperations.mockResolvedValue({
      items: [{
        id: 3507,
        doc_id: 'doc-1',
        doc_name: 'scanned_partial_fail.pdf',
        operation: 'reindex',
        result: 'success',
        created_at: '2026-11-16 19:11:03',
        trace_id: 'trace-1',
        batch_id: null,
        detail: JSON.stringify({ chunk_count: 27, file_hash: '', executor: 'rag_index_worker' }),
        duration_ms: 44313,
        user_id: '1014',
        source: '172.21.0.10 | Web',
      }],
      total: 1,
      active_uploads: [],
    })

    const { root, container } = mount()
    await act(async () => { await Promise.resolve() })

    const link = container.querySelector<HTMLAnchorElement>('a[href*="/knowledge/operations/traces/trace-1"]')
    expect(link).not.toBeNull()
    const url = new URL(link!.href)
    expect(url.searchParams.get('docId')).toBe('doc-1')
    expect(url.searchParams.get('documentName')).toBe('scanned_partial_fail.pdf')
    expect(url.searchParams.get('operation')).toBe('reindex')
    expect(url.searchParams.get('operationAt')).toBe('2026-11-16 19:11:03')
    expect(url.searchParams.get('returnTab')).toBe('operations')
    expect(url.searchParams.has('fileHash')).toBe(false)

    act(() => root.unmount())
  })

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
    expect(container.textContent).toContain('处理中 · 元数据处理')

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(operationApi.getOperations).toHaveBeenCalledTimes(2)

    act(() => root.unmount())
  })

  it('将超过处理时限的记录显示为待核对并降低轮询频率', async () => {
    vi.useFakeTimers()
    operationApi.getOperations.mockResolvedValue({
      items: [],
      total: 0,
      active_uploads: [{
        upload_id: 'upload-stale',
        celery_task_id: 'celery-stale',
        doc_name: 'stale.pdf',
        doc_id: 'doc-stale',
        kb_id: 'policy_general',
        department: 'general',
        status: 'indexing',
        stage: 'embedding',
        error: '',
        created_at: '2026-10-19 01:00:00',
        updated_at: '2026-10-19 01:00:00',
        stale: true,
        inactive_seconds: 7200,
        stale_after_seconds: 3660,
      }],
    })

    const { root, container } = mount()
    await act(async () => { await Promise.resolve() })

    expect(container.textContent).toContain('状态待核对 · 1 个长时间未更新任务')
    expect(container.textContent).toContain('页面每 30 秒复核')
    expect(container.textContent).toContain('stale.pdf')
    expect(container.textContent).toContain('状态待核对 · 已 2 小时未更新（提醒阈值 1 小时）')
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(operationApi.getOperations).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(27000) })
    expect(operationApi.getOperations).toHaveBeenCalledTimes(2)

    act(() => root.unmount())
  })

  it('较早的筛选请求晚返回时不覆盖较新的结果', async () => {
    const first = deferred<any>()
    const second = deferred<any>()
    operationApi.getOperations
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)

    const { root, container } = mount()
    expect(operationApi.getOperations).toHaveBeenCalledTimes(1)

    await act(async () => {
      const select = container.querySelector('select')!
      select.value = 'upload'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(operationApi.getOperations).toHaveBeenCalledTimes(2)

    await act(async () => {
      second.resolve({
        items: [], total: 0,
        active_uploads: [{ upload_id: 'new-upload', doc_name: 'new.docx', status: 'indexing', stage: 'metadata' }],
      })
    })
    await act(async () => {
      first.resolve({
        items: [], total: 0,
        active_uploads: [{ upload_id: 'old-upload', doc_name: 'old.docx', status: 'indexing', stage: 'metadata' }],
      })
    })

    expect(container.textContent).toContain('new.docx')
    expect(container.textContent).not.toContain('old.docx')
    act(() => root.unmount())
  })

  it('请求失败时保留最近一次成功的活动任务', async () => {
    vi.useFakeTimers()
    operationApi.getOperations
      .mockResolvedValueOnce({
        items: [], total: 0,
        active_uploads: [{
          upload_id: 'upload-kept', celery_task_id: 'celery-kept', doc_name: 'still-processing.docx',
          doc_id: '', kb_id: 'policy_general', department: 'general', status: 'indexing', stage: 'metadata',
          error: '', created_at: '', updated_at: '',
        }],
      })
      .mockRejectedValueOnce(new Error('网络暂时不可用'))

    const { root, container } = mount()
    await act(async () => { await Promise.resolve() })
    expect(container.textContent).toContain('still-processing.docx')

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(container.textContent).toContain('still-processing.docx')
    expect(operationApi.getOperations).toHaveBeenCalledTimes(2)

    act(() => root.unmount())
  })
})

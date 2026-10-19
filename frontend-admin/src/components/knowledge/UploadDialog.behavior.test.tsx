import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  uploadDocument: vi.fn(),
  fetchRaw: vi.fn(),
  randomId: vi.fn(() => 'batch-test'),
  toast: {
    info: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    show: vi.fn(),
  },
}))

vi.mock('@/api/knowledge', () => ({
  knowledgeService: { uploadDocument: mocks.uploadDocument },
}))

vi.mock('@/api/client', () => ({
  fetchRaw: mocks.fetchRaw,
  randomId: mocks.randomId,
}))

vi.mock('@/components/shared/Toast', () => ({
  useToast: () => mocks.toast,
}))

import UploadDialog from './UploadDialog'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

function mount(open: boolean, onClose: () => void, onSuccess: () => void) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<UploadDialog open={open} onClose={onClose} onSuccess={onSuccess} />))
  return { root, container }
}

describe('UploadDialog 后台上传体验', () => {
  it('关闭窗口后保留上传任务，并通知受理和最终结果', async () => {
    mocks.fetchRaw.mockImplementation(async (url: string) => {
      const body = url.includes('knowledge-bases')
        ? { knowledge_bases: [{ id: 'policy_general', name: '政策库', depts: [{ id: 'general', label: '通用' }] }] }
        : { ready: true }
      return new Response(JSON.stringify(body), { status: 200 })
    })

    let acceptUpload!: (value: { ok: boolean; doc: { doc_id: string } }) => void
    let onAccepted!: (receipt: { upload_id: string; filename: string }) => void
    mocks.uploadDocument.mockImplementation(
      (_file, _progress, _batchId, _kbId, _department, accepted) => {
        onAccepted = accepted
        return new Promise(resolve => { acceptUpload = resolve })
      },
    )

    const onClose = vi.fn()
    const onSuccess = vi.fn()
    const { root, container } = mount(true, onClose, onSuccess)
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })

    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [new File(['Q: 年假?\nA: 按制度执行'], 'faq_only.docx')],
    })
    await act(async () => {
      input.dispatchEvent(new Event('change', { bubbles: true }))
    })

    const uploadButton = Array.from(container.querySelectorAll('button'))
      .find(button => button.textContent?.includes('上传并索引'))
    expect(uploadButton).toBeTruthy()
    await act(async () => {
      uploadButton!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await Promise.resolve()
    })

    const backgroundButton = Array.from(container.querySelectorAll('button'))
      .find(button => button.textContent?.includes('转入后台'))
    expect(backgroundButton).toBeTruthy()
    act(() => backgroundButton!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(mocks.toast.info).toHaveBeenCalledWith(
      '上传会继续在后台运行；受理后可在操作日志查看处理中状态',
    )

    act(() => root.render(<UploadDialog open={false} onClose={onClose} onSuccess={onSuccess} />))
    act(() => onAccepted({ upload_id: 'upload-42', filename: 'faq_only.docx' }))
    expect(mocks.toast.success).toHaveBeenCalledWith(
      '后台任务已受理：faq_only.docx（upload-42）',
    )

    await act(async () => {
      acceptUpload({ ok: true, doc: { doc_id: 'doc-42' } })
      await Promise.resolve()
    })
    expect(mocks.toast.success).toHaveBeenCalledWith('后台处理完成：1 个文件成功')
    expect(onSuccess).toHaveBeenCalledTimes(1)

    act(() => root.unmount())
  })
})

'use client'

/**
 * SourcePreviewDrawer — 原文快照预览抽屉（原文定位 P1，2026-10-03）。
 *
 * 企业口径：预览走平台快照（DOCS_DIRECTORY 存的上传原件），不走活链——
 * 文档被编辑/移动后，引用时刻的答案必须与用户点开看到的原文一致。
 *
 * 实现要点：
 * - 二进制端点带 Authorization，<iframe>/<embed> 无法自定义头 →
 *   fetch blob → pdfjs getDocument({ data }) 直接喂字节；
 * - pdfjs 动态 import（避免 SSR 拉起 worker，也缩小首屏包体）；
 * - 初始页 = 引用页 pages[0]，页脚可翻页；bbox 页内高亮属 P2（元数据已有）。
 */
import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, FileText, Loader2, X } from 'lucide-react'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { bearerHeaders } from '@/lib/auth'
import { formatSourcePages } from '@/lib/ragAnswer'

interface SourcePreviewDrawerProps {
  docId: string
  filename: string
  /** 引用页（升序，后端切分层回映射；抽屉初始定位到第一引用页） */
  pages?: number[]
  onClose: () => void
}

export default function SourcePreviewDrawer({ docId, filename, pages, onClose }: SourcePreviewDrawerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  // v6 语义：销毁走 loadingTask.destroy()（会连带释放文档），doc 实例单独持有用于翻页
  const taskRef = useRef<{ destroy: () => void } | null>(null)
  const pdfRef = useRef<PDFDocumentProxy | null>(null)
  const renderSeq = useRef(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [numPages, setNumPages] = useState(0)
  // 初始页 = 第一引用页（非法值兜底 1）
  const [current, setCurrent] = useState(() => {
    const first = pages?.[0]
    return typeof first === 'number' && first >= 1 ? first : 1
  })

  // 拉取快照并解析 PDF（组件卸载/换文档时销毁实例防泄漏）
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    setNumPages(0)
    ;(async () => {
      try {
        const resp = await fetch(`/api/rag/documents/${docId}/file`, {
          headers: bearerHeaders(),
        })
        if (cancelled) return
        if (!resp.ok) {
          setError(resp.status === 404 ? '原文不存在或无权限查看' : '原文服务暂不可用，请稍后重试')
          setLoading(false)
          return
        }
        const data = await resp.arrayBuffer()
        if (cancelled) return
        const pdfjs = await import('pdfjs-dist')
        // worker 走 public/ 静态文件（绕开 webpack/Terser 对 .mjs 的处理，
        // pdfjs v6 worker 含 import.meta 会编译失败，见 2026-10-07 部署记录）
        pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'
        const task = pdfjs.getDocument({ data })
        taskRef.current = task
        const pdf = await task.promise
        if (cancelled) {
          void task.destroy()
          return
        }
        pdfRef.current = pdf
        setNumPages(pdf.numPages)
        setLoading(false)
      } catch {
        if (!cancelled) {
          setError('原文加载失败，请稍后重试')
          setLoading(false)
        }
      }
    })()
    return () => {
      cancelled = true
      if (taskRef.current) {
        void taskRef.current.destroy()
        taskRef.current = null
      }
      pdfRef.current = null
    }
  }, [docId])

  // 渲染当前页到 canvas（序号守卫防快速翻页的乱序回写）
  useEffect(() => {
    const pdf = pdfRef.current
    const canvas = canvasRef.current
    if (!pdf || !canvas || loading || numPages === 0) return
    let cancelled = false
    const seq = ++renderSeq.current
    ;(async () => {
      const page = await pdf.getPage(Math.min(Math.max(current, 1), numPages))
      if (cancelled || seq !== renderSeq.current || !page) return
      const viewport = page.getViewport({ scale: 1.6 })
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      canvas.width = viewport.width
      canvas.height = viewport.height
      // v6 起 RenderParameters 必须携带 canvas（canvasContext 仍用于绘制）
      await page.render({ canvas, canvasContext: ctx, viewport }).promise
    })().catch(() => {})
    return () => {
      cancelled = true
    }
  }, [current, loading, numPages])

  // 键盘：Esc 关闭、左右翻页
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowLeft') setCurrent((v) => Math.max(1, v - 1))
      if (e.key === 'ArrowRight') setCurrent((v) => Math.min(numPages || v + 1, v + 1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, numPages])

  const pagesLabel = formatSourcePages(pages)

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`原文预览：${filename}`}>
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative flex h-full w-[min(760px,92vw)] flex-col bg-white shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-3">
          <FileText className="h-4 w-4 shrink-0 text-text-muted" />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">{filename}</span>
          {pagesLabel && (
            <span className="shrink-0 rounded-md bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
              引用 {pagesLabel}
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-muted hover:bg-gray-100"
            aria-label="关闭预览"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto bg-gray-100 p-4">
          {loading && (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-text-muted">
              <Loader2 className="h-4 w-4 animate-spin" /> 原文加载中…
            </div>
          )}
          {!loading && error && (
            <div className="flex h-full items-center justify-center text-sm text-text-muted">{error}</div>
          )}
          <canvas ref={canvasRef} className={`mx-auto block bg-white shadow-card ${loading || error ? 'hidden' : ''}`} />
        </div>

        {!loading && !error && numPages > 0 && (
          <div className="flex items-center justify-center gap-3 border-t border-border-subtle px-4 py-2.5">
            <button
              type="button"
              onClick={() => setCurrent((v) => Math.max(1, v - 1))}
              disabled={current <= 1}
              className="rounded-lg border border-border-subtle p-1.5 text-text-muted hover:bg-gray-50 disabled:opacity-40"
              aria-label="上一页"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-xs text-text-muted">
              第 {current} / {numPages} 页
            </span>
            <button
              type="button"
              onClick={() => setCurrent((v) => Math.min(numPages, v + 1))}
              disabled={current >= numPages}
              className="rounded-lg border border-border-subtle p-1.5 text-text-muted hover:bg-gray-50 disabled:opacity-40"
              aria-label="下一页"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

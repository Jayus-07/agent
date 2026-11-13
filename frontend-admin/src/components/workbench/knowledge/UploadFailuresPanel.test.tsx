import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { knowledgeService } from '@/api/knowledge'

vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) =>
    <a href={href} {...props}>{children}</a>,
}))

import UploadFailuresPanel from './UploadFailuresPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

beforeEach(() => {
  vi.spyOn(knowledgeService, 'listUploadFailures').mockResolvedValue({
    ok: true,
    total: 5,
    counts: { async_queue: 4, quality_gate: 1 },
    items: [{
      upload_id: 'registry:UF-09-01',
      doc_id: 'UF-09-01',
      file_path: '/app/data/docs/report.pdf',
      record_kind: 'quality_gate',
      retryable: false,
      error: 'quality_anomaly:prepaid_integrity(ocr_coverage_partial)',
      failure_details: '切分缺 3 处，表格少 4 行、2 个单元格',
      trace_id: 'trace-1',
      created_at: '2026-11-13 10:00:00',
    }],
  })
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

describe('UploadFailuresPanel', () => {
  it('区分异步任务与质量失败，并显示 Trace 入口', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }

    act(() => root.render(<UploadFailuresPanel />))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    expect(container.textContent).toContain('待处理任务 4 项')
    expect(container.textContent).toContain('质量门禁失败 1 份')
    expect(container.textContent).toContain('report.pdf')
    expect(container.textContent).toContain('切分缺 3 处，表格少 4 行、2 个单元格')
    expect(container.textContent).toContain('查看 Trace')
    expect(container.querySelector('a[href="/knowledge/operations/traces/trace-1"]')).not.toBeNull()
  })

  it('明确显示历史记录没有可用 Trace', async () => {
    vi.spyOn(knowledgeService, 'listUploadFailures').mockResolvedValueOnce({
      ok: true,
      total: 1,
      counts: { async_queue: 0, quality_gate: 1 },
      items: [{
        upload_id: 'registry:UF-14-01',
        doc_id: 'UF-14-01',
        file_path: '/app/data/docs/progit_en_501p.pdf',
        record_kind: 'quality_gate',
        retryable: false,
        trace_id: '',
        trace_status: 'missing_historical',
      }],
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }

    act(() => root.render(<UploadFailuresPanel />))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    expect(container.textContent).toContain('无历史 Trace')
    expect(container.querySelector('a[href*="/traces/"]')).toBeNull()
  })

  it('按真实接口的72条响应显示异步失败、质量失败和可追溯入口', async () => {
    const items = Array.from({ length: 72 }, (_, index) => ({
      upload_id: `upload-${index}`,
      file_path: `/app/data/docs/sample-${index}.pdf`,
      record_kind: index >= 68 ? 'quality_gate' : 'async_queue',
      error: 'sample failure',
      trace_id: index < 5 ? `trace-${index}` : '',
      trace_status: index < 5 ? 'available' : 'missing_historical',
      created_at: '2026-11-13 10:00:00',
    }))
    items[0] = {
      ...items[0],
      upload_id: 'b8ef59d4404c',
      file_path: '/app/data/docs/scanned_partial_fail.pdf',
      trace_id: '90c00499af23',
    }
    vi.spyOn(knowledgeService, 'listUploadFailures').mockResolvedValueOnce({
      ok: true,
      total: 72,
      counts: { async_queue: 68, quality_gate: 4 },
      items,
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }

    act(() => root.render(<UploadFailuresPanel />))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    expect(container.textContent).toContain('异常 72 项')
    expect(container.textContent).toContain('待处理任务 68 项')
    expect(container.textContent).toContain('质量门禁失败 4 份')
    expect(container.querySelectorAll('tbody tr')).toHaveLength(72)
    expect(container.querySelector('a[href="/knowledge/operations/traces/90c00499af23"]'))
      .not.toBeNull()
  })

  it('把五种已观察到的失败说清楚，并将部门码翻译成中文', async () => {
    vi.spyOn(knowledgeService, 'listUploadFailures').mockResolvedValueOnce({
      ok: true,
      total: 5,
      counts: { async_queue: 5, quality_gate: 0 },
      items: [
        { upload_id: 'budget', file_path: '/docs/scanned.pdf', department: 'general', record_kind: 'async_queue', error: '请求预算已超限: request_tokens', trace_id: 'trace-budget' },
        { upload_id: 'ocr', file_path: '/docs/ocr.pdf', department: 'general', record_kind: 'async_queue', error: '质量门禁硬异常(付费前预检): ocr_coverage_partial', trace_id: 'trace-ocr' },
        { upload_id: 'audit', file_path: '/docs/audit.pdf', department: 'general', record_kind: 'async_queue', error: '质量门禁硬异常(付费前预检): parser_pdf_source_text_audit_gap', trace_id: 'trace-audit' },
        { upload_id: 'coverage', file_path: '/docs/coverage.pdf', department: 'general', record_kind: 'async_queue', error: '质量门禁硬异常(付费前预检): coverage_gap', trace_id: 'trace-coverage' },
        { upload_id: 'empty', file_path: '/docs/empty.pdf', department: 'general', record_kind: 'async_queue', error: 'cleaned_chars(cleaned_chars=0 < 20)', trace_id: 'trace-empty' },
      ],
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }

    act(() => root.render(<UploadFailuresPanel />))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })

    expect(container.textContent).toContain('通用')
    expect(container.textContent).toContain('超出 Token 预算')
    expect(container.textContent).toContain('OCR 只覆盖了部分页面')
    expect(container.textContent).toContain('未能完成逐页核对')
    expect(container.textContent).toContain('原文有内容未进入切分结果')
    expect(container.textContent).toContain('提取文本过短或为空')
    expect(container.textContent).not.toContain('解析出 0 个分块')
  })
})

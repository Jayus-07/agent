import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Source } from '@/lib/types'
import SourceCard from './SourceCard'

vi.mock('./SourcePreviewDrawer', () => ({
  default: (props: { docId: string; filename: string; pages?: number[] }) => createElement(
    'div',
    {
      'data-testid': 'source-preview',
      'data-doc-id': props.docId,
      'data-pages': JSON.stringify(props.pages),
    },
    props.filename,
  ),
}))

const mounted: { container: HTMLElement; root: Root }[] = []

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('SourceCard citation details', () => {
  it('matches a cited PDF page to its number and section without a score label', () => {
    const source: Source = {
      index: 2,
      filename: '退款处理制度.pdf',
      doc_type: 'policy',
      type_label: '制度',
      score: 0.87,
      pages: [4, 5],
      section: '退款审批',
      doc_id: 'policy-doc',
      version_id: 'v2',
      effective_from: '2026-01-01',
      effective_to: '2026-12-31',
    }

    const html = renderToStaticMarkup(createElement(SourceCard, {
      sources: [source],
      showCitationIndex: true,
      showScore: false,
    }))

    expect(html).toContain('[2]')
    expect(html).toContain('退款审批')
    expect(html).toContain('第 4-5 页')
    expect(html).toContain('版本 v2')
    expect(html).toContain('有效期 2026-01-01 至 2026-12-31')
    expect(html).toContain('点击查看原文')
    expect(html).not.toContain('0.87')
  })

  it('keeps a source without a document ID non-clickable', () => {
    const html = renderToStaticMarkup(createElement(SourceCard, {
      sources: [{
        index: 1,
        filename: '员工手册.md',
        doc_type: 'policy',
        type_label: '制度',
        score: null,
      }],
      showCitationIndex: true,
      showScore: false,
    }))

    expect(html).toContain('[1]')
    expect(html).toContain('disabled=""')
    expect(html).not.toContain('点击查看原文')
  })

  it('opens a text citation with a document ID even when it has no page number', async () => {
    const source: Source = {
      index: 3,
      filename: '员工手册.md',
      doc_type: 'policy',
      type_label: '制度',
      score: null,
      doc_id: 'manual-doc',
    }
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(createElement(SourceCard, {
        sources: [source],
        showCitationIndex: true,
        showScore: false,
      }))
    })

    const sourceButton = container.querySelector('button')
    expect(sourceButton?.disabled).toBe(false)
    expect(sourceButton?.title).toContain('点击查看原文')
    await act(async () => sourceButton?.click())

    expect(container.querySelector('[data-testid="source-preview"]')?.getAttribute('data-doc-id'))
      .toBe('manual-doc')
  })

  it('keeps unsupported binary citations non-clickable', () => {
    const html = renderToStaticMarkup(createElement(SourceCard, {
      sources: [{
        index: 1,
        filename: '员工手册.docx',
        doc_type: 'policy',
        type_label: '制度',
        score: null,
        doc_id: 'manual-doc',
      }],
      showCitationIndex: true,
      showScore: false,
    }))

    expect(html).toContain('disabled=""')
    expect(html).not.toContain('点击查看原文')
  })

  it('opens the source preview for a page-located PDF citation', async () => {
    const source: Source = {
      index: 1,
      filename: '退款处理制度.pdf',
      doc_type: 'policy',
      type_label: '制度',
      score: null,
      pages: [4],
      doc_id: 'policy-doc',
    }
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    await act(async () => {
      root.render(createElement(SourceCard, {
        sources: [source],
        showCitationIndex: true,
        showScore: false,
      }))
    })

    const sourceButton = container.querySelector('button')
    expect(sourceButton?.disabled).toBe(false)
    await act(async () => sourceButton?.click())

    expect(
      container.querySelector('[data-testid="source-preview"]')
        ?.getAttribute('data-doc-id'),
    ).toBe('policy-doc')
    expect(
      container.querySelector('[data-testid="source-preview"]')
        ?.getAttribute('data-pages'),
    ).toBe('[4]')
  })
})

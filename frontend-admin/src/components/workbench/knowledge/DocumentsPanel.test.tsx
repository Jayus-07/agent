import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { knowledgeService } from '@/api/knowledge'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/components/knowledge/UploadDialog', () => ({ default: () => null }))
vi.mock('@/components/shared/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

import DocumentsPanel from './DocumentsPanel'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { root: Root; container: HTMLDivElement } | null = null

const documentFixture = {
  id: 'doc-1',
  name: '旅游测试资料.md',
  file_name: '旅游测试资料.md',
  doc_type: 'general',
  status: 'active',
  quality_status: 'pass',
  quality_score: 100,
  quality_issues: '',
  chunk_count: 1,
  chunks: 1,
  updated_at: '2026-11-14 00:00:00',
  created_at: '2026-11-14 00:00:00',
  last_indexed: '2026-11-14 00:00:00',
  last_operation_at: '2026-11-14 00:00:00',
  last_operation: 'upload',
  last_trace_id: 'trace-1',
  kb_id: 'travel',
  department: 'general',
  business_domain: 'travel',
  size: 100,
  type: 'md',
  path: '/tmp/旅游测试资料.md',
  embedding_model: 'bge-m3',
  chunk_size: 500,
  overlap: 50,
  index_version: 'v1',
  hash: 'hash-1',
}

function stats(count: number) {
  return {
    kb_count: 1,
    doc_count: count,
    chunk_count: count,
    total_chunks: count,
    embedding_model: 'bge-m3',
    vector_db: 'pgvector',
    quality_status_counts: { pass: count, soft_warning: 0, failed: 0, unknown: 0 },
  }
}

function mountPanel() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  act(() => root.render(<DocumentsPanel />))
  return container
}

function click(button: HTMLButtonElement) {
  act(() => button.dispatchEvent(new MouseEvent('click', { bubbles: true })))
}

async function settleUi() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

function findButton(container: HTMLElement, label: string) {
  const button = Array.from(container.querySelectorAll('button'))
    .find(candidate => candidate.textContent?.trim() === label)
  if (!(button instanceof HTMLButtonElement)) throw new Error(`找不到按钮：${label}`)
  return button
}

beforeEach(() => {
  let documentRequest = 0
  vi.spyOn(knowledgeService, 'getDocuments').mockImplementation(async () => {
    documentRequest += 1
    return documentRequest === 1
      ? { documents: [documentFixture], total: 1 }
      : { documents: [], total: 0 }
  })
  vi.spyOn(knowledgeService, 'getStats')
    .mockResolvedValueOnce(stats(2))
    .mockResolvedValue(stats(1))
  vi.spyOn(knowledgeService, 'listKnowledgeBases')
    .mockResolvedValueOnce({ knowledge_bases: [{ id: 'travel', name: '旅游知识库', doc_count: 2 }] })
    .mockResolvedValue({ knowledge_bases: [{ id: 'travel', name: '旅游知识库', doc_count: 1 }] })
  vi.spyOn(knowledgeService, 'deleteDocument').mockResolvedValue({
    ok: true, operation_state: 'success', degraded: false, warnings: null,
  })
  vi.spyOn(knowledgeService, 'batchDelete').mockResolvedValue({
    ok: 1, failed: [], unknown: [], warnings: [],
  })
  vi.spyOn(knowledgeService, 'getChunkDetail').mockResolvedValue({
    doc_id: 'doc-1',
    total: 1,
    chunks: [{
      chunk_index: 0,
      chunk_id: 'doc-1_0',
      parent_chunk_id: '',
      granularity: 'leaf',
      parent_scope: '',
      document_title: '旅游测试资料',
      section_title: '办理须知',
      section_titles: ['办理须知'],
      chunk_type: 'text',
      content: '办理所需材料以原文为准。',
      char_count: 13,
      keywords: '物流,订单',
      llm_keywords: '跨境运输',
      llm_model: 'doubao-test',
      doc_type: 'travel_guide',
      kb_id: 'travel',
      department: 'general',
      simulated_questions: ['办理需要哪些材料？'],
      metadata: {
        source_format: 'pdf',
        parser_version: 'pdf-parser-v2',
        source_locators_json: [{ page: 4 }],
        chunk_tokens: 128,
        embedding_version: 'embed-v4',
      },
    }],
  })
})

afterEach(() => {
  if (mounted) {
    act(() => mounted?.root.unmount())
    mounted.container.remove()
    mounted = null
  }
  vi.restoreAllMocks()
})

describe('DocumentsPanel 文档计数刷新', () => {
  it('单篇删除后同步刷新列表、质量统计和知识库计数', async () => {
    const container = mountPanel()
    await settleUi()

    expect(container.textContent).toContain('当前可见 2 篇：通过 2')
    expect(container.querySelector('option[value="travel"]')?.textContent).toBe('旅游知识库（2）')

    click(container.querySelector('button[title="删除"]') as HTMLButtonElement)
    click(findButton(container, '删除'))
    await settleUi()

    expect(container.textContent).toContain('当前可见 1 篇：通过 1')
    expect(container.querySelector('option[value="travel"]')?.textContent).toBe('旅游知识库（1）')
    expect(container.textContent).toContain('暂无文档')
    expect(knowledgeService.getStats).toHaveBeenCalledTimes(2)
    expect(knowledgeService.listKnowledgeBases).toHaveBeenCalledTimes(2)
  })

  it('批量删除后也刷新列表、质量统计和知识库计数', async () => {
    const container = mountPanel()
    await settleUi()

    const rowCheckbox = container.querySelector('tbody tr button')
    if (!(rowCheckbox instanceof HTMLButtonElement)) throw new Error('找不到文档选择框')
    click(rowCheckbox)
    click(findButton(container, '批量删除'))
    click(findButton(container, '删除 1 项'))
    await settleUi()

    expect(knowledgeService.batchDelete).toHaveBeenCalledWith([{ id: 'doc-1', name: '旅游测试资料.md' }])
    expect(container.textContent).toContain('当前可见 1 篇：通过 1')
    expect(container.querySelector('option[value="travel"]')?.textContent).toBe('旅游知识库（1）')
    expect(container.textContent).toContain('暂无文档')
    expect(knowledgeService.getStats).toHaveBeenCalledTimes(2)
    expect(knowledgeService.listKnowledgeBases).toHaveBeenCalledTimes(2)
  })
})

describe('DocumentsPanel 分块详情', () => {
  it('直接读取分块正文与来源元数据，不依赖可能过期的历史 Trace', async () => {
    const container = mountPanel()
    await settleUi()

    click(container.querySelector('button[title="查看分块"]') as HTMLButtonElement)
    await settleUi()

    expect(knowledgeService.getChunkDetail).toHaveBeenCalledWith('doc-1')
    expect(container.textContent).toContain('分块详情')
    expect(container.textContent).toContain('办理所需材料以原文为准。')
    expect(container.textContent).toContain('原文件格式')
    expect(container.textContent).toContain('pdf')
    expect(container.textContent).toContain('原文位置')
    expect(container.textContent).toContain('Token 数')
    expect(container.textContent).toContain('128')
    expect(container.textContent).toContain('物流,订单')
    expect(container.textContent).toContain('跨境运输')
    expect(container.textContent).toContain('办理需要哪些材料？')
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('读取失败时明确显示错误，而不是显示空白详情', async () => {
    vi.spyOn(knowledgeService, 'getChunkDetail').mockRejectedValueOnce(new Error('文档已下线或待审核'))
    const container = mountPanel()
    await settleUi()

    click(container.querySelector('button[title="查看分块"]') as HTMLButtonElement)
    await settleUi()

    expect(container.querySelector('[role="alert"]')?.textContent).toBe('文档已下线或待审核')
  })

  it('旧接口没有返回元数据时不误报成源资料缺失', async () => {
    vi.spyOn(knowledgeService, 'getChunkDetail').mockResolvedValueOnce({
      doc_id: 'doc-1',
      total: 1,
      chunks: [{
        chunk_index: 0,
        chunk_id: 'doc-1_0',
        parent_chunk_id: '',
        granularity: 'leaf',
        parent_scope: '',
        document_title: '',
        section_title: '',
        section_titles: [],
        chunk_type: '',
        content: '正文仍可查看。',
        char_count: 7,
        keywords: '',
        llm_keywords: '',
        llm_model: '',
        doc_type: 'travel_guide',
        kb_id: 'travel',
        department: 'general',
        simulated_questions: [],
      }],
    })
    const container = mountPanel()
    await settleUi()

    click(container.querySelector('button[title="查看分块"]') as HTMLButtonElement)
    await settleUi()

    expect(container.textContent).toContain('本次接口没有返回来源或切分信息')
    expect(container.textContent).not.toContain('没有保存可展示的来源或切分元数据')
    expect(container.textContent).toContain('正文仍可查看。')
  })
})

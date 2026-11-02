import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'
import RunsTab from './RunsTab'

vi.mock('@/lib/auth', () => ({ atLeast: () => true }))
vi.mock('@/api/knowledge', () => ({ listKbAuthority: async () => ({ knowledge_bases: [{ kb_id: 'kb', active: true }] }) }))
vi.mock('@/api/prompts', () => ({ promptsService: { list: async () => [] } }))

it('从 RAG 切换到客服后隐藏方式并固定提交 offline，再切回保留 RAG 选择', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue(['rag', 'cs'].map(module => ({
    name: module, module, version: '1', suite_version_id: module,
    source: 'evaluation_v2', status: 'published', case_count: 1,
  })) as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const create = vi.spyOn(evaluationService, 'createV2Run').mockResolvedValue({ run: { run_id: 'run' } } as Awaited<ReturnType<typeof evaluationService.createV2Run>>)
  vi.spyOn(evaluationService, 'getV2Run').mockResolvedValue({ fingerprint: null } as unknown as Awaited<ReturnType<typeof evaluationService.getV2Run>>)
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  try {
    await act(async () => { root.render(<RunsTab />) })
    expect(container.querySelector('[aria-label="评测方式"]')).not.toBeNull()
    const suite = container.querySelector('[aria-label="数据集"]') as HTMLSelectElement
    await act(async () => { suite.value = 'cs'; suite.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(container.querySelector('[aria-label="评测方式"]')).toBeNull()
    expect(container.textContent).toContain('contract_only')
    expect(container.textContent).not.toContain('在线模式将真实调用模型')
    await act(async () => { Array.from(container.querySelectorAll('button')).find(item => item.textContent?.includes('开始运行'))!.click() })
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ suite_version_id: 'cs', evaluation_mode: 'offline' }))
    await act(async () => { suite.value = 'rag'; suite.dispatchEvent(new Event('change', { bubbles: true })) })
    expect((container.querySelector('[aria-label="评测方式"]') as HTMLSelectElement).value).toBe('self+ragas')
  } finally {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
  }
})

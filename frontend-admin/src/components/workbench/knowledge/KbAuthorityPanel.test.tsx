import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { evaluationAssets } from '@/api/evaluation-assets'

vi.mock('@/components/auth/RoleGate', () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }))
vi.mock('@/lib/auth', () => ({ atLeast: () => true }))
vi.mock('@/api/knowledge', () => ({
  listKbAuthority: vi.fn(async () => ({ departments: [], knowledge_bases: [{ kb_id: 'policy_general', name: '企业公共制度', domain: 'general', owner_depts: ['all'], audience: 'internal', active: true, version: 1, doc_count: 28 }], total: 1 })),
  activateKnowledgeBase: vi.fn(), createKnowledgeBase: vi.fn(), deactivateKnowledgeBase: vi.fn(), updateKnowledgeBase: vi.fn(),
}))
vi.mock('@/api/evaluation-assets', () => ({ evaluationAssets: { bindings: vi.fn(async () => [{ kb_id: 'policy_general', binding_id: 'binding' }]) } }))
import KbAuthorityPanel from './KbAuthorityPanel'
let root: Root
let container: HTMLDivElement
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
afterEach(() => { act(() => root?.unmount()); document.body.innerHTML = ''; vi.clearAllMocks() })
async function mount() {
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container)
  await act(async () => root.render(<KbAuthorityPanel />))
}
describe('知识库评测入口', () => {
  it('显示关联题集数量并携带知识库标识进入评测中心', async () => {
    await mount()
    expect(container.textContent).toContain('1 个题集版本')
    const link = Array.from(container.querySelectorAll('a')).find(a => a.textContent === '关联评测')!
    expect(link.getAttribute('href')).toBe('/evaluations/center?tab=bindings&kb_id=policy_general')
  })
  it('关联读取失败不能显示零个题集', async () => {
    vi.mocked(evaluationAssets.bindings).mockRejectedValueOnce(new Error('离线'))
    await mount()
    expect(container.textContent).toContain('读取失败 · 前往重试')
    expect(container.textContent).not.toContain('0 个题集版本')
  })
})

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const listGovernanceReleases = vi.hoisted(() => vi.fn())
const createGovernanceRelease = vi.hoisted(() => vi.fn())
const getGovernanceReleaseDiff = vi.hoisted(() => vi.fn())
const publishGovernanceRelease = vi.hoisted(() => vi.fn())
const rollbackGovernanceRelease = vi.hoisted(() => vi.fn())
const importGovernanceConfiguration = vi.hoisted(() => vi.fn())
const getReleaseLatest = vi.hoisted(() => vi.fn())
const getReleases = vi.hoisted(() => vi.fn())
const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
  router: { push: vi.fn(), replace: vi.fn() },
}))

vi.mock('@/api/governanceReleases', () => ({
  listGovernanceReleases,
  createGovernanceRelease,
  getGovernanceReleaseDiff,
  publishGovernanceRelease,
  rollbackGovernanceRelease,
  importGovernanceConfiguration,
}))
vi.mock('@/api/governance', () => ({
  getReleaseLatest,
  getReleases,
}))
vi.mock('next/navigation', () => ({
  useRouter: () => navigation.router,
  usePathname: () => '/releases',
  useSearchParams: () => navigation.params,
}))

import ReleasesPage from './page'
import type { GovernanceRelease } from '@/api/governanceReleases'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  document.body.innerHTML = ''
  navigation.params = new URLSearchParams()
  vi.clearAllMocks()
})

function mount(): Root {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<ReleasesPage />))
  return root
}

function flush(): Promise<void> {
  return act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

const RELEASE: GovernanceRelease = {
  id: 'gov_rel_abc',
  version: 1,
  status: 'draft',
  description: '基线版本',
  connection_count: 2,
  bundle_count: 1,
  created_by: 'u1',
  published_by: '',
  published_at: null,
  created_at: '2026-10-28T00:00:00',
}

describe('ReleasesPage 治理配置发布档', () => {
  it('默认渲染治理档：指标条 + 创建快照 + 版本历史表', async () => {
    listGovernanceReleases.mockResolvedValue({ releases: [RELEASE] })
    const root = mount()
    await flush()

    expect(document.body.textContent).toContain('治理配置发布')
    expect(document.body.textContent).toContain('创建快照')
    expect(document.querySelector('[data-testid="gov-release-table"]')).not.toBeNull()
    expect(document.body.textContent).toContain('基线版本')
    expect(document.querySelector('[data-testid="gov-publish-1"]')).not.toBeNull()
    act(() => root.unmount())
  })

  it('空版本列表出空态；导入抽屉可打开并提交', async () => {
    listGovernanceReleases.mockResolvedValue({ releases: [] })
    importGovernanceConfiguration.mockResolvedValue({
      created_connections: ['a'],
      created_bundles: [],
      skipped: [],
      rejected: [],
      redacted: [],
    })
    const root = mount()
    await flush()

    expect(document.body.textContent).toContain('尚无版本')
    const importButton = [...document.querySelectorAll('button')]
      .find((b) => b.textContent?.includes('导入配置')) as HTMLButtonElement
    act(() => importButton.click())
    await flush()
    const textarea = document.querySelector('textarea') as HTMLTextAreaElement
    await act(async () => {
      const nativeSetter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype, 'value')?.set
      nativeSetter?.call(textarea, JSON.stringify({ version: 1, connections: [], capability_bundles: [] }))
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const submit = document.querySelector('[data-testid="gov-import-submit"]') as HTMLButtonElement
    act(() => submit.click())
    await flush()
    expect(importGovernanceConfiguration).toHaveBeenCalled()
    expect(document.querySelector('[data-testid="gov-import-result"]')).not.toBeNull()
    act(() => root.unmount())
  })

  it('查看差异抽屉渲染 counts 与变更条目', async () => {
    listGovernanceReleases.mockResolvedValue({ releases: [RELEASE] })
    getGovernanceReleaseDiff.mockResolvedValue({
      changes: [{
        kind: 'connection', name: 'rel-conn-a', change: 'modified',
        fields: [{ field: 'base_url', release: 'http://a', current: 'http://b' }],
      }],
      counts: { modified: 1, added: 0, removed: 0 },
    })
    const root = mount()
    await flush()

    const diffButton = [...document.querySelectorAll('button')]
      .find((b) => b.textContent === '查看差异') as HTMLButtonElement
    act(() => diffButton.click())
    await flush()

    expect(getGovernanceReleaseDiff).toHaveBeenCalledWith('gov_rel_abc')
    expect(document.body.textContent).toContain('差异预览')
    expect(document.querySelectorAll('[data-testid="gov-diff-entry"]').length).toBe(1)
    expect(document.body.textContent).toContain('base_url')
    act(() => root.unmount())
  })
})

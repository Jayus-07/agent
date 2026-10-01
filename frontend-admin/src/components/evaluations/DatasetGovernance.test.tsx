import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import DatasetReviewQueue from './DatasetReviewQueue'
import DatasetVersionDetail from './DatasetVersionDetail'
import type { DatasetCandidate, DatasetVersionDetail as DatasetVersionDetailModel } from '@/api/evaluation'

const unredactedCandidate: DatasetCandidate = {
  candidate_id: 'cand-raw',
  module: 'rag',
  question: '原始问题',
  expected: {},
  metadata: {},
  source_type: 'trace',
  redacted: false,
  owner: 'ops',
  status: 'pending_review',
  dataset_version: '5.0.0',
  created_at: '2026-10-01T00:00:00Z',
  reviewer: '',
  review_reason: '',
  approved_version: '',
  content_hash: '',
}

const version: DatasetVersionDetailModel = {
  dataset_id: 'rag',
  version: '5.0.0',
  immutable: true,
  owner: 'quality',
  review_status: 'approved',
  case_count: 26,
  content_hash: 'sha256:abc123',
  metadata: { kb_id: 'rag_eval_kb', fixture_set: 'baseline' },
  coverage: {},
  case_diff: { added: [], removed: [], changed: [] },
  suite_membership: [{ name: 'pr_baseline', case_count: 26 }],
  audit: [],
}

function mount(element: React.ReactElement): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(element))
  return { root, container }
}

afterEach(() => { document.body.innerHTML = '' })

it('does not expose an approve action for an unredacted candidate', () => {
  const { root, container } = mount(
    <DatasetReviewQueue candidate={unredactedCandidate} onApprove={vi.fn()} />,
  )
  expect(container.querySelector('button')).toBeNull()
  expect(container.textContent).toContain('待脱敏')
  act(() => root.unmount())
})

it('shows suite scope and provenance in the dataset detail', () => {
  const { root, container } = mount(<DatasetVersionDetail dataset={version} />)
  expect(container.textContent).toContain('pr_baseline')
  expect(container.textContent).toContain('内容 hash')
  expect(container.textContent).toContain('sha256:abc123')
  act(() => root.unmount())
})

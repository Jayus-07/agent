import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import PromptReleasePanel, { type PromptReleaseView } from './PromptReleasePanel'

function release(overrides: Partial<PromptReleaseView> = {}): PromptReleaseView {
  return {
    release_id: 'rel-1',
    prompt_key: 'rag.qa',
    version: 2,
    target_env: 'production',
    status: 'running',
    eval_suite: 'pr_baseline',
    dataset_provenance: { version: '5.0.0', kb_id: 'rag_eval_kb', fixture_set: 'baseline' },
    executor: 'github',
    model_binding_fingerprint: 'model-abc',
    tool_contract_fingerprint: 'tool-xyz',
    metrics: {},
    failure_reason: '',
    approved_by: '',
    published_by: '',
    runtime_status: { epoch: 4, processes: [] },
    ...overrides,
  }
}

function mount(view: PromptReleaseView, onPublish = vi.fn()): { root: Root; container: HTMLDivElement; onPublish: ReturnType<typeof vi.fn> } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => {
    root.render(<PromptReleasePanel release={view} onPublish={onPublish} />)
  })
  return { root, container, onPublish }
}

afterEach(() => {
  document.body.innerHTML = ''
})
it('does not enable production publish while evaluation is running', () => {
  const { root, container } = mount(release({ status: 'running' }))

  const button = Array.from(container.querySelectorAll('button')).find(
    item => item.textContent?.includes('发布到生产'),
  )
  expect(button).toBeDefined()
  expect((button as HTMLButtonElement).disabled).toBe(true)
  act(() => root.unmount())
})
it('shows dataset and model provenance for a passed release', () => {
  const { root, container } = mount(release({
    status: 'passed',
    metrics: { pass_rate: 0.96, recall_at_5: 0.91 },
  }))

  expect(container.textContent).toContain('pr_baseline')
  expect(container.textContent).toContain('模型绑定')
  expect(container.textContent).toContain('model-abc')
  act(() => root.unmount())
})


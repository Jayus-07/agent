import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'
import { evaluationAssets, type EvaluationBinding } from '@/api/evaluation-assets'

vi.mock('@/api/evaluation-assets', () => ({ evaluationAssets: {
  publicationPolicy: vi.fn(async () => ({ policy: null })), setPublicationPolicy: vi.fn(async () => ({})),
} }))
import PublicationGateControl from './PublicationGateControl'
let root: Root
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
afterEach(() => { act(() => root?.unmount()); document.body.innerHTML = ''; vi.clearAllMocks() })

it('门禁默认收起，读取真实状态后由明确操作启用', async () => {
  const container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container)
  await act(async () => root.render(<PublicationGateControl binding={{ binding_id: 'binding', kb_id: 'policy_general' } as EvaluationBinding} />))
  expect(evaluationAssets.publicationPolicy).not.toHaveBeenCalled()
  expect(evaluationAssets.setPublicationPolicy).not.toHaveBeenCalled()
  const details = container.querySelector('details')!
  await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle', { bubbles: true })) })
  expect(container.textContent).toContain('尚未启用评测门禁')
  await act(async () => container.querySelector('button')!.click())
  expect(evaluationAssets.setPublicationPolicy).toHaveBeenCalledWith('policy_general', 'binding', true)
})

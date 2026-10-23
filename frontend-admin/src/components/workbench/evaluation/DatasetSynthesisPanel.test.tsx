import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { evaluationAssets } from '@/api/evaluation-assets'
vi.mock('@/api/evaluation-assets', () => ({ evaluationAssets: { task: vi.fn() } }))
vi.mock('@/api/knowledge', () => ({ listKbAuthority: vi.fn(async () => ({ knowledge_bases: [] })) }))
import DatasetSynthesisPanel from './DatasetSynthesisPanel'
it('恢复等待自动重试的合成任务，继续轮询直至成功', async () => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.useFakeTimers()
  localStorage.setItem('evaluation-synthesis-task', 'probe-task')
  vi.mocked(evaluationAssets.task).mockResolvedValueOnce({ task_id: 'probe-task', run_id: 'probe-task', status: 'RETRYING' }).mockResolvedValue({ task_id: 'probe-task', run_id: 'probe-task', status: 'SUCCESS' })
  const container = document.createElement('div'); document.body.appendChild(container)
  const root = createRoot(container)
  try {
    await act(async () => root.render(<DatasetSynthesisPanel />))
    expect(container.textContent).toContain('等待自动重试')
    await act(async () => vi.advanceTimersByTimeAsync(2100))
    expect(evaluationAssets.task).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('候选已生成')
  } finally { act(() => root.unmount()); container.remove(); localStorage.clear(); vi.useRealTimers(); vi.clearAllMocks() }
})

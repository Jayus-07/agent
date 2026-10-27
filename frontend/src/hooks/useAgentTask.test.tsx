import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { controlAgentTask, getAgentTask } from '@/api/agentTasks'
import { useAgentTask } from './useAgentTask'

vi.mock('@/api/agentTasks', () => ({ controlAgentTask: vi.fn(), getAgentTask: vi.fn() }))
;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true

afterEach(() => { vi.clearAllMocks() })

it('暂停/继续使用同一任务编号；卸载页面不会发送取消', async () => {
  const node = document.createElement('div')
  const root = createRoot(node)
  let api!: ReturnType<typeof useAgentTask>
  function Harness() { api = useAgentTask('original-task'); return null }
  vi.mocked(getAgentTask).mockResolvedValue({ task_id: 'original-task', status: 'RUNNING' } as any)
  vi.mocked(controlAgentTask).mockResolvedValue({ status: 'RUNNING', message: '' })
  await act(async () => { root.render(<Harness />) })
  await act(async () => { await api.control('pause') })
  expect(controlAgentTask).toHaveBeenLastCalledWith('original-task', 'pause')
  vi.mocked(getAgentTask).mockResolvedValue({ task_id: 'original-task', status: 'PAUSED' } as any)
  await act(async () => { await api.control('resume') })
  expect(controlAgentTask).toHaveBeenLastCalledWith('original-task', 'resume')
  await act(async () => { root.unmount() })
  expect(controlAgentTask).toHaveBeenCalledTimes(2)
})

import { beforeEach, expect, it, vi } from 'vitest'
import { request } from '@/api/client'
import { useChatStore } from '@/store/chat'
import { restoreAgentTasks, type AgentTask } from './agentTasks'

vi.mock('@/api/client', () => ({ request: vi.fn() }))

const task = { task_id: 'task-1', session_id: 's1', question: '长报告', status: 'PAUSED',
  result: { answer: '已经完成的部分' }, created_at: '2026-10-27T10:00:00Z' } as AgentTask

beforeEach(() => {
  vi.clearAllMocks()
  useChatStore.setState({ currentId: 's1', sessions: [] })
  vi.mocked(request).mockResolvedValue({ tasks: [task] })
})

it('刷新后从服务端恢复暂停任务，重复读取不追加重复消息', async () => {
  useChatStore.setState({ historyError: '会话不存在' })
  await restoreAgentTasks('s1')
  await restoreAgentTasks('s1')
  const messages = useChatStore.getState().sessions[0].messages
  expect(messages).toHaveLength(2)
  expect(messages[1]).toMatchObject({ taskId: 'task-1', content: '已经完成的部分' })
  expect(request).toHaveBeenCalledWith('/api/chat/agent/tasks?session_id=s1')
  expect(useChatStore.getState().historyError).toBeNull()
})

it('晚到的任务历史不会覆盖用户刚切换的会话', async () => {
  let resolve!: (value: unknown) => void
  vi.mocked(request).mockReturnValue(new Promise((done) => { resolve = done }))
  const pending = restoreAgentTasks('s1')
  useChatStore.setState({ currentId: 's2' })
  resolve({ tasks: [task] })
  await pending
  expect(useChatStore.getState().sessions).toEqual([])
})

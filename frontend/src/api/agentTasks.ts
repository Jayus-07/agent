import { request } from '@/api/client'
import { useChatStore } from '@/store/chat'
import type { Message } from '@/lib/types'

export interface AgentTask {
  task_id: string
  session_id: string
  question: string
  status: 'PENDING' | 'RUNNING' | 'PAUSED' | 'WAITING_USER' | 'SUCCESS' | 'FAILED' | 'CANCELLED'
  progress: string
  error_message: string
  error_type: string
  trace_id: string
  result: { answer?: string; partial?: boolean } | null
  created_at: string
  updated_at: string
  steps?: { id: number; node: string; at: string }[]
  node_labels?: Record<string, string>
}

const prefix = '/api/chat/agent/tasks'
export const listAgentTasks = (sessionId?: string) => request<{ tasks: AgentTask[] }>(
  `${prefix}${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`,
)
export const getAgentTask = (id: string) => request<AgentTask>(`${prefix}/${encodeURIComponent(id)}`)
export const controlAgentTask = (id: string, action: 'pause' | 'resume') => request<{status: string; message: string}>(
  `${prefix}/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: {} },
)

/** 任务表保存未完成/取消的输出，成功问答已有记忆时只关联任务卡。 */
export async function restoreAgentTasks(sessionId: string) {
  const { tasks } = await listAgentTasks(sessionId)
  if (useChatStore.getState().currentId !== sessionId) return
  useChatStore.setState((state) => ({
    // 新建长任务尚未成功结束时还没有成功轮次记忆，任务记录本身可恢复。
    historyError: tasks.length && state.historyError === '会话不存在' ? null : state.historyError,
    sessions: (state.sessions.some((session) => session.id === sessionId) ? state.sessions : [
      ...state.sessions, { id: sessionId, title: '新对话', mode: 'chat' as const, messages: [], createdAt: Date.now(), updatedAt: Date.now() },
    ]).map((session) => {
      if (session.id !== sessionId) return session
      const messages = [...session.messages]
      for (const task of [...tasks].reverse()) {
        if (messages.some((message) => message.taskId === task.task_id)) continue
        const existing = messages.findIndex((message, index) =>
          message.role === 'assistant' && message.content === task.result?.answer &&
          messages[index - 1]?.content === task.question && !message.taskId,
        )
        if (existing >= 0) {
          messages[existing] = { ...messages[existing], taskId: task.task_id }
          continue
        }
        const timestamp = new Date(task.created_at).getTime()
        const recovered: Message[] = [
          { id: `${task.task_id}:user`, role: 'user', content: task.question, timestamp },
          { id: `${task.task_id}:assistant`, role: 'assistant', content: task.result?.answer || '', taskId: task.task_id, timestamp: timestamp + 1 },
        ]
        messages.push(...recovered)
      }
      return { ...session, messages: messages.sort((a, b) => a.timestamp - b.timestamp),
        title: session.title === '新对话' && tasks.length ? tasks[tasks.length - 1].question.slice(0, 30) : session.title }
    }),
  }))
}

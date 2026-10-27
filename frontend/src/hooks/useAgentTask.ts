'use client'

import { useCallback, useEffect, useState } from 'react'
import { controlAgentTask, getAgentTask, type AgentTask } from '@/api/agentTasks'

export function useAgentTask(taskId: string) {
  const [task, setTask] = useState<AgentTask | null>(null)
  const [error, setError] = useState('')
  const [operation, setOperation] = useState<'pause' | 'resume' | null>(null)
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let failures = 0
    setTask(null)
    setOperation(null)
    const poll = async () => {
      let terminal = false
      try {
        const next = await getAgentTask(taskId)
        if (!active) return
        setTask(next)
        setError('')
        failures = 0
        terminal = ['SUCCESS', 'FAILED', 'CANCELLED'].includes(next.status)
        if (next.status === 'PAUSED' || terminal) setOperation(null)
      } catch (err) {
        failures += 1
        if (active) setError(err instanceof Error ? err.message : '读取任务状态失败')
      } finally {
        // 暂停态仍轻量查询，管理端恢复/取消后页面也能同步。
        if (active && !terminal) timer = setTimeout(poll, Math.min(15000, 1500 * 2 ** failures))
      }
    }
    void poll()
    return () => { active = false; if (timer) clearTimeout(timer) }
  }, [taskId])

  const control = useCallback(async (action: 'pause' | 'resume') => {
    setOperation(action)
    setError('')
    try {
      await controlAgentTask(taskId, action)
      setTask(await getAgentTask(taskId))
      if (action === 'resume') setOperation(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '任务控制失败')
      setOperation(null)
    }
  }, [taskId])
  return { task, error, operation, control }
}

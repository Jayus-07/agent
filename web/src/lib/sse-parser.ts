import type { SSEEvent } from './types'

export interface StepInfo {
  stepId: string
  status: string
  description: string
  node: string
  elapsed?: number
}

export interface Progress {
  steps: StepInfo[]
  total: number
  completed: number
  failed: number
  running: StepInfo | null
  isDone: boolean
}

export function parseProgress(events: SSEEvent[]): Progress {
  const stepMap = new Map<string, StepInfo>()
  let planCount = 0
  let isDone = false

  for (const event of events) {
    if (event.stage === 'planning') {
      planCount = event.data.task_count ?? 0
    }
    if (event.stage === 'executing' && event.data.step_id) {
      const existing = stepMap.get(event.data.step_id)
      if (!existing || existing.status === 'running') {
        stepMap.set(event.data.step_id, {
          stepId: event.data.step_id,
          status: event.data.status ?? 'running',
          description: event.data.description ?? '',
          node: event.node,
          elapsed: event.data.elapsed,
        })
      }
    }
    if (event.stage === 'done') {
      isDone = true
    }
  }

  const steps = Array.from(stepMap.values())
  const total = planCount || steps.length
  const completed = steps.filter(s => s.status === 'success').length
  const failed = steps.filter(s => s.status === 'failed').length
  const running = steps.find(s => s.status === 'running') ?? null

  return { steps, total, completed, failed, running, isDone }
}

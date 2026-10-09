import type {
  Itinerary,
  PlanResponse,
  TravelPlanLatest,
  TravelStreamEvent,
} from '@/api/travel'

export type TravelStreamOutcome =
  // draft 仅保留给未挂载的旧 LiveWorkspace 组件类型兼容；V2 成功行程统一归为 plan。
  | { kind: 'plan' | 'draft' | 'answer' | 'clarification'; response: PlanResponse }
  | { kind: 'failed'; message: string }

/** 将旅游域现有 SSE 终态归一化，未收到 done 时绝不把本轮当作成功。 */
export function resolveTravelStreamOutcome(events: TravelStreamEvent[]): TravelStreamOutcome {
  const errorEvent = [...events].reverse().find((event) => event.event === 'error')
  const done = [...events].reverse().find((event) => event.event === 'done')
  if (errorEvent) {
    const message = errorEvent.data.message
    return { kind: 'failed', message: typeof message === 'string' ? message : '旅游规划执行失败' }
  }
  if (!done) return { kind: 'failed', message: '连接已结束，但旅游规划流没有返回完成结果。' }

  const raw = done.data.result
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    const message = done.data.message
    return { kind: 'failed', message: typeof message === 'string' ? message : '旅游规划流未返回结构化结果。' }
  }
  const response = raw as PlanResponse
  if (done.data.status === 'failed' || response.status === 'failed') {
    return {
      kind: 'failed',
      message: response.final_answer || (typeof done.data.message === 'string' ? done.data.message : '旅游规划执行失败'),
    }
  }
  if (response.status === 'needs_clarification' || response.result_kind === 'clarification') {
    return { kind: 'clarification', response }
  }
  if (response.itinerary) {
    // V2 成功结果自动写入正式 Trip；V1 的 waiting_confirmation 不再拦截保存。
    return { kind: 'plan', response }
  }
  return { kind: 'answer', response }
}

/** 历史恢复始终优先显示服务端确认的 Active 版本。 */
export function resolveTravelActiveItinerary(latest: TravelPlanLatest): Itinerary | null {
  if (latest.plan_status === 'waiting_confirmation') {
    return latest.active_itinerary ?? null
  }
  return latest.itinerary ?? latest.active_itinerary ?? null
}

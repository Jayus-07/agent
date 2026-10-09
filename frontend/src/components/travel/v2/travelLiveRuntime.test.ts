import { describe, expect, it } from 'vitest'
import type { PlanResponse, TravelPlanLatest, TravelStreamEvent } from '@/api/travel'
import { resolveTravelActiveItinerary, resolveTravelStreamOutcome } from './travelLiveRuntime'

const plan: PlanResponse = {
  status: 'success', final_answer: '行程已生成',
  itinerary: { brief: { destination: '杭州' } } as PlanResponse['itinerary'],
}

function event(eventName: string, data: Record<string, unknown>): TravelStreamEvent {
  return { event: eventName as TravelStreamEvent['event'], data }
}

describe('旅游实时页面结果解析', () => {
  it('读取 SSE 完成事件中的结构化行程', () => {
    expect(resolveTravelStreamOutcome([
      event('stage.started', { stage: '规划' }),
      event('done', { status: 'success', result: plan }),
    ])).toEqual({ kind: 'plan', response: plan })
  })

  it('V2 自动保存成功行程，不受 V1 待确认标记影响', () => {
    const waitingConfirmation = { ...plan, plan_status: 'waiting_confirmation' } as PlanResponse
    expect(resolveTravelStreamOutcome([
      event('done', { status: 'success', result: waitingConfirmation }),
    ])).toEqual({ kind: 'plan', response: waitingConfirmation })
  })

  it('把澄清结果与普通问答分开呈现', () => {
    expect(resolveTravelStreamOutcome([
      event('done', { status: 'success', result: { status: 'needs_clarification', final_answer: '准备玩几天？', itinerary: null } }),
    ])).toMatchObject({ kind: 'clarification' })
    expect(resolveTravelStreamOutcome([
      event('done', { status: 'success', result: { status: 'answered', final_answer: '西湖全天开放。', itinerary: null } }),
    ])).toMatchObject({ kind: 'answer' })
  })

  it('区分服务端失败与缺少完成事件', () => {
    expect(resolveTravelStreamOutcome([event('done', { status: 'failed', message: '依赖不可用' })]))
      .toMatchObject({ kind: 'failed', message: '依赖不可用' })
    expect(resolveTravelStreamOutcome([event('error', { message: '连接断开' })]))
      .toMatchObject({ kind: 'failed', message: '连接断开' })
    expect(resolveTravelStreamOutcome([])).toMatchObject({ kind: 'failed' })
  })

  it('最新版本为待确认草案时，恢复当前 Active 内容', () => {
    const latest = {
      plan_status: 'waiting_confirmation',
      itinerary: { ...plan.itinerary, plan_version: 3 },
      active_plan_status: 'confirmed',
      active_itinerary: { ...plan.itinerary, plan_version: 2 },
    } as TravelPlanLatest
    expect(resolveTravelActiveItinerary(latest)?.plan_version).toBe(2)
  })

  it('无草案时读取最新已确认版本', () => {
    const latest = { plan_status: 'confirmed', itinerary: plan.itinerary } as TravelPlanLatest
    expect(resolveTravelActiveItinerary(latest)).toBe(plan.itinerary)
  })
})

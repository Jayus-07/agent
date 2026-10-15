import { afterEach, describe, expect, it } from 'vitest'
import {
  defaultTravelV2ConversationId,
  parseTravelV2RestoreRevisionRequest,
  setTravelV2TripAssociation,
  travelV2ConversationIdForTrip,
  travelV2ChatHref,
  travelV2WorkspaceHref,
  travelV2WorkspaceRestoreHref,
} from './travelV2Conversation'

describe('V2 Trip 会话关联', () => {
  afterEach(() => window.localStorage.clear())

  it('同一 Trip 使用稳定会话 ID，入口链接保留会话关联', () => {
    const tripId = 'trip-a'
    const conversationId = defaultTravelV2ConversationId(tripId)

    expect(travelV2ConversationIdForTrip(tripId)).toBe(conversationId)
    expect(travelV2WorkspaceHref(tripId, conversationId)).toBe('/travel/itineraries/trip-a?conversation_id=travel-v2-trip-trip-a')
    expect(travelV2WorkspaceRestoreHref(tripId, conversationId, 2)).toBe('/travel/itineraries/trip-a?conversation_id=travel-v2-trip-trip-a&restore_revision=2')
    expect(travelV2ChatHref(tripId, conversationId)).toBe('/travel/chat?trip_id=trip-a&conversation_id=travel-v2-trip-trip-a')
  })

  it('工作台恢复规划会话，并拒绝其他 Trip 的候选会话', () => {
    setTravelV2TripAssociation('planning-conversation', 'trip-a')

    expect(travelV2ConversationIdForTrip('trip-a')).toBe('planning-conversation')
    expect(travelV2ConversationIdForTrip('trip-a', 'planning-conversation')).toBe('planning-conversation')
    expect(travelV2ConversationIdForTrip('trip-b', 'planning-conversation')).toBe(defaultTravelV2ConversationId('trip-b'))
  })

  it('只把明确指定正式版本的恢复指令识别为 TRIP_RESTORE', () => {
    expect(parseTravelV2RestoreRevisionRequest('把这趟行程恢复到正式版本 v3')).toBe(3)
    expect(parseTravelV2RestoreRevisionRequest('请还原到第 2 版')).toBe(2)
    expect(parseTravelV2RestoreRevisionRequest('把西湖改到上午十点')).toBeNull()
    expect(parseTravelV2RestoreRevisionRequest('恢复一下行程')).toBeNull()
  })
})

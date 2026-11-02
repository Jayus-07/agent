import { describe, expect, it } from 'vitest'
import { travelChatRedirectHref } from './redirect'

describe('/travel/chat 旧链接重定向（决策 5：chat 独立页废弃）', () => {
  it('带 trip_id 重定向到正式工作台，会话候选一并透传', () => {
    expect(travelChatRedirectHref({ trip_id: 'trip-9', conversation_id: 'travel-conv-a' })).toBe(
      '/travel/itineraries/trip-9?conversation_id=travel-conv-a',
    )
  })

  it('带 trip_id 无会话候选时重定向到工作台裸路径', () => {
    expect(travelChatRedirectHref({ trip_id: 'trip-9' })).toBe('/travel/itineraries/trip-9')
  })

  it('trip_id 为空白时不进入工作台分支（防止无效行程路径）', () => {
    expect(travelChatRedirectHref({ trip_id: '   ', prompt: '杭州两日' })).toBe(
      '/travel/itineraries/new?prompt=%E6%9D%AD%E5%B7%9E%E4%B8%A4%E6%97%A5',
    )
  })

  it('带 prompt 重定向到工作台生成态并保留 prompt', () => {
    expect(travelChatRedirectHref({ prompt: '周末去杭州' })).toBe(
      `/travel/itineraries/new?prompt=${encodeURIComponent('周末去杭州')}`,
    )
  })

  it('prompt 与 conversation_id 同时保留（Live 工作台沿用会话继续生成）', () => {
    const href = travelChatRedirectHref({ prompt: '调整行程', conversation_id: 'travel-conv-b' })
    const url = new URL(`https://example.com${href}`)
    expect(url.pathname).toBe('/travel/itineraries/new')
    expect(url.searchParams.get('prompt')).toBe('调整行程')
    expect(url.searchParams.get('conversation_id')).toBe('travel-conv-b')
  })

  it('裸访问旧链接重定向到生成态裸路径', () => {
    expect(travelChatRedirectHref({})).toBe('/travel/itineraries/new')
  })
})

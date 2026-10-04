import { describe, expect, it } from 'vitest'
import { countRecentlyActiveSessions } from './sessionMetrics'

const NOW = Date.parse('2026-10-01T00:00:00.000Z')

function session(lastActiveAt: string) {
  return { lastActiveAt }
}

describe('countRecentlyActiveSessions', () => {
  it('只统计最近 15 分钟内的会话，并包含边界时刻', () => {
    const count = countRecentlyActiveSessions([
      session('2026-09-30T23:44:59.000Z'),
      session('2026-09-30T23:45:01.000Z'),
      session('2026-09-30T23:59:59.000Z'),
      session('2026-10-01T00:00:00.000Z'),
    ], NOW)

    expect(count).toBe(3)
  })

  it('不统计无效时间和未来时间', () => {
    const count = countRecentlyActiveSessions([
      session('not-a-date'),
      session('2026-10-01T00:00:01.000Z'),
    ], NOW)

    expect(count).toBe(0)
  })
})

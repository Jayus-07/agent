import { describe, expect, it } from 'vitest'
import { transitionTravelTripWrite, type TravelTripWriteTransition } from './travelV2WriteState'

function event(
  kind: TravelTripWriteTransition['kind'],
  signature: string,
  key: string,
  status: TravelTripWriteTransition['status'],
): TravelTripWriteTransition {
  return { kind, tripId: 'trip-a', signature, key, status }
}

describe('同一 Trip 正式写入守卫', () => {
  it('UNKNOWN 编辑期间拒绝 Restore，且只放行原编辑请求重试', () => {
    let current = null
    const begin = transitionTravelTripWrite(current, event('edit', 'edit-v4', 'edit-key', 'VALIDATING'))
    expect(begin.accepted).toBe(true)
    current = begin.current
    current = transitionTravelTripWrite(current, event('edit', 'edit-v4', 'edit-key', 'SAVING')).current
    current = transitionTravelTripWrite(current, event('edit', 'edit-v4', 'edit-key', 'UNKNOWN')).current

    const restore = transitionTravelTripWrite(current, event('restore', 'restore-v1', 'restore-key', 'VALIDATING'))
    expect(restore.accepted).toBe(false)
    expect(restore.current).toEqual(current)

    const retry = transitionTravelTripWrite(current, event('edit', 'edit-v4', 'edit-key', 'VALIDATING'))
    expect(retry.accepted).toBe(true)
    expect(retry.current).toMatchObject({ kind: 'edit', signature: 'edit-v4', key: 'edit-key', status: 'VALIDATING' })
  })

  it('UNKNOWN Restore 期间拒绝编辑及不同目标 Restore', () => {
    let current = transitionTravelTripWrite(null, event('restore', 'restore-v3-to-v1', 'restore-key', 'VALIDATING')).current
    current = transitionTravelTripWrite(current, event('restore', 'restore-v3-to-v1', 'restore-key', 'SAVING')).current
    current = transitionTravelTripWrite(current, event('restore', 'restore-v3-to-v1', 'restore-key', 'UNKNOWN')).current

    expect(transitionTravelTripWrite(current, event('edit', 'edit-v3', 'edit-key', 'VALIDATING')).accepted).toBe(false)
    expect(transitionTravelTripWrite(current, event('restore', 'restore-v3-to-v2', 'new-restore-key', 'VALIDATING')).accepted).toBe(false)
    expect(transitionTravelTripWrite(current, event('restore', 'restore-v3-to-v1', 'restore-key', 'VALIDATING')).accepted).toBe(true)
  })

  it.each(['SAVED', 'INVALID', 'CONFLICT', 'FAILED'] as const)(
    '%s 后释放守卫，允许新的正式写入', (terminalStatus) => {
      const active = transitionTravelTripWrite(null, event('selection', 'meal-v2', 'meal-key', 'VALIDATING')).current
      const terminal = transitionTravelTripWrite(active, event('selection', 'meal-v2', 'meal-key', terminalStatus))
      expect(terminal).toEqual({ accepted: true, current: null })
      expect(transitionTravelTripWrite(terminal.current, event('edit', 'edit-v2', 'edit-key', 'VALIDATING')).accepted).toBe(true)
    },
  )
})

import { describe, expect, it } from 'vitest'
import { applyDemoTripEdit, undoDemoTripEdit, type DemoTripState } from './travelV2State'

function makeState(): DemoTripState {
  return {
    demoOnly: true,
    hasLocalEdits: false,
    undoItinerary: null,
    undoHistory: [],
    itinerary: {
      destination: '杭州',
      people: 2,
      budget: 3000,
      style: '轻松',
      days: [{
        day: 1,
        date: '4月12日',
        stops: [
          { id: 'lingyin', title: '灵隐寺', time: '09:00', duration: 120, transport: 'walk' },
          { id: 'west-lake', title: '西湖', time: '13:00', duration: 90, transport: 'transit' },
        ],
      }],
    },
  }
}

describe('旅游 V2 本地演示行程状态', () => {
  it('添加地点只改演示行程并保留撤销快照', () => {
    const before = makeState()
    const after = applyDemoTripEdit(before, {
      type: 'add-stop',
      dayIndex: 0,
      stop: { id: 'botanical', title: '杭州植物园', time: '15:00', duration: 90, transport: 'walk' },
    })

    expect(after.demoOnly).toBe(true)
    expect(after.hasLocalEdits).toBe(true)
    expect(after.itinerary.days[0].stops.map((stop) => stop.title)).toEqual(['灵隐寺', '西湖', '杭州植物园'])
    expect(before.itinerary.days[0].stops).toHaveLength(2)
    expect(after.undoItinerary).toEqual(before.itinerary)
  })

  it('重新排序、删除和切换交通都不改写原始演示数据', () => {
    const original = makeState()
    const reordered = applyDemoTripEdit(original, { type: 'move-stop', dayIndex: 0, fromIndex: 1, toIndex: 0 })
    const removed = applyDemoTripEdit(reordered, { type: 'delete-stop', dayIndex: 0, stopId: 'lingyin' })
    const changed = applyDemoTripEdit(removed, { type: 'set-transport', dayIndex: 0, stopId: 'west-lake', transport: 'drive' })

    expect(changed.itinerary.days[0].stops).toEqual([
      { id: 'west-lake', title: '西湖', time: '13:00', duration: 90, transport: 'drive' },
    ])
    expect(original.itinerary.days[0].stops.map((stop) => stop.id)).toEqual(['lingyin', 'west-lake'])
  })

  it('撤销恢复上一次修改并移除本地修改提示', () => {
    const before = makeState()
    const edited = applyDemoTripEdit(before, { type: 'delete-stop', dayIndex: 0, stopId: 'west-lake' })
    const undone = undoDemoTripEdit(edited)

    expect(undone.itinerary).toEqual(before.itinerary)
    expect(undone.hasLocalEdits).toBe(false)
    expect(undone.undoItinerary).toBeNull()
  })

  it('一次调整同时更新开始时间和停留时长', () => {
    const state = applyDemoTripEdit(makeState(), {
      type: 'set-time-and-duration', dayIndex: 0, stopId: 'lingyin', time: '10:30', duration: 150,
    })

    expect(state.itinerary.days[0].stops[0]).toMatchObject({ time: '10:30', duration: 150 })
  })

  it('连续编辑时可以逐次撤销，而不会把仍存在的演示改动标成已清空', () => {
    const before = makeState()
    const added = applyDemoTripEdit(before, {
      type: 'add-stop', dayIndex: 0,
      stop: { id: 'plant', title: '植物园', time: '15:00', duration: 60, transport: 'walk' },
    })
    const moved = applyDemoTripEdit(added, { type: 'move-stop', dayIndex: 0, fromIndex: 2, toIndex: 1 })
    const undoLast = undoDemoTripEdit(moved)
    const undoAll = undoDemoTripEdit(undoLast)

    expect(undoLast.hasLocalEdits).toBe(true)
    expect(undoLast.itinerary).toEqual(added.itinerary)
    expect(undoAll.hasLocalEdits).toBe(false)
    expect(undoAll.itinerary).toEqual(before.itinerary)
  })
})

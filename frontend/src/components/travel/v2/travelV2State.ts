export type DemoTransportMode = 'walk' | 'transit' | 'drive'

export interface DemoTripStop {
  id: string
  title: string
  time: string
  duration: number
  transport: DemoTransportMode
  category?: '景点' | '餐饮' | '休息' | '活动'
  image?: string
  description?: string
}

export interface DemoTripDay {
  day: number
  date: string
  stops: DemoTripStop[]
}

export interface DemoTripItinerary {
  destination: string
  people: number
  budget: number
  style: string
  days: DemoTripDay[]
}

export interface DemoTripState {
  /** 演示状态绝不代表服务端已保存。 */
  demoOnly: true
  hasLocalEdits: boolean
  undoItinerary: DemoTripItinerary | null
  undoHistory: DemoTripItinerary[]
  itinerary: DemoTripItinerary
}

export type DemoTripEdit =
  | { type: 'add-stop'; dayIndex: number; stop: DemoTripStop }
  | { type: 'delete-stop'; dayIndex: number; stopId: string }
  | { type: 'move-stop'; dayIndex: number; fromIndex: number; toIndex: number }
  | { type: 'move-stop-to-day'; fromDayIndex: number; toDayIndex: number; stopId: string }
  | { type: 'set-transport'; dayIndex: number; stopId: string; transport: DemoTransportMode }
  | { type: 'set-time'; dayIndex: number; stopId: string; time: string }
  | { type: 'set-time-and-duration'; dayIndex: number; stopId: string; time: string; duration: number }
  | { type: 'set-duration'; dayIndex: number; stopId: string; duration: number }
  | { type: 'replace-stop'; dayIndex: number; stopId: string; replacement: DemoTripStop }
  | { type: 'add-day' }

/** 仅更新浏览器内的演示行程；写入动作由后续 TripEditService 接管。 */
export function applyDemoTripEdit(state: DemoTripState, edit: DemoTripEdit): DemoTripState {
  const days = state.itinerary.days.map((day) => ({ ...day, stops: [...day.stops] }))

  if (edit.type === 'add-day') {
    const nextDayNumber = days.length + 1
    days.push({ day: nextDayNumber, date: `第 ${nextDayNumber} 天`, stops: [] })
  } else if (edit.type === 'move-stop-to-day') {
    const from = days[edit.fromDayIndex]
    const to = days[edit.toDayIndex]
    if (!from || !to || edit.fromDayIndex === edit.toDayIndex) return state
    const stopIndex = from.stops.findIndex((stop) => stop.id === edit.stopId)
    if (stopIndex < 0) return state
    const [stop] = from.stops.splice(stopIndex, 1)
    to.stops.push(stop)
  } else {
    const day = days[edit.dayIndex]
    if (!day) return state

    if (edit.type === 'add-stop') {
      day.stops.push(edit.stop)
    } else if (edit.type === 'delete-stop') {
      day.stops = day.stops.filter((stop) => stop.id !== edit.stopId)
    } else if (edit.type === 'move-stop') {
      if (edit.fromIndex < 0 || edit.fromIndex >= day.stops.length || edit.toIndex < 0 || edit.toIndex >= day.stops.length) return state
      const [stop] = day.stops.splice(edit.fromIndex, 1)
      day.stops.splice(edit.toIndex, 0, stop)
    } else if (edit.type === 'set-transport') {
      day.stops = day.stops.map((stop) => stop.id === edit.stopId ? { ...stop, transport: edit.transport } : stop)
    } else if (edit.type === 'set-time') {
      day.stops = day.stops.map((stop) => stop.id === edit.stopId ? { ...stop, time: edit.time } : stop)
    } else if (edit.type === 'set-time-and-duration') {
      day.stops = day.stops.map((stop) => stop.id === edit.stopId ? { ...stop, time: edit.time, duration: edit.duration } : stop)
    } else if (edit.type === 'set-duration') {
      day.stops = day.stops.map((stop) => stop.id === edit.stopId ? { ...stop, duration: edit.duration } : stop)
    } else if (edit.type === 'replace-stop') {
      day.stops = day.stops.map((stop) => stop.id === edit.stopId ? { ...edit.replacement, id: stop.id } : stop)
    }
  }

  return {
    ...state,
    hasLocalEdits: true,
    undoItinerary: state.itinerary,
    undoHistory: [...state.undoHistory, state.itinerary],
    itinerary: { ...state.itinerary, days },
  }
}

/** 撤销最近一次本地演示编辑，不发送请求，也不声称服务端保存状态。 */
export function undoDemoTripEdit(state: DemoTripState): DemoTripState {
  const history = state.undoHistory
  const previous = history[history.length - 1] ?? state.undoItinerary
  if (!previous) return state
  const remaining = history.slice(0, -1)
  return {
    ...state,
    hasLocalEdits: remaining.length > 0,
    undoItinerary: remaining[remaining.length - 1] ?? null,
    undoHistory: remaining,
    itinerary: previous,
  }
}

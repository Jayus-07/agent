import type { IntercityTrain, Itinerary, TransitLeg } from '@/api/travel'
import type {
  IntercityTrainArrangementV2,
  PlaceSelectionV2,
  PlaceSnapshotV2,
  TripDocumentV2,
  TripItemV2,
  TripLegV2,
} from '@/api/travelV2'

/** 将本轮旅游 Agent 的结构化输出转成 V2 正式文档，不读取或迁移旧行程记录。 */
export function itineraryToTripDocumentV2(itinerary: Itinerary): TripDocumentV2 {
  const brief = itinerary.brief
  const days = itinerary.days ?? []
  const destination = String(brief?.destination ?? '').trim()
  if (!destination || days.length === 0) {
    throw new Error('旅游 Agent 未返回完整行程，无法保存 V2 行程。')
  }

  const selections = new Map<string, PlaceSelectionV2>()
  const dayDocuments = days.map((day, dayIndex) => {
    const items: TripItemV2[] = (day.items ?? []).map((item, itemIndex) => {
      const id = `ai-day-${day.day_index || dayIndex + 1}-item-${itemIndex + 1}`
      const kind = String(item.kind ?? '').toLowerCase()
      const activityType = activityTypeFor(kind)
      const poi = item.poi
      if (!poi || activityType) {
        return {
          item_id: id,
          kind: 'activity',
          activity_type: activityType ?? 'custom',
          title: item.title,
          start_time: validClock(item.start) ? item.start : null,
          duration_min: safeMinutes(item.minutes),
          fixed_start: false,
          place: poi ? mapPlace(poi, item.title) : null,
          must_visit: false,
          locked: false,
          note: item.note ?? '',
        }
      }

      const place = mapPlace(poi, item.title)
      const mustVisit = Boolean(poi.required) || (brief.must_go ?? []).includes(poi.name)
      const existing = selections.get(place.place_id)
      if (!existing) {
        selections.set(place.place_id, {
          place_id: place.place_id,
          name: place.name,
          preference: mustVisit ? 'must_visit' : 'interested',
        })
      } else if (mustVisit) {
        selections.set(place.place_id, { ...existing, preference: 'must_visit' })
      }

      return {
        item_id: id,
        kind: 'place',
        activity_type: null,
        title: item.title,
        start_time: validClock(item.start) ? item.start : null,
        duration_min: safeMinutes(item.minutes),
        fixed_start: false,
        place,
        must_visit: mustVisit,
        locked: mustVisit,
        note: item.note ?? '',
      }
    })

    return {
      day_id: `ai-day-${day.day_index || dayIndex + 1}`,
      date: validDate(day.day_date)
        ? day.day_date
        : validDate(brief.start_date) ? addDays(brief.start_date, dayIndex) : null,
      title: `第 ${day.day_index || dayIndex + 1} 天`,
      items,
      legs: mapLegs(day.legs ?? [], day.items ?? [], items, dayIndex),
    }
  })

  const warnings = (itinerary.warnings ?? []).filter((item) => String(item).trim())
  const total = finiteOrNull(itinerary.cost?.total)
    ?? [itinerary.cost?.tickets, itinerary.cost?.meals, itinerary.cost?.lodging, itinerary.cost?.transit]
      .map((value) => finiteOrNull(value) ?? 0)
      .reduce((sum, value) => sum + value, 0)
  const allLegs = dayDocuments.flatMap((day) => day.legs)

  return {
    schema_version: 2,
    brief: {
      origin: String(brief.origin ?? ''),
      destination,
      timezone: 'Asia/Shanghai',
      start_date: validDate(brief.start_date) ? brief.start_date : null,
      day_count: days.length,
      travelers: { adults: Math.max(1, Math.floor(Number(brief.party_size) || 1)), children: 0 },
      budget: { amount: finiteOrNull(brief.budget_cny), currency: 'CNY' },
      pace: brief.pace === 'relaxed' ? 'relaxed' : brief.pace === 'intense' ? 'intense' : 'balanced',
      interests: [...(brief.preferences ?? [])],
      requirements: [
        ...(brief.must_go ?? []).map((name) => `必去：${name}`),
        ...(brief.avoid ?? []).map((name) => `避开：${name}`),
        ...[brief.diet, brief.lodging, brief.transport].filter((value) => Boolean(value)).map(String),
      ],
    },
    selections: [...selections.values()],
    days: dayDocuments,
    arrangements: {
      lodgings: [],
      intercity_trains: (itinerary.intercity ?? [])
        .map(mapTrain)
        .filter((train): train is IntercityTrainArrangementV2 => train !== null),
    },
    totals: {
      currency: 'CNY',
      estimated_cost: total,
      transit_min: allLegs.reduce((sum, leg) => sum + (leg.duration_min ?? 0), 0),
      distance_m: allLegs.some((leg) => leg.distance_m !== null)
        ? allLegs.reduce((sum, leg) => sum + (leg.distance_m ?? 0), 0)
        : null,
    },
    health: {
      status: warnings.length ? 'needs_attention' : 'ok',
      issues: warnings.map((message, index) => ({
        code: `planner_warning_${index + 1}`,
        severity: 'warning',
        message: String(message),
      })),
    },
  }
}

function mapPlace(poi: NonNullable<Itinerary['days'][number]['items'][number]['poi']>, title: string): PlaceSnapshotV2 {
  const raw = poi as typeof poi & { address?: unknown; rating?: unknown; open_status?: unknown; opening_hours?: unknown }
  const lat = finiteOrNull(poi.lat)
  const lng = finiteOrNull(poi.lng)
  const source = String(poi.source ?? 'travel_agent')
  const coordinateStatus = String(poi.location_status ?? '').toLowerCase()
  const verification = coordinateStatus === 'verified'
    ? 'verified'
    : lat !== null && lng !== null ? 'estimated' : 'unknown'
  return {
    place_id: String(poi.poi_id || `agent:${title}`),
    name: String(poi.name || title),
    lat,
    lng,
    address: typeof raw.address === 'string' ? raw.address : null,
    facts: {
      opening_hours: raw.opening_hours ?? null,
      ticket_price_cny: finiteOrNull(poi.ticket_cny),
      rating: finiteOrNull(raw.rating),
      open_status: typeof raw.open_status === 'string' ? raw.open_status : null,
      verification,
      source,
      observed_at: null,
    },
  }
}

function mapLegs(
  legs: TransitLeg[],
  sourceItems: Itinerary['days'][number]['items'],
  targetItems: TripItemV2[],
  dayIndex: number,
): TripLegV2[] {
  let previousFromIndex = -1
  return legs.flatMap((leg, legIndex) => {
    const fromIndex = sourceItems.findIndex((item, index) => index > previousFromIndex
      && item.title === leg.from_title && sourceItems[index + 1]?.title === leg.to_title)
    if (fromIndex < 0) return []
    previousFromIndex = fromIndex
    const fromTarget = targetItems[fromIndex]
    const toTarget = targetItems[fromIndex + 1]
    if (!fromTarget || !toTarget) return []
    const coordinateFreeActivity = [fromTarget, toTarget].some((item) =>
      item.kind === 'activity' && (!item.place || item.place.lat === null || item.place.lng === null))
    if (coordinateFreeActivity) return []
    const duration = finiteOrNull(leg.minutes)
    if (duration === null || duration < 0) return []
    const mode = String(leg.mode ?? '').toLowerCase()
    const selectedMode = mode.includes('walk') || mode.includes('步行')
      ? 'walk'
      : mode.includes('taxi') || mode.includes('打车') || mode.includes('出租')
        ? 'taxi'
        : mode.includes('transit') || mode.includes('bus') || mode.includes('subway') || mode.includes('公交') || mode.includes('地铁')
          ? 'transit'
          : 'drive'
    const distance = finiteOrNull(leg.distance_km)
    const observed = leg.observed_at && isIsoDateTime(leg.observed_at) ? leg.observed_at : null
    return [{
      leg_id: `ai-day-${dayIndex + 1}-leg-${legIndex + 1}`,
      from_item_id: fromTarget.item_id,
      to_item_id: toTarget.item_id,
      selected_mode: selectedMode,
      duration_min: duration,
      distance_m: distance === null ? null : Math.round(distance * 1000),
      cost_cny: finiteOrNull(leg.cost_cny),
      reliability: leg.is_estimate === false ? 'verified' : 'estimated',
      source: leg.source || null,
      observed_at: observed,
    }]
  })
}

function mapTrain(train: IntercityTrain): IntercityTrainArrangementV2 | null {
  const duration = durationMinutes(train.duration)
  if (!train.train_no || !validDate(train.date) || !train.from_station || !train.to_station
    || !validClock(train.start_time) || !validClock(train.arrive_time)
    || duration === null || !isIsoDateTime(train.queried_at)) return null
  return {
    selection_id: `agent-train:${train.date}:${train.train_no}`,
    train_code: train.train_no,
    travel_date: train.date,
    departure_station: train.from_station,
    arrival_station: train.to_station,
    departure_time: train.start_time,
    arrival_time: train.arrive_time,
    duration_min: duration,
    tickets: { ...(train.seats ?? {}) },
    fares: { ...(train.prices ?? {}) },
    ticket_source: train.source || '12306',
    fare_source: Object.keys(train.prices ?? {}).length ? train.source || '12306' : null,
    queried_at: train.queried_at,
    verification: 'delayed',
    booking_status: 'not_booked',
  }
}

function activityTypeFor(kind: string): TripItemV2['activity_type'] | null {
  if (kind === 'meal') return 'meal'
  if (kind === 'rest') return 'rest'
  if (kind === 'shopping') return 'shopping'
  if (kind === 'free_time') return 'free_time'
  if (kind === 'visit') return null
  return 'custom'
}

function durationMinutes(value: string): number | null {
  const hours = value.match(/(\d+)\s*小时/)
  const minutes = value.match(/(\d+)\s*分/)
  const total = (hours ? Number(hours[1]) * 60 : 0) + (minutes ? Number(minutes[1]) : 0)
  return total > 0 ? total : null
}

function safeMinutes(value: number): number {
  const minutes = finiteOrNull(value)
  return minutes === null ? 0 : Math.max(0, Math.min(24 * 60, Math.floor(minutes)))
}

function finiteOrNull(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return value >= 0 ? value : null
}

function validDate(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
}

function addDays(value: string, days: number): string {
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day + days))
  return date.toISOString().slice(0, 10)
}

function validClock(value: unknown): value is string {
  return typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(value)
}

function isIsoDateTime(value: string): boolean {
  return Boolean(value) && !Number.isNaN(Date.parse(value))
}

import type { PlaceVerificationV2, TripDayV2 } from '@/api/travelV2'

export interface TravelMapPoint {
  id: string
  name: string
  lat: number
  lng: number
  verification: PlaceVerificationV2
  source: string | null
  candidate: boolean
}

export interface TravelMapDayPoint extends TravelMapPoint {
  /** 所属天（0 起）。候选点挂在当前选中天上。 */
  dayIndex: number
}

// 天色清单（2026-11-03 用户口径：同天标记+折线同色，按天区分动线）。
// 取自原型 DAYCOLORS；超过 6 天循环使用。
export const TRAVEL_MAP_DAY_COLORS: readonly string[] = [
  '#2878f5', '#6d3fd1', '#0d9488', '#d97706', '#dc2626', '#2563eb',
]

export function travelMapDayColor(dayIndex: number): string {
  const count = TRAVEL_MAP_DAY_COLORS.length
  return TRAVEL_MAP_DAY_COLORS[((dayIndex % count) + count) % count]
}

export interface TemporaryTravelMapLocation {
  name: string
  lat: number
  lng: number
  verification?: unknown
  source?: string | null
}

export function isValidMapCoordinate(lat: unknown, lng: unknown): boolean {
  return typeof lat === 'number'
    && typeof lng === 'number'
    && Number.isFinite(lat)
    && Number.isFinite(lng)
    && lat >= -90
    && lat <= 90
    && lng >= -180
    && lng <= 180
}

export function normalizeMapVerification(value: unknown): PlaceVerificationV2 {
  return value === 'verified' || value === 'estimated' ? value : 'unknown'
}

export function travelMapPointColor(verification: PlaceVerificationV2): string {
  if (verification === 'verified') return '#2878f5'
  if (verification === 'estimated') return '#b96818'
  return '#667b94'
}

export function getTravelMapPoints(
  day: TripDayV2 | null,
  selectedLocation: TemporaryTravelMapLocation | null,
): TravelMapPoint[] {
  const savedPoints: TravelMapPoint[] = (day?.items ?? []).flatMap((item) => {
    const place = item.place
    if (!place || typeof place.lat !== 'number' || typeof place.lng !== 'number') return []
    if (!isValidMapCoordinate(place.lat, place.lng)) return []
    return [{
      id: item.item_id,
      name: item.title,
      lat: place.lat,
      lng: place.lng,
      verification: normalizeMapVerification(place.facts?.verification),
      source: place.facts?.source ?? null,
      candidate: false,
    }]
  })

  if (
    !selectedLocation
    || !isValidMapCoordinate(selectedLocation.lat, selectedLocation.lng)
  ) {
    return savedPoints
  }

  return [...savedPoints, {
    id: 'search-result',
    name: selectedLocation.name,
    lat: selectedLocation.lat,
    lng: selectedLocation.lng,
    verification: normalizeMapVerification(selectedLocation.verification),
    source: selectedLocation.source ?? null,
    candidate: true,
  }]
}

/** 全天数取点（2026-11-03 地图按天分色连线）：候选点只挂当前选中天。 */
export function getAllTravelMapPoints(
  days: TripDayV2[],
  activeDayIndex: number,
  selectedLocation: TemporaryTravelMapLocation | null,
): TravelMapDayPoint[] {
  const saved: TravelMapDayPoint[] = days.flatMap((day, dayIndex) =>
    (day?.items ?? []).flatMap((item) => {
      const place = item.place
      if (!place || typeof place.lat !== 'number' || typeof place.lng !== 'number') return []
      if (!isValidMapCoordinate(place.lat, place.lng)) return []
      return [{
        id: item.item_id,
        name: item.title,
        lat: place.lat,
        lng: place.lng,
        verification: normalizeMapVerification(place.facts?.verification),
        source: place.facts?.source ?? null,
        candidate: false,
        dayIndex,
      }]
    }),
  )
  if (
    !selectedLocation
    || !isValidMapCoordinate(selectedLocation.lat, selectedLocation.lng)
    || days.length === 0
  ) {
    return saved
  }
  const active = Math.min(Math.max(activeDayIndex, 0), days.length - 1)
  return [...saved, {
    id: 'search-result',
    name: selectedLocation.name,
    lat: selectedLocation.lat,
    lng: selectedLocation.lng,
    verification: normalizeMapVerification(selectedLocation.verification),
    source: selectedLocation.source ?? null,
    candidate: true,
    dayIndex: active,
  }]
}

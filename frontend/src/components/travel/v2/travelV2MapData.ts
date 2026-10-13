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

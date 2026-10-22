import TravelTripWorkspace from '@/components/travel/v2/TravelTripWorkspace'
import TravelV2TripWorkspace from '@/components/travel/v2/TravelV2TripWorkspace'

export default async function TravelItineraryEditRoute({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return id === 'demo'
    ? <TravelTripWorkspace tripId={id} editMode />
    : <TravelV2TripWorkspace tripId={id} editMode />
}

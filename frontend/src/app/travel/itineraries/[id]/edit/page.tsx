import TravelTripWorkspace from '@/components/travel/v2/TravelTripWorkspace'
import TravelV2TripWorkspace from '@/components/travel/v2/TravelV2TripWorkspace'

export default function TravelItineraryEditRoute({ params }: { params: { id: string } }) {
  return params.id === 'demo'
    ? <TravelTripWorkspace tripId={params.id} editMode />
    : <TravelV2TripWorkspace tripId={params.id} editMode />
}

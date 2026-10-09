import TravelTripWorkspace from '@/components/travel/v2/TravelTripWorkspace'
import TravelV2TripWorkspace from '@/components/travel/v2/TravelV2TripWorkspace'

export default function TravelItineraryRoute({ params }: { params: { id: string } }) {
  return params.id === 'demo'
    ? <TravelTripWorkspace tripId={params.id} />
    : <TravelV2TripWorkspace tripId={params.id} />
}

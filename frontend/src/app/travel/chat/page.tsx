import TravelChatPage from '@/components/travel/v2/TravelChatPage'

export default function TravelChatRoute({ searchParams }: { searchParams: { prompt?: string; conversation_id?: string; trip_id?: string } }) {
  return <TravelChatPage key={`${searchParams.trip_id ?? ''}:${searchParams.conversation_id ?? ''}`} initialPrompt={searchParams.prompt ?? ''} initialConversationId={searchParams.conversation_id ?? ''} initialTripId={searchParams.trip_id ?? ''} />
}

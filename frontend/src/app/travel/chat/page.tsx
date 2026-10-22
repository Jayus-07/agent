import TravelChatPage from '@/components/travel/v2/TravelChatPage'

export default async function TravelChatRoute({ searchParams }: { searchParams: Promise<{ prompt?: string; conversation_id?: string; trip_id?: string }> }) {
  const query = await searchParams
  return <TravelChatPage key={`${query.trip_id ?? ''}:${query.conversation_id ?? ''}`} initialPrompt={query.prompt ?? ''} initialConversationId={query.conversation_id ?? ''} initialTripId={query.trip_id ?? ''} />
}

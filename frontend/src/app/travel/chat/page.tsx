import TravelChatPage from '@/components/travel/v2/TravelChatPage'

export default function TravelChatRoute({ searchParams }: { searchParams: { prompt?: string; conversation_id?: string } }) {
  return <TravelChatPage initialPrompt={searchParams.prompt ?? ''} initialConversationId={searchParams.conversation_id ?? ''} />
}

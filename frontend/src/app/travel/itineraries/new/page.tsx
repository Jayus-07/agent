import TravelWorkbenchGenerate from '@/components/travel/v2/TravelWorkbenchGenerate'

export default async function TravelWorkbenchGenerateRoute({ searchParams }: { searchParams: Promise<{ prompt?: string; conversation_id?: string }> }) {
  const query = await searchParams
  return <TravelWorkbenchGenerate key={query.conversation_id ?? ''} initialPrompt={query.prompt ?? ''} initialConversationId={query.conversation_id ?? ''} />
}

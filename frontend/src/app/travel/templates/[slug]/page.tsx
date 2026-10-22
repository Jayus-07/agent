import TravelTemplatePreview from '@/components/travel/v2/TravelTemplatePreview'

export default async function TravelTemplateRoute({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  return <TravelTemplatePreview slug={slug} />
}

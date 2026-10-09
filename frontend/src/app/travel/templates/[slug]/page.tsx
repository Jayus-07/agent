import TravelTemplatePreview from '@/components/travel/v2/TravelTemplatePreview'

export default function TravelTemplateRoute({ params }: { params: { slug: string } }) {
  return <TravelTemplatePreview slug={params.slug} />
}

import type { Metadata } from 'next'
import TravelV2PublicShare from '@/components/travel/v2/TravelV2PublicShare'

export const metadata: Metadata = {
  title: '旅行行程分享',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
}

export default async function TravelSharePage({ params }: { params: Promise<{ share_id: string }> }) {
  const { share_id } = await params
  return <TravelV2PublicShare shareId={share_id} />
}

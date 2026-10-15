import type { Metadata } from 'next'
import TravelV2PublicShare from '@/components/travel/v2/TravelV2PublicShare'

export const metadata: Metadata = {
  title: '旅行行程分享',
  robots: { index: false, follow: false },
  referrer: 'no-referrer',
}

export default function TravelSharePage({ params }: { params: { share_id: string } }) {
  return <TravelV2PublicShare shareId={params.share_id} />
}

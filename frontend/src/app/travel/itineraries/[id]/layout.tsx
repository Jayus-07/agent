import type { ReactNode } from 'react'
import { DemoTripProvider } from '@/components/travel/v2/DemoTripContext'

export default function TravelItineraryLayout({ children, params }: { children: ReactNode; params: { id: string } }) {
  return <DemoTripProvider key={params.id}>{children}</DemoTripProvider>
}

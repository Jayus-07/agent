import type { ReactNode } from 'react'
import { DemoTripProvider } from '@/components/travel/v2/DemoTripContext'

export default async function TravelItineraryLayout({ children, params }: { children: ReactNode; params: Promise<{ id: string }> }) {
  const { id } = await params
  return <DemoTripProvider key={id}>{children}</DemoTripProvider>
}

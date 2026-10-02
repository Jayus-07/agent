import { redirect } from 'next/navigation'
import { legacyRouteToWorkbench } from '@/lib/workbenchRoutes'

function toSearchParams(searchParams: Record<string, string | string[] | undefined>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(searchParams)) {
    if (Array.isArray(value)) value.forEach((item) => params.append(key, item))
    else if (value !== undefined) params.set(key, value)
  }
  return params
}

export default function LegacySelectionFunnelPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>
}) {
  redirect(legacyRouteToWorkbench('/selection-funnel', toSearchParams(searchParams)) ?? '/selection-workbench?tab=funnel')
}

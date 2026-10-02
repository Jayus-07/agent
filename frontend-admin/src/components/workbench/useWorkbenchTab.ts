'use client'

import { useCallback } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { resolveWorkbenchTab } from '@/lib/workbenchRoutes'

export function useWorkbenchTab<TabId extends string>(
  tabIds: readonly TabId[],
  defaultTab: TabId,
): { tab: TabId; selectTab: (nextTab: TabId) => void } {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const tab = resolveWorkbenchTab(searchParams.get('tab'), tabIds, defaultTab)

  const selectTab = useCallback(
    (nextTab: TabId) => {
      const nextParams = new URLSearchParams(searchParams.toString())
      nextParams.set('tab', nextTab)
      const query = nextParams.toString()
      router.push(query ? pathname + '?' + query : pathname)
    },
    [pathname, router, searchParams],
  )

  return { tab, selectTab }
}

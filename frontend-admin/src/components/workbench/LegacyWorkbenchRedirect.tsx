'use client'

import { useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { legacyRouteToWorkbench } from '@/lib/workbenchRoutes'

export default function LegacyWorkbenchRedirect({ legacyPath }: { legacyPath: string }) {
  const router = useRouter()
  const searchParams = useSearchParams()

  useEffect(() => {
    const target = legacyRouteToWorkbench(legacyPath, new URLSearchParams(searchParams.toString()))
    router.replace(target ?? '/')
  }, [legacyPath, router, searchParams])

  return <p className="p-6 text-xs text-text-muted">正在打开工作台…</p>
}

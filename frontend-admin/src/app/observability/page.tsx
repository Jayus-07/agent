'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

export default function ObservabilityPage() {
  const router = useRouter()
  useEffect(() => { router.replace('/observability/traces') }, [router])
  return null
}

'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

/** /knowledge 裸路由无页面：重定向到首个子项（文档入库），对齐 /observability 的处理 */
export default function KnowledgeIndexPage() {
  const router = useRouter()
  useEffect(() => { router.replace('/knowledge/documents') }, [router])
  return null
}

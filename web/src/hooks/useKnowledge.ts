'use client'

import { useState, useEffect, useCallback } from 'react'
import { knowledgeService, type KnowledgeStats, type KnowledgeDoc } from '@/services/knowledge'
import { KB_STATS, KB_DOCS } from '@/services/mock/knowledge'

// Hook — 知识库统计（API 优先，失败回退 Mock）
const MOCK_STATS: KnowledgeStats = { kb_count: KB_STATS.kbCount, doc_count: KB_STATS.docCount, chunk_count: KB_STATS.chunkCount, embedding_model: KB_STATS.embeddingModel, vector_db: KB_STATS.vectorDb }

export function useKnowledgeStats() {
  const [data, setData] = useState<KnowledgeStats & { loading: boolean }>({ ...MOCK_STATS, loading: true })

  useEffect(() => {
    knowledgeService.getStats()
      .then(d => { if (d.doc_count > 0) setData({ ...d, loading: false }) })
      .catch(() => setData(prev => ({ ...prev, loading: false })))
  }, [])

  return data
}

// Hook — 文档列表（API 优先，支持搜索/分页）
export function useDocuments() {
  const [data, setData] = useState<(KnowledgeDoc & { _mock?: boolean })[]>(
    KB_DOCS.map(d => ({ id: d.id, name: d.name, kb_id: 'default', type: d.type, size: d.size, chunk_count: d.chunks, chunks: d.chunks, hash: '', status: 'active', last_indexed: d.updatedAt, _mock: true }))
  )
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [keyword, setKeyword_] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 20

  const refresh = useCallback(() => {
    setLoading(true)
    knowledgeService.getDocuments({ keyword, page, page_size: pageSize })
      .then(d => {
        if (d.total > 0 || keyword) {
          // 有搜索条件或 API 有数据时用 API 结果
          setData(d.documents)
          setTotal(d.total)
        }
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [keyword, page])

  useEffect(() => { refresh() }, [refresh])

  // 防抖搜索：修改关键词时重置页码
  const setKeyword = useCallback((kw: string) => {
    setKeyword_(kw)
    setPage(1)
  }, [])

  return { documents: data, loading, total, page, pageSize, keyword, setKeyword, setPage, refresh }
}

// Hook — 单个文档详情（独立 fetch）
export function useDocument(docId: string) {
  const [doc, setDoc] = useState<KnowledgeDoc | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(() => {
    setLoading(true)
    knowledgeService.getDocument(docId)
      .then(d => { if (d.ok && d.doc) setDoc(d.doc); setLoading(false) })
      .catch(() => setLoading(false))
  }, [docId])

  useEffect(() => { refresh() }, [refresh])

  return { doc, loading, refresh }
}

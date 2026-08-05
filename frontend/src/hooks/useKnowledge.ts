'use client'

import { useState, useEffect, useCallback } from 'react'
import { knowledgeService, type KnowledgeStats, type KnowledgeDoc } from '@/services/knowledge'

// Hook — 知识库统计（真实 API，无 Mock 回退）
export function useKnowledgeStats() {
  const [data, setData] = useState<KnowledgeStats & { loading: boolean; error: string }>({
    kb_count: 0, doc_count: 0, chunk_count: 0, total_chunks: 0, embedding_model: '', vector_db: '',
    loading: true, error: '',
  })

  useEffect(() => {
    knowledgeService.getStats()
      .then((d: KnowledgeStats) => setData({ ...d, loading: false, error: '' }))
      .catch((e: Error) => setData(prev => ({ ...prev, loading: false, error: '获取统计失败: ' + String(e) })))
  }, [])

  return data
}

// Hook — 文档列表（真实 API，支持搜索/分页/筛选）
export function useDocuments() {
  const [data, setData] = useState<KnowledgeDoc[]>([])
  const [loading, setLoading] = useState(true)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [currentFingerprint, setCurrentFingerprint] = useState('')
  const [keyword, setKeyword_] = useState('')
  const [status, setStatus_] = useState('')
  const [type, setType_] = useState('')
  const [kbId, setKbId_] = useState('')
  const [dept, setDept_] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 20

  const refresh = useCallback(() => {
    setLoading(true)
    setError('')
    knowledgeService.getDocuments({ keyword, status, type, kb_id: kbId, department: dept, page, page_size: pageSize })
      .then((d: { documents: KnowledgeDoc[]; total: number; current_fingerprint?: string }) => {
        setData(d.documents ?? [])
        setTotal(d.total)
        setCurrentFingerprint(d.current_fingerprint || '')
        setLoading(false)
      })
      .catch((e: Error) => {
        setError('获取文档列表失败: ' + String(e))
        setLoading(false)
      })
  }, [keyword, status, type, kbId, dept, page])

  useEffect(() => { refresh() }, [refresh])

  const setKeyword = useCallback((kw: string) => {
    setKeyword_(kw)
    setPage(1)
  }, [])

  const setStatus = useCallback((s: string) => {
    setStatus_(s)
    setPage(1)
  }, [])

  const setType = useCallback((t: string) => {
    setType_(t)
    setPage(1)
  }, [])

  const setKbId = useCallback((k: string) => {
    setKbId_(k)
    setPage(1)
  }, [])

  const setDept = useCallback((d: string) => {
    setDept_(d)
    setPage(1)
  }, [])

  return { documents: data, loading, total, page, pageSize, keyword, setKeyword, status, setStatus, type, setType, kbId, setKbId, dept, setDept, setPage, error, refresh, currentFingerprint }
}

// Hook — 单个文档详情（独立 fetch）
export function useDocument(docId: string) {
  const [doc, setDoc] = useState<KnowledgeDoc | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(() => {
    setLoading(true)
    setError('')
    knowledgeService.getDocument(docId)
      .then((d: { ok: boolean; doc?: KnowledgeDoc; error?: string }) => {
        if (d.ok && d.doc) { setDoc(d.doc); setError('') }
        else { setDoc(null); setError(d.error || '文档不存在') }
        setLoading(false)
      })
      .catch((e: Error) => {
        setError('获取文档详情失败: ' + String(e))
        setLoading(false)
      })
  }, [docId])

  useEffect(() => { refresh() }, [refresh])

  return { doc, loading, error, refresh }
}

'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { Activity, FileText, Search, Trash2, RefreshCw, Grid3X3, Cpu, Loader2, ChevronLeft, ChevronRight, CheckSquare, Square, X } from 'lucide-react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useDocuments, useKnowledgeStats } from '@/hooks/useKnowledge'
import { knowledgeService, type ChunkDetailItem, type LLMChunkingFallbackStats } from '@/api/knowledge'
import UploadDialog from '@/components/knowledge/UploadDialog'
import { useToast } from '@/components/shared/Toast'
import { getReindexTerminalState } from '@/lib/reindex-status'
import {
  formatChunkMetadataValue,
  getBusinessDomainLabel,
  getChunkMetadataLabel,
  getChunkStrategyLabel,
  getChunkTypeLabel,
  getDepartmentLabel,
  getDocumentTypeLabel,
  getFallbackTypeLabel,
  getGranularityLabel,
  getKnowledgeBaseLabel,
  getCacheStatusLabel,
  getModelRoleLabel,
  getOperationLabel,
  getParentScopeLabel,
  getProcessingCodeLabel,
  getProcessingRouteLabel,
  getProcessingStageLabel,
  getProcessingStatusLabel,
  getProviderLabel,
  getQualityIssueLabel,
} from '@/lib/knowledge-display'

const QUALITY_STATUS_CONFIG: Record<string, { label: string; className: string; title: string }> = {
  pass: { label: '质量通过', className: 'bg-green-50 text-green-700', title: '当前质量检查通过' },
  soft_warning: { label: '需复核', className: 'bg-amber-50 text-amber-700', title: '存在质量提醒，建议复核原文和分块' },
  failed: { label: '质量失败', className: 'bg-red-50 text-red-700', title: '质量门禁失败，文档不应作为合格资料' },
  unknown: { label: '未核验', className: 'bg-gray-100 text-gray-600', title: '没有可用的质量结论，不能按通过处理' },
}

const KNOWLEDGE_BASE_AUDIENCE_CN: Record<string, string> = {
  customer: '对客库',
  internal: '内部库',
  test: '测试库',
  unknown: '未分类',
}

/** 格式化文档时间：年-月-日 时:分:秒 */
function fmtDocTime(t?: string): string {
  if (!t) return '-'
  const d = new Date((t + '').replace(' ', 'T') + (t.includes('Z') ? '' : 'Z'))
  if (isNaN(d.getTime())) return t.slice(0, 19)
  return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

// 统一状态徽章映射
const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  uploading:  { label: '上传中', className: 'bg-blue-50 text-blue-600' },
  parsing:    { label: '解析中', className: 'bg-purple-50 text-purple-600' },
  embedding:  { label: '向量化', className: 'bg-amber-50 text-amber-600' },
  active:     { label: '活跃',   className: 'bg-green-50 text-green-600' },
  failed:     { label: '失败',   className: 'bg-red-50 text-red-600' },
  deleted:    { label: '已删除', className: 'bg-gray-50 text-gray-400' },
  // 兼容旧状态
  done:       { label: '已完成', className: 'bg-green-50 text-green-600' },
  processing: { label: '处理中', className: 'bg-amber-50 text-amber-600' },
  error:      { label: '失败',   className: 'bg-red-50 text-red-600' },
  indexing:   { label: '索引中', className: 'bg-amber-50 text-amber-600' },
  unknown:    { label: '未知状态', className: 'bg-gray-50 text-gray-500' },
}

export default function DocumentsPanel() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const toast = useToast()
  const initialPage = Number(searchParams.get('doc_page') || '1')
  const { documents, loading, total, page, pageSize, keyword, setKeyword, status, setStatus, qualityStatus, setQualityStatus, type, setType, kbId, setKbId, dept, setDept, setPage, error, refresh } = useDocuments(initialPage)
  const knowledgeStats = useKnowledgeStats()
  const qualityCounts = knowledgeStats.quality_status_counts ?? {}
  const [uploadOpen, setUploadOpen] = useState(false)
  const [reindexing, setReindexing] = useState<Set<string>>(new Set())
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null)
  const [deletePending, setDeletePending] = useState(false)
  // 批量操作
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [batchReindexing, setBatchReindexing] = useState(false)
  const [batchDeleting, setBatchDeleting] = useState(false)
  const [batchDeleteTarget, setBatchDeleteTarget] = useState<number | null>(null)
  const [lineageDoc, setLineageDoc] = useState<{ id: string; name: string } | null>(null)
  const [lineageRuns, setLineageRuns] = useState<any[]>([])
  const [lineageDetail, setLineageDetail] = useState<any | null>(null)
  const [lineageLoading, setLineageLoading] = useState(false)
  const [chunkDoc, setChunkDoc] = useState<{ id: string; name: string } | null>(null)
  const [chunkItems, setChunkItems] = useState<ChunkDetailItem[]>([])
  const [chunkLoading, setChunkLoading] = useState(false)
  const [chunkError, setChunkError] = useState('')
  const chunkRequestRef = useRef(0)
  const [llmFallbackStats, setLlmFallbackStats] = useState<LLMChunkingFallbackStats | null>(null)
  const [llmFallbackStatsLoading, setLlmFallbackStatsLoading] = useState(false)
  const [llmFallbackStatsError, setLlmFallbackStatsError] = useState('')
  const llmFallbackStatsMountedRef = useRef(false)
  const llmFallbackStatsRequestRef = useRef(false)
  // G2：知识库筛选选项 = 后端注册库真实清单（含文档计数），不再硬编码
  const [kbOptions, setKbOptions] = useState<Array<{
    id: string
    name: string
    doc_count?: number
    depts?: Array<{ id: string; label: string }>
  }>>([])
  // 受控搜索：本地 input 状态（立即响应）+ 防抖同步到 store（API 调用）
  const [inputValue, setInputValue] = useState(keyword)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  // 轮询循环的卸载守卫（重索引任务化后页面离开即停止跟踪）
  const mountedRef = useRef(true)

  // 防抖搜索：本地 input 立即更新，API 请求 300ms 后发出
  const handleSearch = (value: string) => {
    setInputValue(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => setKeyword(value), 300)
  }

  // 清除按钮：立即清空本地 + 立即清空 store（不等防抖）
  const handleClear = () => {
    setInputValue('')
    if (debounceRef.current) clearTimeout(debounceRef.current)
    setKeyword('')
  }

  useEffect(() => {
    return () => {
      mountedRef.current = false
      chunkRequestRef.current += 1
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const refreshLlmFallbackStats = useCallback(async () => {
    if (llmFallbackStatsRequestRef.current) return
    llmFallbackStatsRequestRef.current = true
    setLlmFallbackStatsLoading(true)
    try {
      const result = await knowledgeService.getLLMChunkingFallbackStats({ days: 30 })
      if (!llmFallbackStatsMountedRef.current) return
      setLlmFallbackStats(result)
      setLlmFallbackStatsError('')
    } catch (error) {
      if (!llmFallbackStatsMountedRef.current) return
      setLlmFallbackStatsError(error instanceof Error ? error.message : '切分统计读取失败')
    } finally {
      llmFallbackStatsRequestRef.current = false
      if (llmFallbackStatsMountedRef.current) setLlmFallbackStatsLoading(false)
    }
  }, [])

  useEffect(() => {
    llmFallbackStatsMountedRef.current = true
    void refreshLlmFallbackStats()
    const timer = window.setInterval(() => void refreshLlmFallbackStats(), 60_000)
    return () => {
      llmFallbackStatsMountedRef.current = false
      window.clearInterval(timer)
    }
  }, [refreshLlmFallbackStats])

  useEffect(() => {
    if (!chunkDoc) return
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      chunkRequestRef.current += 1
      setChunkDoc(null)
      setChunkItems([])
      setChunkError('')
      setChunkLoading(false)
    }
    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [chunkDoc])

  // G2：加载真实知识库清单（注册库 + 文档计数）
  const refreshKbOptions = useCallback(async () => {
    const d: any = await knowledgeService.listKnowledgeBases()
    if (!Array.isArray(d?.knowledge_bases)) return
    setKbOptions(d.knowledge_bases.map((k: any) => ({
      id: k.id,
      name: k.name || '',
      doc_count: k.doc_count,
      depts: Array.isArray(k.depts) ? k.depts : [],
    })))
  }, [])

  useEffect(() => { void refreshKbOptions() }, [refreshKbOptions])

  const refreshAfterDocumentMutation = useCallback(() => {
    refresh()
    void knowledgeStats.refresh()
    void refreshKbOptions()
  }, [knowledgeStats.refresh, refresh, refreshKbOptions])

  // 翻页/搜索/过滤变化时清空选择（选中集只对当前页有意义）
  useEffect(() => {
    clearSelection()
  }, [page, keyword, status, type, kbId, dept])

  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString())
    if (page > 1) params.set('doc_page', String(page))
    else params.delete('doc_page')
    const nextQuery = params.toString()
    const currentQuery = searchParams.toString()
    if (nextQuery !== currentQuery) {
      router.replace(`/knowledge/workbench${nextQuery ? `?${nextQuery}` : ''}`, { scroll: false })
    }
  }, [page, router, searchParams])

  const handleDelete = async () => {
    if (!deleteTarget || deletePending) return
    const { id, name } = deleteTarget
    setDeletePending(true)
    try {
      const result = await knowledgeService.deleteDocument(id)
      if (result?.ok === true) {
        if (result.degraded || result.warnings?.length) {
          const detail = result.warnings?.length ? `：${result.warnings.join('；')}` : ''
          toast.warning(`已删除「${name}」，但部分清理未完成${detail}`)
        } else {
          toast.success(`已删除「${name}」`)
        }
      } else if (result?.operation_state === 'unknown') {
        toast.warning(`「${name}」删除状态暂未确认。列表已刷新，请核对文档是否仍存在后再决定是否重试。${result.error ? `（${result.error}）` : ''}`)
      } else {
        toast.error(`删除「${name}」失败：${result?.error || '服务端拒绝删除请求'}`)
      }
      setDeleteTarget(null)
    } catch (err) {
      toast.error(`删除「${name}」失败：${err instanceof Error ? err.message : '未知错误'}`)
      setDeleteTarget(null)
    } finally {
      refreshAfterDocumentMutation()
      setDeletePending(false)
    }
  }

  // 重索引进度轮询：任务化后提交即返回。任务状态是主要依据；只有缺少任务状态时，
  // 才接受本次提交后写入的终态进度，防止复用上一次任务的完成状态。
  const pollReindexStatus = async (id: string, name: string, submittedAtMs: number) => {
    const started = Date.now()
    while (mountedRef.current && Date.now() - started < 600_000) {
      await new Promise(r => setTimeout(r, 1500))
      if (!mountedRef.current) return
      let res: any
      try {
        res = await knowledgeService.getReindexStatus(id)
      } catch {
        continue // 瞬时网络错误不中断轮询
      }
      if (!res?.ok) continue
      const status = res.task?.status as string | undefined
      const stage = res.progress?.stage as string | undefined
      const terminal = getReindexTerminalState({
        taskStatus: status,
        progressStage: stage,
        progressUpdatedAt: res.progress?.updated_at,
        submittedAtMs,
      })
      if (terminal === 'success') {
        const chunks = res.progress?.detail?.chunk_count
        toast.success(`「${name}」重索引完成${chunks ? `（${chunks} 个分块）` : ''}`)
        return
      }
      if (terminal === 'failure') {
        toast.error(`「${name}」重索引失败：${res.task?.error || res.progress?.message || '未知错误'}`)
        return
      }
    }
    if (mountedRef.current) toast.info(`「${name}」重索引仍在后台执行，可稍后在操作记录中查看结果`)
  }

  const handleReindex = async (id: string, name: string) => {
    if (reindexing.has(id)) return
    setReindexing(prev => new Set(prev).add(id))
    const submittedAtMs = Date.now()
    try {
      const res = await knowledgeService.reindexDocument(id)
      if (!res?.ok) {
        toast.error(`「${name}」重索引提交失败：${res?.error || '未知错误'}`)
        return
      }
      if (!res.async) {
        // 同步降级路径（任务队列不可达时的本机回退）：响应即终态
        toast.success(`「${name}」重索引完成`)
        refreshAfterDocumentMutation()
        return
      }
      toast.info(res.joined ? `「${name}」已有重索引进行中，正在跟踪进度` : `「${name}」重索引任务已提交`)
      await pollReindexStatus(id, name, submittedAtMs)
      refreshAfterDocumentMutation()
    } finally {
      setReindexing(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  const openLineage = async (doc: { id: string; name: string }) => {
    setLineageDoc(doc)
    setLineageLoading(true)
    setLineageDetail(null)
    try {
      const result = await knowledgeService.getProcessingRuns(doc.id, { page: 1, page_size: 10 })
      const runs = result.items ?? []
      setLineageRuns(runs)
      if (runs[0]?.run_id) {
        setLineageDetail(await knowledgeService.getProcessingRun(doc.id, runs[0].run_id))
      }
    } finally {
      setLineageLoading(false)
    }
  }

  const selectLineageRun = async (runId: string) => {
    if (!lineageDoc) return
    setLineageLoading(true)
    try {
      setLineageDetail(await knowledgeService.getProcessingRun(lineageDoc.id, runId))
    } finally {
      setLineageLoading(false)
    }
  }

  // ── 批量操作 ──
  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    setSelectedIds(prev => {
      const pageIds = documents.map(d => d.id)
      const allSelected = pageIds.length > 0 && pageIds.every(id => prev.has(id))
      const next = new Set(prev)
      if (allSelected) {
        pageIds.forEach(id => next.delete(id))
      } else {
        pageIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  const clearSelection = () => setSelectedIds(new Set())

  const handleBatchDelete = async () => {
    const docs = documents.filter(d => selectedIds.has(d.id)).map(d => ({ id: d.id, name: d.name }))
    setBatchDeleteTarget(null)
    setBatchDeleting(true)
    try {
      const result = await knowledgeService.batchDelete(docs)
      const unknown = result.unknown || []
      if (result.failed.length > 0 || unknown.length > 0) {
        const failedText = result.failed.length
          ? `${result.failed.length} 篇失败：${result.failed.map((f: { name: string; error: string }) => `${f.name}(${f.error})`).join('；')}`
          : ''
        const unknownText = unknown.length
          ? `${unknown.length} 篇状态未确认：${unknown.map((f: { name: string; error: string }) => `${f.name}(${f.error})`).join('；')}`
          : ''
        const parts = [failedText, unknownText].filter(Boolean).join('；')
        if (unknown.length > 0 && result.failed.length === 0) {
          toast.warning(`${result.ok} 篇已删除；${parts}。列表已刷新，请核对后再决定是否重试`)
        } else {
          toast.error(`${result.ok} 篇已删除；${parts}`)
        }
      } else if (result.warnings.length > 0) {
        const wText = result.warnings.map((w: { name: string; warnings: string[] }) => `${w.name}: ${w.warnings.join(', ')}`).join('；')
        toast.warning(`已删除 ${result.ok} 篇（部分清理异常：${wText}）`)
      } else {
        toast.success(`已删除 ${result.ok} 篇文档`)
      }
      clearSelection()
    } finally {
      refreshAfterDocumentMutation()
      setBatchDeleting(false)
    }
  }

  const handleBatchReindex = async () => {
    const ids = Array.from(selectedIds)
    setBatchReindexing(true)
    try {
      // 任务化后批量 = 逐个提交进队列（执行/互斥在 worker 侧），逐条进度
      // 不在此跟踪，结果以操作记录与列表刷新为准
      const res = await knowledgeService.batchReindex(ids)
      const failedCount = res?.failed?.length ?? 0
      if (failedCount > 0) {
        toast.error(`${failedCount} 个文档提交重索引失败${res?.ok ? `，${res.ok} 个已提交` : ''}`)
      } else {
        toast.success(`已提交 ${res?.ok ?? ids.length} 个重索引任务，完成后可在操作记录查看`)
      }
      clearSelection()
      refreshAfterDocumentMutation()
    } finally {
      setBatchReindexing(false)
    }
  }

  const handleStopPropagation = (e: React.MouseEvent, fn: () => void) => {
    e.stopPropagation()
    fn()
  }

  const closeChunkDetails = () => {
    chunkRequestRef.current += 1
    setChunkDoc(null)
    setChunkItems([])
    setChunkError('')
    setChunkLoading(false)
  }

  const openChunkDetails = async (doc: { id: string; name: string }) => {
    const requestId = chunkRequestRef.current + 1
    chunkRequestRef.current = requestId
    setChunkDoc(doc)
    setChunkItems([])
    setChunkError('')
    setChunkLoading(true)
    try {
      const result = await knowledgeService.getChunkDetail(doc.id)
      if (requestId !== chunkRequestRef.current) return
      setChunkItems(result.chunks)
    } catch (error) {
      if (requestId !== chunkRequestRef.current) return
      setChunkError(error instanceof Error ? error.message : '读取分块失败')
    } finally {
      if (requestId === chunkRequestRef.current) setChunkLoading(false)
    }
  }

  // Trace 与分块正文分开查看；历史 Trace 可能过期，不能用它代替分块详情。
  const openProcessingTrace = (d: { id: string; name: string; hash?: string; last_trace_id?: string | null; last_processing_run_id?: string | null; last_operation?: string | null; last_operation_at?: string | null }) => {
    const params = new URLSearchParams({
      documentName: d.name,
      docId: d.id,
      returnTab: 'documents',
      returnPage: String(page),
      ...(d.hash ? { fileHash: d.hash } : {}),
      ...(d.last_processing_run_id ? { runId: d.last_processing_run_id } : {}),
      ...(d.last_operation ? { operation: d.last_operation } : {}),
      ...(d.last_operation_at ? { operationAt: d.last_operation_at } : {}),
    });
    if (d.last_trace_id) {
      router.push(`/knowledge/operations/traces/${encodeURIComponent(d.last_trace_id)}?${params.toString()}`)
    } else if (d.last_processing_run_id) {
      params.set("view", "processing-run");
      router.push(`/knowledge/operations/traces/processing-${encodeURIComponent(d.last_processing_run_id)}?${params.toString()}`)
    } else {
      // 没有文件行直连的 run ID 时，详情页会按当前文件 SHA 查找历史运行，
      // 只打开内容哈希一致且保存了阶段明细的记录。
      params.set("view", "processing-run");
      router.push(`/knowledge/operations/traces/processing-history?${params.toString()}`)
    }
  }

  const totalPages = Math.ceil(total / pageSize)

  return (
    <>
      <div className="w-full max-w-none px-3 py-5 sm:px-5 lg:px-8 lg:py-7">

        <section aria-labelledby="llm-fallback-stats-title" className="mb-5 rounded-lg border border-border-subtle bg-surface-base px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="llm-fallback-stats-title" className="text-xs font-semibold text-text-primary">规则切分后的大模型兜底记录</h2>
              <p className="mt-1 text-[10px] leading-4 text-text-muted">
                最近 {llmFallbackStats?.window_days ?? 30} 天，按处理运行次数统计当前账号可见知识库；同一文件重复处理会分别计数。测试库单独标记，不触发生产规则升级提示；内部库和对客库同类调用达到 {llmFallbackStats?.review_threshold ?? 5} 次时提示人工评估，不自动改规则。
              </p>
            </div>
            <button onClick={() => void refreshLlmFallbackStats()} disabled={llmFallbackStatsLoading} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[10px] text-text-secondary hover:bg-surface-hover hover:text-text-primary disabled:opacity-50" aria-label="刷新大模型切分兜底统计">
              <RefreshCw size={12} className={llmFallbackStatsLoading ? 'animate-spin' : ''} />
              刷新
            </button>
          </div>
          {llmFallbackStatsError ? (
            <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-[10px] text-red-700">{llmFallbackStatsError}</p>
          ) : (
            <>
              <dl className="mt-3 grid grid-cols-2 gap-y-2 border-t border-border-subtle pt-3 text-[10px] sm:grid-cols-5">
                <div><dt className="text-text-muted">触发记录</dt><dd className="mt-0.5 font-semibold text-text-primary">{llmFallbackStats?.candidate_count ?? (llmFallbackStatsLoading ? '读取中' : 0)}</dd></div>
                <div><dt className="text-text-muted">大模型调用</dt><dd className="mt-0.5 font-semibold text-text-primary">{llmFallbackStats?.llm_call_count ?? (llmFallbackStatsLoading ? '读取中' : 0)}</dd></div>
                <div><dt className="text-text-muted">边界采用</dt><dd className="mt-0.5 font-semibold text-text-primary">{llmFallbackStats?.applied_count ?? (llmFallbackStatsLoading ? '读取中' : 0)} 次</dd></div>
                <div><dt className="text-text-muted">未采用</dt><dd className="mt-0.5 font-semibold text-text-primary">{llmFallbackStats?.not_applied_count ?? (llmFallbackStatsLoading ? '读取中' : 0)} 次</dd></div>
                <div><dt className="text-text-muted">输出块</dt><dd className="mt-0.5 font-semibold text-text-primary">{llmFallbackStats?.output_chunk_count ?? (llmFallbackStatsLoading ? '读取中' : 0)}</dd></div>
              </dl>
              {!!llmFallbackStats?.by_type?.length ? (
                <div className="mt-3 overflow-x-auto border-t border-border-subtle pt-2">
                  <table className="w-full min-w-[840px] text-left text-[10px]">
                    <thead className="text-text-muted"><tr><th className="py-1.5 font-medium">知识库受众</th><th className="py-1.5 font-medium">文件格式</th><th className="py-1.5 font-medium">触发类型</th><th className="py-1.5 font-medium">规则 / 解析版本</th><th className="py-1.5 font-medium">调用 / 采用</th><th className="py-1.5 font-medium">模型服务失败 / 边界拒绝 / 输出块</th><th className="py-1.5 font-medium">规则复核</th></tr></thead>
                    <tbody className="divide-y divide-border-subtle">
                      {llmFallbackStats.by_type.map((item) => (
                        <tr key={`${item.kb_audience}:${item.source_format}:${item.fallback_type}:${item.rule_strategy}:${item.rule_version}:${item.parser_version}`} className="text-text-secondary">
                          <td className="py-1.5">{KNOWLEDGE_BASE_AUDIENCE_CN[item.kb_audience] || '未分类用途'}</td>
                          <td className="py-1.5">{item.source_format.toUpperCase()}</td>
                          <td className="py-1.5">{getFallbackTypeLabel(item.fallback_type)}</td>
                          <td className="py-1.5">{getChunkStrategyLabel(item.rule_strategy)}（{item.rule_version}）/ 解析器（{item.parser_version}）</td>
                          <td className="py-1.5">{item.llm_call_count} / {item.applied_count}</td>
                          <td className="py-1.5">{item.provider_failure_count} / {item.boundary_rejection_count} / {item.output_chunk_count}</td>
                          <td className="py-1.5">{item.upgrade_review_required ? <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">建议评估升级</span> : item.kb_audience === 'test' ? '测试观察（不触发升级）' : item.kb_audience === 'unknown' ? '未分类，先核对库身份' : '继续观察'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="mt-3 border-t border-border-subtle pt-2 text-[10px] text-text-muted">{llmFallbackStatsLoading ? '正在读取历史兜底记录…' : '当前时间范围暂无触发记录；后续每次实际兜底都会写入处理记录并自动累计。'}</p>
              )}
            </>
          )}
        </section>

        {/* Toolbar */}
        <div className="mb-4 flex flex-wrap items-center gap-2 sm:gap-3">
          <div className="flex min-w-[220px] flex-[1_1_320px] items-center gap-2 bg-surface-base rounded-lg border border-border-subtle px-3 py-2">
            <Search size={14} className="text-text-muted" />
            <input
              placeholder="搜索文档..."
              className="bg-transparent outline-none text-xs text-text-primary flex-1"
              value={inputValue}
              onChange={e => handleSearch(e.target.value)}
            />
            {inputValue && (
              <button onClick={handleClear} className="text-text-muted hover:text-text-primary text-[10px]">✕ 清除</button>
            )}
          </div>
          {/* 状态过滤 */}
          <select
            value={status}
            onChange={e => setStatus(e.target.value)}
            className="min-w-[132px] flex-1 px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors sm:flex-none"
          >
            <option value="">全部状态</option>
            <option value="active">活跃</option>
            <option value="uploading">上传中</option>
            <option value="parsing">解析中</option>
            <option value="embedding">向量化</option>
            <option value="failed">失败</option>
            <option value="deleted">已删除</option>
          </select>
          {/* 类型过滤 */}
          <select
            value={type}
            onChange={e => setType(e.target.value)}
            className="min-w-[132px] flex-1 px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors sm:flex-none"
          >
            <option value="">全部类型</option>
            <option value="pdf">PDF</option>
            <option value="md">Markdown</option>
            <option value="txt">TXT</option>
            <option value="docx">DOCX</option>
          </select>
          {/* KB 过滤（G2：真实注册库清单） */}
          <select value={kbId} onChange={e => setKbId(e.target.value)}
            className="min-w-[160px] flex-1 px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors sm:flex-none">
            <option value="">全部知识库</option>
            {kbOptions.map(k => (
              <option key={k.id} value={k.id}>{getKnowledgeBaseLabel(k.id, kbOptions)}{typeof k.doc_count === 'number' ? `（${k.doc_count}）` : ''}</option>
            ))}
          </select>
          {/* 部门过滤 */}
          <select value={dept} onChange={e => setDept(e.target.value)}
            className="min-w-[132px] flex-1 px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors sm:flex-none">
            <option value="">全部部门</option>
            <option value="warehouse">仓储部</option>
            <option value="supply_chain">供应链部</option>
            <option value="order_dept">订单部</option>
            <option value="customer">客服部</option>
            <option value="product_dept">商品部</option>
            <option value="hr">人事部</option>
            <option value="finance">财务部</option>
            <option value="admin">行政部</option>
            <option value="general">通用</option>
          </select>
          <button onClick={() => setUploadOpen(true)} className="min-w-[132px] flex-1 px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors sm:flex-none">+ 上传文档</button>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mb-4 text-[11px] text-text-muted">
          <label className="flex items-center gap-2">
            <span>质量检查</span>
            <select value={qualityStatus} onChange={e => setQualityStatus(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40">
              <option value="">全部质量状态</option>
              <option value="unknown">未核验</option>
              <option value="soft_warning">需复核</option>
              <option value="failed">质量失败</option>
              <option value="pass">质量通过</option>
            </select>
          </label>
          <span>当前可见 {knowledgeStats.doc_count ?? 0} 篇：通过 {qualityCounts.pass ?? 0}，需复核 {qualityCounts.soft_warning ?? 0}，失败 {qualityCounts.failed ?? 0}，未核验 {qualityCounts.unknown ?? 0}</span>
          <span title="停用知识库中的资料不会混入当前活动文档列表，可在知识库管理中打开停用库查看。">停用库资料请到“知识库”页查看</span>
        </div>

        {/* 批量操作条（选中 >0 时显示） */}
        {selectedIds.size > 0 && (
          <div className="flex items-center gap-3 mb-3 px-3 py-2 bg-accent/5 border border-accent/20 rounded-lg text-xs">
            <span className="text-text-primary font-medium">已选 {selectedIds.size} 项</span>
            <button onClick={() => setBatchDeleteTarget(selectedIds.size)} disabled={batchDeleting || batchReindexing}
              className="flex items-center gap-1 px-3 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-40 transition-colors">
              {batchDeleting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              批量删除
            </button>
            <button onClick={handleBatchReindex} disabled={batchDeleting || batchReindexing}
              className="flex items-center gap-1 px-3 py-1 rounded border border-border-subtle text-text-secondary hover:bg-surface-hover disabled:opacity-40 transition-colors">
              {batchReindexing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              批量重索引
            </button>
            <button onClick={clearSelection} className="ml-auto text-text-muted hover:text-text-primary flex items-center gap-1">
              <X size={12} /> 取消选择
            </button>
          </div>
        )}

        {/* Table */}
        {loading && <div className="bg-surface-base rounded-xl border border-border-subtle p-3 text-center text-xs text-text-muted animate-pulse mb-2">加载中...</div>}
        {error && <div className="bg-surface-base rounded-xl border border-red-200 p-3 text-center text-xs text-red-500 mb-2">{error}</div>}
        <div className="overflow-x-auto bg-surface-base rounded-xl border border-border-subtle">
          <table className="w-full min-w-[1080px] text-xs">
            <thead><tr className="border-b border-border-subtle bg-surface-elevated text-text-muted">
              <th className="text-left px-4 py-2.5 w-8">
                {documents.length > 0 && (
                  <button onClick={toggleSelectAll} className="text-text-muted hover:text-accent">
                    {documents.every(d => selectedIds.has(d.id)) ? <CheckSquare size={14} /> : <Square size={14} />}
                  </button>
                )}
              </th>
              {['文件名', '知识库', '部门', '业务域', '文档类型', '状态', '更新时间', '操作'].map(h => <th key={h} className="text-left px-4 py-2.5 font-medium">{h}</th>)}
            </tr></thead>
            <tbody>
              {documents.map(d => (
                <tr key={d.id} className="border-b border-border-subtle hover:bg-surface-hover transition-colors">
                  <td className="px-4 py-2.5">
                    <button onClick={(e) => handleStopPropagation(e, () => toggleSelect(d.id))} className="text-text-muted hover:text-accent">
                      {selectedIds.has(d.id) ? <CheckSquare size={14} className="text-accent" /> : <Square size={14} />}
                    </button>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <FileText size={14} className="text-text-muted" />
                      <span className="text-text-primary hover:text-accent transition-colors truncate max-w-[240px]">{d.name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-text-muted text-[10px]">{getKnowledgeBaseLabel(d.kb_id, kbOptions)}</td>
                  <td className="px-4 py-2.5 text-text-muted text-[10px]">{getDepartmentLabel(d.department, kbOptions.flatMap(k => k.depts || []))}</td>
                  <td className="px-4 py-2.5 text-text-muted text-[10px]">{getBusinessDomainLabel(d.business_domain)}</td>
                  <td className="px-4 py-2.5 text-text-muted">{getDocumentTypeLabel(d.doc_type)}</td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={d.status} />
                    {(() => {
                      const quality = QUALITY_STATUS_CONFIG[d.quality_status || 'unknown'] || QUALITY_STATUS_CONFIG.unknown
                      const qualityIssues = String(d.quality_issues || '')
                        .split(/,\s+/)
                        .filter(Boolean)
                      const legacyReport = qualityIssues.some(issue => issue.includes('legacy_quality_record_incomplete'))
                      const contentIssues = qualityIssues.filter(issue => !issue.includes('legacy_quality_record_incomplete'))
                      const qualityKind = legacyReport
                        ? contentIssues.length ? '审计记录缺项 + 内容/解析提醒' : '审计记录缺项'
                        : contentIssues.length ? '内容/解析提醒' : ''
                      const contentIssueLabels = contentIssues
                        .map(getQualityIssueLabel)
                        .filter(Boolean)
                      const isFixture = d.fixture_set === 'rag_eval' || d.kb_id === 'rag_eval_kb'
                      const title = [
                        legacyReport
                          ? '历史质量记录缺少分块完整性、内容覆盖和清洗覆盖检查；当前需复核，旧分数仅供参考。'
                          : '',
                        contentIssueLabels.length
                          ? `内容/解析提醒：${contentIssueLabels.join('；')}`
                          : '',
                        !legacyReport && !contentIssueLabels.length
                          ? quality.title
                          : '',
                        isFixture ? '评测/测试语料：此处提醒多由语料特性（近重复、扫描页、图表页）触发，属预期。' : '',
                      ].filter(Boolean).join(' ')
                      const scoreText = d.quality_score
                        ? `${legacyReport ? '旧分' : ''}${Number(d.quality_score).toFixed(0)}${legacyReport ? '' : '分'}`
                        : ''
                      return (
                        <div className="mt-1">
                          <span
                            data-testid="quality-status"
                            title={title}
                            className={`inline-flex cursor-help items-center rounded px-1.5 py-0.5 text-[10px] ${quality.className}`}
                          >
                            {quality.label}
                            {qualityKind ? ` · ${qualityKind}` : ''}
                            {scoreText ? ` · ${scoreText}` : ''}
                            {isFixture ? ' · 属预期' : ''}
                          </span>
                          {d.quality_status === 'soft_warning' && (
                            <button
                              onClick={(e) => handleStopPropagation(e, () => { const params = new URLSearchParams(searchParams.toString()); params.set('tab', 'pending'); router.push(`/knowledge/workbench?${params.toString()}`) })}
                              className="ml-1 inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] text-amber-700 underline decoration-amber-300 underline-offset-2 hover:bg-amber-50"
                              aria-label={"去处理需复核：" + d.name}
                              title="查看待复核列表并处理（通过 / 拒绝）"
                            >
                              去处理
                            </button>
                          )}
                        </div>
                      )
                    })()}
                    {(d.processing_status || d.metadata_route || d.model_count) && (
                      <div className="mt-1 text-[10px] text-text-muted">
                        {d.metadata_route ? '处理流程：' + getProcessingRouteLabel(d.metadata_route) : '已处理'}
                        {typeof d.model_count === 'number' ? ` · ${d.model_count} 个模型` : ''}
                      </div>
                    )}
                    {typeof d.ocr_used === 'boolean' && (
                      <div className="mt-1 text-[10px] text-text-muted">
                        OCR：{d.ocr_used ? (d.ocr_model || '已执行') : '未触发'}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-text-muted text-[10px]">
                    {d.last_operation_at ? fmtDocTime(d.last_operation_at) + ' ' + getOperationLabel(d.last_operation) : '-'}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-1">
                      <button onClick={(e) => handleStopPropagation(e, () => { void openChunkDetails({ id: d.id, name: d.name }) })} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-accent transition-colors" title="查看分块" aria-label={"查看分块：" + d.name}><Grid3X3 size={13} /></button>
                      <button onClick={(e) => handleStopPropagation(e, () => openProcessingTrace(d))} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-accent transition-colors" title="查看 Trace / 运行记录" aria-label={"查看 Trace / 运行记录：" + d.name}><Activity size={13} /></button>
                      <button onClick={(e) => handleStopPropagation(e, () => openLineage({ id: d.id, name: d.name }))} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-accent transition-colors" title="查看模型血缘"><Cpu size={13} /></button>
                      <button onClick={(e) => handleStopPropagation(e, () => handleReindex(d.id, d.name))} disabled={reindexing.has(d.id)} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-accent transition-colors disabled:opacity-50" title="重新解析并重建索引">
                        {reindexing.has(d.id) ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                      </button>
                      <button onClick={(e) => handleStopPropagation(e, () => setDeleteTarget({ id: d.id, name: d.name }))} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-red-500 transition-colors" title="删除"><Trash2 size={13} /></button>
                    </div>
                  </td>
                </tr>
              ))}
              {!loading && documents.length === 0 && (
                <tr><td colSpan={9} className="px-4 py-8 text-center text-text-muted">暂无文档</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-3 text-xs text-text-muted">
            <span>共 {total} 篇文档</span>
            <div className="flex items-center gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="p-1 rounded hover:bg-surface-base disabled:opacity-30 transition-colors"><ChevronLeft size={14} /></button>
              <span>{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1 rounded hover:bg-surface-base disabled:opacity-30 transition-colors"><ChevronRight size={14} /></button>
            </div>
          </div>
        )}
      </div>
    <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} onSuccess={refreshAfterDocumentMutation} />

    {chunkDoc && (
      <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={closeChunkDetails}>
        <aside role="dialog" aria-modal="true" aria-label={"分块详情：" + chunkDoc.name} className="flex h-full w-full max-w-[1440px] flex-col overflow-hidden border-l border-border-subtle bg-surface-base shadow-xl sm:w-[94vw] xl:w-[82vw]" onClick={event => event.stopPropagation()}>
          <header className="flex items-center gap-3 border-b border-border-subtle px-5 py-4">
            <Grid3X3 size={16} className="shrink-0 text-accent" />
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-text-primary">分块详情</h3>
              <p className="truncate text-[11px] text-text-muted">{chunkDoc.name} · {chunkItems.length} 块</p>
            </div>
            <button onClick={closeChunkDetails} className="ml-auto rounded p-1 text-text-muted hover:bg-surface-hover hover:text-text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" aria-label="关闭分块详情"><X size={15} /></button>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-5">
            {chunkLoading && <div className="flex items-center gap-2 py-8 text-xs text-text-muted"><Loader2 size={14} className="animate-spin" />正在读取分块与来源信息…</div>}
            {!chunkLoading && chunkError && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">{chunkError}</div>}
            {!chunkLoading && !chunkError && chunkItems.length === 0 && <div className="py-8 text-center text-xs text-text-muted">该文档没有可查看的分块。</div>}
            {!chunkLoading && !chunkError && chunkItems.map((chunk, index) => {
              const metadata = chunk.metadata || {}
              const entries = Object.entries(metadata)
              const metadataReturned = Object.prototype.hasOwnProperty.call(chunk, 'metadata')
              const granularityLabel = getGranularityLabel(chunk.granularity)
              return (
                <section key={chunk.chunk_id || chunk.chunk_index || index} className="mb-4 overflow-hidden rounded-xl border border-border-subtle">
                  <div className="flex flex-wrap items-start gap-2 border-b border-border-subtle bg-surface-elevated px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <h4 className="text-xs font-semibold text-text-primary">{chunk.section_title || chunk.document_title || ('分块 ' + (index + 1))}</h4>
                      <p className="mt-1 break-all font-mono text-[10px] text-text-muted">{chunk.chunk_id || '无分块编号'}</p>
                    </div>
                    <span className="rounded bg-surface-base px-2 py-1 text-[10px] text-text-secondary">索引 {chunk.chunk_index}</span>
                    <span className="rounded bg-surface-base px-2 py-1 text-[10px] text-text-secondary">{granularityLabel}</span>
                    <span className="rounded bg-surface-base px-2 py-1 text-[10px] text-text-secondary">{chunk.char_count} 字</span>
                  </div>
                  <div className="space-y-3 p-4">
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[10px]">
                      <div><dt className="text-text-muted">知识库</dt><dd className="mt-0.5 break-all text-text-primary">{getKnowledgeBaseLabel(chunk.kb_id, kbOptions)}</dd></div>
                      <div><dt className="text-text-muted">文档类型</dt><dd className="mt-0.5 break-all text-text-primary">{getDocumentTypeLabel(chunk.doc_type)}</dd></div>
                      <div><dt className="text-text-muted">所属部门</dt><dd className="mt-0.5 break-all text-text-primary">{getDepartmentLabel(chunk.department, kbOptions.flatMap(k => k.depts || []))}</dd></div>
                      <div><dt className="text-text-muted">父块编号</dt><dd className="mt-0.5 break-all font-mono text-text-primary">{chunk.parent_chunk_id || '-'}</dd></div>
                      <div><dt className="text-text-muted">块类型</dt><dd className="mt-0.5 break-all text-text-primary">{getChunkTypeLabel(chunk.chunk_type)}</dd></div>
                      <div><dt className="text-text-muted">父块范围</dt><dd className="mt-0.5 break-all text-text-primary">{getParentScopeLabel(chunk.parent_scope)}</dd></div>
                      <div className="col-span-2"><dt className="text-text-muted">覆盖章节</dt><dd className="mt-0.5 break-all text-text-primary">{chunk.section_titles?.join('、') || chunk.section_title || '-'}</dd></div>
                    </dl>
                    <details open className="rounded-lg border border-border-subtle">
                      <summary className="cursor-pointer px-3 py-2 text-[11px] font-medium text-text-secondary">来源与切分元数据（{entries.length} 项）</summary>
                      {!metadataReturned ? <p className="border-t border-border-subtle px-3 py-2 text-[10px] text-amber-700">本次接口没有返回来源或切分信息；正文可查，元数据暂不能核对。</p> : entries.length > 0 ? (
                        <dl className="grid grid-cols-1 gap-2 border-t border-border-subtle p-3 sm:grid-cols-2">
                          {entries.map(([key, value]) => (
                            <div key={key} className="min-w-0 rounded bg-surface-elevated px-2.5 py-2">
                              <dt className="text-[10px] text-text-muted">{getChunkMetadataLabel(key)}</dt>
                              <dd className="mt-1 break-all whitespace-pre-wrap font-mono text-[10px] text-text-primary">{formatChunkMetadataValue(key, value)}</dd>
                            </div>
                          ))}
                        </dl>
                      ) : <p className="border-t border-border-subtle px-3 py-2 text-[10px] text-text-muted">没有保存可展示的来源或切分元数据；缺失字段不会按通过处理。</p>}
                    </details>
                    {(chunk.keywords || chunk.llm_keywords || chunk.llm_model) && (
                      <dl className="grid grid-cols-1 gap-2 text-[10px] sm:grid-cols-2">
                        <div className="rounded bg-surface-elevated px-2.5 py-2"><dt className="text-text-muted">关键词</dt><dd className="mt-1 break-all whitespace-pre-wrap text-text-primary">{chunk.keywords || '-'}</dd></div>
                        <div className="rounded bg-surface-elevated px-2.5 py-2"><dt className="text-text-muted">模型补充关键词</dt><dd className="mt-1 break-all whitespace-pre-wrap text-text-primary">{chunk.llm_keywords || '-'}</dd></div>
                        {chunk.llm_model && <div className="rounded bg-surface-elevated px-2.5 py-2"><dt className="text-text-muted">关键词模型</dt><dd className="mt-1 break-all text-text-primary">{chunk.llm_model}</dd></div>}
                      </dl>
                    )}
                    <div>
                      <h5 className="mb-1.5 text-[11px] font-medium text-text-secondary">分块正文</h5>
                      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-elevated p-3 font-sans text-[11px] leading-5 text-text-primary">{chunk.content || '（正文为空）'}</pre>
                    </div>
                    {!!chunk.simulated_questions?.length && (
                      <div>
                        <h5 className="mb-1.5 text-[11px] font-medium text-text-secondary">关联的模拟问题</h5>
                        <ul className="space-y-1 text-[10px] text-text-muted">{chunk.simulated_questions.map((question, questionIndex) => <li key={questionIndex}>· {question}</li>)}</ul>
                      </div>
                    )}
                  </div>
                </section>
              )
            })}
          </div>
        </aside>
      </div>
    )}

    {/* 上传/重索引实际模型血缘：以 processing_runs 为权威，不显示当前配置冒充历史模型 */}
    {lineageDoc && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={() => setLineageDoc(null)}>
        <div className="bg-surface-base rounded-2xl border border-border-subtle shadow-xl w-[760px] max-h-[85vh] overflow-hidden" onClick={e => e.stopPropagation()}>
          <div className="flex items-center gap-3 px-6 py-4 border-b border-border-subtle">
            <Cpu size={16} className="text-accent" />
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-text-primary">模型处理血缘</h3>
              <p className="text-[10px] text-text-muted truncate">{lineageDoc.name}</p>
            </div>
            <button onClick={() => setLineageDoc(null)} className="ml-auto p-1 text-text-muted hover:text-text-primary"><X size={15} /></button>
          </div>
          <div className="flex min-h-[360px] max-h-[calc(85vh-64px)]">
            <div className="w-[210px] border-r border-border-subtle overflow-y-auto p-3">
              <div className="text-[10px] text-text-muted px-2 pb-2">处理运行（最新在前）</div>
              {lineageRuns.map(run => (
                <button key={run.run_id} onClick={() => selectLineageRun(run.run_id)} className={`w-full text-left rounded-lg px-3 py-2 mb-1 transition-colors ${lineageDetail?.run_id === run.run_id ? 'bg-accent/10 text-accent' : 'hover:bg-surface-hover text-text-secondary'}`}>
                  <div className="text-[11px] font-mono truncate">{run.run_id}</div>
                  <div className="text-[10px] mt-1 flex justify-between"><span>{getProcessingStatusLabel(run.status)}</span><span>{fmtDocTime(run.started_at)}</span></div>
                </button>
              ))}
              {!lineageLoading && lineageRuns.length === 0 && <div className="text-[10px] text-text-muted px-2 py-5">暂无运行记录</div>}
            </div>
            <div className="flex-1 overflow-y-auto p-5">
              {lineageLoading && <div className="text-xs text-text-muted animate-pulse">加载血缘中...</div>}
              {!lineageLoading && lineageDetail && (
                <>
                    <div className="grid grid-cols-2 gap-2 mb-4 text-[10px]">
                    <div className="rounded-lg bg-surface-elevated px-3 py-2"><span className="text-text-muted">处理流水线版本</span><div className="font-mono text-text-primary mt-1">{lineageDetail.pipeline_version || '-'}</div></div>
                    <div className="rounded-lg bg-surface-elevated px-3 py-2"><span className="text-text-muted">Trace / 任务 ID</span><div className="font-mono text-text-primary mt-1 truncate">{lineageDetail.trace_id || lineageDetail.task_id || '-'}</div></div>
                  </div>
                  {!!lineageDetail.model_summary?.length && <div className="mb-5"><div className="text-xs font-medium text-text-primary mb-2">本次实际使用模型</div><div className="flex flex-wrap gap-2">{lineageDetail.model_summary.map((model: any, index: number) => <span key={`${model.role || model.stage}-${index}`} className="rounded-full bg-accent/10 text-accent px-2.5 py-1 text-[10px]">{model.stage ? getProcessingStageLabel(model.stage) : getModelRoleLabel(model.role)}： {model.provider ? '供应商 ' + getProviderLabel(model.provider) + ' / ' : ''}{model.model_name || '-'}</span>)}</div></div>}
                  <div className="text-xs font-medium text-text-primary mb-2">阶段时间线</div>
                   <div className="space-y-2">{(lineageDetail.steps || []).map((step: any) => <div key={step.step_id} className="rounded-lg border border-border-subtle px-3 py-2"><div className="flex items-center gap-2"><span className="text-xs font-medium text-text-primary">{getProcessingStageLabel(step.stage)}</span><span className="text-[10px] text-text-muted">{getModelRoleLabel(step.role || step.engine_type)}</span><span className="ml-auto text-[10px] text-text-muted">{getProcessingStatusLabel(step.status)}</span></div><div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-text-muted"><span>模型：<b className="font-mono text-text-secondary">{step.model_name ? `${step.provider ? '供应商 ' + getProviderLabel(step.provider) + ' / ' : ''}${step.model_name}` : getProcessingCodeLabel(step.skip_reason) + '（未调用模型）'}</b></span><span>输入/输出：{step.input_count ?? 0}/{step.output_count ?? 0}</span>{step.total_tokens ? <span>词元数：{step.total_tokens}</span> : null}{step.duration_ms ? <span>耗时：{Math.round(step.duration_ms)} 毫秒</span> : null}{step.cache_status && step.cache_status !== 'miss' ? <span>缓存：{getCacheStatusLabel(step.cache_status)}</span> : null}{step.prompt_version ? <span>提示词版本：{step.prompt_version}</span> : null}{step.rules_version ? <span>规则版本：{step.rules_version}</span> : null}{step.taxonomy_version ? <span>分类体系版本：{step.taxonomy_version}</span> : null}</div></div>)}</div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    )}

    {/* 删除确认对话框 */}
    {deleteTarget && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={() => !deletePending && setDeleteTarget(null)}>
        <div className="bg-surface-base rounded-2xl border border-border-subtle shadow-xl w-[400px] p-6" onClick={e => e.stopPropagation()}>
          <h3 className="text-sm font-semibold text-text-primary mb-2">确认删除</h3>
          <p className="text-xs text-text-muted mb-4">
            确定要删除 <span className="text-text-primary font-medium">"{deleteTarget.name}"</span> 吗？
            <br />该操作不可恢复，文档将从知识库中移除。
          </p>
          <div className="flex justify-end gap-2">
            <button onClick={() => setDeleteTarget(null)} disabled={deletePending} className="px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50">取消</button>
            <button onClick={handleDelete} disabled={deletePending} className="inline-flex items-center gap-1.5 px-4 py-2 text-xs rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-70">
              {deletePending && <Loader2 size={12} className="animate-spin" />}
              {deletePending ? '正在删除…' : '删除'}
            </button>
          </div>
        </div>
      </div>
    )}

    {/* 批量删除确认 */}
    {batchDeleteTarget !== null && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={() => setBatchDeleteTarget(null)}>
        <div className="bg-surface-base rounded-2xl border border-border-subtle shadow-xl w-[400px] p-6" onClick={e => e.stopPropagation()}>
          <h3 className="text-sm font-semibold text-text-primary mb-2">批量删除确认</h3>
          <p className="text-xs text-text-muted mb-4">
            确定要删除选中的 <span className="text-text-primary font-medium">{batchDeleteTarget}</span> 个文档吗？
            <br />该操作不可恢复，将同时删除原文件与向量数据。
          </p>
          <div className="flex justify-end gap-2">
            <button onClick={() => setBatchDeleteTarget(null)} className="px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors">取消</button>
            <button onClick={handleBatchDelete} className="px-4 py-2 text-xs rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors">删除 {batchDeleteTarget} 项</button>
          </div>
        </div>
      </div>
    )}
    </>
  )
}

function StatusBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.unknown
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${config.className}`}>{config.label}</span>
}

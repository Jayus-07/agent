'use client'

import { useState, useEffect, useMemo, Fragment } from 'react'
import Link from 'next/link'
import { ChevronLeft, ChevronRight, RefreshCw, Loader2, ChevronDown, ChevronUp } from 'lucide-react'
import { useToast } from '@/components/shared/Toast'
import { knowledgeService, OperationLog, type OperationType } from '@/services/knowledge'

/** 操作徽章配置 */
const OPERATION_CONFIG: Record<string, { label: string; className: string }> = {
  upload:  { label: '上传',   className: 'bg-green-50 text-green-600' },
  reindex: { label: '重索引', className: 'bg-blue-50 text-blue-600' },
  delete:  { label: '删除',   className: 'bg-red-50 text-red-600' },
  '':      { label: '未知',   className: 'bg-gray-50 text-gray-600' },
}

export default function OperationsPage() {
  const toast = useToast()
  const [items, setItems] = useState<OperationLog[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [operation, setOperation] = useState<OperationType | ''>('')
  const [expandedBatches, setExpandedBatches] = useState<Set<string>>(new Set())

  /** 将操作日志按批次折叠：
   *  - 同批次 ≥2 条 → 折叠为批次行
   *  - 单条（含 batch_id 但只有 1 条）→ 作为独立行
   */
  const groupedItems = useMemo(() => {
    const batchMap = new Map<string, OperationLog[]>()
    const singles: OperationLog[] = []

    for (const item of items) {
      if (item.batch_id) {
        if (!batchMap.has(item.batch_id)) batchMap.set(item.batch_id, [])
        batchMap.get(item.batch_id)!.push(item)
      } else {
        singles.push(item)
      }
    }

    // 拆分：同批次 ≥2 条才折叠，单条的合并回独立行
    const BATCH_LABELS: Record<string, string> = { upload: '批量上传', delete: '批量删除', reindex: '批量重索引' }
    const batches: { batch_id: string; logs: OperationLog[]; okCount: number; failCount: number; latestTime: string; firstFile: string; opLabel: string }[] = []
    Array.from(batchMap.entries()).forEach(([bid, logs]) => {
      if (logs.length >= 2) {
        const okCount = logs.filter((l: OperationLog) => l.result === 'success').length
        const failCount = logs.filter((l: OperationLog) => l.result === 'failed').length
        const latestTime = logs.reduce((max: string, l: OperationLog) => l.created_at > max ? l.created_at : max, '')
        const firstFile = logs[0]?.doc_name || ''
        batches.push({ batch_id: bid, logs, okCount, failCount, latestTime, firstFile, opLabel: BATCH_LABELS[logs[0]?.operation] || '批量操作' })
      } else {
        singles.push(...logs)
      }
    })
    // 批次和单条混在一起按时间倒序
    const all: ({ kind: "batch"; batch_id: string; logs: OperationLog[]; okCount: number; failCount: number; latestTime: string; firstFile: string; opLabel: string } | { kind: "single"; log: OperationLog })[] = [
      ...batches.map(b => ({ kind: "batch" as const, ...b })),
      ...singles.map(log => ({ kind: "single" as const, log })),
    ]
    all.sort((a, b) => {
      const ta = a.kind === "batch" ? a.latestTime : a.log.created_at
      const tb = b.kind === "batch" ? b.latestTime : b.log.created_at
      return tb.localeCompare(ta)
    })

    return { all }
  }, [items])

  const toggleBatch = (batchId: string) => {
    const next = new Set(expandedBatches)
    next.has(batchId) ? next.delete(batchId) : next.add(batchId)
    setExpandedBatches(next)
  }

  const fetchOps = async () => {
    setLoading(true)
    try {
      const res = await knowledgeService.getOperations({ page, page_size: pageSize, operation: operation || undefined })
      setItems(res.items)
      setTotal(res.total)
    } catch (e) {
      toast.error(`加载失败: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchOps()
  }, [page, operation])

  const totalPages = Math.ceil(total / pageSize)

  /** 格式化耗时 */
  const formatDuration = (ms: number) => {
    if (!ms || ms <= 0) return '-'
    if (ms < 1000) return `${ms}ms`
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
    return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
  }

  /** 格式化来源：提取可读信息 */
  const formatSource = (source: string) => {
    if (!source) return '-'
    // "127.0.0.1 | curl/8.19.0" → 取 IP + 客户端
    const parts = source.split(' | ')
    if (parts.length >= 2) {
      const ip = parts[0]
      const ua = parts.slice(1).join(' | ')
      // 常见 UA 简化
      const client = ua.includes('curl') ? 'API' : ua.includes('Mozilla') ? 'Web' : ua.slice(0, 20)
      return `${ip} · ${client}`
    }
    return source.slice(0, 30)
  }

  /** 解析 detail 字段为对象（兼容 JSON 字符串 / 已解析对象） */
  const parseDetail = (detail: OperationLog['detail']): Record<string, unknown> | null => {
    if (!detail) return null
    if (typeof detail === 'string') {
      try { return JSON.parse(detail) } catch { return null }
    }
    return detail as Record<string, unknown>
  }

  const formatDetail = (detail: OperationLog['detail']) => {
    const obj = parseDetail(detail)
    if (!obj) return null
    if (obj.error) return { text: `错误: ${obj.error}`, type: 'error' as const }
    if (obj.duplicate) return { text: '已存在，跳过索引', type: 'duplicate' as const }
    // 删除操作：只含 file_path，提取文件名展示
    if (obj.file_path && Object.keys(obj).length <= 2) {
      const fname = (obj.file_path as string).split(/[\\/]/).pop() || ''
      return { text: `已删除: ${fname}`, type: 'normal' as const, doc_type: '', llm_used: false, confidence: undefined }
    }
    const parts: string[] = []
    if (obj.chunk_count !== undefined) parts.push(`Chunks: ${obj.chunk_count}`)
    return {
      text: parts.join(' · ') || JSON.stringify(obj).slice(0, 100),
      type: 'normal' as const,
      doc_type: (obj.doc_type as string) || '',
      llm_used: Boolean(obj.llm_used),
      confidence: obj.confidence as number | undefined,
    }
  }

  /** 渲染详情列：doc_type badge + chunk 数 + LLM 标识 */
  const renderDetailCell = (detail: OperationLog['detail']) => {
    const info = formatDetail(detail)
    if (!info) return <span className="text-text-muted">-</span>
    if (info.type === 'error') return <span className="text-red-500 truncate" title={info.text}>{info.text}</span>
    if (info.type === 'duplicate') return <span className="text-amber-500 text-[10px]">{info.text}</span>
    return (
      <div className="flex items-center gap-1.5 flex-wrap">
        {info.doc_type && info.doc_type !== 'general' && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-50 text-blue-600 shrink-0">
            {info.doc_type}
          </span>
        )}
        {info.llm_used && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-violet-50 text-violet-600 shrink-0" title="已调用 LLM 提取关键词">
            🔮LLM
          </span>
        )}
        <span className="text-text-muted text-[11px]">{info.text}</span>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">文档操作中心</h1>
            <p className="text-xs text-text-muted mt-1">记录文档上传、重索引、删除操作及关联链路追踪</p>
          </div>
          <button onClick={fetchOps} disabled={loading} className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-secondary hover:bg-surface-hover disabled:opacity-40 transition-colors">
            {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            刷新
          </button>
        </div>

        {/* 过滤器 */}
        <div className="flex items-center gap-3 mb-4">
          <select
            value={operation}
            onChange={e => setOperation(e.target.value as OperationType | '')}
            className="px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors"
          >
            <option value="">全部操作</option>
            <option value="upload">上传</option>
            <option value="reindex">重索引</option>
            <option value="delete">删除</option>
          </select>
        </div>

        {/* 表格 */}
        {loading && <div className="bg-surface-base rounded-xl border border-border-subtle p-3 text-center text-xs text-text-muted animate-pulse mb-2">加载中...</div>}
        <div className="bg-surface-base rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-border-subtle bg-surface-elevated text-text-muted">
              {['时间', '文档名', '操作', '操作者', '来源', '结果', '耗时', '详情', 'Trace ID', '链路'].map(h => <th key={h} className="text-left px-3 py-2.5 font-medium text-[10px]">{h}</th>)}
            </tr></thead>
            <tbody>
              {groupedItems.all.map(item => {
                if (item.kind === "batch") {
                  const batch = item
                  const batchTotalMs = batch.logs.reduce((sum, l) => sum + (l.duration_ms || 0), 0)
                  return (
                    <Fragment key={batch.batch_id}>
                      <tr className="border-b border-border-subtle bg-surface-elevated/50 hover:bg-surface-hover transition-colors cursor-pointer"
                        onClick={() => toggleBatch(batch.batch_id)}>
                        <td className="px-3 py-2.5 text-text-muted text-[10px]">{new Date((batch.latestTime + '').replace(' ', 'T') + 'Z').toLocaleString('zh-CN', { hour12: false })}</td>
                        <td className="px-3 py-2.5 text-text-primary flex items-center gap-1.5">
                          {expandedBatches.has(batch.batch_id) ? <ChevronUp size={12} className="text-text-muted" /> : <ChevronDown size={12} className="text-text-muted" />}
                          <span className="font-medium truncate max-w-[180px]">{batch.opLabel} · {batch.firstFile} 等 {batch.logs.length} 个文件</span>
                        </td>
                        <td className="px-3 py-2.5"><span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-purple-50 text-purple-600">批次</span></td>
                        <td className="px-3 py-2.5 text-text-muted text-[10px]">{batch.logs[0]?.user_id || '-'}</td>
                        <td className="px-3 py-2.5 text-text-muted text-[10px]">{formatSource(batch.logs[0]?.source || '')}</td>
                        <td className="px-3 py-2.5">
                          <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${batch.failCount > 0 ? 'bg-amber-50 text-amber-600' : 'bg-green-50 text-green-600'}`}>
                            ✓ {batch.okCount}{batch.failCount > 0 ? ` ✗ ${batch.failCount}` : ''}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-text-muted text-[10px] tabular-nums">{formatDuration(batchTotalMs)}</td>
                        <td className="px-3 py-2.5 text-text-muted text-[10px]">{batch.logs.length} 条记录</td>
                        <td className="px-3 py-2.5 text-text-muted text-[10px]">-</td>
                        <td className="px-3 py-2.5 text-text-muted text-[10px]">点击展开</td>
                      </tr>
                      {expandedBatches.has(batch.batch_id) && batch.logs.map(op => (
                        <tr key={op.id} className="border-b border-border-subtle bg-violet-50/30 hover:bg-surface-hover transition-colors">
                          <td className="px-3 py-2.5 text-text-muted text-[10px] pl-6">{new Date((op.created_at + '').replace(' ', 'T') + 'Z').toLocaleString('zh-CN', { hour12: false })}</td>
                          <td className="px-3 py-2.5 text-text-primary truncate max-w-[180px] pl-6" title={op.doc_name}>└ {op.doc_name}</td>
                          <td className="px-3 py-2.5">
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${OPERATION_CONFIG[op.operation].className}`}>
                              {OPERATION_CONFIG[op.operation].label}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-text-muted text-[10px]">{op.user_id}</td>
                          <td className="px-3 py-2.5 text-text-muted text-[10px]">{formatSource(op.source)}</td>
                          <td className="px-3 py-2.5">
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${op.result === 'success' ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'}`}>
                              {op.result === 'success' ? '成功' : '失败'}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-text-muted text-[10px] tabular-nums">{formatDuration(op.duration_ms)}</td>
                          <td className="px-3 py-2.5 max-w-[160px]">{renderDetailCell(op.detail)}</td>
                          <td className="px-3 py-2.5 text-[10px] font-mono text-text-muted">{op.trace_id ? op.trace_id.slice(0, 12) : '-'}</td>
                          <td className="px-3 py-2.5">
                            {op.trace_id ? (
                              <Link href={`/knowledge/operations/traces/${op.trace_id}`} className="text-accent hover:text-accent-hover hover:underline text-[10px]">
                                查看Trace
                              </Link>
                            ) : parseDetail(op.detail)?.duplicate ? (
                              <span className="text-text-muted text-[10px]">已跳过</span>
                            ) : (
                              <span className="text-text-muted text-[10px]">-</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  )
                } else {
                  const op = item.log
                  return (
                    <tr key={op.id} className="border-b border-border-subtle hover:bg-surface-hover transition-colors">
                      <td className="px-3 py-2.5 text-text-muted text-[10px]">{new Date((op.created_at + '').replace(' ', 'T') + 'Z').toLocaleString('zh-CN', { hour12: false })}</td>
                      <td className="px-3 py-2.5 text-text-primary truncate max-w-[160px]" title={op.doc_name}>{op.doc_name}</td>
                      <td className="px-3 py-2.5">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${OPERATION_CONFIG[op.operation].className}`}>
                          {OPERATION_CONFIG[op.operation].label}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-text-muted text-[10px]">{op.user_id}</td>
                      <td className="px-3 py-2.5 text-text-muted text-[10px]">{formatSource(op.source)}</td>
                      <td className="px-3 py-2.5">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${op.result === 'success' ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'}`}>
                          {op.result === 'success' ? '成功' : '失败'}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-text-muted text-[10px] tabular-nums">{formatDuration(op.duration_ms)}</td>
                      <td className="px-3 py-2.5 max-w-[160px]">{renderDetailCell(op.detail)}</td>
                      <td className="px-3 py-2.5 text-[10px] font-mono text-text-muted">{op.trace_id ? op.trace_id.slice(0, 12) : '-'}</td>
                      <td className="px-3 py-2.5">
                        {op.trace_id ? (
                          <Link href={`/knowledge/operations/traces/${op.trace_id}`} className="text-accent hover:text-accent-hover hover:underline text-[10px]">
                            查看Trace
                          </Link>
                        ) : (
                          <span className="text-text-muted text-[10px]">-</span>
                        )}
                      </td>
                    </tr>
                  )
                }
              })}
              {!loading && groupedItems.all.length === 0 && (
                <tr><td colSpan={10} className="px-4 py-8 text-center text-text-muted">暂无操作记录</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* 分页 */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-3 text-xs text-text-muted">
            <span>共 {total} 条记录</span>
            <div className="flex items-center gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="p-1 rounded hover:bg-surface-base disabled:opacity-30 transition-colors"><ChevronLeft size={14} /></button>
              <span>{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1 rounded hover:bg-surface-base disabled:opacity-30 transition-colors"><ChevronRight size={14} /></button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { ChevronLeft, ChevronRight, RefreshCw, Loader2 } from 'lucide-react'
import { useToast } from '@/components/shared/Toast'
import { knowledgeService, OperationLog, OperationType } from '@/services/knowledge'

/** 操作徽章配置 */
const OPERATION_CONFIG: Record<OperationType, { label: string; className: string }> = {
  upload:  { label: '上传',   className: 'bg-green-50 text-green-600' },
  reindex: { label: '重索引', className: 'bg-blue-50 text-blue-600' },
  delete:  { label: '删除',   className: 'bg-red-50 text-red-600' },
}

export default function OperationsPage() {
  const toast = useToast()
  const [items, setItems] = useState<OperationLog[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [operation, setOperation] = useState<OperationType | ''>('')

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

  const formatDetail = (detail: OperationLog['detail']) => {
    if (!detail) return '-'
    if (typeof detail === 'string') {
      try {
        const obj = JSON.parse(detail)
        if (obj.error) return `错误: ${obj.error}`
        if (obj.chunk_count !== undefined) return `Chunks: ${obj.chunk_count}`
        return detail.slice(0, 100)
      } catch {
        return detail.slice(0, 100)
      }
    }
    if (detail.error) return `错误: ${detail.error}`
    if (detail.chunk_count !== undefined) return `Chunks: ${detail.chunk_count}`
    return JSON.stringify(detail).slice(0, 100)
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
              {['时间', '文档名', '操作', '操作人', '结果', '详情', '链路'].map(h => <th key={h} className="text-left px-4 py-2.5 font-medium">{h}</th>)}
            </tr></thead>
            <tbody>
              {items.map(op => (
                <tr key={op.id} className="border-b border-border-subtle hover:bg-surface-hover transition-colors">
                  <td className="px-4 py-2.5 text-text-muted">{new Date(op.created_at).toLocaleString('zh-CN')}</td>
                  <td className="px-4 py-2.5 text-text-primary truncate max-w-[200px]" title={op.doc_name}>{op.doc_name}</td>
                  <td className="px-4 py-2.5">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${OPERATION_CONFIG[op.operation].className}`}>
                      {OPERATION_CONFIG[op.operation].label}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-text-muted">{op.user_id}</td>
                  <td className="px-4 py-2.5">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${op.result === 'success' ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'}`}>
                      {op.result === 'success' ? '成功' : '失败'}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-text-muted truncate max-w-[150px]" title={formatDetail(op.detail)}>{formatDetail(op.detail)}</td>
                  <td className="px-4 py-2.5">
                    {op.trace_id ? (
                      <Link href={`/observability/traces/${op.trace_id}`} className="text-accent hover:text-accent-hover hover:underline text-[10px]">
                        查看链路
                      </Link>
                    ) : (
                      <span className="text-text-muted text-[10px]">无链路</span>
                    )}
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-text-muted">暂无操作记录</td></tr>
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
'use client'

import { useState, useRef, useEffect } from 'react'
import { FileText, Search, Trash2, RefreshCw, Grid3X3, Loader2, ChevronLeft, ChevronRight, CheckSquare, Square, X } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useDocuments } from '@/hooks/useKnowledge'
import { knowledgeService } from '@/services/knowledge'
import UploadDialog from '@/components/knowledge/UploadDialog'
import { useToast } from '@/components/shared/Toast'

// 中文标签映射
const KB_LABELS: Record<string, string> = {
  biz_inventory: '库存业务', biz_order: '订单业务', biz_product: '商品业务',
  policy_hr: '人事制度', policy_finance: '财务制度', policy_general: '企业公共',
}
const DEPT_LABELS: Record<string, string> = {
  warehouse: '仓储部', supply_chain: '供应链部', order_dept: '订单部', customer: '客服部',
  product_dept: '商品部', hr: '人事部', finance: '财务部', admin: '行政部', general: '通用',
}
const DOC_TYPE_CN: Record<string, string> = {
  compliance: '合规', policy: '制度', legal: '法律', financial: '财务',
  faq: 'FAQ', product_spec: '商品规格', sop: '操作流程', listing: '上架', general: '通用',
}
const DOMAIN_CN: Record<string, string> = {
  product: '商品', order: '订单', inventory: '库存', logistics: '物流',
  customer: '客户', supplier: '供应商', marketing: '营销', advertising: '广告',
  analytics: '数据分析', data: '数据治理', general: '通用',
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
}

export default function DocumentsPage() {
  const router = useRouter()
  const toast = useToast()
  const { documents, loading, total, page, pageSize, keyword, setKeyword, status, setStatus, type, setType, kbId, setKbId, dept, setDept, setPage, error, refresh } = useDocuments()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [reindexing, setReindexing] = useState<Set<string>>(new Set())
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null)
  // 批量操作
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [batchReindexing, setBatchReindexing] = useState(false)
  const [batchDeleting, setBatchDeleting] = useState(false)
  const [batchDeleteTarget, setBatchDeleteTarget] = useState<number | null>(null)
  // 受控搜索：本地 input 状态（立即响应）+ 防抖同步到 store（API 调用）
  const [inputValue, setInputValue] = useState(keyword)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

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
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [])

  // 翻页/搜索/过滤变化时清空选择（选中集只对当前页有意义）
  useEffect(() => {
    clearSelection()
  }, [page, keyword, status, type, kbId, dept])

  const handleDelete = async () => {
    if (!deleteTarget) return
    const { id } = deleteTarget
    setDeleteTarget(null)
    await knowledgeService.deleteDocument(id)
    refresh()
  }

  const handleReindex = async (id: string) => {
    setReindexing(prev => new Set(prev).add(id))
    try {
      await knowledgeService.reindexDocument(id)
      refresh()
    } finally {
      setReindexing(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
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
      if (result.failed.length > 0) {
        toast.error(`${result.ok} 篇已删除，${result.failed.length} 篇失败：${result.failed.map(f => `${f.name}(${f.error})`).join('；')}`)
      } else if (result.warnings.length > 0) {
        const wText = result.warnings.map(w => `${w.name}: ${w.warnings.join(', ')}`).join('；')
        toast.warning(`已删除 ${result.ok} 篇（部分清理异常：${wText}）`)
      } else {
        toast.success(`已删除 ${result.ok} 篇文档`)
      }
      clearSelection()
      refresh()
    } finally {
      setBatchDeleting(false)
    }
  }

  const handleBatchReindex = async () => {
    const ids = Array.from(selectedIds)
    setBatchReindexing(true)
    try {
      await knowledgeService.batchReindex(ids)
      clearSelection()
      refresh()
    } finally {
      setBatchReindexing(false)
    }
  }

  const handleStopPropagation = (e: React.MouseEvent, fn: () => void) => {
    e.stopPropagation()
    fn()
  }

  const totalPages = Math.ceil(total / pageSize)

  return (
    <>
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">文档管理</h1>
          <p className="text-xs text-text-muted mt-1">管理 RAG 知识库文档，支持 PDF、TXT、Markdown、DOCX</p>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center gap-2 flex-1 bg-surface-base rounded-lg border border-border-subtle px-3 py-2">
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
            className="px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors"
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
            className="px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors"
          >
            <option value="">全部类型</option>
            <option value="pdf">PDF</option>
            <option value="md">Markdown</option>
            <option value="txt">TXT</option>
            <option value="docx">DOCX</option>
          </select>
          {/* KB 过滤 */}
          <select value={kbId} onChange={e => setKbId(e.target.value)}
            className="px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors">
            <option value="">全部知识库</option>
            <option value="biz_inventory">库存业务</option>
            <option value="biz_order">订单业务</option>
            <option value="biz_product">商品业务</option>
            <option value="policy_hr">人事制度</option>
            <option value="policy_finance">财务制度</option>
            <option value="policy_general">企业公共</option>
          </select>
          {/* 部门过滤 */}
          <select value={dept} onChange={e => setDept(e.target.value)}
            className="px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors">
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
          <button onClick={() => setUploadOpen(true)} className="px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors">+ 上传文档</button>
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
        <div className="bg-surface-base rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full text-xs">
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
                <tr key={d.id} className="border-b border-border-subtle hover:bg-surface-hover transition-colors cursor-pointer"
                  onClick={() => router.push(d.last_trace_id ? `/knowledge/operations/traces/${d.last_trace_id}` : `/knowledge/documents/${d.id}`)}>
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
                  <td className="px-4 py-2.5 text-text-muted text-[10px]">{KB_LABELS[d.kb_id] || (d.kb_id !== 'default' ? d.kb_id : '-')}</td>
                  <td className="px-4 py-2.5 text-text-muted text-[10px]">{DEPT_LABELS[d.department || ''] || d.department || '-'}</td>
                  <td className="px-4 py-2.5 text-text-muted text-[10px]">{DOMAIN_CN[d.business_domain || ''] || d.business_domain || '-'}</td>
                  <td className="px-4 py-2.5 text-text-muted">{DOC_TYPE_CN[d.doc_type || ''] || d.doc_type || '-'}</td>
                  <td className="px-4 py-2.5"><StatusBadge status={d.status} /></td>
                  <td className="px-4 py-2.5 text-text-muted text-[10px]">
                    {d.last_operation_at ? `${fmtDocTime(d.last_operation_at)} ${d.last_operation === 'upload' ? '上传' : d.last_operation === 'reindex' ? '重建' : d.last_operation === 'delete' ? '删除' : d.last_operation}` : '-'}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-1">
                      <button onClick={(e) => handleStopPropagation(e, () => router.push(`/knowledge/chunks?docId=${d.id}`))} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-accent transition-colors" title="查看Chunks"><Grid3X3 size={13} /></button>
                      <button onClick={(e) => handleStopPropagation(e, () => handleReindex(d.id))} disabled={reindexing.has(d.id)} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-accent transition-colors disabled:opacity-50" title="重新解析">
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
    </div>
    <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} onSuccess={refresh} />

    {/* 删除确认对话框 */}
    {deleteTarget && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={() => setDeleteTarget(null)}>
        <div className="bg-surface-base rounded-2xl border border-border-subtle shadow-xl w-[400px] p-6" onClick={e => e.stopPropagation()}>
          <h3 className="text-sm font-semibold text-text-primary mb-2">确认删除</h3>
          <p className="text-xs text-text-muted mb-4">
            确定要删除 <span className="text-text-primary font-medium">"{deleteTarget.name}"</span> 吗？
            <br />该操作不可恢复，文档将从知识库中移除。
          </p>
          <div className="flex justify-end gap-2">
            <button onClick={() => setDeleteTarget(null)} className="px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors">取消</button>
            <button onClick={handleDelete} className="px-4 py-2 text-xs rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors">删除</button>
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
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.active
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${config.className}`}>{config.label}</span>
}

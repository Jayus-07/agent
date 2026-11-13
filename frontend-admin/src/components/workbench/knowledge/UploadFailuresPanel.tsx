'use client'

/**
 * /knowledge/upload-failures — 入库失败待处理列表。
 *
 * 数据 = rag_index_runs 派生口径（failed 且同文件无更新的 published 运行），
 * 重传成功后条目自动消除，无需人工关闭。失败发生在上传对话框关闭之后
 * （worker 异步索引阶段），此页是「事后发现」的出口；仪表盘「知识库入库
 * 失败」卡片跳转至此。
 */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react'
import { knowledgeService } from '@/api/knowledge'

/** 把后端原始错误翻译成可行动的文案（未知原因保留原文）。 */
function humanizeError(raw: string): string {
  if (!raw) return '未知错误'
  if (raw.includes('request_tokens') || raw.includes('请求预算已超限'))
    return '本次处理超出 Token 预算，文档未发布；查看 Trace 确认超限环节后调整预算'
  if (raw.includes('ocr_coverage_partial'))
    return 'OCR 只覆盖了部分页面，完整性门禁未通过；查看 Trace 核对页预算和未识别页'
  if (raw.includes('parser_pdf_source_text_audit_gap'))
    return 'PDF 原文与解析结果未能完成逐页核对，完整性门禁未通过；查看 Trace 检查未解析页面'
  if (raw.includes('coverage_gap'))
    return '原文有内容未进入切分结果，完整性门禁未通过；查看 Trace 检查覆盖缺口'
  if (raw.includes('cleaned_chars'))
    return '提取文本过短或为空，可能是扫描件或空白文档；检查源文件后重传'
  if (raw.includes('quality_anomaly') || raw.includes('prepaid_integrity') || raw.includes('质量门禁'))
    return '质量门禁未通过；查看 Trace 中的具体检查项，再修正原文件后重传'
  if (raw.includes('produced 0 chunks'))
    return '解析出 0 个分块（文件可能损坏、加密或无有效文本）'
  if (raw.includes('暂存源文件缺失'))
    return '系统内部文件传输中断，请重新上传'
  if (raw.includes('provider') || raw.includes('embedding') || raw.includes('Embedding'))
    return '向量化服务暂时异常，稍后重新上传即可'
  return raw
}

const DEPARTMENT_CN: Record<string, string> = {
  warehouse: '仓储部', supply_chain: '供应链部', order_dept: '订单部', customer: '客服部',
  product_dept: '商品部', hr: '人事部', finance: '财务部', admin: '行政部', general: '通用',
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path
}

/** 后端 created_at 为 UTC 字符串（'YYYY-MM-DD HH:MM:SS'），转本地时间显示。 */
function fmtTime(raw: string): string {
  if (!raw) return '-'
  const d = new Date(raw.includes('T') ? raw : raw.replace(' ', 'T') + 'Z')
  return Number.isNaN(d.getTime()) ? raw : d.toLocaleString('zh-CN', { hour12: false })
}

export default function UploadFailuresPanel() {
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState({ async_queue: 0, quality_gate: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await knowledgeService.listUploadFailures(100)
      if (!res?.ok) {
        setError(res?.error || '加载失败')
        return
      }
      setItems(res.items ?? [])
      setTotal(res.total ?? 0)
      setCounts(res.counts ?? { async_queue: res.total ?? 0, quality_gate: 0 })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-start justify-end">
        <button onClick={load} disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border-subtle text-xs text-text-muted hover:text-accent hover:border-accent transition-colors disabled:opacity-50">
          {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          刷新
        </button>
      </div>

      {error && (
        <div className="mt-4 flex items-center gap-2 rounded-lg bg-red-50 text-red-600 px-3 py-2 text-xs">
          <AlertCircle size={13} /> {error}
        </div>
      )}

      <div className="mt-4 rounded-xl border border-black/5 bg-white shadow-card overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
          <span className="text-[13px] font-medium text-text-primary">
            异常 {total} 项：待处理任务 {counts.async_queue} 项，质量门禁失败 {counts.quality_gate} 份
          </span>
        </div>
        {loading ? (
          <div className="flex items-center justify-center py-16 text-text-muted">
            <Loader2 size={18} className="animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <div className="py-16 text-center text-sm text-text-muted">
            没有待处理的入库失败
          </div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-text-muted border-b border-border-subtle">
                <th className="px-4 py-2.5 font-medium">类型</th>
                <th className="px-4 py-2.5 font-medium">文件名</th>
                <th className="px-4 py-2.5 font-medium">部门</th>
                <th className="px-4 py-2.5 font-medium">失败时间</th>
                <th className="px-4 py-2.5 font-medium">原因</th>
                <th className="px-4 py-2.5 font-medium">Trace</th>
              </tr>
            </thead>
            <tbody>
                {items.map((it) => (
                <tr key={it.upload_id || it.doc_id} className="border-b border-black/[0.04] last:border-0 hover:bg-black/[0.02]">
                  <td className="px-4 py-3 text-text-muted whitespace-nowrap">
                    {it.record_kind === 'quality_gate' ? '质量门禁' : '异步任务'}
                  </td>
                  <td className="px-4 py-3 text-text-primary">{basename(it.file_path || '')}</td>
                  <td className="px-4 py-3 text-text-muted">{DEPARTMENT_CN[it.department || ''] || it.department || '-'}</td>
                  <td className="px-4 py-3 text-text-muted whitespace-nowrap">{fmtTime(it.created_at)}</td>
                  <td className="px-4 py-3 text-text-primary" title={it.error || ''}>
                    {humanizeError(it.error || '')}
                    {it.failure_details && (
                      <div className="mt-1 text-[11px] leading-4 text-text-muted">
                        {it.failure_details}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    {it.trace_id ? <Link className="text-accent hover:underline" href={`/knowledge/operations/traces/${encodeURIComponent(it.trace_id)}`} title={`Trace ID：${it.trace_id}`}>查看 Trace</Link> : <span className="text-text-muted" title="历史记录中没有可用的 Trace ID，无法恢复原始跟踪详情">无历史 Trace</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

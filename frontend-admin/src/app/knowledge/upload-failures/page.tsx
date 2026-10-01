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
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react'
import { knowledgeService } from '@/api/knowledge'

/** 把后端原始错误翻译成可行动的文案（未知原因保留原文）。 */
function humanizeError(raw: string): string {
  if (!raw) return '未知错误'
  if (raw.includes('cleaned_chars') || raw.includes('质量门禁'))
    return '提取文本过短或为空（可能是扫描件/空白文档），请检查源文件后重传'
  if (raw.includes('produced 0 chunks'))
    return '解析出 0 个分块（文件可能损坏、加密或无有效文本）'
  if (raw.includes('暂存源文件缺失'))
    return '系统内部文件传输中断，请重新上传'
  if (raw.includes('provider') || raw.includes('embedding') || raw.includes('Embedding'))
    return '向量化服务暂时异常，稍后重新上传即可'
  return raw
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

export default function UploadFailuresPage() {
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
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
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">入库失败</h1>
          <p className="mt-1 text-xs text-text-muted">
            上传受理后索引在后台异步执行，失败会记录在这里；修复源文件后重新上传，同一文件成功入库后本条自动消除。
          </p>
        </div>
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
            待处理 {total} 个文件
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
                <th className="px-4 py-2.5 font-medium">文件名</th>
                <th className="px-4 py-2.5 font-medium">部门</th>
                <th className="px-4 py-2.5 font-medium">失败时间</th>
                <th className="px-4 py-2.5 font-medium">原因</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.upload_id} className="border-b border-black/[0.04] last:border-0 hover:bg-black/[0.02]">
                  <td className="px-4 py-3 text-text-primary">{basename(it.file_path || '')}</td>
                  <td className="px-4 py-3 text-text-muted">{it.department || '-'}</td>
                  <td className="px-4 py-3 text-text-muted whitespace-nowrap">{fmtTime(it.created_at)}</td>
                  <td className="px-4 py-3 text-text-primary" title={it.error || ''}>
                    {humanizeError(it.error || '')}
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

'use client'

import { useState } from 'react'
import { Download } from 'lucide-react'
import { evaluationService } from '@/api/evaluation'

/** 导入模板三件套：RAG JSONL 样例 / 业务 CSV 样例 / 字段说明。 */
export default function ImportTemplateButtons({ domain }: { domain: string }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const download = async (format: 'jsonl' | 'csv' | 'fields') => {
    setBusy(true); setError('')
    try { await evaluationService.downloadImportTemplate(domain, format) }
    catch (e) { setError(e instanceof Error ? e.message : '模板下载失败') }
    finally { setBusy(false) }
  }
  const buttonClass = 'inline-flex min-h-9 items-center gap-1 rounded-md border border-border-subtle px-3 text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-50'
  return <div className="flex flex-wrap items-center gap-2">
    <span className="text-xs text-text-muted">先下载模板填写：</span>
    {domain === 'rag'
      ? <button type="button" className={buttonClass} disabled={busy} onClick={() => void download('jsonl')}><Download size={13} aria-hidden="true" />JSONL 模板（RAG）</button>
      : <button type="button" className={buttonClass} disabled={busy} onClick={() => void download('csv')}><Download size={13} aria-hidden="true" />CSV 模板（{domain}）</button>}
    <button type="button" className={buttonClass} disabled={busy} onClick={() => void download('fields')}><Download size={13} aria-hidden="true" />字段说明</button>
    {error && <span className="text-xs text-red-700" role="alert">{error}</span>}
  </div>
}

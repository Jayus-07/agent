'use client'

import { useState } from 'react'
import { CheckCircle2, Clock3, Play, RefreshCw, UploadCloud, XCircle } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useToast } from '@/components/shared/Toast'
import { atLeast } from '@/lib/auth'
import { importPriceVersion, listPriceVersions, reviewPriceVersion, runPriceCanary, validatePriceImport, validatePriceRows, type PriceRow, type PriceVersion } from '@/api/modelPrices'

const EXAMPLE = JSON.stringify([
  { model_name: 'model-name', component: 'llm', dimension: 'input', price_per_unit: '0.125000', unit: 'per_1m_tokens', currency: 'USD' },
  { model_name: 'model-name', component: 'llm', dimension: 'output', price_per_unit: '0.500000', unit: 'per_1m_tokens', currency: 'USD' },
], null, 2)

export default function PriceTab() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const canAdmin = atLeast('admin')
  const versions = useQuery({ queryKey: ['price-versions'], queryFn: listPriceVersions, refetchInterval: 30_000 })
  const [version, setVersion] = useState('')
  const [source, setSource] = useState('')
  const [rawRows, setRawRows] = useState(EXAMPLE)
  const [validation, setValidation] = useState<{ valid: boolean; errors: string[] } | null>(null)
  const [busy, setBusy] = useState(false)

  function parseRows(): PriceRow[] | null {
    try {
      const rows = JSON.parse(rawRows) as PriceRow[]
      if (!Array.isArray(rows)) throw new Error('必须是 JSON 数组')
      const result = validatePriceRows(rows)
      setValidation(result)
      return result.valid ? rows : null
    } catch (error) { setValidation({ valid: false, errors: [error instanceof Error ? error.message : 'JSON 格式错误'] }); return null }
  }

  async function importVersion() {
    const rows = parseRows()
    if (!rows || !version.trim() || !source.trim()) { toast.error('请填写版本、来源并通过价格校验'); return }
    setBusy(true)
    try { const result = await validatePriceImport({ version: version.trim(), source: source.trim(), rows }); if (!result.valid) { setValidation(result); return } await importPriceVersion({ version: version.trim(), source: source.trim(), rows }); toast.success('价格版本已导入，等待双人审核'); setVersion(''); setSource(''); await queryClient.invalidateQueries({ queryKey: ['price-versions'] }) } catch (error) { toast.error(error instanceof Error ? error.message : '价格版本导入失败') } finally { setBusy(false) }
  }

  async function review(item: PriceVersion, decision: 'approve' | 'reject') {
    const reason = decision === 'reject' ? window.prompt('请输入拒绝原因') ?? '' : ''
    if (decision === 'reject' && !reason.trim()) return
    setBusy(true)
    try { await reviewPriceVersion(item.version, decision, reason); toast.success(decision === 'approve' ? '已记录审核' : '价格版本已拒绝'); await queryClient.invalidateQueries({ queryKey: ['price-versions'] }) } catch (error) { toast.error(error instanceof Error ? error.message : '审核失败') } finally { setBusy(false) }
  }

  async function canary(item: PriceVersion, action: 'start' | 'complete') {
    setBusy(true)
    try { await runPriceCanary(item.version, action); toast.success(action === 'start' ? '已开始 24 小时灰度' : '价格版本已生效'); await queryClient.invalidateQueries({ queryKey: ['price-versions'] }) } catch (error) { toast.error(error instanceof Error ? error.message : '灰度操作失败') } finally { setBusy(false) }
  }

  return <div><div className="mb-4 rounded-lg border border-amber-100 bg-amber-50 px-4 py-3 text-xs text-amber-800">本 tab 的变更需要双人审核并经过 24 小时灰度；其余配置保存后按注册表刷新周期生效。</div>{canAdmin && <section className="mb-5 rounded-xl border border-black/5 bg-white p-4 shadow-card"><div className="flex items-center gap-2"><UploadCloud size={15} className="text-accent" /><h2 className="text-xs font-medium text-text-primary">导入待审核版本</h2></div><div className="mt-3 grid gap-3 md:grid-cols-2"><label className="text-xs text-text-secondary">版本号<input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="price-2026-09-19" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" /></label><label className="text-xs text-text-secondary">来源<input value={source} onChange={(event) => setSource(event.target.value)} placeholder="供应商官方价目表" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-xs" /></label></div><textarea value={rawRows} onChange={(event) => setRawRows(event.target.value)} rows={6} className="mt-3 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-[11px]" /><div className="mt-3 flex items-center justify-between gap-3"><div className="text-xs">{validation && (validation.valid ? <span className="flex items-center gap-1 text-emerald-700"><CheckCircle2 size={13} />校验通过</span> : <span className="flex items-center gap-1 text-red-700"><XCircle size={13} />{validation.errors.join('；')}</span>)}</div><button disabled={busy} onClick={() => { void importVersion() }} className="rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50">校验并导入</button></div></section>}{!canAdmin && <div className="mb-5 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-xs text-blue-800">当前为只读模式；价格导入、审核和灰度需要管理员。</div>}<section className="overflow-hidden rounded-xl border border-black/5 bg-white shadow-card"><div className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><h2 className="text-xs font-medium text-text-primary">价格版本状态</h2><button onClick={() => versions.refetch()} className="flex items-center gap-1 text-[11px] text-text-muted hover:text-accent"><RefreshCw size={12} className={versions.isFetching ? 'animate-spin' : ''} />刷新</button></div><div className="overflow-x-auto"><table className="w-full min-w-[800px] text-left text-xs"><thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">版本</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">导入人</th><th className="px-4 py-3">覆盖率</th><th className="px-4 py-3">灰度</th><th className="px-4 py-3 text-right">操作</th></tr></thead><tbody>{(versions.data?.items ?? []).map((item) => <PriceRowView key={item.version} item={item} canAdmin={canAdmin} busy={busy} onReview={review} onCanary={canary} />)}</tbody></table>{!versions.isLoading && (versions.data?.items ?? []).length === 0 && <div className="px-4 py-10 text-center text-xs text-text-muted">暂无价格版本</div>}</div></section></div>
}

function PriceRowView({ item, canAdmin, busy, onReview, onCanary }: { item: PriceVersion; canAdmin: boolean; busy: boolean; onReview: (item: PriceVersion, decision: 'approve' | 'reject') => void; onCanary: (item: PriceVersion, action: 'start' | 'complete') => void }) {
  const coverage = item.coverage_ratio ?? 0
  const gated = coverage >= 1 && (item.missing_price_count ?? 0) === 0 && (item.price_calculation_error_count ?? 0) === 0
  return <tr className="border-b border-slate-50"><td className="px-4 py-3"><div className="font-mono text-text-primary">{item.version}</div><div className="text-[10px] text-text-muted">{item.source}</div></td><td className="px-4 py-3"><span className="rounded-full bg-slate-100 px-2 py-1 text-[10px]">{item.status}</span></td><td className="px-4 py-3 text-[11px] text-text-secondary">{item.imported_by || '仅 ID'}</td><td className={`px-4 py-3 font-mono ${gated ? 'text-emerald-700' : 'text-red-700'}`}>{Math.round(coverage * 100)}%</td><td className="px-4 py-3 text-[11px] text-text-secondary">{item.status === 'canary' ? <span className="flex items-center gap-1"><Clock3 size={12} />24 小时灰度中</span> : item.status === 'active' ? '已生效' : '通过覆盖率门槛后灰度'}</td><td className="px-4 py-3 text-right"><div className="flex justify-end gap-1.5">{canAdmin && (item.status === 'pending' || item.status === 'reviewed_1') && <><button disabled={busy} onClick={() => onReview(item, 'approve')} className="rounded-lg bg-emerald-600 px-2 py-1.5 text-[10px] text-white">批准</button><button disabled={busy} onClick={() => onReview(item, 'reject')} className="rounded-lg border border-red-200 px-2 py-1.5 text-[10px] text-red-700">拒绝</button></>}{canAdmin && item.status === 'scheduled' && <button disabled={busy || !gated} onClick={() => onCanary(item, 'start')} className="flex items-center gap-1 rounded-lg bg-accent px-2 py-1.5 text-[10px] text-white"><Play size={11} />开始灰度</button>}{canAdmin && item.status === 'canary' && <button disabled={busy || !gated} onClick={() => onCanary(item, 'complete')} className="rounded-lg bg-accent px-2 py-1.5 text-[10px] text-white">完成生效</button>}</div></td></tr>
}

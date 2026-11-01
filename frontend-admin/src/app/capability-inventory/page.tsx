'use client'

/**
 * /capability-inventory — 客服意图目录（capability-governance G4，运营只读）
 *
 * 数据源 GET /cs/capabilities/inventory（后端 supervisor-only；平台 admin
 * 经 is_supervisor 的平台管理员分支放行）。意图全集来自代码唯一事实源
 * FINE_INTENTS，无数据的意图照列（目录 ≠ Top 榜）。
 */
import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import {
  AssetActionButton, AssetSection, AssetStatCard, AssetState,
} from '@/components/layout/AssetPageShell'
import {
  getCsInventory, type CsInventoryResponse, type InventoryRange,
} from '@/api/csInventory'

const RANGE_OPTIONS: { value: InventoryRange; label: string }[] = [
  { value: 'today', label: '今天' },
  { value: 'd7', label: '近 7 天' },
  { value: 'd30', label: '近 30 天' },
]

function RateCell({ rate }: { rate: number | null }) {
  if (rate === null) return <span className="text-text-muted">—（无会话）</span>
  const pct = `${(rate * 100).toFixed(1)}%`
  const tone = rate >= 0.5 ? 'text-red-700' : rate > 0 ? 'text-amber-700' : 'text-green-700'
  return <span className={tone}>{pct}</span>
}

export default function CsInventoryPage() {
  const [range, setRange] = useState<InventoryRange>('d7')
  const [data, setData] = useState<CsInventoryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load(silent = false) {
    if (!silent) setLoading(true)
    setError('')
    try {
      setData(await getCsInventory(range))
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [range])

  return (
    <div className="mx-auto w-full max-w-7xl px-6 py-6">
      <div className="mb-4 flex items-center justify-between gap-2">
        <p className="text-[12px] text-text-secondary">
          客服域意图目录（G4 试点）：意图全集 × 调用 / 转人工率 / 未解决；
          意图清单以代码 FINE_INTENTS 为唯一事实源，无数据意图照列。
          仅主管（supervisor / 平台管理员）可见。
        </p>
        <div className="flex items-center gap-2">
          <select
            data-testid="cs-inventory-range"
            value={range}
            onChange={(e) => setRange(e.target.value as InventoryRange)}
            className="rounded-md border border-border-subtle bg-surface-base px-2 py-1.5 text-[12px] text-text-primary outline-none focus:border-accent"
          >
            {RANGE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <AssetActionButton icon={<RefreshCw size={13} className={loading ? 'animate-spin' : ''} />} onClick={() => load(true)}>
            刷新
          </AssetActionButton>
        </div>
      </div>

      {loading ? (
        <AssetState>加载中…</AssetState>
      ) : error ? (
        <AssetState className="text-red-700">{error}</AssetState>
      ) : !data || data.intents.length === 0 ? (
        <AssetState>意图目录为空（intents.py 未定义或接口未就绪）</AssetState>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <AssetStatCard label="意图总数" value={data.total} hint="FINE_INTENTS 全集" />
            <AssetStatCard label="窗口调用" value={data.totals.calls} hint="用户消息数（按意图归属）" />
            <AssetStatCard
              label="未解决"
              value={data.totals.unresolved}
              hint="主导会话命中未解决登记（rag_miss / sql_empty）"
            />
          </div>

          <AssetSection title="意图目录" meta={`${data.totals.intents_with_data} / ${data.total} 个意图有调用`} bodyClassName="p-0">
            <div className="overflow-x-auto">
              <table data-testid="cs-inventory-table" className="w-full text-left text-[12px]">
                <thead>
                  <tr className="text-[11px] text-text-muted">
                    <th className="px-4 py-2 font-normal">意图</th>
                    <th className="px-4 py-2 font-normal">说明</th>
                    <th className="px-4 py-2 font-normal">调用</th>
                    <th className="px-4 py-2 font-normal">转人工率</th>
                    <th className="px-4 py-2 font-normal">未解决</th>
                  </tr>
                </thead>
                <tbody>
                  {data.intents.map((entry) => (
                    <tr key={entry.intent} className="border-t border-border-subtle">
                      <td className="px-4 py-2.5"><code className="text-text-primary">{entry.intent}</code></td>
                      <td className="px-4 py-2.5 text-text-secondary">{entry.description}</td>
                      <td className="px-4 py-2.5 text-text-secondary">{entry.stats.calls}</td>
                      <td className="px-4 py-2.5"><RateCell rate={entry.stats.handoff_rate} /></td>
                      <td className="px-4 py-2.5">
                        <span className={entry.stats.unresolved > 0 ? 'text-red-700' : 'text-text-muted'}>
                          {entry.stats.unresolved}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </AssetSection>
        </div>
      )}
    </div>
  )
}

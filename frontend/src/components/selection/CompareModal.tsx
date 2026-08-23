'use client'

/**
 * 多品对比弹窗：价格/评分/评价数/促销/库存/卖点并排，差异单元格高亮。
 * 数据来源 GET /selection/compare。
 */

import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { selectionService, type CompareItem } from '@/services/selection'

interface Props {
  urls: string[]
  onClose: () => void
}

const FIELDS: { key: keyof CompareItem; label: string }[] = [
  { key: 'price', label: '现价' },
  { key: 'original_price', label: '划线价' },
  { key: 'rating', label: '评分' },
  { key: 'review_count', label: '评价数' },
  { key: 'promo_text', label: '促销' },
  { key: 'in_stock', label: '库存' },
  { key: 'highlights', label: '卖点' },
]

function render(val: unknown): string {
  if (val === null || val === undefined || val === '') return '-'
  if (typeof val === 'boolean') return val ? '有货' : '无货'
  return String(val)
}

export default function CompareModal({ urls, onClose }: Props) {
  const [items, setItems] = useState<CompareItem[]>([])
  const [diffFields, setDiffFields] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    selectionService.compare(urls)
      .then((r) => { setItems(r.items); setDiffFields(r.diff_fields) })
      .catch((e) => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [urls])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="bg-surface-base rounded-xl shadow-2xl max-w-4xl w-full max-h-[80vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle sticky top-0 bg-surface-base z-10">
          <div className="text-sm font-medium text-text-primary">竞品对比（{items.length} 项）</div>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-black/5">
            <X size={18} className="text-text-muted hover:text-text-primary" />
          </button>
        </div>

        {error && <div className="p-4 text-sm text-red-600">{error}</div>}

        {loading && !error && (
          <div className="flex items-center justify-center py-16">
            <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
            <span className="ml-2 text-xs text-text-muted">正在抓取对比数据...</span>
          </div>
        )}

        {!loading && !error && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle text-left">
                <th className="px-4 py-2 font-medium text-text-muted w-24">字段</th>
                {items.map((it) => (
                  <th key={it.url} className="px-4 py-2 font-medium text-text-primary max-w-[200px] truncate" title={it.name}>
                    {it.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {FIELDS.map(({ key, label }) => {
                const isDiff = diffFields.includes(key)
                return (
                  <tr key={key} className="border-b border-border-subtle/50">
                    <td className="px-4 py-2 text-text-muted">
                      {label}{isDiff && <span className="ml-1 text-amber-500">•</span>}
                    </td>
                    {items.map((it) => (
                      <td key={it.url} className={`px-4 py-2 text-text-secondary align-top ${isDiff ? 'bg-amber-50' : ''}`}>
                        {render(it[key])}
                      </td>
                    ))}
                  </tr>
                )
              })}
              <tr>
                <td className="px-4 py-2 text-text-muted">抓取时间</td>
                {items.map((it) => (
                  <td key={it.url} className="px-4 py-2 text-xs text-text-muted">
                    {it.crawled_at?.slice(0, 16).replace('T', ' ') ?? '-'}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

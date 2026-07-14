'use client'

import { Database, ArrowUpRight } from 'lucide-react'
import { useState, useEffect } from 'react'
import { DATA_ASSETS } from '@/services/mock/pipeline'
import { dataService } from '@/lib/services/dataService'

export default function DataAssetsPage() {
  const [assets, setAssets] = useState<any[]>(DATA_ASSETS)
  useEffect(() => { dataService.getAssets().then(a => { if (a.length) setAssets(a as any) }) }, [])
  const totalRows = assets.reduce((s, a) => s + a.rows, 0)

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">数据资产中心</h1>
            <p className="text-xs text-text-muted mt-1">{assets.length} 个数据集 · {totalRows.toLocaleString()} 条记录</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {assets.map(a => (
            <div key={a.id} className="bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card transition-shadow">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-lg bg-accent/8 flex items-center justify-center">
                    <Database size={14} className="text-accent" />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-text-primary">{a.name}</div>
                    <div className="text-[10px] text-text-muted">{a.source}</div>
                  </div>
                </div>
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${a.quality >= 95 ? 'bg-green-50 text-green-600' : 'bg-amber-50 text-amber-600'}`}>
                  {a.status}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center mb-3">
                <div><div className="text-xs text-text-muted">行数</div><div className="text-sm font-semibold text-text-primary">{a.rows.toLocaleString()}</div></div>
                <div><div className="text-xs text-text-muted">字段</div><div className="text-sm font-semibold text-text-primary">{a.fields}</div></div>
                <div><div className="text-xs text-text-muted">质量</div>
                  <div className={`text-sm font-semibold ${a.quality >= 95 ? 'text-green-500' : 'text-amber-500'}`}>{a.quality}%</div></div>
              </div>
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-text-muted">更新: {a.updatedAt}</span>
                <button className="flex items-center gap-1 text-accent hover:underline">详情 <ArrowUpRight size={10} /></button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

'use client'

import { useState, useRef, useCallback } from 'react'
import { Upload, FileText, Loader2, CheckCircle2, Play, RotateCw, Database } from 'lucide-react'
import { useDataSources } from '@/hooks/useDataSources'
import { dataService } from '@/lib/services/dataService'
import type { CollectAllResult } from '@/lib/api-data'

const TABS = [
  { key: 'local', label: '本地文件上传', icon: Upload, desc: 'CSV / Excel / JSON' },
  { key: 'datasets', label: '开源数据集', icon: FileText, desc: 'Kaggle / UCI / HuggingFace' },
  { key: 'api', label: '开放平台', icon: FileText, desc: 'API 接入' },
  { key: 'generator', label: '数据采集中心', icon: Database, desc: '一键采集清洗分析' },
]

const DCC_DATASETS = [
  { key: 'products', label: '商品数据', rows: 12, desc: '12 条商品，5 大品类' },
  { key: 'orders', label: '订单数据', rows: 15, desc: '15 条订单，5 国渠道' },
  { key: 'shops', label: '店铺数据', rows: 8, desc: '8 个店铺，3 个平台' },
  { key: 'inventory', label: '库存数据', rows: 12, desc: '12 条库存，多仓库' },
  { key: 'suppliers', label: '供应商数据', rows: 10, desc: '10 家供应商，中越两地' },
]

export default function DataSourcePage() {
  const [activeTab, setActiveTab] = useState('local')
  const fileRef = useRef<HTMLInputElement>(null)
  const { datasets, uploadResult, uploading, upload } = useDataSources()

  // ── DCC 采集状态 ──
  const [collecting, setCollecting] = useState(false)
  const [collectResult, setCollectResult] = useState<CollectAllResult | null>(null)
  const [selectedDatasets, setSelectedDatasets] = useState<Set<string>>(
    new Set(DCC_DATASETS.map(d => d.key))
  )

  const toggleDataset = useCallback((key: string) => {
    setSelectedDatasets(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }, [])

  const runCollect = useCallback(async (all: boolean) => {
    setCollecting(true)
    setCollectResult(null)
    try {
      if (all) {
        const res = await dataService.triggerCollectAll(false)
        setCollectResult(res)
      }
      // 单个采集按顺序执行选中的数据集
      const keys = all ? DCC_DATASETS.map(d => d.key) : Array.from(selectedDatasets)
      const results: CollectAllResult = { ok: true, total: keys.length, success: 0, failed: 0, total_parsed: 0, total_cleaned: 0, total_elapsed_ms: 0, results: [] }
      for (const key of keys) {
        try {
          const r = await dataService.triggerCollect(key, false)
          results.results.push({ ...r, dataset: key })
          if (r.ok) { results.success++; results.total_parsed += r.parsed_rows || 0; results.total_cleaned += r.cleaned_rows || 0 }
          else results.failed++
          results.total_elapsed_ms += r.elapsed_ms || 0
        } catch {
          results.results.push({ ok: false, dataset: key, status: 'failed', elapsed_ms: 0, error: '网络错误' })
          results.failed++
        }
      }
      setCollectResult(results)
    } finally {
      setCollecting(false)
    }
  }, [selectedDatasets])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">数据接入中心</h1>
          <p className="text-xs text-text-muted mt-1">支持本地文件、开源数据集、开放平台 API、DCC 数据采集四种方式</p>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 mb-6">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setActiveTab(t.key)}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm transition-colors ${
                activeTab === t.key ? 'bg-accent text-white' : 'bg-surface-base border border-border-subtle text-text-secondary hover:text-text-primary'}`}>
              <t.icon size={15} /> {t.label}
            </button>
          ))}
        </div>

        {/* Content by tab */}
        {activeTab === 'local' && (
          <div>
            <input type="file" ref={fileRef} accept=".csv,.json" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) upload(f) }} />
            <div className="bg-surface-base rounded-xl border border-border-subtle p-8 text-center">
              <Upload size={40} className="mx-auto mb-4 text-text-muted" />
              <h3 className="text-sm font-medium text-text-primary mb-2">上传 CSV 或 JSON 文件</h3>
              <p className="text-xs text-text-muted mb-4">自动识别字段、统计行数、生成数据预览</p>
              <button onClick={() => fileRef.current?.click()} disabled={uploading}
                className="px-5 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors disabled:opacity-50 inline-flex items-center gap-2">
                {uploading ? <><Loader2 size={15} className="animate-spin" /> 上传中...</> : '选择文件'}
              </button>
            </div>

            {/* 上传结果 */}
            {uploadResult && (
              <div className="mt-4 bg-surface-base rounded-xl border border-border-subtle p-5">
                <div className="flex items-center gap-2 mb-3">
                  {uploadResult.ok ? <CheckCircle2 size={16} className="text-green-500" /> : <span className="text-red-500 text-sm">上传失败</span>}
                  <span className="text-sm font-medium text-text-primary">{uploadResult.filename}</span>
                  <span className="text-xs text-text-muted">{(uploadResult.size_bytes || 0 / 1024).toFixed(1)} KB</span>
                </div>
                {uploadResult.ok && uploadResult.fields && (
                  <>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mb-3">
                      {uploadResult.fields.map(f => (
                        <div key={f.name} className="bg-surface-elevated rounded-lg px-3 py-1.5 text-xs">
                          <span className="text-text-primary font-medium">{f.name}</span>
                          <span className="text-text-muted ml-1.5">({f.type})</span>
                        </div>
                      ))}
                    </div>
                    <div className="grid grid-cols-4 gap-3 text-center text-xs">
                      <div><span className="text-text-muted">行数</span><div className="font-semibold text-text-primary">{uploadResult.total_rows?.toLocaleString()}</div></div>
                      <div><span className="text-text-muted">字段</span><div className="font-semibold text-text-primary">{uploadResult.fields.length}</div></div>
                      <div><span className="text-text-muted">状态</span><div className="font-semibold text-green-500">已就绪</div></div>
                      <div><span className="text-text-muted">下一步</span><div className="font-semibold text-accent">进入清洗</div></div>
                    </div>
                  </>
                )}
                {!uploadResult.ok && <p className="text-xs text-red-500">{uploadResult.error}</p>}
              </div>
            )}
          </div>
        )}

        {activeTab === 'datasets' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {datasets.map(d => (
              <div key={d.id} className="bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card transition-shadow">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-text-primary">{d.name}</span>
                  <span className="text-[10px] bg-accent/5 text-accent px-2 py-0.5 rounded-full">{d.source}</span>
                </div>
                <p className="text-xs text-text-muted mb-3">{d.desc}</p>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-text-muted">{d.rows.toLocaleString()} 条 · {d.fields.length} 字段</span>
                  <button className="px-3 py-1.5 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors">导入</button>
                </div>
              </div>
            ))}
          </div>
        )}

        {activeTab === 'api' && (
          <div className="space-y-4">
            {[
              { name: '抖音开放平台', caps: ['商品数据', '订单数据', '广告数据'], status: '未连接' },
              { name: '天猫开放平台', caps: ['商品数据', '交易数据', '物流数据'], status: '未连接' },
            ].map(p => (
              <div key={p.name} className="bg-surface-base rounded-xl border border-border-subtle p-5">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-medium text-text-primary">{p.name}</span>
                  <span className="text-[10px] bg-red-50 text-red-600 px-2 py-0.5 rounded-full">{p.status}</span>
                </div>
                <div className="flex gap-2 mb-4">{[...p.caps].map((c: string) => <span key={c} className="text-[10px] bg-surface-elevated px-2 py-0.5 rounded text-text-muted">{c}</span>)}</div>
                <div className="grid grid-cols-3 gap-3 mb-3">
                  {['AppKey', 'Secret', 'API 地址'].map(k => <div key={k}><span className="text-[10px] text-text-muted">{k}</span><input disabled className="w-full mt-1 bg-surface-elevated rounded px-2 py-1.5 text-xs text-text-muted border border-border-subtle" placeholder="****" /></div>)}
                </div>
                <button className="px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors">测试连接</button>
              </div>
            ))}
          </div>
        )}

        {activeTab === 'generator' && (
          <div className="space-y-6">
            {/* 数据集选择 */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-6">
              <h3 className="text-sm font-medium text-text-primary mb-1">Data Collection Center</h3>
              <p className="text-xs text-text-muted mb-4">
                选中数据集 → 一键触发 Pipeline：StaticFetcher → JsonParser → DefaultCleaner → StatsAnalyzer
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-5">
                {DCC_DATASETS.map(d => (
                  <label key={d.key}
                    className={`flex items-start gap-3 p-3 rounded-lg border transition-colors cursor-pointer ${
                      selectedDatasets.has(d.key)
                        ? 'border-accent bg-accent/5'
                        : 'border-border-subtle bg-surface-elevated hover:border-border-default'
                    }`}>
                    <input type="checkbox" checked={selectedDatasets.has(d.key)}
                      onChange={() => toggleDataset(d.key)} className="mt-0.5 accent-accent" />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-text-primary">{d.label}</div>
                      <div className="text-[11px] text-text-muted mt-0.5">{d.desc}</div>
                      <div className="text-[10px] text-text-muted">{d.rows} 条记录</div>
                    </div>
                  </label>
                ))}
              </div>
              <div className="flex gap-3">
                <button onClick={() => runCollect(false)} disabled={collecting || selectedDatasets.size === 0}
                  className="px-5 py-2 text-sm rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors disabled:opacity-50 inline-flex items-center gap-2">
                  {collecting ? <><Loader2 size={15} className="animate-spin" /> 采集中...</>
                   : <><Play size={15} /> 采集选中 ({selectedDatasets.size})</>}
                </button>
                <button onClick={() => runCollect(true)} disabled={collecting}
                  className="px-5 py-2 text-sm rounded-lg border border-accent text-accent hover:bg-accent/5 transition-colors disabled:opacity-50 inline-flex items-center gap-2">
                  <RotateCw size={15} /> 一键采集全部 5 个
                </button>
              </div>
            </div>

            {/* 采集结果 */}
            {collectResult && (
              <div className="bg-surface-base rounded-xl border border-border-subtle p-6">
                <div className="flex items-center gap-3 mb-4">
                  <h3 className="text-sm font-medium text-text-primary">采集结果</h3>
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
                    collectResult.failed === 0 ? 'bg-green-50 text-green-600' : 'bg-amber-50 text-amber-600'
                  }`}>
                    {collectResult.success}/{collectResult.total} 成功
                  </span>
                </div>

                {/* 汇总 */}
                <div className="grid grid-cols-4 gap-3 mb-5 text-center">
                  <div className="bg-surface-elevated rounded-lg p-3">
                    <div className="text-xs text-text-muted">总解析</div>
                    <div className="text-lg font-semibold text-text-primary">{collectResult.total_parsed}</div>
                  </div>
                  <div className="bg-surface-elevated rounded-lg p-3">
                    <div className="text-xs text-text-muted">总清洗</div>
                    <div className="text-lg font-semibold text-text-primary">{collectResult.total_cleaned}</div>
                  </div>
                  <div className="bg-surface-elevated rounded-lg p-3">
                    <div className="text-xs text-text-muted">总耗时</div>
                    <div className="text-lg font-semibold text-text-primary">{collectResult.total_elapsed_ms.toFixed(0)}ms</div>
                  </div>
                  <div className="bg-surface-elevated rounded-lg p-3">
                    <div className="text-xs text-text-muted">成功率</div>
                    <div className="text-lg font-semibold text-text-primary">
                      {collectResult.total > 0 ? Math.round(collectResult.success / collectResult.total * 100) : 0}%
                    </div>
                  </div>
                </div>

                {/* 明细 */}
                <div className="space-y-2">
                  {collectResult.results.map(r => (
                    <div key={r.dataset}
                      className={`flex items-center justify-between px-4 py-2.5 rounded-lg ${
                        r.ok ? 'bg-green-50/50' : 'bg-red-50/50'
                      }`}>
                      <div className="flex items-center gap-2">
                        {r.ok ? <CheckCircle2 size={14} className="text-green-500" />
                         : <span className="text-red-500 text-xs">✕</span>}
                        <span className="text-sm text-text-primary">{r.dataset}</span>
                      </div>
                      <div className="flex items-center gap-4 text-xs text-text-muted">
                        <span>解析 {r.parsed_rows || 0}</span>
                        <span>清洗 {r.cleaned_rows || 0}</span>
                        {r.dedup_removed ? <span className="text-amber-500">去重 {r.dedup_removed}</span> : null}
                        <span>{(r.elapsed_ms || 0).toFixed(0)}ms</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

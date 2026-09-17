'use client'

/**
 * /selection-funnel — 选品漏斗数据工作台（管理端）
 *
 * 漏斗海选数据源的上传入口（2026-09-17）：
 *   - 三类表格上传：商品榜(products) / 关键词榜(keywords) / 差评(reviews)
 *   - 候选池管理：列表 + 按批次清除 + 单品痛点查询
 *   - 赛道画像：关键词 top 榜 + 机会词
 *
 * 数据消费方：对话「给 XX 做一次智能选品」→ selection_funnel 域图，
 * 建池吃 products，报告吃 keywords/reviews 画像。
 */

import { Fragment, useCallback, useEffect, useState } from 'react'
import {
  Upload, Table2, MessageSquareText, BarChart3, RefreshCw,
  Trash2, ChevronDown, ChevronUp, Loader2,
} from 'lucide-react'
import {
  selectionFunnelService,
  ImportCandidate,
  ImportKind,
  MarketSnapshot,
  PainPointItem,
} from '@/api/selectionFunnel'
import { useToast } from '@/components/shared/Toast'

const KIND_META: Record<ImportKind, { title: string; hint: string; columns: string }> = {
  products: {
    title: '商品榜',
    hint: '生意参谋/竞品工具导出的商品列表 → 漏斗候选池（海选主源）',
    columns: '标题、价格、评分、评价数、销量、平台、类目、链接（任意组合，缺列保留）',
  },
  keywords: {
    title: '关键词榜',
    hint: '搜索词榜单 → 报告「赛道画像」：top 词 + 机会词（搜索高、竞争低）',
    columns: '关键词、搜索人气、点击率、转化率、竞争度',
  },
  reviews: {
    title: '差评数据',
    hint: '竞品评论导出 → 报告「痛点机会」：规则桶聚类出改良机会点',
    columns: '商品标题、评论内容、星级（≤3 或缺星级计差评）',
  },
}

function UploadCard({ kind, category, onDone }: {
  kind: ImportKind
  category: string
  onDone: () => void
}) {
  const toast = useToast()
  const meta = KIND_META[kind]
  const [mode, setMode] = useState<'file' | 'text'>('file')
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit() {
    setBusy(true)
    try {
      const r = mode === 'file'
        ? await selectionFunnelService.importFile(file!, kind, category)
        : await selectionFunnelService.importText(text, kind, category)
      toast.success(`${meta.title}：${r.count} 条已入库（${r.batch_id}）`)
      setFile(null)
      setText('')
      onDone()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导入失败')
    } finally {
      setBusy(false)
    }
  }

  const ready = mode === 'file' ? !!file : text.trim().length > 0

  return (
    <div className="bg-surface-base border border-border-subtle rounded-xl p-4 flex flex-col gap-2">
      <div className="flex items-center gap-1.5 text-sm font-medium text-text-primary">
        {kind === 'products' ? <Table2 size={15} /> : kind === 'keywords' ? <BarChart3 size={15} /> : <MessageSquareText size={15} />}
        {meta.title}
        <span className="text-[11px] font-normal text-text-muted">CSV / Excel / 粘贴 TSV</span>
      </div>
      <p className="text-xs text-text-muted leading-relaxed">{meta.hint}</p>
      <p className="text-[11px] text-text-secondary bg-surface-elevated rounded-md px-2 py-1.5">表头：{meta.columns}</p>

      <div className="flex gap-2 text-xs">
        {(['file', 'text'] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-2 py-1 rounded-md transition-colors ${mode === m ? 'bg-accent text-white' : 'bg-surface-elevated text-text-muted hover:text-text-primary'}`}
          >
            {m === 'file' ? '选文件' : '粘贴文本'}
          </button>
        ))}
      </div>

      {mode === 'file' ? (
        <label className="text-xs">
          <input
            type="file"
            accept=".csv,.tsv,.xlsx,.txt"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-text-secondary file:mr-2 file:rounded-md file:border-0 file:bg-surface-elevated file:px-2 file:py-1 file:text-xs file:text-text-primary hover:file:bg-surface-hover"
          />
        </label>
      ) : (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="从 Excel 直接 Ctrl+C 复制后粘贴到这里（首行为表头）"
          rows={4}
          className="w-full rounded-md border border-border-subtle bg-surface-elevated px-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent/50"
        />
      )}

      <button
        onClick={submit}
        disabled={!ready || busy}
        className="flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
        {busy ? '上传中…' : '上传入库'}
      </button>
    </div>
  )
}

function SnapshotCard({ snapshot }: { snapshot: MarketSnapshot | null }) {
  if (!snapshot) return null
  if (snapshot.count === 0) {
    return (
      <section className="bg-surface-base border border-border-subtle rounded-xl p-4">
        <div className="text-sm font-medium text-text-primary mb-1">赛道画像</div>
        <p className="text-xs text-text-muted">{snapshot.hint ?? '暂无关键词榜数据'}</p>
      </section>
    )
  }
  return (
    <section className="bg-surface-base border border-border-subtle rounded-xl p-4">
      <div className="text-sm font-medium text-text-primary mb-2">
        赛道画像 <span className="text-xs font-normal text-text-muted">（关键词 {snapshot.count} 个）</span>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <div className="text-xs text-text-muted mb-1">搜索人气 Top 10</div>
          <ul className="text-xs divide-y divide-border-subtle/50">
            {snapshot.top.map((k) => (
              <li key={k.keyword} className="py-1.5 flex justify-between">
                <span className="text-text-primary">{k.keyword}</span>
                <span className="tabular-nums text-text-secondary">
                  人气 {k.search_pop?.toLocaleString() ?? '-'} · 竞争 {k.competition?.toLocaleString() ?? '-'}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="text-xs text-text-muted mb-1">机会词（搜索高、竞争低，优先做标题覆盖）</div>
          {snapshot.opportunities.length === 0 ? (
            <p className="text-xs text-text-muted py-2">需要同时有人气与竞争度两列数据</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {snapshot.opportunities.map((k) => (
                <span key={k.keyword} className="px-2 py-1 rounded-full bg-emerald-100 text-emerald-700 text-xs">
                  {k.keyword}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

export default function SelectionFunnelPage() {
  const toast = useToast()
  const [category, setCategory] = useState('')
  const [candidates, setCandidates] = useState<ImportCandidate[]>([])
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [painOpen, setPainOpen] = useState<Record<string, PainPointItem | null>>({})
  const [painLoading, setPainLoading] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [cand, snap] = await Promise.all([
        selectionFunnelService.listCandidates(category),
        selectionFunnelService.marketSnapshot(category),
      ])
      setCandidates(cand.items)
      setSnapshot(snap)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category])

  useEffect(() => { load() }, [load])

  const togglePain = async (title: string) => {
    if (painOpen[title] !== undefined) {
      setPainOpen((s) => { const n = { ...s }; delete n[title]; return n })
      return
    }
    setPainLoading(title)
    try {
      const r = await selectionFunnelService.painPoints(category, [title])
      setPainOpen((s) => ({ ...s, [title]: r.items[0] ?? null }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '痛点查询失败')
    } finally {
      setPainLoading(null)
    }
  }

  const removeBatch = async (batchId: string) => {
    try {
      const r = await selectionFunnelService.clearBatch(batchId)
      toast.success(`已清除批次 ${batchId}（${r.removed} 条）`)
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '清除失败')
    }
  }

  const batches = Array.from(new Set(candidates.map((c) => c.batch_id)))

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-6">
        {/* 标题栏 */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
              <Upload size={18} /> 选品漏斗 · 数据工作台
            </h1>
            <p className="text-xs text-text-muted mt-0.5">
              上传商品榜/关键词榜/差评 → 对话「给 XX 做一次智能选品」即走七层漏斗出 Top-5 报告
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors px-2 py-1.5 disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>

        {/* 类目过滤 */}
        <div className="flex items-center gap-2 text-xs">
          <span className="text-text-muted">类目过滤（空 = 全部）：</span>
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="如：宠物零食"
            className="w-48 rounded-md border border-border-subtle bg-surface-base px-2 py-1 text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent/50"
          />
        </div>

        {/* 上传区 */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {(Object.keys(KIND_META) as ImportKind[]).map((k) => (
            <UploadCard key={k} kind={k} category={category} onDone={load} />
          ))}
        </div>

        {/* 赛道画像 */}
        <SnapshotCard snapshot={snapshot} />

        {/* 候选池 */}
        <section className="bg-surface-base border border-border-subtle rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-border-subtle bg-surface-elevated text-sm font-medium text-text-primary flex items-center justify-between">
            <span>候选池 <span className="text-xs font-normal text-text-muted">（{candidates.length} 条）</span></span>
            {batches.length > 0 && (
              <span className="flex items-center gap-1.5 text-xs font-normal text-text-muted">
                批次：
                {batches.map((b) => (
                  <span key={b} className="inline-flex items-center gap-0.5 bg-surface-elevated rounded-md px-1.5 py-0.5">
                    {b}
                    <button onClick={() => removeBatch(b)} className="text-text-muted hover:text-red-600 transition-colors" title="清除该批次">
                      <Trash2 size={12} />
                    </button>
                  </span>
                ))}
              </span>
            )}
          </div>
          {candidates.length === 0 ? (
            <div className="p-8 text-center text-sm text-text-muted">
              候选池为空：先上传商品榜（漏斗海选的主数据源）
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-text-muted border-b border-border-subtle">
                  <th className="px-4 py-2 font-medium">商品</th>
                  <th className="px-4 py-2 font-medium">平台</th>
                  <th className="px-4 py-2 text-right font-medium">价格</th>
                  <th className="px-4 py-2 text-right font-medium">评分</th>
                  <th className="px-4 py-2 text-right font-medium">评价数</th>
                  <th className="px-4 py-2 text-right font-medium">销量</th>
                  <th className="px-4 py-2 font-medium">类目</th>
                  <th className="px-4 py-2 font-medium">历史</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <Fragment key={c.id}>
                    <tr className="border-b border-border-subtle/50 hover:bg-surface-hover/30 transition-colors">
                      <td className="px-4 py-2 max-w-[240px] truncate text-text-primary" title={c.title}>{c.title}</td>
                      <td className="px-4 py-2 text-text-secondary">{c.platform || '-'}</td>
                      <td className="px-4 py-2 text-right text-text-primary">{c.price?.toFixed(2) ?? '-'}</td>
                      <td className="px-4 py-2 text-right text-text-secondary">{c.rating ?? '-'}</td>
                      <td className="px-4 py-2 text-right text-text-secondary">{c.review_count?.toLocaleString() ?? '-'}</td>
                      <td className="px-4 py-2 text-right text-text-secondary">{c.sales?.toLocaleString() ?? '-'}</td>
                      <td className="px-4 py-2 text-text-secondary">{c.category || '-'}</td>
                      <td className="px-4 py-2">
                        {(c.history_batches ?? 1) >= 2 ? (
                          <span
                            className="inline-flex text-[11px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200"
                            title={`同款已导入 ${c.history_batches} 次，已积累价格/评价时间序列——跑一次漏斗即可在报告中看趋势`}
                          >
                            {c.history_batches} 次记录
                          </span>
                        ) : (
                          <span className="text-text-muted text-xs">-</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <button
                          onClick={() => togglePain(c.title)}
                          disabled={painLoading === c.title}
                          className="text-accent hover:text-accent-hover inline-flex items-center gap-0.5 text-xs transition-colors disabled:opacity-50"
                        >
                          {painLoading === c.title ? <Loader2 size={13} className="animate-spin" /> : painOpen[c.title] !== undefined ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                          痛点
                        </button>
                      </td>
                      </tr>
                    {painOpen[c.title] !== undefined && (
                      <tr className="border-b border-border-subtle/50 bg-surface-elevated/50">
                        <td colSpan={9} className="px-4 py-2.5 text-xs">
                          {painOpen[c.title] === null ? (
                            <span className="text-text-muted">无匹配差评数据（检查评论的商品标题是否包含该商品名）</span>
                          ) : (
                            <span className="text-text-primary">
                              差评 {painOpen[c.title]!.negative}/{painOpen[c.title]!.review_total} —— 痛点：
                              {painOpen[c.title]!.pains.length > 0
                                ? painOpen[c.title]!.pains.map(([p, n]) => `${p}(${n})`).join('、')
                                : '未聚类出高频桶'}
                            </span>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  )
}

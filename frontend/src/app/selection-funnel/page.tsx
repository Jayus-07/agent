'use client'

/**
 * /selection-funnel — 智能选品专属页（多域隔离收官 M4，2026-10-06）
 *
 * 补上第四扇门：此前选品漏斗只能靠主图对话 prefilter 截流进域图执行，
 * 页面缺位 = 隔离方向下「产出作品送专属页」没有落地页。本页是最小闭环：
 *   导入池（CSV/TSV 文件 或 Excel 复制出的 TSV 文本粘贴）
 *     → 候选池可见（GET /import/candidates）
 *     → 跑漏斗（POST /run 直达域图，不经主图对话）
 *     → 报告渲染（漏斗 Markdown 报告 + Top 榜 + 各层留淘计数）。
 * 赛道画像 / 痛点聚类是二期，明确不进本页（防扩范围）。
 *
 * 跨轮契约：conversation_id 由页面持有（running 结果带回），need_info 短路
 * 后用户补参数重跑，同 conversation_id 续跑候选不丢（后端 E1 机制同步进
 * ConversationContext，页面不自建第二份状态）。
 * 入口参数：主图引导卡带参跳转 ?category=…&platform=… 预填跑漏斗表单。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Upload } from 'lucide-react'
import PageHeader from '@/components/layout/PageHeader'
import MarkdownContent from '@/components/chat/MarkdownContent'
import {
  importFunnelFile,
  importFunnelText,
  listFunnelCandidates,
  runFunnel,
  type FunnelCandidate,
  type FunnelKind,
  type FunnelRunResult,
} from '@/api/selectionFunnel'

const KIND_TABS: { value: FunnelKind; label: string; hint: string }[] = [
  { value: 'products', label: '商品候选', hint: '标题/价格/评分/评价数/销量/类目/链接' },
  { value: 'keywords', label: '关键词榜', hint: '关键词/搜索人气/点击率/转化率/竞争度' },
  { value: 'reviews', label: '差评', hint: '商品标题/评论内容/星级' },
]

export default function SelectionFunnelPage() {
  // ── 导入 ──
  const [kind, setKind] = useState<FunnelKind>('products')
  const [importCategory, setImportCategory] = useState('')
  const [importPlatform, setImportPlatform] = useState('')
  const [pasteText, setPasteText] = useState('')
  const [importing, setImporting] = useState(false)
  const [importNotes, setImportNotes] = useState<string[]>([])
  const fileRef = useRef<HTMLInputElement>(null)

  // ── 候选池 ──
  const [candidates, setCandidates] = useState<FunnelCandidate[]>([])
  const [candidatesLoading, setCandidatesLoading] = useState(false)

  // ── 跑漏斗 ──
  const [runCategory, setRunCategory] = useState('')
  const [runPlatform, setRunPlatform] = useState('')
  const [runMessage, setRunMessage] = useState('')
  const [conversationId, setConversationId] = useState('')
  const [running, setRunning] = useState(false)
  const [pageError, setPageError] = useState('')
  const [result, setResult] = useState<FunnelRunResult | null>(null)

  // 引导卡带参跳转预填（?category=…&platform=…），只在挂载时读一次
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const category = params.get('category') || ''
    const platform = params.get('platform') || ''
    if (category) setRunCategory(category)
    if (platform) setRunPlatform(platform)
  }, [])

  const refreshCandidates = useCallback(async (category = '') => {
    setCandidatesLoading(true)
    try {
      const data = await listFunnelCandidates(category)
      setCandidates(data.items || [])
    } catch {
      setCandidates([])
    } finally {
      setCandidatesLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshCandidates()
  }, [refreshCandidates])

  const handleImportFile = async (file: File) => {
    setImporting(true)
    setPageError('')
    setImportNotes([])
    try {
      const res = await importFunnelFile(file, {
        kind,
        category: importCategory,
        platform: importPlatform,
      })
      setImportNotes([`${res.batch_id}：导入 ${res.count} 条`, ...res.notes])
      await refreshCandidates(kind === 'products' ? importCategory : importCategory)
    } catch (e) {
      setPageError(e instanceof Error ? e.message : '导入失败')
    } finally {
      setImporting(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleImportText = async () => {
    if (!pasteText.trim()) return
    setImporting(true)
    setPageError('')
    setImportNotes([])
    try {
      const res = await importFunnelText(pasteText, {
        kind,
        category: importCategory,
        platform: importPlatform,
      })
      setImportNotes([`${res.batch_id}：导入 ${res.count} 条`, ...res.notes])
      setPasteText('')
      await refreshCandidates(importCategory)
    } catch (e) {
      setPageError(e instanceof Error ? e.message : '导入失败')
    } finally {
      setImporting(false)
    }
  }

  const handleRun = async () => {
    setRunning(true)
    setPageError('')
    try {
      const res = await runFunnel({
        message: runMessage,
        category: runCategory,
        platform: runPlatform,
        conversation_id: conversationId,
      })
      setResult(res)
      setConversationId(res.conversation_id)
    } catch (e) {
      setPageError(e instanceof Error ? e.message : '漏斗执行失败')
    } finally {
      setRunning(false)
    }
  }

  const stageLogs = result?.funnel_context?.stage_summary || []
  const topItems = result?.funnel_context?.top || []

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-8 py-6">
      <PageHeader
        title="智能选品"
        desc="导入商品/关键词榜/差评数据 → 跑完整漏斗（粗筛 → 核验 → 经济性 → 排序）→ 报告在本页生成。对话里的选品请求会引导到这里。"
      />

      {pageError && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {pageError}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[400px_1fr] items-start">
        {/* ── 左栏：导入 + 运行 ── */}
        <div className="flex flex-col gap-4">
          <section className="rounded-2xl border border-border-subtle bg-surface-base p-4">
            <h2 className="text-sm font-semibold text-text-primary">1. 导入数据池</h2>
            <div className="mt-3 flex gap-1.5">
              {KIND_TABS.map((tab) => (
                <button
                  key={tab.value}
                  type="button"
                  onClick={() => setKind(tab.value)}
                  className={`rounded-lg px-2.5 py-1.5 text-xs transition ${
                    kind === tab.value
                      ? 'bg-accent text-white'
                      : 'border border-border-subtle text-text-secondary hover:bg-bg-hover'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs text-text-muted">
              表格首行为表头：{KIND_TABS.find((t) => t.value === kind)?.hint}
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <input
                value={importCategory}
                onChange={(e) => setImportCategory(e.target.value)}
                placeholder="类目（批次级，可空）"
                className="rounded-lg border border-border-subtle bg-bg-root px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-accent/50"
              />
              {kind === 'products' ? (
                <input
                  value={importPlatform}
                  onChange={(e) => setImportPlatform(e.target.value)}
                  placeholder="平台（可空）"
                  className="rounded-lg border border-border-subtle bg-bg-root px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-accent/50"
                />
              ) : (
                <span className="self-center text-xs text-text-muted">平台仅商品池使用</span>
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.tsv,.xlsx,.xls,.txt"
              disabled={importing}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleImportFile(file)
              }}
              className="mt-3 block w-full text-xs text-text-secondary file:mr-3 file:rounded-lg file:border-0
                file:bg-accent/10 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-accent
                hover:file:bg-accent/20"
            />
            <textarea
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder="或直接粘贴表格文本（Excel Ctrl+C 复制的 TSV，首行表头）"
              rows={3}
              className="mt-2 w-full resize-y rounded-lg border border-border-subtle bg-bg-root px-2.5 py-2
                text-xs text-text-primary outline-none focus:border-accent/50"
            />
            <button
              type="button"
              disabled={importing || !pasteText.trim()}
              onClick={() => void handleImportText()}
              className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs
                font-medium text-white transition hover:opacity-90 disabled:opacity-40"
            >
              {importing ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
              导入文本
            </button>
            {importNotes.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-xs text-text-secondary">
                {importNotes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-2xl border border-border-subtle bg-surface-base p-4">
            <h2 className="text-sm font-semibold text-text-primary">2. 跑漏斗</h2>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <input
                value={runCategory}
                onChange={(e) => setRunCategory(e.target.value)}
                placeholder="类目（如：宠物零食）"
                className="rounded-lg border border-border-subtle bg-bg-root px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-accent/50"
              />
              <input
                value={runPlatform}
                onChange={(e) => setRunPlatform(e.target.value)}
                placeholder="平台（可空）"
                className="rounded-lg border border-border-subtle bg-bg-root px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-accent/50"
              />
            </div>
            <textarea
              value={runMessage}
              onChange={(e) => setRunMessage(e.target.value)}
              placeholder="补充诉求（可空）：如「侧重好评率 95 以上、客单价 50 以内」"
              rows={2}
              className="mt-2 w-full resize-y rounded-lg border border-border-subtle bg-bg-root px-2.5 py-2
                text-xs text-text-primary outline-none focus:border-accent/50"
            />
            <button
              type="button"
              disabled={running}
              onClick={() => void handleRun()}
              className="mt-2 inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent
                px-3 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-40"
            >
              {running && <Loader2 size={14} className="animate-spin" />}
              {running ? '漏斗执行中…' : '开始选品'}
            </button>
            {conversationId && (
              <p className="mt-2 text-xs text-text-muted">
                会话锚点 {conversationId}（缺槽位追问后补参数重跑，候选不丢）
              </p>
            )}
          </section>

          <section className="rounded-2xl border border-border-subtle bg-surface-base p-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-text-primary">候选池</h2>
              <button
                type="button"
                onClick={() => void refreshCandidates()}
                className="text-xs text-accent hover:underline"
              >
                刷新
              </button>
            </div>
            {candidatesLoading ? (
              <p className="mt-2 text-xs text-text-muted">加载中…</p>
            ) : candidates.length === 0 ? (
              <p className="mt-2 text-xs text-text-muted">
                池子为空——先在上面导入表格（商家工具导出的 CSV 直接传即可）。
              </p>
            ) : (
              <ul className="mt-2 max-h-64 space-y-1.5 overflow-y-auto">
                {candidates.slice(0, 100).map((c, i) => (
                  <li
                    key={`${c.url || c.title}-${i}`}
                    className="flex items-center justify-between gap-2 rounded-lg bg-bg-root px-2.5 py-1.5 text-xs"
                  >
                    <span className="min-w-0 flex-1 truncate text-text-primary" title={c.title}>
                      {c.title || c.url}
                    </span>
                    <span className="shrink-0 text-text-muted">
                      {c.platform}
                      {c.price != null ? ` · ¥${c.price}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-xs text-text-muted">共 {candidates.length} 条（展示前 100）</p>
          </section>
        </div>

        {/* ── 右栏：报告 + Top 榜 + 各层计数 ── */}
        <div className="flex flex-col gap-4">
          <section
            data-testid="funnel-report"
            className="rounded-2xl border border-border-subtle bg-surface-base p-5"
          >
            <h2 className="text-sm font-semibold text-text-primary">漏斗报告</h2>
            {!result ? (
              <p className="mt-2 text-xs text-text-muted">
                还没有运行记录——左侧「开始选品」后报告在这里生成。
              </p>
            ) : (
              <div className="mt-2">
                <MarkdownContent content={result.final_answer} />
              </div>
            )}
          </section>

          {stageLogs.length > 0 && (
            <section className="rounded-2xl border border-border-subtle bg-surface-base p-5">
              <h2 className="text-sm font-semibold text-text-primary">各层留淘</h2>
              <div className="mt-3 flex flex-wrap gap-2">
                {stageLogs.map((log, i) => (
                  <span
                    key={i}
                    className="rounded-lg border border-border-subtle bg-bg-root px-2.5 py-1 text-xs text-text-secondary"
                  >
                    {log.stage}：留 {log.kept} / 淘 {log.dropped}
                    {log.elapsed_ms != null ? ` · ${log.elapsed_ms}ms` : ''}
                  </span>
                ))}
              </div>
            </section>
          )}

          {topItems.length > 0 && (
            <section className="rounded-2xl border border-border-subtle bg-surface-base p-5">
              <h2 className="text-sm font-semibold text-text-primary">Top 候选</h2>
              <div className="table-scroll mt-3">
                <table className="w-full min-w-[640px] text-left text-xs">
                  <thead>
                    <tr className="border-b border-border-subtle text-text-muted">
                      <th className="py-2 pr-3 font-medium">#</th>
                      <th className="py-2 pr-3 font-medium">商品</th>
                      <th className="py-2 pr-3 font-medium">价格</th>
                      <th className="py-2 pr-3 font-medium">评分</th>
                      <th className="py-2 pr-3 font-medium">综合分</th>
                      <th className="py-2 font-medium">毛利率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topItems.map((item, i) => (
                      <tr key={i} className="border-b border-border-subtle/60 text-text-primary">
                        <td className="py-2 pr-3">{item.rank ?? i + 1}</td>
                        <td className="max-w-[280px] truncate py-2 pr-3" title={item.title}>
                          {item.url ? (
                            <a href={item.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                              {item.title}
                            </a>
                          ) : (
                            item.title
                          )}
                        </td>
                        <td className="py-2 pr-3">{item.price != null ? `¥${item.price}` : '—'}</td>
                        <td className="py-2 pr-3">{item.rating != null ? item.rating : '—'}</td>
                        <td className="py-2 pr-3">{item.score_total != null ? item.score_total : '—'}</td>
                        <td className="py-2">
                          {item.gross_margin != null
                            ? `${(item.gross_margin * 100).toFixed(1)}%`
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}

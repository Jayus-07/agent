'use client'

/**
 * CityGuideDrawer — 城市指南抽屉（M3-g/h）。
 *
 * 内容四级链（拍板口径，数据来自 /api/travel/city-guide，缓存 TTL 7 天）：
 *   ① travel 库文档 LLM 摘要 → ② RAG 语料贴士 → ③ 知乎攻略（标 AI 生成）
 *   → ④ 「暂无指南，问助手」
 * 入口：顶栏书本图标 + 聊天快捷 chip「介绍下{目的地}特色」。
 * Esc 关闭；打开即按当前目的地拉取（缓存命中秒出）。
 */
import { useCallback, useEffect, useState } from 'react'
import { BookOpen, Compass, FileText, Flame, Info, Lightbulb, MapPin, X } from 'lucide-react'
import { fetchCityGuide, type CityGuide } from '@/api/travel'

const SOURCE_LEVEL_LABEL: Record<number, string> = {
  1: '来自本地攻略文档',
  2: '来自知识库语料',
  3: '来自知乎攻略（AI 汇总，仅供参考）',
  4: '暂无指南',
}

export default function CityGuideDrawer({
  open, onClose, destination, onAsk,
}: {
  open: boolean
  onClose: () => void
  /** 当前目的地（空 = 未定，显示引导） */
  destination: string
  /** 「问助手」兜底出口：把问题代发给旅行助手 */
  onAsk: (text: string) => void
}) {
  const [guide, setGuide] = useState<CityGuide | null>(null)
  const [loading, setLoading] = useState(false)

  const dest = destination.trim()

  const load = useCallback(async (force = false) => {
    if (!dest) return
    setLoading(true)
    try {
      setGuide(await fetchCityGuide(dest, { force }))
    } catch {
      setGuide(null)
    } finally {
      setLoading(false)
    }
  }, [dest])

  useEffect(() => {
    if (open && dest) void load(false)
  }, [open, dest, load])

  // Esc 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-40 bg-[#183037]/25"
      role="presentation"
      onClick={onClose}
    >
      <aside
        className="flex h-full w-[440px] max-w-[94vw] flex-col border-r border-[#dae7e5] bg-white shadow-2xl"
        aria-label="城市指南"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <header className="shrink-0 border-b border-[#dae7e5] bg-[#087b73] px-5 py-4 text-white">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/15">
              <BookOpen size={16} aria-hidden />
            </span>
            <h2 className="flex-1 text-base font-bold">城市指南 · {dest || '未定目的地'}</h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭城市指南"
              className="cursor-pointer rounded-lg p-1.5 transition-colors hover:bg-white/15"
            >
              <X size={15} />
            </button>
          </div>
          <p className="mt-1 text-[11px] text-white/75">
            {guide ? SOURCE_LEVEL_LABEL[guide.source_level] : '正在打开…'}
            {guide?.status === 'ok' && guide.source_level <= 2 && (
              <button
                type="button"
                onClick={() => void load(true)}
                className="ml-2 cursor-pointer rounded bg-white/15 px-1.5 py-0.5 text-[10px] hover:bg-white/25"
              >
                强制刷新
              </button>
            )}
          </p>
        </header>

        {/* 内容 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4" aria-live="polite">
          {!dest && (
            <Empty text="先定一个目的地，我再给你讲这座城的故事。" />
          )}
          {dest && loading && !guide && (
            <div className="space-y-3" aria-hidden>
              <div className="h-20 animate-pulse rounded-xl bg-[#f5faf9]" />
              <div className="h-28 animate-pulse rounded-xl bg-[#f5faf9]" />
              <div className="h-28 animate-pulse rounded-xl bg-[#f5faf9]" />
              <p className="text-center text-[11px] text-[#8fa5a3]">正在翻攻略（首次生成约 5~10 秒，之后 7 天内秒开）…</p>
            </div>
          )}
          {dest && guide && guide.status === 'ok' && (
            <div className="space-y-5">
              {/* ① 城市气质（文档摘要 / 来源标注） */}
              {guide.summary && (
                <section aria-label="城市气质">
                  <SecTitle icon={<Compass size={13} />} title="城市气质" />
                  <div className="rounded-xl border border-[#e2f0ee] bg-[#f5faf9] p-3">
                    <p className="text-xs leading-relaxed text-[#183037]">{guide.summary.summary}</p>
                    <p className="mt-2 flex items-center gap-1 text-[10px] text-[#8fa5a3]">
                      <FileText size={10} aria-hidden />
                      出处：{guide.summary.title} · 时效未核实，以官方最新为准
                    </p>
                  </div>
                </section>
              )}

              {/* ② 避雷贴士 / 语料 */}
              {guide.tips.length > 0 && (
                <section aria-label="贴士与避雷">
                  <SecTitle icon={<Lightbulb size={13} />} title="贴士与避雷" />
                  <ul className="space-y-2">
                    {guide.tips.map((tip, i) => (
                      <li key={i} className="flex gap-2 text-xs leading-relaxed text-[#183037]">
                        <span className="mt-0.5 shrink-0 text-[#b4690e]">◆</span>
                        <span className="min-w-0">{tip}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-[10px] text-[#8fa5a3]">来自知识库语料 · 时效未核实</p>
                </section>
              )}

              {/* ③ 知乎攻略卡（横滑） */}
              {guide.guides.length > 0 && (
                <section aria-label="攻略文章">
                  <SecTitle icon={<Flame size={13} />} title="攻略文章" />
                  <div className="relative">
                    <div className="flex gap-2 overflow-x-auto pb-1 [scrollbar-width:thin]">
                      {guide.guides.map((g, i) => {
                        const title = String(g.title ?? '')
                        const url = typeof g.url === 'string' && g.url.startsWith('http') ? g.url : ''
                        const summary = typeof g.summary === 'string' ? g.summary : ''
                        return (
                          <article key={i} className="w-[230px] shrink-0 rounded-xl border border-[#e3e9f5] bg-[#fbfcff] p-2.5">
                            {url ? (
                              <a href={url} target="_blank" rel="noopener noreferrer"
                                className="block truncate text-xs font-semibold text-[#2d5bd1] hover:underline" title={title}>
                                {title}
                              </a>
                            ) : (
                              <p className="truncate text-xs font-semibold text-[#183037]" title={title}>{title}</p>
                            )}
                            {summary && <p className="mt-1 line-clamp-3 text-[10.5px] leading-relaxed text-[#5c7074]">{summary}</p>}
                            <p className="mt-1.5 text-[9.5px] text-[#8fa5a3]">知乎 · AI 汇总仅供参考</p>
                          </article>
                        )
                      })}
                    </div>
                    <span aria-hidden className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-white to-transparent" />
                  </div>
                </section>
              )}
            </div>
          )}
          {dest && guide && guide.status === 'empty' && (
            <div className="space-y-4 py-8 text-center">
              <MapPin size={28} className="mx-auto text-[#c3d6d2]" aria-hidden />
              <p className="text-sm text-[#5c7074]">{guide.note}</p>
              <button
                type="button"
                onClick={() => { onClose(); onAsk(`介绍下${dest}特色，适合玩几天、有什么必吃必逛`) }}
                className="cursor-pointer rounded-lg bg-[#087b73] px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-[#06655f]"
              >
                去问旅行助手
              </button>
            </div>
          )}
        </div>

        <footer className="shrink-0 border-t border-[#dae7e5] bg-[#f5faf9] px-5 py-2.5">
          <p className="flex items-center gap-1.5 text-[10px] text-[#8fa5a3]">
            <Info size={10} aria-hidden />
            内容按「本地攻略文档 → 知识库 → 知乎」优先级生成，结果缓存 7 天
          </p>
        </footer>
      </aside>
    </div>
  )
}

function SecTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <h3 className="mb-2 flex items-center gap-1.5 text-[13px] font-bold text-[#183037]">
      <span className="text-[#087b73]">{icon}</span>
      {title}
    </h3>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="py-16 text-center text-xs text-[#8fa5a3]">
      <Compass size={28} className="mx-auto mb-3 text-[#c3d6d2]" aria-hidden />
      {text}
    </div>
  )
}

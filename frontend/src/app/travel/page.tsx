'use client'

/**
 * /travel — 旅游行程页（2026-09-22 P0-5 建页；2026-10-01 重做展示 + 加改单抽屉；同日二轮视觉升级）
 *
 * 两条入口，语义必须清楚（页面上有文字说明，别让用户猜）：
 *   1. 表单「生成行程」= **开一份新行程**：轮换会话线程（conversation_id），
 *      清掉上一份行程，从零排；
 *   2. 右侧「对话改行程」抽屉 = **在当前行程上改**：复用同一个 conversation_id，
 *      后端 checkpointer 取回上一轮 brief 合并 + 指纹比对，变了才重排
 *      （见 components/travel/TravelChatDrawer.tsx 与 src/api/travel.ts）。
 *
 * 为什么结构化行程走 REST 而非 SSE：逐日时间轴/地图打点/费用拆分/ICS 导出
 * 都要完整 itinerary JSON，SSE 只有文本流。
 *
 * 额度条与 /agent 用同一个组件（BudgetStatusBar）：额度是**账号级**的，
 * 两处显示不一致会让人以为是两个额度；且额度打满时两处都要禁输入。
 *
 * 本轮视觉升级的边界：只动展示层——所有「智能感」（灵感卡、目的地联想、
 * 出发倒计时、费用占比）都从既有接口数据派生（travelDisplay.ts），不新增
 * 后端契约、不引依赖；生成中卡片的四步是后端真实阶段（slot→检索→排程→
 * 校验）的循环示意，不是真实进度读数（后端没有分步进度 API，不伪造百分比）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, CalendarDays, Loader2, MapPin, Minus, Plane, Plus, Sparkles } from 'lucide-react'
import BudgetStatusBar from '@/components/chat/BudgetStatusBar'
import ItineraryView from '@/components/travel/ItineraryView'
import TravelChatDrawer from '@/components/travel/TravelChatDrawer'
import {
  EMPTY_PLAN_STATE,
  applyPlanResponse,
  composePlanMessage,
  readConversationId,
  rotateConversationId,
  type PlanState,
} from '@/components/travel/planState'
import { cityHue } from '@/components/travel/travelDisplay'
import {
  fetchItineraryIcs,
  fetchTravelRecommendations,
  planTravel,
  sendTravelFeedback,
  type PlanResponse,
  type Recommendation,
} from '@/api/travel'

const PREFERENCE_OPTIONS = ['自然', '人文', '美食', '亲子', '购物', '夜生活', '摄影'] as const
const PACE_OPTIONS = [
  { value: '', label: '默认（适中）' },
  { value: 'relaxed', label: '轻松' },
  { value: 'moderate', label: '适中' },
  { value: 'intense', label: '紧凑' },
] as const

const FIELD_CLS =
  'mt-1 w-full rounded-lg border border-border-subtle bg-surface-elevated px-2.5 py-1.5 text-sm text-text-primary ' +
  'outline-none transition-colors focus:border-accent/50 focus:bg-surface-base'

/** 预算输入带 ¥ 前缀时用（pl 让开前缀；pl 在 Tailwind 生成序里晚于 px，可覆盖） */
const FIELD_MONEY_CLS =
  'mt-1 w-full rounded-lg border border-border-subtle bg-surface-elevated pl-6 pr-2.5 py-1.5 text-sm text-text-primary ' +
  'outline-none transition-colors focus:border-accent/50 focus:bg-surface-base'

/** 明天的本地 ISO 日期（智能默认：出发日期留白时预填明天，可改可清） */
function tomorrowIso(): string {
  const t = new Date()
  t.setDate(t.getDate() + 1)
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`
}

export default function TravelPage() {
  // ── 表单 ──
  const [destination, setDestination] = useState('')
  const [days, setDays] = useState('2')
  const [partySize, setPartySize] = useState('2')
  const [budget, setBudget] = useState('')
  const [startDate, setStartDate] = useState('')
  const [pace, setPace] = useState('')
  const [preferences, setPreferences] = useState<string[]>([])
  const [extra, setExtra] = useState('')

  // ── 结果与线程 ──
  const [conversationId, setConversationId] = useState(readConversationId)
  const [planState, setPlanState] = useState<PlanState>(EMPTY_PLAN_STATE)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [feedbackSent, setFeedbackSent] = useState<'' | 'positive' | 'negative'>('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [budgetBlocked, setBudgetBlocked] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const preferenceKey = preferences.join(',')

  // 推荐随偏好变化（本地数据、无外部依赖，不必节流）。top=6：3 张灵感卡 +
  // 目的地联想共用一份，多拉几条让联想有得选。
  useEffect(() => {
    let alive = true
    fetchTravelRecommendations(preferenceKey ? preferenceKey.split(',') : [], 6)
      .then((items) => { if (alive) setRecommendations(items) })
      .catch(() => { /* 推荐失败不影响主流程 */ })
    return () => { alive = false }
  }, [preferenceKey])

  // 出发日期智能默认：只在首次进页时预填明天（useEffect 而非 useState 初始化，
  // 避免 SSR 与客户端时钟不同天造成 hydration 不一致）
  useEffect(() => {
    setStartDate((prev) => prev || tomorrowIso())
  }, [])

  useEffect(() => () => abortRef.current?.abort(), [])

  const itinerary = planState.plan?.itinerary ?? null

  // ── 表单提交：开一份新行程 ──
  const submit = useCallback(async () => {
    if (loading || budgetBlocked) return
    const message = composePlanMessage({
      destination, days, startDate, partySize, budget, pace, preferences, extra,
    })
    // 新行程 = 新线程。旧行程属于旧线程，留着会让「改单」打到错误的行程上，
    // 因此立刻清空（配合下方的生成中占位，不会显得东西凭空消失）。
    const cid = rotateConversationId()
    setConversationId(cid)
    setPlanState(EMPTY_PLAN_STATE)
    setFeedbackSent('')
    setError('')
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const data = await planTravel(message, cid, { signal: controller.signal })
      setPlanState(applyPlanResponse(EMPTY_PLAN_STATE, data))
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : '规划请求失败，请稍后再试')
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setLoading(false)
    }
  }, [
    budget, budgetBlocked, days, destination, extra, loading, partySize, pace, preferences, startDate,
  ])

  // ── 抽屉回复：在当前行程上改 ──
  const handleDrawerResponse = useCallback((data: PlanResponse) => {
    setPlanState((prev) => applyPlanResponse(prev, data))
  }, [])

  const startNewTrip = useCallback(() => {
    abortRef.current?.abort()
    const cid = rotateConversationId()
    setConversationId(cid)
    setPlanState(EMPTY_PLAN_STATE)
    setFeedbackSent('')
    setError('')
  }, [])

  const downloadIcs = useCallback(async () => {
    if (!itinerary) return
    setExporting(true)
    try {
      const blob = await fetchItineraryIcs(itinerary)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `travel-${itinerary.brief.destination || 'trip'}.ics`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : '日历导出失败，请稍后再试')
    } finally {
      setExporting(false)
    }
  }, [itinerary])

  const sendFeedback = useCallback(async (vote: 'positive' | 'negative') => {
    if (!itinerary || feedbackSent) return
    try {
      await sendTravelFeedback({
        conversationId,
        vote,
        destination: itinerary.brief.destination,
        planVersion: itinerary.plan_version,
      })
      setFeedbackSent(vote)
    } catch {
      setError('反馈提交失败')
    }
  }, [conversationId, feedbackSent, itinerary])

  // 灵感卡取前 3 条；联想下拉用全量（含偏好加权后的顺序）
  const inspiration = useMemo(() => recommendations.slice(0, 3), [recommendations])
  const hasDestination = destination.trim().length > 0

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${drawerOpen ? 'lg:pr-[440px]' : ''}`}>
      {/* 额度条：与 /agent 同一个组件、同一数据源（/api/budgets/me） */}
      <BudgetStatusBar onBlockedChange={setBudgetBlocked} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-5 px-6 py-6">
          {/* ── 头部横幅：旅行感的品牌渐变，替代原「图标+免责声明」平排 ── */}
          <section className="relative overflow-hidden rounded-2xl px-6 py-7 text-white
            bg-[linear-gradient(118deg,#3b54d4_0%,#4D6BFE_52%,#4a9fe8_100%)] shadow-card">
            {/* 装饰：大图标本 + 光斑，纯视觉、不拦指针 */}
            <Plane size={150} className="pointer-events-none absolute -right-6 -top-9 rotate-12 text-white/10" aria-hidden />
            <div className="pointer-events-none absolute -left-10 top-8 h-28 w-28 rounded-full bg-white/10 blur-2xl" aria-hidden />
            <div className="relative">
              <h1 className="text-xl font-bold tracking-wide">想去哪儿，交给规划引擎</h1>
              <p className="mt-1.5 max-w-xl text-[13px] leading-relaxed text-white/90">
                填多少算多少，缺的会追问。行程由确定性规则基于真实路况与天气排出，
                景点与票价为参考值，出发前请核实。
              </p>
            </div>
          </section>

          {/* ── 需求表单（上提盖住横幅下缘，制造层次） ── */}
          <section className="relative z-10 -mt-4 space-y-3 rounded-2xl border border-border-subtle bg-surface-base p-4 shadow-card">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <DestinationField
                value={destination}
                onChange={setDestination}
                suggestions={recommendations}
              />
              <StepperField label="天数" value={days} onChange={setDays} min={1} max={10} />
              <StepperField label="人数" value={partySize} onChange={setPartySize} min={1} max={20} />
              <label className="text-sm text-text-secondary">
                <span>预算（元）</span>
                <span className="relative mt-1 block">
                  <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-text-muted" aria-hidden>¥</span>
                  <input
                    type="number" min={0} value={budget}
                    onChange={(e) => setBudget(e.target.value)}
                    placeholder="选填"
                    className={FIELD_MONEY_CLS}
                  />
                </span>
              </label>
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <label className="text-sm text-text-secondary">
                <span className="inline-flex items-center gap-1">
                  <CalendarDays size={12} className="text-text-muted" aria-hidden />
                  出发日期
                </span>
                <input
                  type="date" value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className={FIELD_CLS}
                />
              </label>
              <label className="text-sm text-text-secondary">
                <span>节奏</span>
                <select
                  value={pace}
                  onChange={(e) => setPace(e.target.value)}
                  className={FIELD_CLS}
                >
                  {PACE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
              <label className="col-span-2 text-sm text-text-secondary">
                <span>其他要求</span>
                <input
                  value={extra}
                  onChange={(e) => setExtra(e.target.value)}
                  placeholder="如：必去三坊七巷、不吃辣"
                  className={FIELD_CLS}
                />
              </label>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-text-muted">偏好</span>
              {PREFERENCE_OPTIONS.map((tag) => {
                const active = preferences.includes(tag)
                return (
                  <button
                    key={tag}
                    type="button"
                    onClick={() => setPreferences((prev) =>
                      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag])}
                    aria-pressed={active}
                    className={`cursor-pointer rounded-full px-3 py-1 text-xs transition-colors ${
                      active
                        ? 'bg-accent text-white'
                        : 'bg-surface-hover text-text-secondary hover:bg-gray-200'
                    }`}
                  >
                    {tag}
                  </button>
                )
              })}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 pt-0.5">
              <p className="max-w-md text-[11px] leading-relaxed text-text-muted">
                「生成行程」会开一份新行程；想改已经生成的这份，用右侧
                「对话改行程」——一句话就行（改成 3 天 / 节奏轻松点 / 不吃辣）。
              </p>
              <button
                type="button"
                onClick={submit}
                disabled={loading || budgetBlocked}
                className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-medium
                  text-white transition-colors hover:bg-accent-hover
                  disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Sparkles size={14} />
                {loading ? '规划中…' : '生成行程'}
              </button>
            </div>
          </section>

          {/* ── 灵感卡：目的地为空时的零门槛起点（点卡片即填目的地） ── */}
          {!hasDestination && inspiration.length > 0 && (
            <section aria-label="热门目的地灵感">
              <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-text-primary">
                <MapPin size={14} className="text-accent" aria-hidden />
                还没想好？从热门开始
              </h2>
              <div className="grid gap-3 sm:grid-cols-3">
                {inspiration.map((r) => {
                  const hue = cityHue(r.city)
                  return (
                    <button
                      key={r.city}
                      type="button"
                      onClick={() => setDestination(r.city)}
                      title={`用 ${r.city} 生成行程`}
                      className="group relative h-36 cursor-pointer overflow-hidden rounded-xl text-left
                        shadow-card transition-shadow hover:shadow-input focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      <span
                        className="absolute inset-0 transition-transform duration-300 group-hover:scale-[1.03]"
                        aria-hidden
                        style={{
                          background:
                            `linear-gradient(to top, rgba(15,23,42,.62), rgba(15,23,42,0) 55%),` +
                            `linear-gradient(135deg, hsl(${hue} 60% 52%), hsl(${(hue + 42) % 360} 58% 40%))`,
                        }}
                      />
                      <span className="relative flex h-full flex-col justify-between p-3">
                        <span className="text-lg font-bold text-white drop-shadow">{r.city}</span>
                        <span>
                          <span className="flex flex-wrap gap-1">
                            {r.highlights.slice(0, 3).map((h) => (
                              <span key={h} className="rounded-full bg-white/20 px-2 py-0.5 text-[10px] text-white backdrop-blur-sm">
                                {h}
                              </span>
                            ))}
                          </span>
                          <span className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-white/95">
                            规划这站
                            <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" aria-hidden />
                          </span>
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </section>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
              <span className="min-w-0 flex-1 break-words">{error}</span>
              <button type="button" onClick={() => setError('')} className="shrink-0 text-red-400 hover:text-red-600">
                关闭
              </button>
            </div>
          )}

          {/* ── 结果 ── */}
          {itinerary ? (
            <ItineraryView
              itinerary={itinerary}
              notice={planState.notice}
              finalAnswer={planState.plan?.final_answer}
              exporting={exporting}
              feedbackSent={feedbackSent}
              onExportIcs={downloadIcs}
              onFeedback={sendFeedback}
            />
          ) : loading ? (
            <GeneratingCard />
          ) : planState.notice ? (
            <div className="whitespace-pre-wrap rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              {planState.notice}
            </div>
          ) : (
            <EmptyHint hasDestination={hasDestination} />
          )}
        </div>
      </div>

      {/* 右侧「对话改行程」：边缘按钮 + 展开抽屉 */}
      <TravelChatDrawer
        open={drawerOpen}
        onOpen={() => setDrawerOpen(true)}
        onClose={() => setDrawerOpen(false)}
        conversationId={conversationId}
        hasItinerary={!!itinerary}
        onResponse={handleDrawerResponse}
        onStartNewTrip={startNewTrip}
        disabled={budgetBlocked}
        disabledHint="本月额度已用尽，暂时不能发起新的规划；额度重置后自动恢复"
      />
    </div>
  )
}

// ── 目的地输入 + 联想下拉 ────────────────────────────────────

/**
 * 目的地联想：从推荐列表（本地数据）按包含关系过滤。
 * 不引 headlessui/downshift——就一个输入框 + 最多 6 条，键盘四键自己管更省；
 * 选项用 onMouseDown 选取（先于 input 的 blur 触发），避免「点选项先关弹层」竞态。
 */
function DestinationField({
  value, onChange, suggestions,
}: {
  value: string
  onChange: (v: string) => void
  suggestions: Recommendation[]
}) {
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const boxRef = useRef<HTMLDivElement>(null)
  const listId = 'destination-suggestions'

  const filtered = useMemo(() => {
    const q = value.trim()
    if (!q) return []
    return suggestions.filter((r) => r.city !== q && r.city.includes(q)).slice(0, 5)
  }, [value, suggestions])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const pick = useCallback((city: string) => {
    onChange(city)
    setOpen(false)
    setActiveIdx(-1)
  }, [onChange])

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || filtered.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx((i) => (i + 1) % filtered.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx((i) => (i - 1 + filtered.length) % filtered.length)
    } else if (e.key === 'Enter') {
      if (activeIdx >= 0) {
        e.preventDefault()
        pick(filtered[activeIdx].city)
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div ref={boxRef} className="relative text-sm text-text-secondary">
      <label htmlFor="travel-destination">
        <span>目的地</span>
        <input
          id="travel-destination"
          role="combobox"
          aria-expanded={open && filtered.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          autoComplete="off"
          value={value}
          onChange={(e) => { onChange(e.target.value); setOpen(true); setActiveIdx(-1) }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="留空看推荐"
          className={FIELD_CLS}
        />
      </label>
      {open && filtered.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          aria-label="目的地候选"
          className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-border-subtle bg-surface-base py-1 shadow-input"
        >
          {filtered.map((r, i) => (
            <li key={r.city} role="option" aria-selected={i === activeIdx}>
              <button
                type="button"
                onMouseDown={(e) => { e.preventDefault(); pick(r.city) }}
                onMouseEnter={() => setActiveIdx(i)}
                className={`flex w-full cursor-pointer flex-col px-3 py-1.5 text-left transition-colors ${
                  i === activeIdx ? 'bg-accent/10' : ''
                }`}
              >
                <span className="text-sm font-medium text-text-primary">{r.city}</span>
                {r.highlights.length > 0 && (
                  <span className="truncate text-[11px] text-text-muted">{r.highlights.slice(0, 3).join(' · ')}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── 数字步进器（天数/人数） ──────────────────────────────────

/** +/− 步进器：保留手输（type=number），按钮兜住触屏/鼠标用户，范围在源头钳制。 */
function StepperField({
  label, value, onChange, min, max,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  min: number
  max: number
}) {
  const clamp = (n: number) => Math.min(max, Math.max(min, n))
  const step = (delta: number) => {
    const n = parseInt(value, 10)
    onChange(String(clamp((Number.isFinite(n) ? n : min) + delta)))
  }
  return (
    <label className="text-sm text-text-secondary">
      <span>{label}</span>
      <span className={`mt-1 flex items-stretch overflow-hidden rounded-lg border border-border-subtle bg-surface-elevated transition-colors focus-within:border-accent/50`}>
        <button
          type="button"
          onClick={() => step(-1)}
          aria-label={`减少${label}`}
          className="w-8 cursor-pointer text-text-muted transition-colors hover:bg-surface-hover hover:text-text-primary"
        >
          <Minus size={13} className="mx-auto" aria-hidden />
        </button>
        <input
          type="number" min={min} max={max} value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full min-w-0 bg-transparent px-1 py-1.5 text-center text-sm text-text-primary outline-none
            [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
        />
        <button
          type="button"
          onClick={() => step(1)}
          aria-label={`增加${label}`}
          className="w-8 cursor-pointer text-text-muted transition-colors hover:bg-surface-hover hover:text-text-primary"
        >
          <Plus size={13} className="mx-auto" aria-hidden />
        </button>
      </span>
    </label>
  )
}

// ── 生成中流程卡 ─────────────────────────────────────────────

/**
 * 规划引擎的四个真实阶段（slot_filler → POI/路况检索 → 排程 → 校验）。
 * 后端没有分步进度 API，这里是**循环示意**（每 1.5s 轮转高亮）+ 真实计时，
 * 不伪造百分比；请求被中止/完成即卸载。
 */
const PLAN_STAGES = ['理解需求', '匹配景点与路况', '排逐日时间轴', '核对预算与约束'] as const

function GeneratingCard() {
  const [elapsed, setElapsed] = useState(0)
  const [stage, setStage] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => {
      setElapsed((s) => s + 1)
      setStage((s) => (s + 1) % PLAN_STAGES.length)
    }, 1500)
    return () => clearInterval(timer)
  }, [])

  return (
    <section
      className="animate-fade-in rounded-2xl border border-border-subtle bg-surface-base p-5 shadow-card"
      aria-live="polite"
      aria-label="正在生成行程"
    >
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent/10">
          <Loader2 size={17} className="animate-spin text-accent" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-text-primary">正在生成行程</p>
          <p className="mt-0.5 text-xs text-text-muted">确定性规则规划，通常数秒内完成</p>
        </div>
        <span className="shrink-0 font-mono text-sm tabular-nums text-text-muted">{elapsed}s</span>
      </div>

      <ol className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {PLAN_STAGES.map((label, i) => (
          <li
            key={label}
            aria-current={i === stage ? 'step' : undefined}
            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs transition-colors duration-300 ${
              i === stage
                ? 'bg-accent/10 font-medium text-accent'
                : 'text-text-muted'
            }`}
          >
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full transition-colors duration-300 ${
                i === stage ? 'bg-accent' : 'bg-border-default'
              }`}
              aria-hidden
            />
            {label}
          </li>
        ))}
      </ol>
    </section>
  )
}

function EmptyHint({ hasDestination }: { hasDestination: boolean }) {
  return (
    <div className="rounded-xl border border-dashed border-border-default bg-surface-elevated px-6 py-8 text-center">
      <p className="text-sm text-text-secondary">
        {hasDestination ? '点「生成行程」，行程会在这里逐日铺开' : '填好上面的信息，或点一张灵感卡开始'}
      </p>
      <p className="mt-1.5 text-xs text-text-muted">
        生成后可以点右侧「对话改行程」，用一句话调整（改成 3 天、节奏轻松点、预算压到 2000）
      </p>
    </div>
  )
}

'use client'

/**
 * /travel — 旅游行程页（2026-09-22 P0-5 建页；2026-10-01 三栏方案版）
 *
 * 布局 = 产品方案 v2 §2 的三栏（参考 HTML 视觉，青绿 #087b73 页面级主题）：
 *   左栏「行程条件」— 需求表单；生成后折叠为摘要，可随时「调整条件」；
 *   中栏「行程」    — 唯一结果主视图（概览/预算/地图/按天 Tab/须知）；
 *   右栏「旅行助手」— 宽屏常驻面板（panel），中窄屏退回覆盖抽屉（drawer）。
 *
 * 两条入口，语义必须清楚（页面上有文字说明，别让用户猜）：
 *   1. 左栏「生成行程」= **开一份新行程**：轮换会话线程（conversation_id），
 *      清掉上一份行程，从零排；
 *   2. 右栏「旅行助手」= **在当前行程上改**：复用同一个 conversation_id，
 *      后端 checkpointer 取回上一轮 brief 合并 + 指纹比对，变了才重排
 *      （见 components/travel/TravelChatDrawer.tsx 与 src/api/travel.ts）。
 *
 * 为什么结构化行程走 REST 而非 SSE：逐日时间轴/地图打点/费用拆分/ICS 导出
 * 都要完整 itinerary JSON，SSE 只有文本流。
 *
 * 额度圆圈与 /agent 用同一个组件（BudgetRing，挂在助手输入区左下）：
 * 额度是**账号级**的，两处显示不一致会让人以为是两个额度；且额度打满时
 * 两处都要禁输入（useBudgetStatus 上抛 blocked）。
 *
 * 实施边界：只动展示层——所有「智能感」（灵感卡、目的地联想、出发倒计时、
 * 费用占比、按天路线投影）都从既有接口数据派生（travelDisplay.ts），不新增
 * 后端契约、不引依赖；生成中卡片的阶段是后端真实阶段的循环示意，不是真实
 * 进度读数（后端没有分步进度 API，不伪造百分比）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, CalendarDays, Loader2, MapPin, Minus, Plus, Sparkles } from 'lucide-react'
import { useBudgetStatus } from '@/hooks/useBudgetStatus'
import ItineraryView from '@/components/travel/ItineraryView'
import TravelChatDrawer from '@/components/travel/TravelChatDrawer'
import {
  EMPTY_PLAN_STATE,
  applyPlanResponse,
  composePlanMessage,
  readConversationId,
  rotateConversationId,
  PACE_LABEL,
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

// 页面级主题（参考 HTML #trip-concept；勿当全局 token 用，全局 accent 仍是蓝）
const TP_INPUT_CLS =
  'mt-1 w-full rounded-lg border border-[#dae7e5] bg-white px-2.5 py-1.5 text-sm text-[#183037] ' +
  'outline-none transition-colors placeholder:text-[#9db4b1] focus:border-[#087b73]/60 focus:ring-2 focus:ring-[#087b73]/10'

const FIELD_LABEL_CLS = 'block text-xs text-[#5c7074]'

/** 明天的本地 ISO 日期（智能默认：出发日期留白时预填明天，可改可清） */
function tomorrowIso(): string {
  const t = new Date()
  t.setDate(t.getDate() + 1)
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`
}

/** 宽屏（xl=1280px+）才用常驻助手右栏；中窄屏走覆盖抽屉。 */
function useIsWide(): boolean {
  const [wide, setWide] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1280px)')
    const sync = () => setWide(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])
  return wide
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
  // 出行程后左栏折叠为摘要；「调整条件」再展开
  const [conditionsOpen, setConditionsOpen] = useState(true)
  const [budgetBlocked, setBudgetBlocked] = useState(false)
  // 预算轮询每页单一数据源；圆圈展示在助手输入区（TravelChatDrawer）
  const { status: budgetStatus } = useBudgetStatus(setBudgetBlocked)
  const abortRef = useRef<AbortController | null>(null)
  const isWide = useIsWide()

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
      // 出了行程 → 左栏折叠成摘要，把空间让给中栏结果（方案 v2 §2.1）
      if (data.itinerary) setConditionsOpen(false)
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

  // ── 助手回复：在当前行程上改 ──
  const handleAssistantResponse = useCallback((data: PlanResponse) => {
    setPlanState((prev) => applyPlanResponse(prev, data))
  }, [])

  const startNewTrip = useCallback(() => {
    abortRef.current?.abort()
    const cid = rotateConversationId()
    setConversationId(cid)
    setPlanState(EMPTY_PLAN_STATE)
    setFeedbackSent('')
    setError('')
    setConditionsOpen(true)
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

  const assistantProps = {
    conversationId,
    hasItinerary: !!itinerary,
    onResponse: handleAssistantResponse,
    onStartNewTrip: startNewTrip,
    disabled: budgetBlocked,
    disabledHint: '本月额度已用尽，暂时不能发起新的规划；额度重置后自动恢复',
    budgetStatus,
  } as const

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${drawerOpen && !isWide ? 'lg:pr-[440px]' : ''}`}>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1560px] px-4 py-4 xl:px-6 xl:py-5">
          <div className="grid items-start gap-4 lg:grid-cols-[264px_minmax(0,1fr)] xl:grid-cols-[264px_minmax(0,1fr)_336px]">
            {/* ── 左栏：行程条件 ── */}
            <aside className="min-w-0 lg:sticky lg:top-0">
              <ConditionsPanel
                open={conditionsOpen || !itinerary}
                hasItinerary={!!itinerary}
                onToggle={() => setConditionsOpen((v) => !v)}
              >
                {conditionsOpen || !itinerary ? (
                  <TripForm
                    destination={destination} onDestination={setDestination}
                    days={days} onDays={setDays}
                    partySize={partySize} onPartySize={setPartySize}
                    budget={budget} onBudget={setBudget}
                    startDate={startDate} onStartDate={setStartDate}
                    pace={pace} onPace={setPace}
                    preferences={preferences} onPreferences={setPreferences}
                    extra={extra} onExtra={setExtra}
                    suggestions={recommendations}
                    loading={loading} budgetBlocked={budgetBlocked}
                    hasItinerary={!!itinerary}
                    onSubmit={submit}
                  />
                ) : (
                  <TripSummary
                    destination={destination} days={days} partySize={partySize}
                    budget={budget} startDate={startDate} pace={pace}
                    preferences={preferences} extra={extra}
                    itineraryDays={itinerary ? itinerary.days.length : null}
                    onAdjust={() => setConditionsOpen(true)}
                  />
                )}
              </ConditionsPanel>
            </aside>

            {/* ── 中栏：行程（唯一结果主视图） ── */}
            <main className="min-w-0 space-y-4">
              {!itinerary && !loading && (
                <header className="rounded-2xl border border-[#dae7e5] bg-white px-5 py-4 shadow-card">
                  <h1 className="text-base font-semibold text-[#183037]">旅游行程规划</h1>
                  <p className="mt-1 text-xs leading-relaxed text-[#5c7074]">
                    填多少算多少，缺的会追问。行程由确定性规则基于真实路况与天气排出，
                    景点与票价为参考值，出发前请核实。
                  </p>
                </header>
              )}

              {error && (
                <div className="flex items-start gap-2 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
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
                <div className="whitespace-pre-wrap rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                  {planState.notice}
                </div>
              ) : (
                <>
                  {/* 灵感卡：目的地为空时的零门槛起点（点卡片即填目的地） */}
                  {!hasDestination && inspiration.length > 0 && (
                    <section aria-label="热门目的地灵感">
                      <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-[#183037]">
                        <MapPin size={14} className="text-[#087b73]" aria-hidden />
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
                                shadow-card transition-shadow hover:shadow-input focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#087b73]"
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
                  <EmptyHint hasDestination={hasDestination} />
                </>
              )}
            </main>

            {/* ── 右栏：旅行助手（宽屏常驻；中窄屏走抽屉，见下方 fixed 渲染） ── */}
            {isWide && (
              <aside className="min-w-0 xl:sticky xl:top-0 xl:h-[calc(100vh-2.5rem)] xl:min-h-[460px]">
                <TravelChatDrawer mode="panel" planVersion={itinerary?.plan_version} {...assistantProps} />
              </aside>
            )}
          </div>
        </div>
      </div>

      {/* 中窄屏：「对话改行程」边缘按钮 + 展开抽屉 */}
      {!isWide && (
        <TravelChatDrawer
          mode="drawer"
          open={drawerOpen}
          onOpen={() => setDrawerOpen(true)}
          onClose={() => setDrawerOpen(false)}
          planVersion={itinerary?.plan_version}
          {...assistantProps}
        />
      )}
    </div>
  )
}

// ── 左栏：行程条件容器（表单 ⇄ 摘要） ────────────────────────

function ConditionsPanel({
  open, hasItinerary, onToggle, children,
}: {
  open: boolean
  hasItinerary: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card" aria-label="行程条件">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[#183037]">行程条件</h2>
        {hasItinerary && !open && (
          <button
            type="button"
            onClick={onToggle}
            className="cursor-pointer rounded-lg border border-[#dae7e5] px-2.5 py-1 text-xs
              text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037]"
          >
            调整条件
          </button>
        )}
      </div>
      {children}
    </section>
  )
}

// ── 左栏：需求表单 ───────────────────────────────────────────

interface TripFormProps {
  destination: string; onDestination: (v: string) => void
  days: string; onDays: (v: string) => void
  partySize: string; onPartySize: (v: string) => void
  budget: string; onBudget: (v: string) => void
  startDate: string; onStartDate: (v: string) => void
  pace: string; onPace: (v: string) => void
  preferences: string[]; onPreferences: (v: string[]) => void
  extra: string; onExtra: (v: string) => void
  suggestions: Recommendation[]
  loading: boolean
  budgetBlocked: boolean
  hasItinerary: boolean
  onSubmit: () => void
}

function TripForm(p: TripFormProps) {
  return (
    <div className="space-y-3">
      <DestinationField value={p.destination} onChange={p.onDestination} suggestions={p.suggestions} />

      <div className="grid grid-cols-2 gap-2">
        <StepperField label="天数" value={p.days} onChange={p.onDays} min={1} max={10} />
        <StepperField label="人数" value={p.partySize} onChange={p.onPartySize} min={1} max={20} />
      </div>

      <label className={FIELD_LABEL_CLS}>
        <span className="inline-flex items-center gap-1">
          <CalendarDays size={12} aria-hidden />
          出发日期
        </span>
        <input type="date" value={p.startDate} onChange={(e) => p.onStartDate(e.target.value)} className={TP_INPUT_CLS} />
      </label>

      <label className={FIELD_LABEL_CLS}>
        <span>预算（元，选填）</span>
        <span className="relative mt-1 block">
          <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-xs text-[#9db4b1]" aria-hidden>¥</span>
          <input
            type="number" min={0} value={p.budget}
            onChange={(e) => p.onBudget(e.target.value)}
            placeholder="如 2000"
            className={TP_INPUT_CLS.replace('px-2.5', 'pl-6 pr-2.5')}
          />
        </span>
      </label>

      <label className={FIELD_LABEL_CLS}>
        <span>节奏</span>
        <select value={p.pace} onChange={(e) => p.onPace(e.target.value)} className={TP_INPUT_CLS}>
          {PACE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      <div>
        <span className={FIELD_LABEL_CLS}>偏好</span>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {PREFERENCE_OPTIONS.map((tag) => {
            const active = p.preferences.includes(tag)
            return (
              <button
                key={tag}
                type="button"
                onClick={() => p.onPreferences(
                  p.preferences.includes(tag)
                    ? p.preferences.filter((t) => t !== tag)
                    : [...p.preferences, tag])}
                aria-pressed={active}
                className={`cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors ${
                  active
                    ? 'border-[#087b73] bg-[#087b73] text-white'
                    : 'border-[#dae7e5] bg-white text-[#5c7074] hover:border-[#087b73]/40 hover:text-[#087b73]'
                }`}
              >
                {tag}
              </button>
            )
          })}
        </div>
      </div>

      <label className={FIELD_LABEL_CLS}>
        <span>其他要求</span>
        <input
          value={p.extra}
          onChange={(e) => p.onExtra(e.target.value)}
          placeholder="如：必去三坊七巷、不吃辣"
          className={TP_INPUT_CLS}
        />
      </label>

      <button
        type="button"
        onClick={p.onSubmit}
        disabled={p.loading || p.budgetBlocked}
        className="flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-lg bg-[#087b73] px-4 py-2 text-sm font-medium
          text-white transition-colors hover:bg-[#06655f]
          disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Sparkles size={14} />
        {p.loading ? '规划中…' : p.hasItinerary ? '重新生成（新行程）' : '生成行程'}
      </button>

      <p className="text-[11px] leading-relaxed text-[#5c7074]">
        「生成行程」会开一份新行程；想改当前这份，用
        {p.hasItinerary ? '右侧「旅行助手」' : '生成后的右侧「旅行助手」'}
        ——一句话就行（改成 3 天 / 节奏轻松点 / 不吃辣）。
      </p>
    </div>
  )
}

// ── 左栏：生成后的条件摘要（方案 v2 §2.1「折叠为摘要」） ─────

function TripSummary(props: {
  destination: string; days: string; partySize: string; budget: string
  startDate: string; pace: string; preferences: string[]; extra: string
  /** 当前行程的实际天数（改单后会变，优先于表单值展示，避免摘要与结果打架） */
  itineraryDays: number | null
  onAdjust: () => void
}) {
  const prefText = props.preferences.length ? props.preferences.join('、') : '未选'
  const tripLabel = props.itineraryDays != null
    ? `${props.itineraryDays} 天（当前行程）`
    : `${props.days || '?'} 天`
  const rows: Array<[string, string]> = [
    ['目的地', props.destination.trim() || '未填'],
    ['行程', tripLabel],
    ['出发', props.startDate || '未定（相关事项按待核实处理）'],
    ['同行', `${props.partySize || '?'} 人`],
    ['预算', props.budget ? `总额 ¥${props.budget}` : '未设上限'],
    ['节奏', `节奏${PACE_LABEL[props.pace] ?? '适中'}`],
    ['偏好', prefText],
  ]
  if (props.extra.trim()) rows.push(['其他要求', props.extra.trim()])
  return (
    <div>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k} className="border-b border-[#dae7e5] py-2 last:border-b-0">
            <dt className="text-[11px] text-[#5c7074]">{k}</dt>
            <dd className="mt-0.5 break-words text-[13px] font-medium text-[#183037]">{v}</dd>
          </div>
        ))}
      </dl>
      <button
        type="button"
        onClick={props.onAdjust}
        className="mt-3 w-full cursor-pointer rounded-lg border border-[#dae7e5] px-3 py-1.5 text-xs
          text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037]"
      >
        调整条件
      </button>
      <p className="mt-2 text-[11px] leading-relaxed text-[#5c7074]">
        调整后点「重新生成」会开一份新行程；小改动（改天数 / 换节奏）更推荐直接在右侧「旅行助手」说一句。
      </p>
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
    <div ref={boxRef} className="relative">
      <label className={FIELD_LABEL_CLS} htmlFor="travel-destination">
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
          className={TP_INPUT_CLS}
        />
      </label>
      {open && filtered.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          aria-label="目的地候选"
          className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-[#dae7e5] bg-white py-1 shadow-input"
        >
          {filtered.map((r, i) => (
            <li key={r.city} role="option" aria-selected={i === activeIdx}>
              <button
                type="button"
                onMouseDown={(e) => { e.preventDefault(); pick(r.city) }}
                onMouseEnter={() => setActiveIdx(i)}
                className={`flex w-full cursor-pointer flex-col px-3 py-1.5 text-left transition-colors ${
                  i === activeIdx ? 'bg-[#087b73]/10' : ''
                }`}
              >
                <span className="text-sm font-medium text-[#183037]">{r.city}</span>
                {r.highlights.length > 0 && (
                  <span className="truncate text-[11px] text-[#5c7074]">{r.highlights.slice(0, 3).join(' · ')}</span>
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
    <label className={FIELD_LABEL_CLS}>
      <span>{label}</span>
      <span className="mt-1 flex items-stretch overflow-hidden rounded-lg border border-[#dae7e5] bg-white transition-colors focus-within:border-[#087b73]/60">
        <button
          type="button"
          onClick={() => step(-1)}
          aria-label={`减少${label}`}
          className="w-8 cursor-pointer text-[#9db4b1] transition-colors hover:bg-[#f5faf9] hover:text-[#183037]"
        >
          <Minus size={13} className="mx-auto" aria-hidden />
        </button>
        <input
          type="number" min={min} max={max} value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full min-w-0 bg-transparent px-1 py-1.5 text-center text-sm text-[#183037] outline-none
            [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
        />
        <button
          type="button"
          onClick={() => step(1)}
          aria-label={`增加${label}`}
          className="w-8 cursor-pointer text-[#9db4b1] transition-colors hover:bg-[#f5faf9] hover:text-[#183037]"
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
      className="animate-fade-in rounded-2xl border border-[#dae7e5] bg-white p-5 shadow-card"
      aria-live="polite"
      aria-label="正在生成行程"
    >
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#087b73]/10">
          <Loader2 size={17} className="animate-spin text-[#087b73]" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-[#183037]">正在生成行程</p>
          <p className="mt-0.5 text-xs text-[#5c7074]">确定性规则规划，通常数秒内完成</p>
        </div>
        <span className="shrink-0 font-mono text-sm tabular-nums text-[#5c7074]">{elapsed}s</span>
      </div>

      <ol className="mt-4 flex flex-wrap gap-1.5">
        {PLAN_STAGES.map((label, i) => (
          <li
            key={label}
            aria-current={i === stage ? 'step' : undefined}
            className={`flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] transition-colors duration-300 ${
              i === stage
                ? 'border-[#087b73]/30 bg-[#087b73]/10 font-medium text-[#087b73]'
                : 'border-[#dae7e5] bg-[#f5faf9] text-[#5c7074]'
            }`}
          >
            {i === stage && <span aria-hidden>✓</span>}
            {label}
          </li>
        ))}
      </ol>
    </section>
  )
}

function EmptyHint({ hasDestination }: { hasDestination: boolean }) {
  return (
    <div className="rounded-2xl border border-dashed border-[#c9dcd7] bg-[#f5faf9] px-6 py-8 text-center">
      <p className="text-sm text-[#5c7074]">
        {hasDestination ? '点左侧「生成行程」，行程会在这里逐日铺开' : '在左侧填好信息，或点一张灵感卡开始'}
      </p>
      <p className="mt-1.5 text-xs text-[#8fa5a3]">
        生成后可以在右侧「旅行助手」用一句话调整（改成 3 天、节奏轻松点、预算压到 2000）
      </p>
    </div>
  )
}

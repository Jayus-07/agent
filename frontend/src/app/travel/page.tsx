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
 * 当前结构化行程走旅游域 REST：逐日时间轴/地图打点/费用拆分/ICS 导出都要
 * 完整 itinerary JSON；过程面板只展示真实请求生命周期，不伪造百分比。
 *
 * 额度圆圈与 /agent 用同一个组件（BudgetRing，挂在助手输入区左下）：
 * 额度是**账号级**的，两处显示不一致会让人以为是两个额度；且额度打满时
 * 两处都要禁输入（useBudgetStatus 上抛 blocked）。
 *
 * 实施边界：只动展示层——所有「智能感」（灵感卡、目的地联想、出发倒计时、
 * 费用占比、按天路线投影）都从既有接口数据派生（travelDisplay.ts），不新增
 * 后端契约、不引依赖；后端没有分步进度 API，生成中只显示等待状态。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, ArrowRight, CalendarDays, CheckCircle2, Hotel, LocateFixed, Loader2, MapPin, Minus, Plus, Sparkles, TrainFront, Utensils } from 'lucide-react'
import { useBudgetStatus } from '@/hooks/useBudgetStatus'
import ItineraryView from '@/components/travel/ItineraryView'
import TravelChatDrawer from '@/components/travel/TravelChatDrawer'
import {
  EMPTY_PLAN_STATE,
  applyPlanResponse,
  clearPendingPlan,
  composePlanMessage,
  readConversationId,
  readPlanState,
  rotateConversationId,
  persistPlanState,
  previewPlanResponse,
  PACE_LABEL,
  type PlanState,
} from '@/components/travel/planState'
import {
  initialTravelProcess,
  reduceTravelStreamEvent,
  type TravelProcessState,
} from '@/components/travel/travelRuntime'
import { cityHue, TRAVEL_STAGE_LABELS, TRAVEL_STAGE_ORDER, TRAVEL_TOOL_LABELS } from '@/components/travel/travelDisplay'
import {
  fetchItineraryIcs,
  fetchTravelRecommendations,
  reverseGeocodeTravelOrigin,
  sendTravelFeedback,
  streamTravelPlan,
  type TravelStreamEvent,
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

const EXAMPLE_SCENARIOS = [
  {
    id: 'live-city-weekend',
    eyebrow: '实时检索示例',
    title: '福州美食周末',
    description: '查真实美食、酒店，再给我一份 2 天游玩安排',
    message: '去福州玩2天，2个人，预算2000元，请查美食和查酒店，偏好人文，节奏轻松点',
    accent: 'from-[#0d5d55] to-[#087b73]',
  },
  {
    id: 'live-train-weekend',
    eyebrow: '车票查询示例',
    title: '福州 → 厦门',
    description: '先查 2026-10-03 的真实高铁，再安排 2 天路线',
    message: '从福州出发去厦门，2026-10-03玩2天，2个人，节奏适中，请查高铁票，喜欢美食',
    accent: 'from-[#243c62] to-[#315c7d]',
  },
  {
    id: 'slow-history',
    eyebrow: '轻松规划示例',
    title: '泉州慢游',
    description: '3 天逛古迹，不绕路，留出喝茶和休息时间',
    message: '去泉州玩3天，2个人，想看古迹和人文，节奏轻松，不要排得太满',
    accent: 'from-[#9a5d39] to-[#bd7b4c]',
  },
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
  const [origin, setOrigin] = useState('')
  const [destination, setDestination] = useState('')
  const [days, setDays] = useState('2')
  const [partySize, setPartySize] = useState('2')
  const [budget, setBudget] = useState('')
  const [startDate, setStartDate] = useState('')
  const [pace, setPace] = useState('')
  const [preferences, setPreferences] = useState<string[]>([])
  const [extra, setExtra] = useState('')
  // 首屏引导的一句话输入：直接交给后端 slot_filler 解析（缺的信息由域内追问补齐）
  const [quickIdea, setQuickIdea] = useState('')

  // ── 结果与线程 ──
  const [conversationId, setConversationId] = useState(readConversationId)
  const [planState, setPlanState] = useState<PlanState>(readPlanState)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [feedbackSent, setFeedbackSent] = useState<'' | 'positive' | 'negative'>('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  // 当前选中天：行程视图与右侧助手共享（点日卡片 → 助手知道「正在看第几天」）
  const [activeDay, setActiveDay] = useState(1)
  // 出行程后左栏折叠为摘要；「调整条件」再展开
  const [conditionsOpen, setConditionsOpen] = useState(false)
  const [locationState, setLocationState] = useState<'idle' | 'loading' | 'success' | 'denied' | 'error'>('idle')
  const [locationHint, setLocationHint] = useState('')
  const [budgetBlocked, setBudgetBlocked] = useState(false)
  const [travelProcess, setTravelProcess] = useState<TravelProcessState | null>(null)
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

  useEffect(() => {
    persistPlanState(planState)
  }, [planState])

  const itinerary = planState.plan?.itinerary ?? null

  const handleTravelEvent = useCallback((event: TravelStreamEvent) => {
    const runId = event.data.run_id
    if (typeof runId !== 'string' || !runId) return
    setTravelProcess((previous) => {
      const current = previous?.runId === runId
        ? previous
        : initialTravelProcess(runId)
      return reduceTravelStreamEvent(current, event)
    })
  }, [])

  // ── 表单提交：开一份新行程 ──
  const submit = useCallback(async (messageOverride?: string) => {
    if (loading || budgetBlocked) return
    const message = messageOverride ?? composePlanMessage({
      origin, destination, days, startDate, partySize, budget, pace, preferences, extra,
    })
    // 新行程 = 新线程。旧行程属于旧线程，留着会让「改单」打到错误的行程上，
    // 因此立刻清空（配合下方的生成中占位，不会显得东西凭空消失）。
    const cid = rotateConversationId()
    setConversationId(cid)
    setPlanState(EMPTY_PLAN_STATE)
    setFeedbackSent('')
    setError('')
    setTravelProcess(null)
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      let data: PlanResponse | null = null
      for await (const event of streamTravelPlan(message, cid, { signal: controller.signal })) {
        handleTravelEvent(event)
        if (event.event === 'error') {
          throw new Error(typeof event.data.message === 'string'
            ? event.data.message : '旅游规划执行失败')
        }
        if (event.event === 'done' && event.data.result && typeof event.data.result === 'object') {
          data = event.data.result as PlanResponse
        }
      }
      if (!data) throw new Error('旅游规划流未返回结构化结果')
      if (data.status === 'failed') {
        throw new Error(data.final_answer || '旅游规划执行失败')
      }
      setPlanState(applyPlanResponse(EMPTY_PLAN_STATE, data))
      // 出了行程 → 左栏折叠成摘要，把空间让给中栏结果（方案 v2 §2.1）
      if (data.itinerary) {
        setConditionsOpen(false)
        // 新行程默认聚焦第一天（日卡片条 + 当日重点详情）
        setActiveDay(1)
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : '规划请求失败，请稍后再试')
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setLoading(false)
    }
  }, [
    budget, budgetBlocked, days, destination, extra, handleTravelEvent, loading,
    origin, partySize, pace, preferences, startDate,
  ])

  // 一句话起步：复用 submit 的自由文本通道（与示例卡同一条链路）
  const runQuickIdea = useCallback(() => {
    const text = quickIdea.trim()
    if (!text || loading || budgetBlocked) return
    setQuickIdea('')
    void submit(text)
  }, [quickIdea, loading, budgetBlocked, submit])

  const runExample = useCallback((example: typeof EXAMPLE_SCENARIOS[number]) => {
    if (loading || budgetBlocked) return
    if (example.id === 'live-city-weekend') {
      setOrigin('')
      setDestination('福州')
      setDays('2')
      setPartySize('2')
      setBudget('2000')
      setPreferences(['人文'])
      setPace('relaxed')
      setExtra('查美食、查酒店')
      setStartDate(tomorrowIso())
    } else if (example.id === 'live-train-weekend') {
      setOrigin('福州')
      setDestination('厦门')
      setDays('2')
      setPartySize('2')
      setBudget('')
      setPreferences(['美食'])
      setPace('moderate')
      setExtra('查高铁票，节奏适中')
      setStartDate('2026-10-03')
    } else {
      setOrigin('')
      setDestination('泉州')
      setDays('3')
      setPartySize('2')
      setBudget('')
      setPreferences(['人文'])
      setPace('relaxed')
      setExtra('不绕路，留出喝茶和休息时间')
      setStartDate(tomorrowIso())
    }
    void submit(example.message)
  }, [budgetBlocked, loading, submit])

  const useCurrentLocation = useCallback(() => {
    if (!navigator.geolocation) {
      setLocationState('error')
      setLocationHint('当前浏览器不支持定位，请手填出发城市')
      return
    }
    setLocationState('loading')
    setLocationHint('仅用于换算出发城市，不会保存精确坐标')
    navigator.geolocation.getCurrentPosition(
      (position) => {
        reverseGeocodeTravelOrigin(position.coords.latitude, position.coords.longitude)
          .then((result) => {
            if (!result) {
              setLocationState('error')
              setLocationHint('没有查到城市，请手填出发城市')
              return
            }
            setOrigin(result.city)
            setLocationState('success')
            setLocationHint(`已定位到${result.label}`)
          })
          .catch(() => {
            setLocationState('error')
            setLocationHint('城市反查失败，请手填出发城市')
          })
      },
      (error) => {
        setLocationState(error.code === error.PERMISSION_DENIED ? 'denied' : 'error')
        setLocationHint(error.code === error.PERMISSION_DENIED ? '你拒绝了定位权限，可直接手填出发城市' : '定位暂时不可用，可直接手填出发城市')
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    )
  }, [])

  // ── 助手回复：在当前行程上改 ──
  const handleAssistantResponse = useCallback((data: PlanResponse) => {
    setPlanState((prev) => applyPlanResponse(prev, data))
    const brief = data.itinerary?.brief
    if (brief) {
      setDestination(brief.destination || '')
      setOrigin(brief.origin || '')
      setDays(brief.days ? String(brief.days) : '')
      setPartySize(brief.party_size ? String(brief.party_size) : '')
      setBudget(brief.budget_cny != null ? String(brief.budget_cny) : '')
      setStartDate(brief.start_date || '')
      setPace(brief.pace || '')
      setPreferences(brief.preferences ?? [])
    }
  }, [])

  const handleAssistantDraft = useCallback((data: PlanResponse) => {
    setPlanState((prev) => previewPlanResponse(prev, data))
  }, [])

  const handleDiscardPending = useCallback(() => {
    setPlanState((prev) => clearPendingPlan(prev))
  }, [])

  const startNewTrip = useCallback(() => {
    abortRef.current?.abort()
    const cid = rotateConversationId()
    setConversationId(cid)
    setPlanState(EMPTY_PLAN_STATE)
    setOrigin('')
    setDestination('')
    setDays('2')
    setPartySize('2')
    setBudget('')
    setPace('')
    setPreferences([])
    setExtra('')
    setStartDate(tomorrowIso())
    setLocationState('idle')
    setLocationHint('')
    setFeedbackSent('')
    setError('')
    setTravelProcess(null)
    setActiveDay(1)
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
    brief: itinerary?.brief ?? null,
    itinerary,
    /** 助手头部「正在看第几天」与快捷话术共用（用户点日卡片即联动） */
    activeDay: itinerary ? activeDay : null,
    onResponse: handleAssistantResponse,
    pendingResponse: planState.pending,
    onDraft: handleAssistantDraft,
    onDiscardPending: handleDiscardPending,
    processState: travelProcess,
    onProcessEvent: handleTravelEvent,
    onStartNewTrip: startNewTrip,
    disabled: budgetBlocked,
    disabledHint: '本月额度已用尽，暂时不能发起新的规划；额度重置后自动恢复',
    budgetStatus,
  } as const

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${drawerOpen && !isWide ? 'lg:pr-[440px]' : ''}`}>
      {/* 一屏布局：lg+ 外层不滚，三栏各自内滚（用户反馈「不要整页滑到很下面」）；
          窄屏退回整页文档流滚动 */}
      <div className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
        <div className="mx-auto max-w-[1560px] px-4 py-4 xl:px-6 xl:py-5 lg:h-full">
          <div className={`grid items-stretch gap-4 lg:h-full ${itinerary
            ? 'lg:grid-cols-[minmax(0,1fr)] xl:grid-cols-[minmax(0,1fr)_336px]'
            : 'lg:grid-cols-[264px_minmax(0,1fr)] xl:grid-cols-[264px_minmax(0,1fr)_336px]'}`}>
            {/* ── 左栏：行程条件（与右栏等高，内容多时栏内滚动） ── */}
            {!itinerary && <aside className="min-w-0 min-h-0 lg:overflow-y-auto lg:pr-0.5">
              <ConditionsPanel
                open={conditionsOpen || !itinerary}
                hasItinerary={!!itinerary}
                onToggle={() => setConditionsOpen((v) => !v)}
              >
                {conditionsOpen || !itinerary ? (
                  loading ? (
                    /* 生成中：左栏收起为「条件已锁定」摘要（设计稿②），把注意力让给过程看板 */
                    <div
                      className="rounded-2xl border border-[#dae7e5] bg-white p-5 shadow-card"
                      aria-label="行程条件（生成中已锁定）"
                    >
                      <div className="flex items-center gap-2">
                        <Loader2 size={14} className="animate-spin text-[#087b73]" aria-hidden />
                        <h2 className="text-sm font-semibold text-[#183037]">行程条件（已锁定）</h2>
                      </div>
                      <dl className="mt-3 space-y-2 text-xs">
                        {[
                          ['目的地', destination || '未填'],
                          ['出发', startDate || '未定'],
                          ['行程', days ? `${days} 天` : '未填'],
                          ['同行', partySize ? `${partySize} 人` : '未填'],
                          ['预算', budget ? `¥${budget}` : '不限'],
                          ['节奏', pace || '适中'],
                        ].map(([label, value]) => (
                          <div key={label} className="flex items-center justify-between gap-3">
                            <dt className="shrink-0 text-[#5c7074]">{label}</dt>
                            <dd className="min-w-0 truncate text-right font-medium text-[#183037]">{value}</dd>
                          </div>
                        ))}
                      </dl>
                      <p className="mt-3 border-t border-[#e8f1ef] pt-2 text-[10px] text-[#8fa5a3]">
                        生成期间条件不可改；完成后可在右侧「旅行助手」用一句话调整
                      </p>
                    </div>
                  ) : (
                    <TripForm
                    origin={origin} onOrigin={setOrigin}
                    locationState={locationState} locationHint={locationHint} onUseCurrentLocation={useCurrentLocation}
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
                  )
                ) : (
                  <TripSummary
                    origin={origin} destination={destination} days={days} partySize={partySize}
                    budget={budget} startDate={startDate} pace={pace}
                    preferences={preferences} extra={extra}
                    itineraryDays={null}
                    onAdjust={() => setConditionsOpen(true)}
                  />
                )}
              </ConditionsPanel>
            </aside>}

            {/* ── 中栏：行程（唯一结果主视图，栏内滚动） ── */}
            <main className="min-w-0 min-h-0 space-y-4 lg:overflow-y-auto lg:pr-0.5">
              {itinerary && (
                <TripSummary
                  origin={origin} destination={destination} days={days} partySize={partySize}
                  budget={budget} startDate={startDate} pace={pace}
                  preferences={preferences} extra={extra}
                  itineraryDays={itinerary.days.length}
                  onAdjust={() => setConditionsOpen(true)}
                  compact
                />
              )}
              {!itinerary && !loading && !planState.notice && (
                <HeroGuide
                  value={quickIdea}
                  onValueChange={setQuickIdea}
                  onSubmitIdea={runQuickIdea}
                  onRun={runExample}
                  disabled={loading || budgetBlocked}
                />
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
                <>
                  {planState.pending?.itinerary && (
                    <section
                      aria-label="行程预览状态"
                      role="status"
                      aria-live="polite"
                      className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                          预览中，尚未应用
                        </span>
                        <span className="text-xs text-amber-900">草案 v{planState.pending.itinerary.plan_version}</span>
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-amber-900">
                        当前行程仍是 v{itinerary.plan_version}；请在右侧选择「应用新行程」或「保留原行程」。
                      </p>
                    </section>
                  )}
                  <ItineraryView
                  itinerary={itinerary}
                  notice={planState.notice}
                  finalAnswer={planState.plan?.final_answer}
                  exporting={exporting}
                  feedbackSent={feedbackSent}
                  conversationId={conversationId}
                  planStatus={planState.plan?.plan_status}
                  selectedDay={activeDay}
                  onSelectedDayChange={setActiveDay}
                  onExportIcs={downloadIcs}
                  onFeedback={sendFeedback}
                  onPlanResponse={handleAssistantResponse}
                  />
                </>
              ) : loading ? (
                <GeneratingCard processState={travelProcess} expectedDays={parseInt(days, 10) || 0} />
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
                </>
              )}
            </main>

            {/* ── 右栏：旅行助手（宽屏常驻、与左栏等高；中窄屏走抽屉，见下方 fixed 渲染） ── */}
            {isWide && (
              <aside className="min-w-0 min-h-0">
                <TravelChatDrawer mode="panel" planVersion={itinerary?.plan_version} {...assistantProps} />
              </aside>
            )}
          </div>
        </div>
      </div>

      {itinerary && conditionsOpen && (
        <div
          className="fixed inset-0 z-40 bg-[#183037]/20"
          role="presentation"
          onClick={() => setConditionsOpen(false)}
        >
          <aside
            className="h-full w-[360px] max-w-[92vw] overflow-y-auto border-r border-[#dae7e5] bg-white p-4 shadow-2xl"
            aria-label="调整行程条件"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-[#183037]">调整行程条件</h2>
              <button
                type="button"
                onClick={() => setConditionsOpen(false)}
                className="rounded-lg px-2 py-1 text-xs text-[#5c7074] hover:bg-[#f5faf9]"
              >
                关闭
              </button>
            </div>
            <TripForm
              origin={origin} onOrigin={setOrigin}
              locationState={locationState} locationHint={locationHint} onUseCurrentLocation={useCurrentLocation}
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
              hasItinerary
              onSubmit={submit}
            />
          </aside>
        </div>
      )}

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
    // h-full：与右栏等高（grid items-stretch 下吃满列高）；内容顶部对齐
    <section className="flex h-full flex-col rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card" aria-label="行程条件">
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
  origin: string; onOrigin: (v: string) => void
  locationState: 'idle' | 'loading' | 'success' | 'denied' | 'error'
  locationHint: string
  onUseCurrentLocation: () => void
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
      <label className={FIELD_LABEL_CLS}>
        <span className="inline-flex items-center gap-1">
          <MapPin size={12} aria-hidden />
          出发城市
        </span>
        <div className="relative mt-1">
          <input
            value={p.origin}
            onChange={(e) => p.onOrigin(e.target.value)}
            placeholder="如：福州（可选）"
            className={`${TP_INPUT_CLS} pr-24`}
          />
          <button
            type="button"
            onClick={p.onUseCurrentLocation}
            disabled={p.locationState === 'loading'}
            className="absolute right-1.5 top-1/2 inline-flex -translate-y-1/2 items-center gap-1 rounded-md px-2 py-1 text-[10px] text-[#087b73] hover:bg-[#e9f3f0] disabled:opacity-50"
          >
            <LocateFixed size={11} /> {p.locationState === 'loading' ? '定位中' : '使用定位'}
          </button>
        </div>
        {p.locationHint && (
          <span className={`mt-1 block text-[10px] ${p.locationState === 'success' ? 'text-[#087b73]' : p.locationState === 'denied' || p.locationState === 'error' ? 'text-amber-700' : 'text-[#7a8e8b]'}`}>
            {p.locationHint}
          </span>
        )}
      </label>

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
        // 不把 React MouseEvent 传进 submit；submit 的参数只接受文本消息覆盖值。
        onClick={() => p.onSubmit()}
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
  origin: string; destination: string; days: string; partySize: string; budget: string
  startDate: string; pace: string; preferences: string[]; extra: string
  /** 当前行程的实际天数（改单后会变，优先于表单值展示，避免摘要与结果打架） */
  itineraryDays: number | null
  onAdjust: () => void
  compact?: boolean
}) {
  const prefText = props.preferences.length ? props.preferences.join('、') : '未选'
  const tripLabel = props.itineraryDays != null
    ? `${props.itineraryDays} 天（当前行程）`
    : `${props.days || '?'} 天`
  const rows: Array<[string, string]> = [
    ['出发城市', props.origin.trim() || '未填（可手填或使用定位）'],
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
    <section className={props.compact
      ? 'rounded-2xl border border-[#dae7e5] bg-white px-4 py-3 shadow-card'
      : ''}>
      <div className="flex items-start justify-between gap-3">
        <dl className="flex min-w-0 flex-wrap gap-x-4 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className={props.compact ? 'min-w-[100px]' : 'border-b border-[#dae7e5] py-2 last:border-b-0'}>
            <dt className="text-[11px] text-[#5c7074]">{k}</dt>
            <dd className="mt-0.5 break-words text-[13px] font-medium text-[#183037]">{v}</dd>
          </div>
        ))}
        </dl>
        <button
          type="button"
          onClick={props.onAdjust}
          className="shrink-0 cursor-pointer rounded-lg border border-[#dae7e5] px-3 py-1.5 text-xs
            text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037]"
        >
          修改条件
        </button>
      </div>
      {!props.compact && (
        <p className="mt-2 text-[11px] leading-relaxed text-[#5c7074]">
          调整后点「重新生成」会开一份新行程；小改动（改天数 / 换节奏）更推荐直接在右侧「旅行助手」说一句。
        </p>
      )}
    </section>
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

// ── 生成中流程卡（动态进度：阶段 stepper + Tool 时间线 + 实时检索 + 行程骨架） ──

type StepStatus = 'pending' | 'running' | 'completed' | 'failed'

function GeneratingCard({
  processState,
  expectedDays,
}: {
  processState: TravelProcessState | null
  expectedDays: number
}) {
  // 「规划过程」默认收起（设计稿②）：结果卡（商户/车票/攻略）始终可见，
  // 想看可视化过程（需求理解 + 全量 Tool 时间线）再展开；失败时强制
  // 展开——失败原因列在过程里，不展开就没人看得见。
  const [processOpen, setProcessOpen] = useState(false)
  const failed = processState?.status === 'error'
  const stageMap = processState?.stages ?? {}
  // stepper = 固定流水线顺序 × 后端真实事件状态；未收到事件的阶段显示
  // 「等待中」——表示还没执行到，不是已完成（不伪造进度）。
  const stepRows: Array<{ key: string; label: string; status: StepStatus }> = TRAVEL_STAGE_ORDER.map((key) => ({
    key,
    label: TRAVEL_STAGE_LABELS[key] ?? key,
    status: stageMap[key]?.status ?? 'pending',
  }))
  // 防御：后端出现了未知 stage key（版本差）时追加在尾部，不静默丢弃
  for (const key of Object.keys(stageMap)) {
    if (!TRAVEL_STAGE_ORDER.includes(key as typeof TRAVEL_STAGE_ORDER[number])) {
      stepRows.push({ key, label: key, status: stageMap[key]?.status ?? 'pending' })
    }
  }
  const startedCount = stepRows.filter((s) => s.status !== 'pending').length
  const doneCount = stepRows.filter((s) => s.status === 'completed').length

  return (
    <section
      className="animate-fade-in space-y-4"
      aria-live="polite"
      aria-label="正在生成行程"
    >
      {/* 头部：状态 + 汇总 */}
      <div className="rounded-2xl border border-[#dae7e5] bg-white p-5 shadow-card">
        <div className="flex items-center gap-3">
          <span className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#087b73]/10">
            {failed
              ? <AlertCircle size={18} className="text-red-500" aria-hidden />
              : <Loader2 size={18} className="animate-spin text-[#087b73]" aria-hidden />}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-base font-semibold text-[#183037]">
              {failed ? '旅游规划失败' : '正在生成你的行程…'}
            </p>
            <p className="mt-0.5 text-xs text-[#5c7074]">
              {failed
                ? '失败原因已在下方过程里列出，未用假数据补齐'
                : `需求分析、Tool 调用、行程卡片都会在这里实时出现（已推进 ${doneCount}/${stepRows.length} 步）`}
            </p>
          </div>
          {!failed && processState && (
            <div className="hidden shrink-0 text-right sm:block" aria-hidden>
              <p className="text-xl font-bold tabular-nums text-[#087b73]">
                {stepRows.length > 0 ? Math.round((doneCount / stepRows.length) * 100) : 0}%
              </p>
              <p className="text-[10px] text-[#8fa5a3]">按真实事件计</p>
            </div>
          )}
          {/* 规划过程展开按钮：默认收起，展开看需求理解 + 全量 Tool 时间线 */}
          {(processState?.tools.length ?? 0) > 0 && (
            <button
              type="button"
              onClick={() => setProcessOpen((v) => !v)}
              aria-expanded={processOpen || failed}
              className={`ml-auto shrink-0 cursor-pointer rounded-lg border px-2.5 py-1.5 text-xs transition-colors sm:ml-0 ${
                processOpen || failed
                  ? 'border-[#087b73]/40 bg-[#087b73]/[0.07] text-[#087b73]'
                  : 'border-[#dae7e5] text-[#5c7074] hover:border-[#087b73]/40 hover:text-[#183037]'
              }`}
            >
              规划过程 · {processState?.tools.length ?? 0} 次调用 {processOpen || failed ? '▴' : '▾'}
            </button>
          )}
        </div>

        {/* 阶段 stepper：真实事件驱动，进行中的步骤带 spinner */}
        <ol className="mt-4 grid gap-x-4 gap-y-2 sm:grid-cols-2">
          {stepRows.map((step) => (
            <li
              key={step.key}
              className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition-colors ${
                step.status === 'failed'
                  ? 'border-red-200 bg-red-50 text-red-600'
                  : step.status === 'running'
                    ? 'border-[#087b73]/40 bg-[#087b73]/[0.07] font-medium text-[#087b73]'
                    : step.status === 'completed'
                      ? 'border-[#dae7e5] bg-[#f5faf9] text-[#5c7074]'
                      : 'border-[#e8f1ef] bg-white text-[#a8bab7]'
              }`}
            >
              {step.status === 'failed' ? (
                <AlertCircle size={13} className="shrink-0" aria-hidden />
              ) : step.status === 'running' ? (
                <Loader2 size={13} className="shrink-0 animate-spin" aria-hidden />
              ) : step.status === 'completed' ? (
                <CheckCircle2 size={13} className="shrink-0 text-[#087b73]" aria-hidden />
              ) : (
                <span className="h-[13px] w-[13px] shrink-0 rounded-full border border-current opacity-60" aria-hidden />
              )}
              <span className="min-w-0 flex-1 truncate">{step.label}</span>
              <span className="shrink-0 text-[10px]">
                {step.status === 'failed' ? '失败'
                  : step.status === 'running' ? '进行中'
                  : step.status === 'completed' ? '完成'
                  : '等待中'}
              </span>
            </li>
          ))}
        </ol>
      </div>

      {/* 实时检索结果：高德商户 / 12306 车票 / 知乎攻略的 tool.result preview
          逐条点亮——这是用户要「能看到结果」的部分，默认可见不收进展开区 */}
      {processState && <LiveSearchBoard processState={processState} />}

      {/* 规划过程（可展开）：需求理解 + 全量 Tool 时间线；失败时强制展开 */}
      {(processOpen || failed) && processState && (
        <div className="animate-fade-in space-y-4" aria-label="规划过程详情">
          {/* 需求理解（requirement.interpreted 事件到达即展示） */}
          {processState.requirement && <RequirementCard requirement={processState.requirement} />}

          {/* Tool 调用时间线（检索类 preview 已在上面展示，这里列全量调用） */}
          {processState.tools.length > 0 && (
            <div className="rounded-2xl border border-[#dae7e5] bg-white px-4 py-3 shadow-card">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-[#183037]">
                <Sparkles size={12} className="text-[#087b73]" aria-hidden />
                Tool 调用时间线
                <span className="font-normal text-[#8fa5a3]">共 {processState.tools.length} 次真实调用</span>
              </p>
              <ol className="mt-2.5 space-y-1.5">
                {processState.tools.map((tool, index) => (
                  <li key={`${tool.tool}-${index}`} className="flex items-center gap-2 text-xs">
                    {tool.status === 'running' ? (
                      <Loader2 size={12} className="shrink-0 animate-spin text-[#087b73]" aria-label="调用中" />
                    ) : tool.status === 'success' ? (
                      <CheckCircle2 size={12} className="shrink-0 text-[#087b73]" aria-label="成功" />
                    ) : (
                      <AlertCircle size={12} className="shrink-0 text-red-500" aria-label="失败" />
                    )}
                    <span className="min-w-0 flex-1 truncate text-[#183037]">
                      {TRAVEL_TOOL_LABELS[tool.tool] ?? tool.tool}
                    </span>
                    <span className={tool.status === 'failed' ? 'shrink-0 text-red-600' : 'shrink-0 text-[#8fa5a3]'}>
                      {tool.status === 'running' ? '调用中…'
                        : tool.status === 'failed' ? `失败${tool.errorType ? ` · ${tool.errorType}` : ''}`
                        : [tool.resultCount != null ? `${tool.resultCount} 条` : '', tool.durationMs != null ? `${(tool.durationMs / 1000).toFixed(1)}s` : '']
                          .filter(Boolean).join(' · ') || '已返回'}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}

      {/* 行程卡片骨架：按用户填的天数占位，生成完成后被真实日卡片替换 */}
      {!failed && expectedDays > 0 && (
        <div>
          <p className="mb-2 text-xs text-[#5c7074]">
            正在排 {expectedDays} 天行程，每天一张卡片
            {startedCount > 0 ? '（下面是等待生成的占位，出稿后自动替换）' : ''}
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {Array.from({ length: Math.min(expectedDays, 10) }, (_, i) => (
              <div key={i} className="rounded-xl border border-[#dae7e5] bg-white p-3" aria-hidden>
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#e2efec] text-[10px] font-bold text-[#8fa5a3]">
                    D{i + 1}
                  </span>
                  <span className="h-3 w-24 animate-pulse rounded bg-[#e2efec]" />
                </div>
                <div className="mt-2.5 h-3 w-full animate-pulse rounded bg-[#eef6f4]" />
                <div className="mt-1.5 h-3 w-3/5 animate-pulse rounded bg-[#eef6f4]" />
                <p className="mt-2 text-[10px] text-[#a8bab7]">第 {i + 1} 天 · 等待生成</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function RequirementCard({
  requirement,
}: {
  requirement: NonNullable<TravelProcessState['requirement']>
}) {
  const brief = requirement.brief
  const rows: Array<[string, string]> = [
    ['目的地', String(brief.destination ?? '')],
    ['出发地', String(brief.origin ?? '未指定')],
    ['天数', brief.days ? `${brief.days} 天` : '待确认'],
    ['同行', brief.party_size ? `${brief.party_size} 人` : '待确认'],
    ['偏好', Array.isArray(brief.preferences) && brief.preferences.length ? brief.preferences.join('、') : '未指定'],
  ]
  return (
    <section className="mt-4 rounded-xl border border-[#b8d8d0] bg-[#f5faf9] px-3.5 py-3" aria-label="需求理解">
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#087b73]/10">
          <CheckCircle2 size={14} className="text-[#087b73]" aria-hidden />
        </span>
        <div>
          <p className="text-xs font-semibold text-[#183037]">我先这样理解你的需求</p>
          <p className="text-[10px] text-[#5c7074]">规则抽取结果，会在生成前展示给你核对</p>
        </div>
      </div>
      <dl className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-1.5">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-[10px] text-[#7a8e8b]">{label}</dt>
            <dd className="truncate text-[11px] font-medium text-[#183037]">{value}</dd>
          </div>
        ))}
      </dl>
      {requirement.assumptions.length > 0 && (
        <p className="mt-2 border-t border-[#dcebe7] pt-2 text-[10px] leading-relaxed text-[#5c7074]">
          {requirement.assumptions.join('；')}
        </p>
      )}
      {requirement.missing.length > 0 && (
        <p className="mt-1 text-[10px] text-amber-700">还缺：{requirement.missing.join('、')}</p>
      )}
    </section>
  )
}

/**
 * HeroGuide — 首屏对话式引导（2026-10-02 三态设计稿①）：
 * 标题 + 一句话输入（走 submit 自由文本通道，后端 slot_filler 解析、缺什么追问什么）
 * + 示例 chips（点即 runExample，与原示例卡同一条真实链路）。
 * 宽屏的「高级选项」= 左栏完整表单（常驻可见），此处只给一句话通道与示例。
 */
function HeroGuide({
  value, onValueChange, onSubmitIdea, onRun, disabled,
}: {
  value: string
  onValueChange: (v: string) => void
  onSubmitIdea: () => void
  onRun: (example: typeof EXAMPLE_SCENARIOS[number]) => void
  disabled: boolean
}) {
  return (
    <section
      className="animate-fade-in rounded-2xl border border-[#c9dcd7] bg-white p-6 shadow-card sm:p-8"
      aria-label="开始规划行程"
    >
      <div className="mx-auto max-w-[640px] text-center">
        <h1 className="text-xl font-semibold text-[#183037] sm:text-2xl">想去哪儿玩？说说你的想法</h1>
        <p className="mt-2 text-xs leading-relaxed text-[#5c7074]">
          一句话描述就行，缺的信息我会追问你。行程基于真实路况与天气排出，
          只有有来源的数据才展示金额，其余明确标为暂无数据。
        </p>
        <form
          className="mt-5 flex items-center gap-2 rounded-xl border border-[#dae7e5] bg-[#f5faf9] p-1.5 transition-colors focus-within:border-[#087b73]/50"
          onSubmit={(event) => {
            event.preventDefault()
            onSubmitIdea()
          }}
        >
          <input
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
            placeholder="例如：周五晚出发去厦门吃两天海鲜，预算 1500"
            disabled={disabled}
            aria-label="一句话描述你的旅行想法"
            className="min-w-0 flex-1 bg-transparent px-3 py-2 text-sm text-[#183037] outline-none placeholder:text-[#8fa5a3] disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={disabled || !value.trim()}
            className="shrink-0 cursor-pointer rounded-lg bg-[#087b73] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#06655f] disabled:cursor-not-allowed disabled:opacity-50"
          >
            生成行程
          </button>
        </form>
        <div className="mt-5 flex items-center gap-3">
          <span className="h-px min-w-0 flex-1 bg-[#dae7e5]" aria-hidden />
          <span className="shrink-0 text-[11px] text-[#5c7074]">不知道怎么说？点一个真实示例</span>
          <span className="h-px min-w-0 flex-1 bg-[#dae7e5]" aria-hidden />
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
          {EXAMPLE_SCENARIOS.map((example) => (
            <button
              key={example.id}
              type="button"
              disabled={disabled}
              onClick={() => onRun(example)}
              title={example.description}
              className="cursor-pointer rounded-full border border-[#087b73]/30 bg-[#e2f0ee] px-3.5 py-1.5 text-[13px] font-medium text-[#087b73] transition-colors hover:border-[#087b73]/60 hover:bg-[#d3e9e5] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {example.title}
            </button>
          ))}
        </div>
        <p className="mt-5 text-[11px] text-[#8fa5a3]">
          想精确控制出发地、日期、天数和预算？用左侧「行程条件」表单逐项填写。
        </p>
      </div>
    </section>
  )
}

function LiveSearchBoard({ processState }: { processState: TravelProcessState }) {
  const toolLabels: Record<string, string> = {
    map_merchant_search_tool: '高德商户搜索',
    travel_train_search_tool: '12306 车票查询',
    zhihu_search_tool: '知乎攻略检索',
  }
  const categoryLabels: Record<string, string> = { food: '美食', hotel: '酒店', train: '车次', guide: '攻略' }
  const items = processState.tools.filter((tool) => (
    tool.tool === 'map_merchant_search_tool'
    || tool.tool === 'travel_train_search_tool'
    || tool.tool === 'zhihu_search_tool'
  ))
  if (!items.length) return null

  return (
    <section className="animate-fade-in rounded-2xl border border-[#ead8bd] bg-[#fffaf2] p-4 shadow-card" aria-live="polite" aria-label="实时检索结果">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-medium tracking-[0.16em] text-[#b36a2d]">LIVE DATA</p>
          <h2 className="mt-1 text-sm font-semibold text-[#183037]">正在把真实结果放进行程</h2>
        </div>
        <span className="text-[10px] text-[#8c7258]">不使用演示数据</span>
      </div>
      <div className="mt-3 space-y-3">
        {items.map((tool, index) => {
          const label = categoryLabels[tool.category ?? ''] ?? toolLabels[tool.tool] ?? tool.tool
          if (tool.status === 'running') {
            return (
              <div key={`${tool.tool}-${index}`} className="flex items-center gap-2 rounded-xl border border-[#f0dfc8] bg-white px-3 py-2.5 text-xs text-[#6c5948]">
                <Loader2 size={14} className="animate-spin text-[#b36a2d]" aria-hidden />
                正在查询{label}…
              </div>
            )
          }
          if (tool.status === 'failed') {
            return (
              <div key={`${tool.tool}-${index}`} className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-700">
                <div className="flex items-center gap-2 font-medium"><AlertCircle size={14} aria-hidden />{label}查询失败</div>
                {tool.error && <p className="mt-1 break-words leading-relaxed text-[11px] text-red-600">{tool.error}</p>}
              </div>
            )
          }
          const preview = tool.preview ?? []
          return (
            <div key={`${tool.tool}-${index}`} className="rounded-xl border border-[#f0dfc8] bg-white p-3">
              <div className="flex items-center gap-2 text-xs font-medium text-[#183037]"><CheckCircle2 size={14} className="text-[#087b73]" aria-hidden />{label}已返回 {tool.resultCount ?? preview.length} 条</div>
              {preview.length > 0 && (tool.category === 'train'
                ? <TrainPreview preview={preview} />
                : tool.category === 'guide'
                  ? <GuidePreview preview={preview} />
                  : <MerchantPreview preview={preview} category={tool.category} />)}
              {preview.length === 0 && <p className="mt-2 text-[11px] text-[#8c7258]">真实查询成功，但当前没有匹配结果。</p>}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function MerchantPreview({ preview, category }: { preview: Array<Record<string, unknown>>; category?: string }) {
  return (
    <div className="mt-2 grid gap-2 sm:grid-cols-2">
      {preview.slice(0, 4).map((item, index) => (
        <article key={`${String(item.id ?? item.name ?? index)}`} className="rounded-lg border border-[#f0dfc8] bg-[#fffdf9] p-2.5">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 rounded-md bg-[#f5e5d1] p-1.5 text-[#b36a2d]">{category === 'hotel' ? <Hotel size={13} aria-hidden /> : <Utensils size={13} aria-hidden />}</span>
            <div className="min-w-0">
              <h3 className="truncate text-xs font-semibold text-[#183037]">{String(item.name ?? '未命名商户')}</h3>
              <p className="mt-0.5 truncate text-[10px] text-[#7a6b5d]">{String(item.category ?? item.address ?? '地址待核实')}</p>
            </div>
          </div>
          <p className="mt-2 truncate text-[10px] text-[#6c5948]">{String(item.address ?? '地址待核实')}</p>
          <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 text-[10px] text-[#8c7258]">
            {item.rating != null && <span>评分 {String(item.rating)}</span>}
            {item.price != null && <span>{String(item.price)}</span>}
            {item.open_status != null && <span>{String(item.open_status)}</span>}
          </div>
        </article>
      ))}
    </div>
  )
}

function TrainPreview({ preview }: { preview: Array<Record<string, unknown>> }) {
  /** 席别价格统一成「¥83」形态：数值/数字串补 ¥，其余原样透传，缺失显示 -- */
  const formatPrice = (value: unknown): string => {
    if (value == null || value === '') return '--'
    if (typeof value === 'number' && Number.isFinite(value)) return `¥${value}`
    const raw = String(value).trim()
    if (raw.startsWith('¥') || raw.startsWith('￥')) return raw
    return /^\d+(\.\d+)?$/.test(raw) ? `¥${raw}` : raw
  }
  return (
    <div className="mt-2 overflow-x-auto rounded-lg border border-[#f0dfc8]">
      <table className="min-w-full text-left text-[10px] text-[#6c5948]">
        <thead className="bg-[#fff3e3] text-[#8c7258]"><tr><th className="px-2 py-1.5 font-medium">车次</th><th className="px-2 py-1.5 font-medium">出发</th><th className="px-2 py-1.5 font-medium">到达</th><th className="px-2 py-1.5 font-medium">历时</th><th className="px-2 py-1.5 font-medium">余票</th><th className="px-2 py-1.5 font-medium">票价</th></tr></thead>
        <tbody>
          {preview.slice(0, 6).map((item, index) => {
            const seats = item.seats
            const seatsText = seats && typeof seats === 'object' && !Array.isArray(seats)
              ? Object.entries(seats as Record<string, unknown>).slice(0, 3)
                  .map(([seat, left]) => `${seat} ${String(left)}`).join(' / ')
              : ''
            const prices = item.prices
            const priceText = prices && typeof prices === 'object' && !Array.isArray(prices)
              ? Object.entries(prices as Record<string, unknown>).slice(0, 2)
                  .map(([seat, price]) => `${seat} ${formatPrice(price)}`).join(' / ')
              : ''
            return (
              <tr key={`${String(item.train_no ?? 'unknown')}-${String(item.start_time ?? index)}-${String(item.arrive_time ?? '')}-${index}`} className="border-t border-[#f4e6d3]">
                <td className="px-2 py-1.5 font-medium text-[#183037]">{String(item.train_no ?? '未知')}</td>
                <td className="px-2 py-1.5">{String(item.start_time ?? '--')}</td>
                <td className="px-2 py-1.5">{String(item.arrive_time ?? '--')}</td>
                <td className="px-2 py-1.5">{String(item.duration ?? '--')}</td>
                <td className="px-2 py-1.5">{seatsText || <span className="text-[#b3a48f]">--</span>}</td>
                <td className="px-2 py-1.5">{priceText || <span className="text-[#b3a48f]">--</span>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="px-2 py-1.5 text-[10px] text-[#8c7258]">余票与票价来自 12306 非官方聚合源，可能延迟；票价仅实时查询前 2 个车次，其余显示 --，出行前请以 12306 官方为准。</p>
    </div>
  )
}

/**
 * GuidePreview — 知乎攻略卡（📝怎么吃/什么值得吃，软内容参考）。
 * 与 MerchantPreview（📍去哪吃，结构化商户）并存分工；条目来自知乎官方
 * MCP 归一化字段（title/url/summary/author_name/vote_up_count/comment_count），
 * 缺失不补造。title 带原文链接（官方开放平台返回的站内/全网 URL）。
 */
function GuidePreview({ preview }: { preview: Array<Record<string, unknown>> }) {
  return (
    <ul className="mt-2 space-y-2">
      {preview.slice(0, 4).map((item, index) => {
        const title = String(item.title ?? '未命名内容')
        const url = typeof item.url === 'string' && item.url.startsWith('http') ? item.url : ''
        const summary = typeof item.summary === 'string' ? item.summary : ''
        const votes = item.vote_up_count
        const comments = item.comment_count
        return (
          <li key={`${title}-${index}`} className="rounded-lg border border-[#e3e9f5] bg-[#fbfcff] p-2.5">
            {url ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="block truncate text-xs font-semibold text-[#2d5bd1] hover:underline"
              >
                {title}
              </a>
            ) : (
              <p className="truncate text-xs font-semibold text-[#183037]">{title}</p>
            )}
            {summary && <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-[#5c7074]">{summary}</p>}
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-[#8c7258]">
              <span className="rounded bg-[#e8f0fe] px-1.5 py-0.5 font-medium text-[#2d5bd1]">知乎</span>
              {typeof item.author_name === 'string' && item.author_name && <span>{item.author_name}</span>}
              {typeof votes === 'number' && votes > 0 && <span>{votes} 赞同</span>}
              {typeof comments === 'number' && comments > 0 && <span>{comments} 评论</span>}
            </div>
          </li>
        )
      })}
    </ul>
  )
}


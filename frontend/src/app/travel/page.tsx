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
import { AlertCircle, CalendarDays, CheckCircle2, ChevronDown, History, Hotel, LocateFixed, Loader2, MapPin, Minus, PanelLeftOpen, Plane, Plus, Sparkles, Utensils } from 'lucide-react'
import { useBudgetStatus } from '@/hooks/useBudgetStatus'
import ItineraryView from '@/components/travel/ItineraryView'
import TravelChatDrawer from '@/components/travel/TravelChatDrawer'
import TravelPlanList from '@/components/travel/TravelPlanList'
import TaskSidebar from '@/components/agent/TaskSidebar'
import SidebarRail from '@/components/agent/SidebarRail'
import {
  EMPTY_PLAN_STATE,
  adoptConversationId,
  applyPlanResponse,
  clearPendingPlan,
  composePlanMessage,
  itineraryTotal,
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
import { TRAVEL_STAGE_LABELS, TRAVEL_STAGE_ORDER, TRAVEL_TOOL_LABELS } from '@/components/travel/travelDisplay'
import {
  fetchItineraryIcs,
  fetchTravelPlanLatest,
  fetchTravelRecommendations,
  reverseGeocodeTravelOrigin,
  sendTravelFeedback,
  streamTravelPlan,
  type ItineraryBrief,
  type TravelStreamEvent,
  type PlanResponse,
  type Recommendation,
} from '@/api/travel'
import { getCachedUser } from '@/lib/auth'

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
    form: { origin: '', destination: '福州', days: '2', partySize: '2', budget: '2000', preferences: ['人文'], pace: 'relaxed', extra: '查美食、查酒店' },
    accent: 'from-[#0d5d55] to-[#087b73]',
  },
  {
    id: 'live-train-weekend',
    eyebrow: '车票查询示例',
    title: '福州 → 厦门',
    description: '查询明天的高铁信息，再安排 2 天路线；数据不可用时明确提示',
    form: { origin: '福州', destination: '厦门', days: '2', partySize: '2', budget: '', preferences: ['美食'], pace: 'moderate', extra: '查高铁票' },
    accent: 'from-[#243c62] to-[#315c7d]',
  },
  {
    id: 'slow-history',
    eyebrow: '轻松规划示例',
    title: '泉州慢游',
    description: '3 天逛古迹，不绕路，留出喝茶和休息时间',
    form: { origin: '', destination: '泉州', days: '3', partySize: '2', budget: '', preferences: ['人文'], pace: 'relaxed', extra: '看古迹，不绕路，留出喝茶和休息时间' },
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
  // 左侧任务栏（与 /agent 同一套 TaskSidebar，travel 模式：历史区=历史规划列表）
  const [sidebarOpen, setSidebarOpen] = useState(true)
  // 出单/恢复后自增，触发侧栏历史规划刷新
  const [plansVersion, setPlansVersion] = useState(0)
  // 恢复中的会话（侧栏列表行内转圈）
  const [restoringCid, setRestoringCid] = useState('')
  // 窄屏（<md 无侧栏）历史规划浮层
  const [sheetOpen, setSheetOpen] = useState(false)
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
    if (abortRef.current || budgetBlocked) return
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
        if (controller.signal.aborted || abortRef.current !== controller) return
        handleTravelEvent(event)
        if (event.event === 'error') {
          throw new Error(typeof event.data.message === 'string'
            ? event.data.message : '旅游规划执行失败')
        }
        if (event.event === 'done' && event.data.result && typeof event.data.result === 'object') {
          data = event.data.result as PlanResponse
        }
      }
      if (controller.signal.aborted || abortRef.current !== controller) return
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
        // 新版本落账 → 侧栏历史规划刷新
        setPlansVersion((v) => v + 1)
      }
    } catch (e) {
      if (!controller.signal.aborted && abortRef.current === controller) {
        setError(e instanceof Error ? e.message : '规划请求失败，请稍后再试')
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setLoading(false)
      }
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
    if (abortRef.current || budgetBlocked) return
    const form = { ...example.form, preferences: [...example.form.preferences], startDate: tomorrowIso() }
    setOrigin(form.origin)
    setDestination(form.destination)
    setDays(form.days)
    setPartySize(form.partySize)
    setBudget(form.budget)
    setPreferences(form.preferences)
    setPace(form.pace)
    setExtra(form.extra)
    setStartDate(form.startDate)
    void submit(composePlanMessage(form))
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
  /** brief → 表单回填（助手改单与恢复历史规划共用一份，避免两处口径漂移） */
  const applyBriefToForm = useCallback((brief: ItineraryBrief) => {
    setDestination(brief.destination || '')
    setOrigin(brief.origin || '')
    setDays(brief.days ? String(brief.days) : '')
    setPartySize(brief.party_size ? String(brief.party_size) : '')
    setBudget(brief.budget_cny != null ? String(brief.budget_cny) : '')
    setStartDate(brief.start_date || '')
    setPace(brief.pace || '')
    setPreferences(brief.preferences ?? [])
  }, [])

  const handleAssistantResponse = useCallback((data: PlanResponse) => {
    setPlanState((prev) => applyPlanResponse(prev, data))
    if (data.itinerary?.brief) applyBriefToForm(data.itinerary.brief)
  }, [applyBriefToForm])

  const handleAssistantDraft = useCallback((data: PlanResponse) => {
    setPlanState((prev) => previewPlanResponse(prev, data))
  }, [])

  const handleDiscardPending = useCallback(() => {
    setPlanState((prev) => clearPendingPlan(prev))
  }, [])

  const startNewTrip = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(false)
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

  // ── 历史规划：点击侧栏/浮层条目恢复 ──
  /** 恢复历史规划：切回该会话线程（收养，不轮换）+ 恢复最新版行程 + 回填表单。 */
  const restorePlan = useCallback(async (cid: string) => {
    if (loading || restoringCid) return
    setRestoringCid(cid)
    setError('')
    try {
      const latest = await fetchTravelPlanLatest(cid)
      if (!latest.itinerary) throw new Error('这份规划没有可恢复的行程内容')
      const data: PlanResponse = {
        status: latest.itinerary.status || 'ready',
        final_answer: '',
        itinerary: latest.itinerary,
        plan_status: latest.plan_status,
        change_record: null,
      }
      abortRef.current?.abort()
      abortRef.current = null
      setLoading(false)
      setConversationId(adoptConversationId(cid))
      setPlanState(applyPlanResponse(EMPTY_PLAN_STATE, data))
      applyBriefToForm(latest.itinerary.brief)
      setFeedbackSent('')
      setError('')
      setTravelProcess(null)
      setActiveDay(1)
      setConditionsOpen(false)
      setSheetOpen(false)
      // 恢复不产生新版本，但要刷新侧栏「当前」高亮所依赖的列表时间戳
      setPlansVersion((v) => v + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : '恢复规划失败，请稍后再试')
    } finally {
      setRestoringCid('')
    }
  }, [applyBriefToForm, loading, restoringCid])

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
    /** 页面级生成中：助手输入禁用 + 占位（设计稿态 2 右栏口径） */
    generating: loading,
    disabled: budgetBlocked,
    disabledHint: '本月额度已用尽，暂时不能发起新的规划；额度重置后自动恢复',
    budgetStatus,
  } as const

  // 左栏三态口径（设计稿）：空态无左栏（合成输入卡是唯一入口）；
  // 生成中=条件锁定；有行程=条件摘要
  const hasLeftRail = loading || !!itinerary
  // 右栏助手：设计稿态1 没有助手栏 —— 空态时连助手都不出现，输入卡是唯一焦点
  const hasRightRail = loading || !!itinerary
  const userName = getCachedUser()?.username || '本地用户'

  return (
    <div className="flex min-h-0 flex-1">
      {/* 左侧任务栏：与 /agent 同一套 TaskSidebar（travel 模式=历史规划列表 + 新建规划）；
          <md 视口下组件自身隐藏，历史入口见顶栏「历史规划」浮层 */}
      {sidebarOpen ? (
        <TaskSidebar
          mode="travel"
          onCollapse={() => setSidebarOpen(false)}
          onNewTask={startNewTrip}
          newLabel="新建规划"
          searchPlaceholder="搜索历史规划…"
          renderHistory={({ keyword, refreshKey, onRefreshingChange }) => (
            <TravelPlanList
              keyword={keyword}
              refreshKey={refreshKey + plansVersion}
              onRefreshingChange={onRefreshingChange}
              onEmptyAction={startNewTrip}
              currentId={conversationId}
              restoringCid={restoringCid}
              onRestore={(cid) => void restorePlan(cid)}
            />
          )}
        />
      ) : (
        <SidebarRail onExpand={() => setSidebarOpen(true)} onNewTask={startNewTrip} newLabel="新建规划" />
      )}

      <div className={`flex min-h-0 min-w-0 flex-1 flex-col ${drawerOpen && !isWide ? 'lg:pr-[440px]' : ''}`}>
        {/* 顶栏（设计稿「TripKit · 行程规划」条）：侧栏收起后的展开入口 + 窄屏历史入口 */}
        <header className="flex h-12 shrink-0 items-center gap-2 border-b border-black/5 bg-white/70 px-4 backdrop-blur">
          {!sidebarOpen && (
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              aria-label="展开任务栏"
              title="展开任务栏"
              className="rounded-lg p-1.5 text-text-muted transition-colors hover:bg-black/5 hover:text-text-primary"
            >
              <PanelLeftOpen size={16} />
            </button>
          )}
          <Plane size={16} className="text-[#087b73]" aria-hidden />
          <span className="text-sm font-semibold text-[#183037]">行程规划</span>
          <div className="ml-auto flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setSheetOpen(true)}
              className="inline-flex items-center gap-1 rounded-lg border border-[#dae7e5] px-2.5 py-1 text-xs text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037] md:hidden"
            >
              <History size={12} aria-hidden />
              历史规划
            </button>
            <span className="hidden items-center gap-1.5 text-xs text-[#5c7074] sm:inline-flex">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#087b73]/10 text-[10px] font-medium text-[#087b73]">
                {userName.slice(0, 1).toUpperCase()}
              </span>
              {userName}
            </span>
          </div>
        </header>

        {/* 一屏布局：lg+ 外层不滚，三栏各自内滚（用户反馈「不要整页滑到很下面」）；
            窄屏退回整页文档流滚动 */}
        <div className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
          <div className="mx-auto max-w-[1560px] px-4 py-4 xl:px-6 xl:py-5 lg:h-full">
            <div className={`grid items-stretch gap-4 lg:h-full ${hasLeftRail
              ? 'lg:grid-cols-[264px_minmax(0,1fr)] xl:grid-cols-[264px_minmax(0,1fr)_336px]'
              : 'lg:grid-cols-[minmax(0,1fr)]'}`}>
              {/* ── 左栏：生成中=条件锁定（态2）；有行程=条件摘要（态3）；空态无左栏 ── */}
              {hasLeftRail && <aside className="min-w-0 min-h-0 space-y-3 lg:overflow-y-auto lg:pr-0.5">
                {loading ? (
                  <>
                    <section
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
                    </section>
                    <section
                      className="rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card opacity-60"
                      aria-label="调整条件（生成中不可用）"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <h2 className="text-sm font-semibold text-[#183037]">调整条件</h2>
                        <span
                          className="cursor-not-allowed rounded-lg border border-[#dae7e5] px-2.5 py-1 text-xs text-[#9db4b1]"
                          title="生成期间条件不可改"
                        >
                          调整条件
                        </span>
                      </div>
                      <p className="mt-2 text-[11px] leading-relaxed text-[#5c7074]">
                        生成完成后可调整条件重开一份，或直接在右侧「旅行助手」说一句。
                      </p>
                    </section>
                  </>
                ) : (
                  <TripSummary
                    /* 摘要跟当前行程走：表单被清空/改了一半时，仍显示行程真实目的地 */
                    destination={itinerary?.brief.destination || destination}
                    days={days} budget={budget}
                    startDate={itinerary?.brief.start_date || startDate}
                    pace={itinerary?.brief.pace || pace}
                    itineraryDays={itinerary?.days.length ?? null}
                    costTotal={itinerary ? itineraryTotal(itinerary.cost) : null}
                    onAdjust={() => setConditionsOpen(true)}
                  />
                )}
              </aside>}

            {/* ── 中栏：行程（唯一结果主视图，栏内滚动） ── */}
            <main className="min-w-0 min-h-0 space-y-4 lg:overflow-y-auto lg:pr-0.5">
              {!itinerary && !loading && !planState.notice && (
                <PlanIntakeCard
                  value={quickIdea}
                  onValueChange={setQuickIdea}
                  onSubmitIdea={runQuickIdea}
                  onRunExample={runExample}
                  disabled={loading || budgetBlocked}
                  form={
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
                  }
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
              ) : null}
            </main>

            {/* ── 右栏：旅行助手（设计稿态1 无助手栏；生成中/有行程才出现） ── */}
            {isWide && hasRightRail && (
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

      {/* 窄屏（<md 无侧栏）历史规划浮层：与侧栏列表同一数据组件 */}
      <TravelHistorySheet
        open={sheetOpen}
        plansVersion={plansVersion}
        currentId={conversationId}
        restoringCid={restoringCid}
        onClose={() => setSheetOpen(false)}
        onRestore={restorePlan}
        onNewPlan={startNewTrip}
      />

      {/* 中窄屏：「对话改行程」边缘按钮 + 展开抽屉（态1 无助手，不出入口） */}
      {!isWide && hasRightRail && (
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
    </div>
  )
}

// ── 左栏：行程条件摘要（设计稿态3：精简卡 + 调整条件卡） ─────

function TripSummary(props: {
  destination: string; days: string; budget: string
  startDate: string; pace: string
  /** 当前行程的实际天数（改单后会变，优先于表单值展示） */
  itineraryDays: number | null
  /** 出单后的实际预算合计（itinerary cost 求和；优先于表单预算展示） */
  costTotal: number | null
  onAdjust: () => void
}) {
  const dayText = props.itineraryDays != null ? String(props.itineraryDays) : (props.days || '?')
  const budgetText = props.costTotal != null
    ? `预算合计 ¥${props.costTotal.toLocaleString()}`
    : props.budget ? `预算合计 ¥${Number(props.budget).toLocaleString()}` : '预算未设上限'
  const metaParts = [
    props.startDate ? `${props.startDate} 出发` : '',
    props.pace ? `节奏${PACE_LABEL[props.pace] ?? '适中'}` : '',
  ].filter(Boolean)
  return (
    <>
      <section className="rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card" aria-label="行程条件">
        <h2 className="text-xs font-medium text-[#5c7074]">行程条件</h2>
        <p className="mt-2 text-xl font-bold text-[#183037]">
          {props.destination.trim() || '未定目的地'} · {dayText} 天
        </p>
        {metaParts.length > 0 && (
          <p className="mt-1.5 text-xs text-[#5c7074]">{metaParts.join(' · ')}</p>
        )}
        <p className="mt-3 text-sm font-semibold text-[#087b73]">{budgetText}</p>
      </section>
      <section className="rounded-2xl border border-[#dae7e5] bg-white p-4 shadow-card" aria-label="调整条件">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-[#183037]">调整条件</h2>
          <button
            type="button"
            onClick={props.onAdjust}
            className="cursor-pointer rounded-lg border border-[#dae7e5] px-2.5 py-1 text-xs
              text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037]"
          >
            调整条件
          </button>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-[#5c7074]">
          小改动（改天数 / 换节奏）更推荐直接在右侧「旅行助手」说一句。
        </p>
      </section>
    </>
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

// ── 历史规划浮层（窄屏 <md：无侧栏时的入口，桌面走左侧任务栏） ──

function TravelHistorySheet({
  open, plansVersion, currentId, restoringCid, onClose, onRestore, onNewPlan,
}: {
  open: boolean
  plansVersion: number
  currentId: string
  restoringCid: string
  onClose: () => void
  onRestore: (cid: string) => void
  onNewPlan: () => void
}) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-40 bg-[#183037]/20 md:hidden"
      role="presentation"
      onClick={onClose}
    >
      <aside
        className="flex h-full w-[320px] max-w-[88vw] flex-col border-r border-[#dae7e5] bg-white shadow-2xl"
        aria-label="历史规划"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-[#e8f1ef] px-4 py-3">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-[#183037]">
            <History size={14} className="text-[#087b73]" aria-hidden />
            历史规划
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-xs text-[#5c7074] hover:bg-[#f5faf9]"
          >
            关闭
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <TravelPlanList
            refreshKey={plansVersion}
            currentId={currentId}
            restoringCid={restoringCid}
            onRestore={onRestore}
            onEmptyAction={onNewPlan}
          />
        </div>
      </aside>
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

// ── 生成中进度卡（聊天流式竖版：事件逐条出现、完成折叠、展开看结果） ──

type StepStatus = 'pending' | 'running' | 'completed' | 'failed'

/**
 * GeneratingCard — 生成中进度（2026-10-02 聊天流式竖版）：
 * 事件流像聊天消息一样逐条动态出现（需求分析 / 每次真实 Tool 调用一条）；
 * 执行完自动折叠成一行（名称 + 结果摘要），进行中/失败默认展开，
 * 用户点击可随时展开看 Tool 执行结果（商户/车次/攻略 preview 直接渲染）。
 * 进度只来自真实 SSE 事件，不伪造百分比。
 */
function GeneratingCard({
  processState,
  expectedDays,
}: {
  processState: TravelProcessState | null
  expectedDays: number
}) {
  const failed = processState?.status === 'error'
  const tools = processState?.tools ?? []
  const requirement = processState?.requirement
  // 「执行完折叠」的手动反转集合：默认 进行中/失败 展开、完成折叠，点击切换
  const [manualToggled, setManualToggled] = useState<Set<string>>(new Set())
  const toggle = (key: string) => {
    setManualToggled((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }
  const isOpen = (key: string, defaultOpen: boolean) =>
    manualToggled.has(key) ? !defaultOpen : defaultOpen

  const reqBrief = (requirement?.brief ?? {}) as Record<string, unknown>
  const reqSummary = [
    typeof reqBrief.days === 'number' && reqBrief.days > 0 ? `${reqBrief.days} 天` : '',
    ...(Array.isArray(reqBrief.preferences) ? reqBrief.preferences : []) as string[],
    typeof reqBrief.budget_cny === 'number' && reqBrief.budget_cny > 0 ? `预算 ¥${reqBrief.budget_cny}` : '',
  ].filter(Boolean).join(' · ')

  const doneCount = tools.filter((t) => t.status !== 'running').length + (requirement ? 1 : 0)

  return (
    <section
      className="animate-fade-in space-y-4"
      aria-live="polite"
      aria-label="正在生成行程"
    >
      <div className="rounded-2xl border border-[#dae7e5] bg-white p-5 shadow-card">
        {/* 轻头部 */}
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#087b73]/10">
            {failed
              ? <AlertCircle size={17} className="text-red-500" aria-hidden />
              : <Loader2 size={17} className="animate-spin text-[#087b73]" aria-hidden />}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[#183037]">
              {failed ? '旅游规划失败' : '正在生成你的行程…'}
            </p>
            <p className="mt-0.5 text-xs text-[#5c7074]">
              {failed
                ? '失败步骤已在下方展开，未用假数据补齐'
                : `已推进 ${doneCount} 步 · 进度来自真实 SSE 事件流，不伪造百分比`}
            </p>
          </div>
        </div>

        {/* 聊天流式竖排：需求分析 + 每次真实 Tool 调用一条 */}
        <ol className="mt-4 space-y-1.5">
          {requirement && (() => {
            const key = 'requirement'
            const open = isOpen(key, false)
            const missing = requirement.missing.length > 0
            const assumptions = requirement.assumptions.length > 0
            return (
              <li key={key} className="overflow-hidden rounded-xl border border-[#e2f0ee] bg-white">
                <button
                  type="button"
                  onClick={() => toggle(key)}
                  aria-expanded={open}
                  className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[#f5faf9]"
                >
                  <CheckCircle2 size={13} className="shrink-0 text-[#087b73]" aria-hidden />
                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-[#183037]">需求分析</span>
                  <span className="shrink-0 text-[10px] text-[#5c7074]">{reqSummary || '完成'}</span>
                  <ChevronDown size={12} className={`shrink-0 text-[#9db4b1] transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
                </button>
                {open && (
                  <div className="border-t border-[#eef4f2] px-3 py-2.5 text-[11px] leading-relaxed text-[#5c7074]">
                    {reqSummary && <p>识别到：{reqSummary || '（等待你说更多信息）'}</p>}
                    {assumptions && <p className="mt-1">假设：{requirement.assumptions.join('；')}</p>}
                    {missing && <p className="mt-1 text-amber-700">待补充：{requirement.missing.join('、')}</p>}
                  </div>
                )}
              </li>
            )
          })()}
          {tools.map((tool, index) => {
            const key = `tool-${index}`
            const running = tool.status === 'running'
            const failedRow = tool.status === 'failed'
            const open = isOpen(key, running || failedRow)
            const label = TRAVEL_TOOL_LABELS[tool.tool] ?? tool.tool
            const summary = running
              ? '调用中…'
              : failedRow
                ? `失败${tool.errorType ? ` · ${tool.errorType}` : ''}`
                : [
                    tool.resultCount != null ? `${tool.resultCount} 条` : '',
                    tool.durationMs != null ? `${(tool.durationMs / 1000).toFixed(1)}s` : '',
                  ].filter(Boolean).join(' · ') || '完成'
            return (
              <li
                key={key}
                className={`overflow-hidden rounded-xl border ${
                  failedRow ? 'border-red-200 bg-red-50/50' : 'border-[#e2f0ee] bg-white'
                }`}
              >
                <button
                  type="button"
                  onClick={() => toggle(key)}
                  aria-expanded={open}
                  className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[#f5faf9]"
                >
                  {running ? (
                    <Loader2 size={13} className="shrink-0 animate-spin text-[#087b73]" aria-label="调用中" />
                  ) : failedRow ? (
                    <AlertCircle size={13} className="shrink-0 text-red-500" aria-label="失败" />
                  ) : (
                    <CheckCircle2 size={13} className="shrink-0 text-[#087b73]" aria-hidden />
                  )}
                  <span className={`min-w-0 flex-1 truncate text-xs ${failedRow ? 'font-medium text-red-700' : 'font-medium text-[#183037]'}`}>
                    {label}
                  </span>
                  <span className={`shrink-0 text-[10px] ${failedRow ? 'text-red-600' : 'text-[#5c7074]'}`}>{summary}</span>
                  <ChevronDown size={12} className={`shrink-0 text-[#9db4b1] transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
                </button>
                {open && (
                  <div className="border-t border-[#eef4f2] px-3 py-2.5">
                    {failedRow ? (
                      <p className="break-words text-[11px] leading-relaxed text-red-600">
                        {tool.error || '该步骤执行失败，未用假数据补齐；其他步骤的结果仍然有效。'}
                      </p>
                    ) : (tool.preview?.length ?? 0) > 0 ? (
                      tool.category === 'train'
                        ? <TrainPreview preview={tool.preview!} />
                        : tool.category === 'guide'
                          ? <GuidePreview preview={tool.preview!} />
                          : <MerchantPreview preview={tool.preview!} category={tool.category} />
                    ) : running ? (
                      <p className="text-[11px] text-[#7a8e8b]">正在调用真实数据源，结果返回后自动折叠…</p>
                    ) : (
                      <p className="text-[11px] text-[#7a8e8b]">
                        {tool.dataStatus === 'empty'
                          ? '查询成功，当前没有匹配结果。'
                          : tool.dataStatus === 'unavailable'
                            ? '数据源暂不可用，已按降级口径继续规划。'
                            : '执行完成。'}
                      </p>
                    )}
                  </div>
                )}
              </li>
            )
          })}
          {tools.length === 0 && !requirement && (
            <li className="py-4 text-center text-xs text-[#7a8e8b]">正在启动规划引擎…</li>
          )}
        </ol>
      </div>

      {/* 行程卡片骨架：按用户填的天数占位，生成完成后被真实日卡片替换 */}
      {!failed && expectedDays > 0 && (
        <div>
          <p className="mb-2 text-xs text-[#5c7074]">
            行程卡片 · 完成后逐张点亮（下面是等待生成的占位）
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

const INTAKE_EMOJI = ['🌶️', '🚄', '🏯'] as const
const HOT_SEARCHES = ['三坊七巷 Citywalk', '平潭蓝眼泪', '武夷山茶山'] as const
const ADVANCED_CHIPS = ['目的地', '出发日期', '天数', '预算'] as const

function PlanIntakeCard({
  value, onValueChange, onSubmitIdea, onRunExample, disabled, form,
}: {
  value: string
  onValueChange: (v: string) => void
  onSubmitIdea: () => void
  onRunExample: (example: typeof EXAMPLE_SCENARIOS[number]) => void
  disabled: boolean
  /** 高级选项展开区内容（完整 TripForm，由页面组装传入） */
  form: React.ReactNode
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  return (
    <section
      className="animate-fade-in rounded-2xl border border-[#c9dcd7] bg-white p-6 shadow-card sm:p-8"
      aria-label="开始规划行程"
    >
      <div className="mx-auto max-w-[680px] text-center">
        <h1 className="text-xl font-semibold text-[#183037] sm:text-2xl">想去哪儿玩？说说你的想法</h1>
        <p className="mt-2 text-xs leading-relaxed text-[#5c7074]">
          一句话描述就行，缺的信息我会追问你。行程基于真实路况与天气排出，
          只要有来源的数据才展示金额，其余明确标为暂无数据。
        </p>

        {/* 示例 chips：点一下直接生成 */}
        <p className="mt-6 text-[11px] text-[#5c7074]">试试这些 · 点一下直接生成</p>
        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
          {EXAMPLE_SCENARIOS.map((example, i) => (
            <button
              key={example.id}
              type="button"
              disabled={disabled}
              onClick={() => onRunExample(example)}
              title={example.description}
              className="cursor-pointer rounded-full border border-[#087b73]/30 bg-[#e2f0ee] px-3.5 py-1.5 text-[13px] font-medium text-[#087b73] transition-colors hover:border-[#087b73]/60 hover:bg-[#d3e9e5] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {INTAKE_EMOJI[i] ?? '📍'} {example.title}
            </button>
          ))}
        </div>

        {/* 主输入行 */}
        <form
          className="mt-4 flex items-center gap-2 rounded-xl border border-[#dae7e5] bg-[#f5faf9] p-1.5 transition-colors focus-within:border-[#087b73]/50"
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

        {/* 高级选项：字段 chips 点开展开完整表单 */}
        <div className="mt-5 flex items-center gap-3" aria-hidden>
          <span className="h-px min-w-0 flex-1 bg-[#dae7e5]" />
          <span className="shrink-0 text-[11px] text-[#5c7074]">或用高级选项精确定制</span>
          <span className="h-px min-w-0 flex-1 bg-[#dae7e5]" />
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
          {ADVANCED_CHIPS.map((chip) => (
            <button
              key={chip}
              type="button"
              disabled={disabled}
              onClick={() => setAdvancedOpen(true)}
              className="cursor-pointer rounded-lg border border-[#dae7e5] bg-white px-3 py-1.5 text-xs text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037] disabled:cursor-not-allowed disabled:opacity-50"
            >
              + {chip}
            </button>
          ))}
          <button
            type="button"
            disabled={disabled}
            onClick={() => setAdvancedOpen((v) => !v)}
            aria-expanded={advancedOpen}
            className="cursor-pointer px-1 text-xs font-medium text-[#087b73] transition-colors hover:text-[#06655f] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {advancedOpen ? '收起高级选项 ▴' : '展开更多 ▾'}
          </button>
        </div>
        {advancedOpen && (
          <div className="mt-5 animate-fade-in text-left">
            {form}
          </div>
        )}

        {/* 大家最近在找：热词回填主输入框 */}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-2 text-[11px] text-[#8fa5a3]">
          <span>大家最近在找：</span>
          {HOT_SEARCHES.map((word) => (
            <button
              key={word}
              type="button"
              disabled={disabled}
              onClick={() => onValueChange(word)}
              className="cursor-pointer rounded-full bg-[#f5faf9] px-3 py-1 text-[11px] text-[#5c7074] transition-colors hover:bg-[#e2f0ee] hover:text-[#087b73] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {word}
            </button>
          ))}
        </div>
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


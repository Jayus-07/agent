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
import { useRouter } from 'next/navigation'
import { AlertCircle, BookOpen, CalendarDays, CheckCircle2, ChevronDown, Gauge, History, Hotel, LocateFixed, Loader2, MapPin, Minus, PanelLeftOpen, Plane, Plus, Sparkles, Users, Utensils, Wallet } from 'lucide-react'
import { useBudgetStatus } from '@/hooks/useBudgetStatus'
import ItineraryView from '@/components/travel/ItineraryView'
import CandidatesPanel from '@/components/travel/CandidatesPanel'
import ToolProcessRows from '@/components/travel/ToolProcessRows'
import TravelChatDrawer, { type TravelChatDrawerHandle } from '@/components/travel/TravelChatDrawer'
import TravelOnboarding from '@/components/travel/TravelOnboarding'
import CityGuideDrawer from '@/components/travel/CityGuideDrawer'
import TravelPlanList from '@/components/travel/TravelPlanList'
import TaskSidebar from '@/components/agent/TaskSidebar'
import SidebarRail from '@/components/agent/SidebarRail'
import {
  EMPTY_PLAN_STATE,
  adoptConversationId,
  applyPlanResponse,
  clearPendingPlan,
  buildTravelBriefInput,
  composePlanMessage,
  itineraryTotal,
  readConversationId,
  readStoredConversationId,
  readPlanState,
  rotateConversationId,
  persistPlanState,
  previewPlanResponse,
  reconcilePlanWithLatest,
  PACE_LABEL,
  type PlanState,
} from '@/components/travel/planState'
import {
  initialTravelProcess,
  reduceTravelStreamEvent,
  type TravelProcessState,
  type TravelProcessTool,
} from '@/components/travel/travelRuntime'
import { TRAVEL_STAGE_LABELS, TRAVEL_STAGE_ORDER, TRAVEL_TOOL_LABELS } from '@/components/travel/travelDisplay'
import {
  fetchItineraryIcs,
  fetchTravelPlanList,
  fetchTravelPlanLatest,
  fetchTravelRecommendations,
  recordTravelDecision,
  reverseGeocodeTravelOrigin,
  sendTravelFeedback,
  streamTravelPlan,
  type ItineraryBrief,
  type TravelStreamEvent,
  type PlanResponse,
  type TravelBriefInput,
  type Recommendation,
  fetchMyPreferences,
  isEmptyPrefs,
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

/**
 * 手机端（≤md=768px）聊天全屏模式（2026-10-07 用户拍板）。
 * 手机上只保留对话给行程计划，行程卡/时间轴/地图放不下也不好看。
 */
function useIsMobileChat(): boolean {
  const [mobile, setMobile] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    const sync = () => setMobile(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])
  return mobile
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
  const [diet, setDiet] = useState('')
  const [transport, setTransport] = useState('')
  const [preferences, setPreferences] = useState<string[]>([])
  const [extra, setExtra] = useState('')
  // 首屏引导的一句话输入：直接交给后端 slot_filler 解析（缺的信息由域内追问补齐）
  const [quickIdea, setQuickIdea] = useState('')
  // M1 三态移交：空态规划卡/示例卡发送的那句话（→ 右侧助手第一条用户气泡）
  const [handoverMessage, setHandoverMessage] = useState('')
  // M2-g 出域轮无 itinerary，若右栏按「有行程才挂载」会连对话带引导卡一起卸载——
  // 本线程发过消息即视为对话已激活，右栏常驻
  const [chatActivated, setChatActivated] = useState(false)
  // M3-h 城市指南抽屉（顶栏书本图标 / 聊天 chip 唤起）
  const [cityGuideOpen, setCityGuideOpen] = useState(false)

  // 多域隔离 M3（2026-10-06）：主图引导卡带参跳转预填（?destination=…&days=…）。
  // 只在挂载时读一次；参数只做表单预填，权威解析仍在本域 slot_filler。
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const dest = params.get('destination') || ''
    const daysParam = params.get('days') || ''
    const partyParam = params.get('party_size') || ''
    const budgetParam = params.get('budget_cny') || ''
    const mustGo = (params.get('must_go') || '').split(',').map((s) => s.trim()).filter(Boolean)
    if (dest) setDestination(dest)
    if (daysParam && /^\d{1,2}$/.test(daysParam)) setDays(daysParam)
    if (partyParam && /^\d{1,2}$/.test(partyParam)) setPartySize(partyParam)
    if (budgetParam && /^\d+(\.\d+)?$/.test(budgetParam)) setBudget(budgetParam)
    if (mustGo.length > 0) {
      setExtra((prev) => {
        const line = `必去：${mustGo.join('、')}`
        return prev ? `${prev}\n${line}` : line
      })
    }
  }, [])

  // ── 结果与线程 ──
  const hadStoredConversation = readStoredConversationId() !== null
  const [conversationId, setConversationId] = useState(readConversationId)
  const [recoverLatestHistory] = useState(() => !hadStoredConversation)
  const [planState, setPlanState] = useState<PlanState>(readPlanState)
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [feedbackSent, setFeedbackSent] = useState<'' | 'positive' | 'negative'>('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  // 手机端（≤md）聊天全屏：首次进入（有行程时）自动展开聊天，行程详情靠返回键切换。
  // 用 ref 记住「已自动展开过」，避免 hasRightRail 由 false→true 时重复弹出打断用户。
  const mobileChatAutoOpenedRef = useRef(false)
  // 当前选中天：行程视图与右侧助手共享（点日卡片 → 助手知道「正在看第几天」）
  const [activeDay, setActiveDay] = useState(1)
  // 出行程后左栏折叠为摘要；「调整条件」再展开
  const [conditionsOpen, setConditionsOpen] = useState(false)
  const [locationState, setLocationState] = useState<'idle' | 'loading' | 'success' | 'denied' | 'error'>('idle')
  const [locationHint, setLocationHint] = useState('')
  const [budgetBlocked, setBudgetBlocked] = useState(false)
  const [travelProcess, setTravelProcess] = useState<TravelProcessState | null>(null)
  // M2-c/d 代发：画布直选与结果卡按钮经 ref 走助手同一条聊天管线
  const chatRef = useRef<TravelChatDrawerHandle>(null)
  // 「换一家」候选 = 最近一次**非空**商户检索的 preview（复用缓存，不重复调外部源）。
  // 不能只看最新 run：后续问答轮（0 Tool）会把首轮商户候选冲掉——画布直选失效。
  const [replaceCandidates, setReplaceCandidates] = useState<Array<Record<string, unknown>>>([])
  // 验收 #102：候选捕获时的行程版本——旧版本候选在换一家弹层打「来自旧版
  // 行程」标记（M2 已知取舍：候选跨轮缓存不失效，代发走草案管线兜底；
  // 标记让用户看得到陈旧，不静默）。
  const [replaceCandidatesVersion, setReplaceCandidatesVersion] = useState(0)
  useEffect(() => {
    const tools = travelProcess?.tools ?? []
    for (let i = tools.length - 1; i >= 0; i--) {
      const t = tools[i]
      if ((t.category === 'food' || t.category === 'hotel' || t.category === 'merchant') && (t.preview?.length ?? 0) > 0) {
        setReplaceCandidates(t.preview!)
        setReplaceCandidatesVersion(planState.plan?.itinerary?.plan_version ?? 0)
        return
      }
    }
  }, [travelProcess, planState.plan?.itinerary?.plan_version])
  /** M4/G1+G3 画布确认替换：先落 decision=canvas_replace 留痕（软失败不阻断）
      再代发修改请求（source=canvas_action 归因进 trace）。 */
  const handleRequestReplace = useCallback((dayIndex: number, itemTitle: string, candidateName: string) => {
    void recordTravelDecision({
      decision: 'canvas_replace',
      conversationId,
      planVersion: planState.plan?.itinerary?.plan_version ?? 0,
      payload: { day_index: dayIndex, item_title: itemTitle, candidate_name: candidateName },
      source: 'canvas_action',
    })
    chatRef.current?.send(`把第 ${dayIndex} 天的「${itemTitle}」换成「${candidateName}」，其他安排尽量保持不变`, 'canvas_action')
  }, [conversationId, planState.plan?.itinerary?.plan_version])
  /** M4/G1+G3 缺口卡快捷协商：先落 decision=budget_negotiate 留痕再代发。 */
  const handleNegotiateBudget = useCallback((text: string) => {
    void recordTravelDecision({
      decision: 'budget_negotiate',
      conversationId,
      planVersion: planState.plan?.itinerary?.plan_version ?? 0,
      payload: { message: text },
      source: 'budget_negotiate',
    })
    chatRef.current?.send(text, 'budget_negotiate')
  }, [conversationId, planState.plan?.itinerary?.plan_version])
  // 左侧任务栏（与 /agent 同一套 TaskSidebar，travel 模式：历史区=历史规划列表）
  // M2 布局拍板：行程+聊天是主角，历史列表默认收成图标条（点开浮层/展开整栏）
  const [sidebarOpen, setSidebarOpen] = useState(false)
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
  const isMobileChat = useIsMobileChat()

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
  const submit = useCallback(async (
    messageOverride?: string,
    structuredBriefOverride?: TravelBriefInput,
  ) => {
    if (abortRef.current || budgetBlocked) return
    const message = messageOverride ?? composePlanMessage({
      origin, destination, days, startDate, partySize, budget, pace, preferences, extra,
    })
    const briefInput = structuredBriefOverride ?? (messageOverride === undefined
      ? buildTravelBriefInput({
        origin, destination, days, startDate, partySize, budget, pace, preferences, extra,
      })
      : undefined)
    // 新行程 = 新线程。旧行程属于旧线程，留着会让「改单」打到错误的行程上，
    // 因此立刻清空（配合下方的生成中占位，不会显得东西凭空消失）。
    const cid = rotateConversationId()
    setConversationId(cid)
    setPlanState(EMPTY_PLAN_STATE)
    setFeedbackSent('')
    setError('')
    setTravelProcess(null)
    // M1 三态移交：这句话作为第一条用户气泡出现在右侧助手聊天流
    setHandoverMessage(message)
    setReplaceCandidates([]) // 新行程：上一份的商户候选作废
    setChatActivated(true)
    // M1 反馈：发送即进入「看结果」模式，自动折叠左侧历史栏腾出中栏空间
    setSidebarOpen(false)
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    // M4/G2：表单通道同样生成前端轮次标识（消息↔trace 打通对两条通道一致）
    const clientRunId = `client-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    try {
      let data: PlanResponse | null = null
      for await (const event of streamTravelPlan(message, cid, {
        signal: controller.signal, clientRunId, source: 'manual',
        mode: 'plan',
        ...(briefInput ? { briefInput } : {}),
      })) {
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
    void submit(composePlanMessage(form), buildTravelBriefInput(form))
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

  // sessionStorage 被清空/标签页重开时，按用户服务端历史自动收养最近一份
  // 会话；这样核心行程不依赖本地缓存，也不会把历史正文复制进另一套状态。
  useEffect(() => {
    if (!recoverLatestHistory) return
    let alive = true
    fetchTravelPlanList(1)
      .then((plans) => {
        const latest = plans[0]
        if (!alive || !latest?.conversation_id) return
        setConversationId(adoptConversationId(latest.conversation_id))
      })
      .catch(() => {
        // 没有历史或服务端暂不可用时保留刚创建的空线程。
      })
    return () => { alive = false }
  }, [recoverLatestHistory])

  // 刷新/多标签页回到本页时，sessionStorage 只作瞬时缓存；服务端版本账本
  // 才是 Active/Draft 真相源。确认版本直接替换，待确认版本只进入 pending。
  useEffect(() => {
    let alive = true
    fetchTravelPlanLatest(conversationId)
      .then((latest) => {
        if (!alive || !latest.itinerary) return
        setPlanState((prev) => reconcilePlanWithLatest(prev, latest))
        if (latest.plan_status !== 'waiting_confirmation') {
          applyBriefToForm(latest.itinerary.brief)
        }
      })
      .catch(() => {
        // 新会话尚未落账时 404 是正常状态，不覆盖本地缓存。
      })
    return () => { alive = false }
  }, [applyBriefToForm, conversationId])

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
    setHandoverMessage('') // 新线程：上一条移交语作废，避免下轮同文案不触发移交
    setChatActivated(false)
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
    /** M1 三态移交：空态发送的那句话 → 聊天流第一条用户气泡 */
    handoverUserMessage: handoverMessage,
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
  const hasRightRail = loading || !!itinerary || chatActivated

  // 手机端首次具备助手时自动展开聊天（只做一次；之后由用户在聊天/行程间手动切换）
  useEffect(() => {
    if (!isMobileChat || !hasRightRail) return
    if (mobileChatAutoOpenedRef.current) return
    mobileChatAutoOpenedRef.current = true
    setDrawerOpen(true)
  }, [isMobileChat, hasRightRail])
  const router = useRouter()
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
        <SidebarRail onExpand={() => setSidebarOpen(true)} onNewTask={startNewTrip} newLabel="新建规划" onHistory={() => setSheetOpen(true)} />
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
          {/* M3-h 城市指南入口（2026-10-07 用户拍板：手机端收掉，仅桌面展示） */}
          <button
            type="button"
            onClick={() => setCityGuideOpen(true)}
            title="城市指南"
            aria-label="城市指南"
            className="hidden md:inline-flex cursor-pointer rounded-lg p-1.5 text-[#087b73] transition-colors hover:bg-[#e2f0ee]"
          >
            <BookOpen size={15} aria-hidden />
          </button>
          {/* M2 布局反馈：条件 chips 并入顶栏（2026-10-07 手机端收掉——移动端
              不展示高级选项表格，桌面保留） */}
          {hasLeftRail && (
            <div className="hidden md:flex items-center">
              <TripConditionsChips
                loading={loading}
                destination={itinerary?.brief.destination || destination}
                startDate={itinerary?.brief.start_date || startDate}
                days={days}
                partySize={partySize}
                pace={itinerary?.brief.pace || pace}
                itineraryDays={itinerary?.days.length ?? null}
                costTotal={itinerary ? itineraryTotal(itinerary.cost) : null}
                budget={budget}
              />
            </div>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            {/* 高级选项表单（调整）：2026-10-07 用户拍板手机端收掉——移动端走
                示例/对话改行程，桌面保留表单入口 */}
            {hasLeftRail && !loading && (
              <button
                type="button"
                onClick={() => setConditionsOpen(true)}
                className="hidden md:inline-flex cursor-pointer rounded-lg border border-[#dae7e5] px-2.5 py-1 text-xs text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037]"
              >
                调整
              </button>
            )}
            <button
              type="button"
              onClick={() => setSheetOpen(true)}
              className="inline-flex items-center gap-1 rounded-lg border border-[#dae7e5] px-2.5 py-1 text-xs text-[#5c7074] transition-colors hover:border-[#087b73]/40 hover:text-[#183037] md:hidden"
            >
              <History size={12} aria-hidden />
              历史规划
            </button>
            {/* 用户头像（2026-10-07）：与 AI 助手顶栏同款，点击进设置页（两端一致） */}
            <button
              type="button"
              onClick={() => router.push('/settings')}
              aria-label="打开设置"
              title="设置"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#087b73]/10 text-[11px] font-medium text-[#087b73] transition-colors hover:bg-[#087b73]/20"
            >
              {userName.slice(0, 1).toUpperCase()}
            </button>
          </div>
        </header>

        {/* 一屏布局：lg+ 外层不滚，各栏内滚（用户反馈「不要整页滑到很下面」）；
            窄屏退回整页文档流滚动。
            2026-10-03 M1 重排：行程条件从左栏卡上移为顶部 chips 条（点击「调整」
            开浮层表单），栅格收敛为 中栏+右栏 两列，右栏加宽 336→460px（聊天
            消息/工具卡片流的主展示面）。 */}
        <div className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
          <div className="mx-auto max-w-none px-4 py-4 xl:px-8 xl:py-5 lg:h-full lg:flex lg:flex-col">
            <div className={`grid min-h-0 flex-1 items-stretch gap-4 lg:h-auto ${hasRightRail
              ? 'lg:grid-cols-[minmax(0,1fr)_520px] xl:grid-cols-[minmax(0,1fr)_600px]'
              : 'lg:grid-cols-[minmax(0,1fr)]'}`}>
              {/* ── 左栏已移除（M1）：条件摘要上移为顶部 TripConditionsBar，调整入口在其「调整」按钮 ── */}

            {/* ── 中栏：行程（唯一结果主视图，栏内滚动） ── */}
            {/* M2 布局反馈：中栏改 flex 列，行程大卡 flex-1 与右栏聊天等高（时间轴/须知各自内滚）
                2026-10-07 手机口径：手机端聊天全屏（常态展开）；点顶栏返回键
                则收起聊天、露出行程详情——两个视图在手机上互斥切换。 */}
            <main
              className={`flex min-w-0 min-h-0 flex-col gap-4 lg:overflow-hidden lg:pr-0.5 ${
                isMobileChat && drawerOpen ? 'hidden' : ''
              }`}
            >
              {!itinerary && !loading && !planState.notice && (
                <TravelOnboardingGate
                  origin={origin} onOrigin={setOrigin}
                  pace={pace} onPace={setPace}
                  diet={diet} onDiet={setDiet}
                  transport={transport} onTransport={setTransport}
                />
              )}
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
                <div className="shrink-0 flex items-start gap-2 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
                  <span className="min-w-0 flex-1 break-words">{error}</span>
                  <button type="button" onClick={() => setError('')} className="shrink-0 text-red-400 hover:text-red-600">
                    关闭
                  </button>
                </div>
              )}

              {/* ── 结果 ── */}
              {itinerary ? (
                <>
                  {/* M1 反馈：Tool 执行记录移入右栏聊天流（内联 Tool 行常驻本轮对话），
                      中栏只保留行程本体 */}
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
                  replaceCandidates={replaceCandidates}
                  replaceCandidatesVersion={replaceCandidatesVersion}
                  onRequestReplace={handleRequestReplace}
                  onAskNearby={(text) => chatRef.current?.send(text, 'canvas_action')}
                  tier={itinerary.brief.tier || 'economy'}
                  budgetNegotiation={planState.plan?.rationale?.budget_negotiation ?? null}
                  onNegotiateBudget={handleNegotiateBudget}
                  onTierChange={(t) => {
                    // M4/G1+G3 档位切换确认：先落 decision=tier_switch 留痕
                    // （from→to）再代发重排（source=tier_switch 归因进 trace）
                    void recordTravelDecision({
                      decision: 'tier_switch',
                      conversationId,
                      planVersion: itinerary.plan_version,
                      tierFrom: itinerary.brief.tier || 'economy',
                      tierTo: t,
                      payload: { message: t === 'comfortable'
                        ? '方案切换成舒适均衡型，帮我重排（住宿餐饮升档，尽量不超预算）'
                        : '方案切换成经济实用型，帮我重排（省钱优先）' },
                      source: 'tier_switch',
                    })
                    chatRef.current?.send(
                      t === 'comfortable'
                        ? '方案切换成舒适均衡型，帮我重排（住宿餐饮升档，尽量不超预算）'
                        : '方案切换成经济实用型，帮我重排（省钱优先）',
                      'tier_switch',
                    )
                  }}
                  />
                  {/* 分类候选表（验收 #10）：换入走既有代发草案管线
                      （decision=canvas_replace 留痕 + canvas_action 代发），
                      与画布换一家同一条链；生成中置灰防双发。 */}
                  <CandidatesPanel
                    conversationId={conversationId}
                    planVersion={itinerary.plan_version}
                    generating={loading}
                    activeDay={activeDay}
                    onAskReplace={(candidate, targetDay) => {
                      void recordTravelDecision({
                        decision: 'canvas_replace',
                        conversationId,
                        planVersion: itinerary.plan_version,
                        payload: {
                          candidate_name: candidate.name,
                          candidate_poi_id: candidate.poi_id,
                          target_day: targetDay,
                          candidate_reason: candidate.reason,
                          entry: 'candidates_panel',
                        },
                        source: 'canvas_action',
                      })
                      chatRef.current?.send(
                        `把「${candidate.name}」加进第 ${targetDay} 天的行程，替换其中最顺路的一个点，其他安排尽量保持不变`,
                        'canvas_action',
                      )
                    }}
                  />
                </>
              ) : loading ? (
                <GeneratingCard processState={travelProcess} expectedDays={parseInt(days, 10) || 0} />
              ) : planState.notice ? (
                <div className="shrink-0 whitespace-pre-wrap rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                  {planState.notice}
                </div>
              ) : null}
            </main>

            {/* ── 右栏：旅行助手（设计稿态1 无助手栏；生成中/有行程才出现） ── */}
            {isWide && hasRightRail && (
              <aside className="min-w-0 min-h-0">
                <TravelChatDrawer ref={chatRef} mode="panel" planVersion={itinerary?.plan_version} introMessage={itinerary ? planState.plan?.final_answer ?? '' : ''} introRationale={planState.plan?.rationale ?? null} onOpenCityGuide={() => setCityGuideOpen(true)} {...assistantProps} />
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

      {/* M3-h 城市指南抽屉（顶栏/聊天 chip 唤起；onAsk 兜底出口走聊天管线） */}
      <CityGuideDrawer
        open={cityGuideOpen}
        onClose={() => setCityGuideOpen(false)}
        destination={itinerary?.brief.destination || destination}
        onAsk={(text) => { setCityGuideOpen(false); chatRef.current?.send(text) }}
      />

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

      {/* 中窄屏：「对话改行程」边缘按钮 + 展开抽屉（态1 无助手，不出入口）
          2026-10-07 手机口径：≤md 只聊天给行程计划——行程卡/时间轴/地图在
          390px 屏放不下也不好看，故手机端让聊天全屏（抽屉常态展开、去掉贴边把手），
          行程细节请在桌面端查看。md~xl 维持原覆盖抽屉形态（把手是设计意图）。 */}
      {!isWide && hasRightRail && (
        <TravelChatDrawer
          mode="drawer"
          // 手机全屏也走 drawerOpen 这一个开关：初次进入由 effect 自动置 true，
          // 点顶栏返回键置 false → 聊天收起、露出行程详情。不能写
          // `drawerOpen || isMobileChat`——那样 isMobileChat 恒真，返回键永远失效。
          open={drawerOpen}
          isMobileFull={isMobileChat}
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

// ── 顶栏：行程条件 chips（M2 布局反馈：并入 TripKit 头部行，省一行高度） ──

function TripConditionsChips(props: {
  loading: boolean
  destination: string
  startDate: string
  days: string
  partySize: string
  pace: string
  itineraryDays: number | null
  costTotal: number | null
  budget: string
}) {
  const dayText = props.itineraryDays != null ? `${props.itineraryDays} 天` : props.days ? `${props.days} 天` : ''
  const budgetText = props.costTotal != null
    ? `合计 ¥${props.costTotal.toLocaleString()}`
    : props.budget ? `预算 ¥${Number(props.budget).toLocaleString()}` : ''
  const chips: string[] = []
  if (props.destination.trim()) chips.push(props.destination.trim())
  else if (props.loading) chips.push('解析中…')
  if (props.startDate || dayText) chips.push([props.startDate, dayText].filter(Boolean).join(' · '))
  if (props.partySize) chips.push(`${props.partySize} 人`)
  if (budgetText) chips.push(budgetText)
  chips.push(`节奏 ${PACE_LABEL[props.pace] ?? '适中'}`)
  if (props.loading) chips.push('生成中条件已锁定')
  return (
    <span className="ml-2 hidden min-w-0 items-center gap-1.5 lg:flex" aria-label="行程条件">
      {chips.map((label, i) => (
        <span
          key={i}
          className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] ${
            props.loading && i === chips.length - 1
              ? 'bg-[#fdf1e0] text-[#b4690e]'
              : 'bg-[#f5faf9] text-[#5c7074]'
          }`}
        >
          <span className="max-w-[180px] truncate">{label}</span>
        </span>
      ))}
    </span>
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

// ── 生成中占位卡（M1 反馈：事件流移入右栏聊天流，中栏只留头部 + 骨架） ──

/**
 * GeneratingCard — 生成中占位（2026-10-03 M1 反馈改版）：
 * Tool 事件流已移到右栏「旅行助手」聊天流内联展示（用户提问 → 工具动态出现 →
 * 助手回答），中栏只保留轻头部 + 行程骨架卡。进度只来自真实 SSE 事件。
 */
function GeneratingCard({
  processState,
  expectedDays,
}: {
  processState: TravelProcessState | null
  expectedDays: number
}) {
  const failed = processState?.status === 'error'
  const doneCount = (processState?.tools ?? []).filter((t) => t.status !== 'running').length
    + (processState?.requirement ? 1 : 0)

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
                ? '失败原因见右侧「旅行助手」，未用假数据补齐'
                : doneCount > 0
                  ? `已推进 ${doneCount} 步 · 工具执行过程见右侧「旅行助手」`
                  : '正在启动规划引擎 · 工具执行过程将在右侧「旅行助手」实时展示'}
            </p>
          </div>
        </div>
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


/**
 * 新用户偏好引导门（2026-10-08 拍板）：进旅游页时查偏好，全空（新用户）
 * 显示 4 题选项卡问卷；答完自动保存并把 origin/pace/diet/transport 回填
 * 表单。老用户不弹；保存失败/跳过都不拦路（引导是增强不是前置门）。
 */
function TravelOnboardingGate({
  origin, onOrigin, pace, onPace, diet, onDiet, transport, onTransport,
}: {
  origin: string; onOrigin: (v: string) => void
  pace: string; onPace: (v: string) => void
  diet: string; onDiet: (v: string) => void
  transport: string; onTransport: (v: string) => void
}) {
  // null=加载中（不渲染防闪）；false=不显示；true=显示问卷
  const [show, setShow] = useState<boolean | null>(null)

  useEffect(() => {
    let alive = true
    fetchMyPreferences().then((prefs) => {
      if (alive && isEmptyPrefs(prefs)) setShow(true)
      else if (alive) setShow(false)
    })
    return () => { alive = false }
  }, [])

  if (show === null || show === false) return null
  return (
    <TravelOnboarding
      onDone={(prefs) => {
        if (prefs.origin && !origin) onOrigin(prefs.origin)
        if (prefs.pace && !pace) onPace(prefs.pace)
        if (prefs.diet && !diet) onDiet(prefs.diet)
        if (prefs.transport && !transport) onTransport(prefs.transport)
        setShow(false)
      }}
      onSkip={() => setShow(false)}
    />
  )
}

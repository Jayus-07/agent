'use client'

/**
 * /travel — 旅游行程页（2026-09-22 P0-5）
 *
 * 独立于 /agent 对话流的旅游规划入口：
 *   - 表单组合需求 → POST /api/travel/plan（非流式，域图为纯规则规划，
 *     秒级返回；跨轮改单靠 conversation_id 线程，刷新后仍可继续改）
 *   - 结构化行程渲染：逐日时间轴 + 费用拆分 + 静态地图打点（服务端代理，
 *     Key 不出后端）+ ICS 日历导出 + 反馈（复用既有 feedback 表）
 *
 * 为什么不用 chat SSE：结构化行程（POI 坐标/时刻/费用）在 SSE 文本流里
 * 会丢失，REST 拿完整 itinerary JSON 才能做地图与导出。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fetchRaw, request } from '@/api/client'
import MarkdownContent from '@/components/chat/MarkdownContent'

// ── 类型（与后端 travel 契约对齐的最小投影） ─────────────────

interface ItineraryPoi {
  poi_id: string
  name: string
  lat: number
  lng: number
  ticket_cny: number
}

interface ItineraryItem {
  title: string
  kind: string
  start: string
  end: string
  minutes: number
  wait_minutes: number
  note: string
  poi: ItineraryPoi | null
}

interface ItineraryDay {
  day_index: number
  day_date: string | null
  items: ItineraryItem[]
  active_minutes: number
  transit_minutes: number
  cost_cny: number
}

interface Itinerary {
  brief: {
    destination: string
    days: number | null
    party_size: number
    start_date: string | null
    preferences: string[]
    must_go: string[]
    pace: string
  }
  days: ItineraryDay[]
  cost: { tickets: number; meals: number; lodging: number; transit: number; total: number }
  status: string
  plan_version: number
  warnings: string[]
}

interface PlanResponse {
  status: string
  final_answer: string
  itinerary: Itinerary | null
  clarification?: string
}

interface Recommendation {
  city: string
  score: number
  highlights: string[]
  matched_preferences: string[]
}

const PREFERENCE_OPTIONS = ['自然', '人文', '美食', '亲子', '购物', '夜生活', '摄影'] as const
const PACE_OPTIONS = [
  { value: '', label: '默认（适中）' },
  { value: 'relaxed', label: '轻松' },
  { value: 'moderate', label: '适中' },
  { value: 'intense', label: '紧凑' },
] as const

function sessionId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `t-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

// ── 静态地图（服务端代理，blob + objectURL，Key 不出后端） ─────

function StaticMap({ itinerary }: { itinerary: Itinerary }) {
  const [url, setUrl] = useState('')
  const [failed, setFailed] = useState(false)

  const markers = useMemo(() => {
    const pts: string[] = []
    for (const d of itinerary.days) {
      for (const item of d.items) {
        if (item.poi && item.kind === 'visit') {
          pts.push(`${item.poi.lat},${item.poi.lng}`)
        }
      }
    }
    return pts
  }, [itinerary])

  useEffect(() => {
    if (markers.length === 0) return
    let revoked = ''
    let alive = true
    const qs = new URLSearchParams({ size: '640*360', zoom: '12' })
    qs.set('markers', markers.slice(0, 10).join(';'))
    fetchRaw(`/api/map/static-map?${qs.toString()}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`map ${res.status}`)
        const blob = await res.blob()
        const objectUrl = URL.createObjectURL(blob)
        revoked = objectUrl
        if (alive) setUrl(objectUrl)
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [markers])

  if (markers.length === 0) return null
  if (failed) return null
  if (!url) {
    return <div className="h-48 w-full animate-pulse rounded-lg bg-gray-100" />
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={url} alt="行程地图" className="h-48 w-full rounded-lg object-cover" />
}

// ── 逐日时间轴 ───────────────────────────────────────────────

function DayTimeline({ day }: { day: ItineraryDay }) {
  const weekday = day.day_date
    ? '日一二三四五六'[new Date(day.day_date + 'T00:00:00').getDay()]
    : null
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="font-semibold text-gray-900">
          第 {day.day_index} 天
          {day.day_date && (
            <span className="ml-2 text-sm font-normal text-gray-500">
              {day.day_date} 周{weekday}
            </span>
          )}
        </h3>
        <span className="text-xs text-gray-500">
          活动 {day.active_minutes}′ ｜ 在途 {day.transit_minutes}′ ｜ ¥{day.cost_cny.toFixed(0)}
        </span>
      </div>
      <ol className="space-y-1.5">
        {day.items.map((item, idx) => (
          <li key={idx} className="flex gap-2 text-sm">
            <span className="w-24 shrink-0 font-mono text-xs leading-6 text-gray-500">
              {item.start}-{item.end}
            </span>
            <span className="leading-6">
              {item.title}
              {item.kind === 'meal' && (
                <span className="ml-1 text-xs text-orange-600">（用餐）</span>
              )}
              {item.wait_minutes > 0 && (
                <span className="ml-1 text-xs text-amber-600">等 {item.wait_minutes}′</span>
              )}
              {item.note && <span className="ml-1 text-xs text-gray-400">{item.note}</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

// ── 主页面 ───────────────────────────────────────────────────

export default function TravelPage() {
  const [destination, setDestination] = useState('')
  const [days, setDays] = useState('2')
  const [partySize, setPartySize] = useState('2')
  const [budget, setBudget] = useState('')
  const [startDate, setStartDate] = useState('')
  const [pace, setPace] = useState('')
  const [preferences, setPreferences] = useState<string[]>([])
  const [extra, setExtra] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [feedbackSent, setFeedbackSent] = useState('')
  const threadRef = useRef(sessionId())

  // 打开页面即拉一次推荐（无偏好时是纯热度榜，仍有参考价值）
  useEffect(() => {
    request<{ recommendations: Recommendation[] }>('/api/travel/recommend')
      .then((r) => setRecommendations(r.recommendations ?? []))
      .catch(() => {})
  }, [])

  const togglePreference = (tag: string) => {
    setPreferences((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    )
  }

  const submit = useCallback(async () => {
    const parts: string[] = []
    if (destination.trim()) parts.push(`${destination.trim()}`)
    else parts.push('帮我推荐个地方规划行程')
    if (days) parts.push(`${days}天`)
    if (startDate) parts.push(`${startDate}出发`)
    if (partySize) parts.push(`${partySize}个人`)
    if (budget) parts.push(`预算${budget}元`)
    if (pace) parts.push(pace === 'relaxed' ? '节奏轻松点' : pace === 'intense' ? '排紧凑些' : '节奏适中')
    if (preferences.length) parts.push(`喜欢${preferences.join('、')}`)
    if (extra.trim()) parts.push(extra.trim())

    setLoading(true)
    setError('')
    setFeedbackSent('')
    try {
      // 2026-09-22 复盘：曾因 request() 不自动序列化 body（误传对象 →
      // "[object Object]" 出网 → 422）改用 fetchRaw 绕开。现根因已在
      // client.ts 修复（JSON 层收口序列化 + 契约测试锁住），按规范回归
      // request()；直接传对象即受自动 stringify 保护。
      // timeout 55s < 网关 60s 读超时：让前端先拿到干净的超时提示而非等 504。
      const data = await request<PlanResponse>('/api/travel/plan', {
        method: 'POST',
        body: {
          message: parts.join('，'),
          session_id: threadRef.current,
          conversation_id: threadRef.current,
        },
        timeout: 55_000,
      })
      setPlan(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : '规划请求失败，请稍后再试')
    } finally {
      setLoading(false)
    }
  }, [destination, days, partySize, budget, startDate, pace, preferences, extra])

  const downloadIcs = useCallback(async () => {
    if (!plan?.itinerary) return
    try {
      const res = await fetchRaw('/api/travel/export/ics', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ itinerary: plan.itinerary }),
      })
      if (!res.ok) throw new Error(`export ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `travel-${plan.itinerary.brief.destination || 'trip'}.ics`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      setError('日历导出失败，请稍后再试')
    }
  }, [plan])

  const sendFeedback = useCallback(async (vote: 'positive' | 'negative') => {
    if (!plan?.itinerary) return
    try {
      await request('/api/travel/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: threadRef.current,
          vote,
          destination: plan.itinerary.brief.destination,
          plan_version: plan.itinerary.plan_version,
        }),
      })
      setFeedbackSent(vote)
    } catch {
      setError('反馈提交失败')
    }
  }, [plan])

  const itinerary = plan?.itinerary ?? null
  const needsInput = plan?.status === 'needs_clarification'

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <header>
        <h1 className="text-xl font-bold text-gray-900">旅游行程规划</h1>
        <p className="mt-1 text-sm text-gray-500">
          填多少算多少，缺的会追问；规划完全基于规则与真实路况/天气数据，无模型发挥。
        </p>
      </header>

      {/* 需求表单 */}
      <section className="space-y-3 rounded-xl border border-gray-200 bg-white p-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <label className="text-sm">
            <span className="text-gray-600">目的地</span>
            <input
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              placeholder="留空看推荐"
              className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-sm">
            <span className="text-gray-600">天数</span>
            <input
              type="number"
              min={1}
              max={10}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-sm">
            <span className="text-gray-600">人数</span>
            <input
              type="number"
              min={1}
              max={20}
              value={partySize}
              onChange={(e) => setPartySize(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-sm">
            <span className="text-gray-600">预算（元）</span>
            <input
              type="number"
              min={0}
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              placeholder="选填"
              className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <label className="text-sm">
            <span className="text-gray-600">出发日期</span>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-sm">
            <span className="text-gray-600">节奏</span>
            <select
              value={pace}
              onChange={(e) => setPace(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            >
              {PACE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <label className="col-span-2 text-sm">
            <span className="text-gray-600">其他要求</span>
            <input
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              placeholder="如：必去三坊七巷、不吃辣"
              className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
            />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {PREFERENCE_OPTIONS.map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => togglePreference(tag)}
              className={`rounded-full px-3 py-1 text-xs transition ${
                preferences.includes(tag)
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {tag}
            </button>
          ))}
        </div>
        {!destination.trim() && recommendations.length > 0 && (
          <div className="rounded-md bg-blue-50 p-3 text-sm text-gray-700">
            目的地还没定？可以看看：
            {recommendations.map((r) => (
              <button
                key={r.city}
                type="button"
                onClick={() => setDestination(r.city)}
                className="mx-1 underline decoration-dotted hover:text-blue-700"
              >
                {r.city}（{r.highlights.slice(0, 2).join('/')}）
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          onClick={submit}
          disabled={loading}
          className="w-full rounded-lg bg-blue-600 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? '规划中（真实路况与天气计算，约数秒）…' : '生成行程'}
        </button>
        {error && <p className="text-sm text-red-600">{error}</p>}
      </section>

      {/* 结果 */}
      {plan && (
        <section className="space-y-4">
          {needsInput && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-gray-700 whitespace-pre-wrap">
              {plan.final_answer}
            </div>
          )}
          {itinerary && (
            <>
              <div className="flex items-center justify-between">
                <h2 className="font-semibold text-gray-900">
                  {itinerary.brief.destination} · {itinerary.days.length} 天 · v
                  {itinerary.plan_version}
                  {itinerary.status !== 'ready' && (
                    <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                      {itinerary.status === 'needs_user_decision' ? '需你决定' : itinerary.status}
                    </span>
                  )}
                </h2>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={downloadIcs}
                    className="rounded-md border border-gray-300 px-3 py-1.5 text-xs hover:bg-gray-50"
                  >
                    导出日历 (ICS)
                  </button>
                  <button
                    type="button"
                    onClick={() => sendFeedback('positive')}
                    disabled={!!feedbackSent}
                    className={`rounded-md border px-3 py-1.5 text-xs ${
                      feedbackSent === 'positive'
                        ? 'border-green-300 bg-green-50 text-green-700'
                        : 'border-gray-300 hover:bg-gray-50'
                    }`}
                  >
                    👍 有用
                  </button>
                  <button
                    type="button"
                    onClick={() => sendFeedback('negative')}
                    disabled={!!feedbackSent}
                    className={`rounded-md border px-3 py-1.5 text-xs ${
                      feedbackSent === 'negative'
                        ? 'border-red-300 bg-red-50 text-red-700'
                        : 'border-gray-300 hover:bg-gray-50'
                    }`}
                  >
                    👎 要改
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 rounded-lg bg-gray-50 p-3 text-sm sm:grid-cols-5">
                {/* cost.total 是后端 pydantic @property，model_dump() 不含该字段 —— 合计前端自算 */}
                <span>门票 ¥{(itinerary.cost?.tickets ?? 0).toFixed(0)}</span>
                <span>餐饮 ¥{(itinerary.cost?.meals ?? 0).toFixed(0)}</span>
                <span>住宿 ¥{(itinerary.cost?.lodging ?? 0).toFixed(0)}</span>
                <span>通勤 ¥{(itinerary.cost?.transit ?? 0).toFixed(0)}</span>
                <span className="font-semibold">
                  合计 ¥
                  {(itinerary.cost?.total ??
                    (itinerary.cost?.tickets ?? 0) +
                      (itinerary.cost?.meals ?? 0) +
                      (itinerary.cost?.lodging ?? 0) +
                      (itinerary.cost?.transit ?? 0)
                  ).toFixed(0)}
                </span>
              </div>

              <StaticMap itinerary={itinerary} />

              <div className="space-y-3">
                {itinerary.days.map((day) => (
                  <DayTimeline key={day.day_index} day={day} />
                ))}
              </div>

              {itinerary.warnings.length > 0 && (
                <div className="rounded-lg border border-gray-200 bg-white p-4">
                  <h3 className="mb-2 text-sm font-semibold text-gray-900">需要你确认</h3>
                  <ul className="list-inside list-disc space-y-1 text-sm text-gray-600">
                    {itinerary.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
          {!needsInput && plan.final_answer && (
            <details className="rounded-lg border border-gray-200 bg-white p-4">
              <summary className="cursor-pointer text-sm text-gray-500">
                查看完整行程单（文本版，可复制发同行人）
              </summary>
              <div className="mt-3 text-sm">
                <MarkdownContent content={plan.final_answer} />
              </div>
            </details>
          )}
        </section>
      )}
    </div>
  )
}

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it } from 'vitest'
import TravelShowcase from '@/components/travel/v2/TravelShowcase'
import type { TravelTripV2 } from '@/api/travelV2'

// B4 橱窗测试（travel-value-showcase）：真实数据唯一、筛选正确、空态诚实、
// 展示侧零「穷游」（红线 1/2）。纯组件测试，不触网。

let root: Root | null = null
let container: HTMLDivElement | null = null

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  container?.remove()
  root = null
  container = null
})

function place(name: string, ticket: number | null, rating: number | null) {
  return {
    item_id: `item-${name}`, kind: 'place' as const, title: name,
    start_time: null, duration_min: 60, fixed_start: false,
    place: {
      place_id: `poi-${name}`, name, lat: null, lng: null, address: null,
      facts: {
        opening_hours: undefined, ticket_price_cny: ticket, rating,
        open_status: null, open_time_today: null, avg_cost_cny: null,
        verification: 'verified' as const, source: 'tencent:lbs', observed_at: null,
      },
    },
    must_visit: false, locked: false, note: '',
  }
}

function makeTrip(overrides: {
  trip_id?: string
  title?: string
  destination?: string
  tier?: 'economy' | 'budget' | 'comfortable'
  interests?: string[]
  estimated_cost?: number | null
  items?: Array<{ name: string; ticket: number | null; rating: number | null }>
}): TravelTripV2 {
  const items = overrides.items ?? [
    { name: '西湖', ticket: 0, rating: 4.8 },
    { name: '灵隐寺', ticket: 30, rating: 4.6 },
  ]
  return {
    trip_id: overrides.trip_id ?? 'trip-1',
    title: overrides.title ?? '杭州慢游',
    status: 'active',
    revision: 1,
    document: {
      schema_version: 2,
      brief: {
        origin: '', destination: overrides.destination ?? '杭州',
        timezone: 'Asia/Shanghai', start_date: null, day_count: 2,
        travelers: { adults: 2, children: 0 },
        budget: { amount: null, currency: 'CNY' },
        pace: 'balanced',
        tier: overrides.tier ?? 'economy',
        interests: overrides.interests ?? ['自然', '人文'],
        requirements: [],
      },
      selections: [],
      days: [{ day_id: 'd1', date: null, title: '第1天', items: items.map((i) => place(i.name, i.ticket, i.rating)), legs: [] }],
      arrangements: { lodgings: [], intercity_trains: [] },
      totals: { currency: 'CNY', estimated_cost: overrides.estimated_cost ?? 1160, transit_min: 60, distance_m: null },
      health: { status: 'ok', issues: [] },
    },
    source_template_id: null,
    source_template_version: null,
    created_at: '2026-11-01T00:00:00Z',
    updated_at: '2026-11-01T00:00:00Z',
  }
}

async function renderShowcase(trips: TravelTripV2[], loading = false) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root?.render(<TravelShowcase trips={trips} loading={loading} />))
}

it('渲染真实行程卡：档位/风格/能力信号/诚实披露，点击透传工作台', async () => {
  await renderShowcase([
    makeTrip({ destination: '厦门', estimated_cost: 330, items: [{ name: '鼓浪屿', ticket: 0, rating: 4.7 }, { name: '南普陀', ticket: 0, rating: 4.5 }] }),
  ])
  expect(container!.querySelector('section[aria-label="推荐行程"]')).not.toBeNull()
  expect(container!.textContent).toContain('推荐行程')
  expect(container!.textContent).toContain('厦门')
  expect(container!.textContent).toContain('免费门票 2 处')
  expect(container!.textContent).toContain('均分 4.6')
  expect(container!.textContent).toContain('预估 · 未核验')
  expect(container!.textContent).toContain('¥330')
  const link = container!.querySelector('a[href="/travel/itineraries/trip-1"]')
  expect(link).not.toBeNull()
  // 红线 2：展示侧零「穷游」
  expect(container!.textContent).not.toContain('穷游')
})

it('档位筛选正确过滤真实数据：平价 chip 只留 budget 行程', async () => {
  await renderShowcase([
    makeTrip({ trip_id: 'a', title: '标准行程', tier: 'economy' }),
    makeTrip({ trip_id: 'b', title: '平价行程', tier: 'budget' }),
  ])
  const budgetChip = [...container!.querySelectorAll('button')].find((b) => b.textContent === '平价')
  expect(budgetChip).toBeDefined()
  await act(async () => budgetChip?.click())
  expect(container!.textContent).toContain('平价行程')
  expect(container!.textContent).not.toContain('标准行程')
})

it('风格筛选来自真实 interests；筛空组合如实空态并给规划入口', async () => {
  await renderShowcase([makeTrip({ interests: ['美食'] })])
  const foodChip = [...container!.querySelectorAll('button')].find((b) => b.textContent === '美食')
  expect(foodChip).toBeDefined()
  const natureChip = [...container!.querySelectorAll('button')].find((b) => b.textContent === '自然')
  expect(natureChip).toBeUndefined() // 风格词表只来自真实数据，不预置
  await act(async () => foodChip?.click())
  expect(container!.textContent).toContain('杭州慢游')
  const budgetChip = [...container!.querySelectorAll('button')].find((b) => b.textContent === '平价')
  await act(async () => budgetChip?.click()) // 美食 × 平价 = 空组合
  expect(container!.textContent).toContain('还没有这类行程')
  expect(container!.querySelector('a[href="/travel/itineraries/new"]')).not.toBeNull()
})

it('无任何真实行程时如实空态，不垫种子/演示数据', async () => {
  await renderShowcase([])
  expect(container!.textContent).toContain('还没有真实行程示范')
  expect(container!.querySelector('a[href="/travel/itineraries/new"]')).not.toBeNull()
  expect(container!.querySelector('img')).toBeNull()
})

it('加载中如实展示加载态', async () => {
  await renderShowcase([], true)
  expect(container!.textContent).toContain('正在加载推荐行程…')
})

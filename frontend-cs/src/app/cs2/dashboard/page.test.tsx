// cs-workstation-v2 P7：数据看板（范围切换/六卡涨跌/意图分布/坐席绩效/角色口径）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const api = vi.hoisted(() => ({
  getDashboard: vi.fn(async (range: string) => ({
    scope: 'all',
    range,
    cards: {
      queue_waiting: 2,
      in_progress: 18,
      received: { value: 96, delta_pct: 0.08 },
      handoff_rate: { value: 0.31, delta_pp: -4.0 },
      avg_first_reply_s: { value: 42, delta_s: -8 },
      csat: { value: 4.6, delta: 0.1 },
    },
    trend: [
      { date: '10-25', received: 72, human: 25 },
      { date: '10-26', received: 81, human: 23 },
      { date: '10-27', received: 65, human: 22 },
      { date: '10-28', received: 90, human: 33 },
      { date: '10-29', received: 78, human: 26 },
      { date: '10-30', received: 96, human: 30 },
      { date: '10-31', received: 88, human: 27 },
    ],
    intents: [
      { intent: '退款/退货', count: 26, pct: 0.26 },
      { intent: '物流查询', count: 22, pct: 0.22 },
    ],
    agents: [
      {
        agent_id: 'a1',
        display_name: '张三',
        status: 'online',
        handled: 32,
        avg_first_reply_s: 38,
        aht_s: 372,
        csat: 4.7,
        qa_score: 92,
      },
      {
        agent_id: 'a2',
        display_name: '李四',
        status: 'away',
        handled: 28,
        avg_first_reply_s: 45,
        aht_s: 423,
        csat: 4.5,
        qa_score: 88,
      },
    ],
    generated_at: '2026-10-31T12:00:00Z',
  })),
}))

vi.mock('@/cs2/workspace/api', () => api)

import Cs2DashboardPage from '@/app/cs2/dashboard/page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (mounted) {
    act(() => mounted?.root.unmount())
    mounted.container.remove()
    mounted = null
  }
  vi.clearAllMocks()
})

async function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<Cs2DashboardPage />)
  })
  mounted = { container, root }
  return container
}

function findButton(c: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(c.querySelectorAll('button')).find((b) =>
    b.textContent?.includes(text),
  )
}

describe('Cs2DashboardPage（P7 数据看板）', () => {
  it('默认今日范围渲染六卡数值与涨跌', async () => {
    const c = await mount()
    await act(async () => {})
    expect(api.getDashboard).toHaveBeenCalledWith('today')
    expect(c.textContent).toContain('排队中')
    expect(c.textContent).toContain('96')
    expect(c.textContent).toContain('31%')
    expect(c.textContent).toContain('42s')
    expect(c.textContent).toContain('4.6')
    // 涨跌徽章（+8% / -4pp / -8s / +0.1）
    expect(c.textContent).toContain('+8%')
    expect(c.textContent).toContain('-4pp')
    expect(c.textContent).toContain('-8s')
    expect(c.textContent).toContain('supervisor：全量')
  })

  it('近7日趋势双柱与意图分布渲染；绩效表两行含质检分与状态', async () => {
    const c = await mount()
    await act(async () => {})
    // 趋势 7 天标签
    expect(c.textContent).toContain('10-25')
    expect(c.textContent).toContain('10-31')
    // 意图分布
    expect(c.textContent).toContain('退款/退货')
    expect(c.textContent).toContain('26%')
    // 绩效表
    expect(c.textContent).toContain('张三')
    expect(c.textContent).toContain('6m12s')
    expect(c.textContent).toContain('92')
    expect(c.textContent).toContain('在线')
    expect(c.textContent).toContain('小休')
  })

  it('切换近30日范围重新拉取', async () => {
    const c = await mount()
    await act(async () => {})
    await act(async () => {
      findButton(c, '近30日')?.click()
    })
    expect(api.getDashboard).toHaveBeenLastCalledWith('d30')
  })
})

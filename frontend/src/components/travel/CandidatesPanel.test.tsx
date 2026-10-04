import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import CandidatesPanel from './CandidatesPanel'
import type { TravelCandidatesResponse } from '@/api/travel'

const { fetchCandidates } = vi.hoisted(() => ({ fetchCandidates: vi.fn() }))
vi.mock('@/api/travel', () => ({ fetchTravelCandidates: fetchCandidates }))

let root: Root
let container: HTMLDivElement

async function mount(props: Partial<Parameters<typeof CandidatesPanel>[0]> = {}) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(
    <CandidatesPanel
      conversationId="trip"
      planVersion={2}
      {...props}
    />,
  ))
  await act(async () => {})
}

function resp(over: Partial<TravelCandidatesResponse> = {}): TravelCandidatesResponse {
  return {
    conversation_id: 'trip', plan_version: 1, destination: '福州',
    groups: {
      景点: [{ poi_id: 'p1', name: '三坊七巷', category: '景点', rating: 4.8, reason: '高分优先', source: 'amap:live' }],
      美食: [{ poi_id: 'm1', name: '同利肉燕', category: '美食', rating: 4.7, reason: '', source: 'zhihu' }],
      酒店: [],
    },
    available: true, hint: '',
    ...over,
  }
}

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  container?.remove()
  vi.clearAllMocks()
})

it('渲染非空分类 tab 与列表（酒店空组隐藏），换入按钮回调带候选与目标天', async () => {
  fetchCandidates.mockResolvedValue(resp())
  const onAskReplace = vi.fn()
  await mount({ onAskReplace, activeDay: 2 })

  const tabTexts = [...container.querySelectorAll('[role="tab"]')].map((b) => b.textContent)
  expect(tabTexts).toEqual(['景点 1', '美食 1']) // 酒店空组不显示（交通同理）
  expect(container.textContent).toContain('三坊七巷')
  expect(container.textContent).toContain('高分优先')

  const replace = [...container.querySelectorAll('button')].find((b) => b.textContent === '换入')!
  await act(async () => replace.click())
  expect(onAskReplace).toHaveBeenCalledTimes(1)
  const [candidate, targetDay] = onAskReplace.mock.calls[0]
  expect(candidate.name).toBe('三坊七巷')
  expect(targetDay).toBe(2)
})

it('旧版本候选打「来自旧版」标记（#102 口径），plan_version 徽章可见', async () => {
  fetchCandidates.mockResolvedValue(resp({ plan_version: 1 }))
  await mount({ planVersion: 3 })
  expect(container.textContent).toContain('v1')
  expect(container.textContent).toContain('来自旧版 v1')
  expect(container.textContent).toContain('当前 v3')
})

it('候选池不可达显示后端 hint，不伪造列表', async () => {
  fetchCandidates.mockResolvedValue(resp({ available: false, hint: '候选池暂不可用（会话状态已过期或未持久化）', groups: { 景点: [], 美食: [], 酒店: [] } }))
  await mount()
  expect(container.textContent).toContain('候选池暂不可用')
  expect(container.textContent).not.toContain('三坊七巷')
  expect(container.querySelector('[role="tab"]')).toBeNull()
})

it('生成中换入按钮置灰（防草案双发）', async () => {
  fetchCandidates.mockResolvedValue(resp())
  await mount({ generating: true, onAskReplace: vi.fn() })
  const replace = [...container.querySelectorAll('button')].find((b) => b.textContent === '换入') as HTMLButtonElement
  expect(replace.disabled).toBe(true)
})

it('请求失败显示加载失败提示', async () => {
  fetchCandidates.mockRejectedValue(new Error('network'))
  await mount()
  expect(container.textContent).toContain('加载失败')
})

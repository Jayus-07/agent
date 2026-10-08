import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import TravelChatDrawer from './TravelChatDrawer'

const { stream } = vi.hoisted(() => ({ stream: vi.fn() }))
vi.mock('@/api/travel', () => ({ streamTravelPlan: stream, confirmTravelPlan: vi.fn() }))
vi.mock('@/components/chat/MarkdownContent', () => ({ default: ({ content }: { content: string }) => <p>{content}</p> }))
vi.mock('@/components/chat/BudgetRing', () => ({ default: () => null }))

let root: Root
let container: HTMLDivElement
const options = [
  { label: '按 3 天参考规划', days: 3, message: '规划丽江3天行程' },
  { label: '自己填天数', days: null, message: '' },
]

async function mountWithOptions() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: {
      status: 'needs_clarification', final_answer: '想玩几天？', itinerary: null,
      clarification_options: options,
    } } }
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChatDrawer
    mode="panel" conversationId="trip" hasItinerary={false}
    pendingResponse={null} processState={null}
    onResponse={vi.fn()} onDraft={vi.fn()} onDiscardPending={vi.fn()}
    onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
  />))
  const textarea = container.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '帮我规划丽江')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
}

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  container?.remove()
  vi.clearAllMocks()
})

it('默认天数经点击接受，连点只发出一次完整规划请求', async () => {
  await mountWithOptions()
  const chip = [...container.querySelectorAll('button')].find((b) => b.textContent === '按 3 天参考规划')!
  expect(chip).toBeDefined()
  expect(stream).toHaveBeenCalledTimes(1)
  expect(stream.mock.calls[0][2]).toMatchObject({ mode: 'chat' })
  let release!: () => void
  stream.mockImplementation(async function* () {
    await new Promise<void>((resolve) => { release = resolve })
    yield { event: 'done', data: { result: { status: 'answered', final_answer: '已接收', itinerary: null } } }
  })
  await act(async () => { chip.click(); chip.click() })
  expect(stream).toHaveBeenCalledTimes(2)
  expect(stream.mock.calls[1].slice(0, 2)).toEqual(['规划丽江3天行程', 'trip'])
  await act(async () => release())
  expect(container.textContent).not.toContain('按 3 天参考规划')
})

it('自填选项聚焦输入，不创建空天数请求', async () => {
  await mountWithOptions()
  const chip = [...container.querySelectorAll('button')].find((b) => b.textContent === '自己填天数')!
  expect(chip).toBeDefined()
  await act(async () => chip.click())
  expect(stream).toHaveBeenCalledTimes(1)
  expect(document.activeElement).toBe(container.querySelector('textarea'))
  expect(container.querySelector('textarea')!.placeholder).toContain('天数')
})

it('停止后的迟到响应不能重新显示旧天数选项', async () => {
  await mountWithOptions()
  let release!: () => void
  stream.mockImplementation(async function* () {
    await new Promise<void>((resolve) => { release = resolve })
    yield { event: 'done', data: { result: {
      status: 'needs_clarification', final_answer: '迟到的回复', itinerary: null,
      clarification_options: options,
    } } }
  })
  const chip = [...container.querySelectorAll('button')].find((b) => b.textContent === '按 3 天参考规划')!
  await act(async () => chip.click())
  const stop = container.querySelector<HTMLButtonElement>('button[aria-label="停止这次调整"]')!
  expect(stop).not.toBeNull()
  await act(async () => stop.click())
  await act(async () => release())
  expect(container.textContent).not.toContain('迟到的回复')
  expect(container.textContent).not.toContain('按 3 天参考规划')
})

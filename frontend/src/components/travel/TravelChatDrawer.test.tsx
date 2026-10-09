import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import TravelChatDrawer from './TravelChatDrawer'
import type { Itinerary, PlanResponse } from '@/api/travel'

const { stream, confirm, discard, audit, getConversationMessages, saveConversationMessages } = vi.hoisted(() => ({
  stream: vi.fn(), confirm: vi.fn(), discard: vi.fn(), audit: vi.fn(),
  getConversationMessages: vi.fn().mockResolvedValue({ messages: [] }),
  saveConversationMessages: vi.fn().mockResolvedValue({ saved: 0 }),
}))
vi.mock('@/api/travel', () => ({
  streamTravelPlan: stream,
  confirmTravelPlan: confirm,
  discardTravelPlan: discard,
  recordTravelDecision: audit,
  getTravelConversationMessages: getConversationMessages,
  saveTravelConversationMessages: saveConversationMessages,
}))
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
  window.sessionStorage.clear()
  vi.clearAllMocks()
  getConversationMessages.mockReset().mockResolvedValue({ messages: [] })
  saveConversationMessages.mockReset().mockResolvedValue({ saved: 0 })
})

it('新设备从服务端恢复旅游对话消息', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  getConversationMessages.mockResolvedValue({
    messages: [
      { id: 41, role: 'user', content: '帮我安排杭州两天', created_at: '2026-10-08T00:00:00+00:00' },
      { id: 42, role: 'assistant', content: '已生成杭州行程草案', created_at: '2026-10-08T00:01:00+00:00' },
    ],
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChatDrawer
    mode="panel" conversationId="trip-remote" hasItinerary={false}
    pendingResponse={null} processState={null}
    onResponse={vi.fn()} onDraft={vi.fn()} onDiscardPending={vi.fn()}
    onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
  />))
  await act(async () => { await Promise.resolve(); await Promise.resolve() })

  expect(getConversationMessages).toHaveBeenCalledWith('trip-remote')
  expect(container.textContent).toContain('帮我安排杭州两天')
  expect(container.textContent).toContain('已生成杭州行程草案')
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 200)) })
  expect(saveConversationMessages).not.toHaveBeenCalled()
})

it('已完成的对话轮次同步到账号级历史', async () => {
  await mountWithOptions()
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 220)) })

  const calls = saveConversationMessages.mock.calls
  const saved = calls[calls.length - 1]
  expect(saved?.[0]).toBe('trip')
  expect(saved?.[1]).toEqual([
    { role: 'user', content: '帮我规划丽江' },
    { role: 'assistant', content: '想玩几天？' },
  ])
})

it('服务端历史加载较慢时保留用户刚发出的新轮次', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  let resolveHistory!: (value: { messages: Array<{
    id: number; role: 'user' | 'assistant'; content: string;
  }> }) => void
  getConversationMessages.mockImplementation(() => new Promise((resolve) => {
    resolveHistory = resolve
  }))
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: {
      status: 'answered', final_answer: '本轮回复', itinerary: null,
    } } }
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChatDrawer
    mode="panel" conversationId="trip-late" hasItinerary={false}
    pendingResponse={null} processState={null}
    onResponse={vi.fn()} onDraft={vi.fn()} onDiscardPending={vi.fn()}
    onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
  />))
  const textarea = container.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
      .set!.call(textarea, '继续问本地消息')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => {
    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 40))
  })
  expect(stream).toHaveBeenCalledTimes(1)
  expect(container.textContent).toContain('继续问本地消息')
  await act(async () => {
    resolveHistory({ messages: [
      { id: 51, role: 'user', content: '之前的旅游问题' },
      { id: 52, role: 'assistant', content: '之前的旅游回答' },
    ] })
    await Promise.resolve()
  })
  expect(container.textContent).toContain('之前的旅游问题')
  expect(container.textContent).toContain('继续问本地消息')
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 220)) })
  const calls = saveConversationMessages.mock.calls
  const saved = calls[calls.length - 1]
  expect(saved?.[1]).toEqual([
    { role: 'user', content: '之前的旅游问题' },
    { role: 'assistant', content: '之前的旅游回答' },
    { role: 'user', content: '继续问本地消息' },
    { role: 'assistant', content: '本轮回复' },
  ])
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

it('服务端放弃失败时保留草案；成功后才清理本地 pending', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  const onDiscardPending = vi.fn()
  const pendingResponse = {
    status: 'ready',
    final_answer: '',
    plan_status: 'waiting_confirmation',
    itinerary: { plan_version: 2, days: [], brief: { destination: '杭州' } },
  } as unknown as PlanResponse
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChatDrawer
    mode="panel" conversationId="trip" hasItinerary
    pendingResponse={pendingResponse} processState={null}
    onResponse={vi.fn()} onDraft={vi.fn()} onDiscardPending={onDiscardPending}
    onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
  />))
  const discardButton = [...container.querySelectorAll('button')]
    .find((button) => button.textContent?.trim() === '放弃')!
  discard.mockRejectedValueOnce(new Error('行程已更新，请刷新后重试'))
  await act(async () => discardButton.click())
  expect(onDiscardPending).not.toHaveBeenCalled()
  expect(container.textContent).toContain('行程已更新，请刷新后重试')

  discard.mockResolvedValueOnce({ status: 'ok', plan_version: 2, plan_status: 'discarded' })
  await act(async () => discardButton.click())
  expect(onDiscardPending).toHaveBeenCalledTimes(1)
  expect(discard).toHaveBeenCalledWith('trip', 2)
})

it('待确认草案期间允许提问，并强制以只读模式发送且保留草案', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  const pendingResponse = {
    status: 'ready',
    final_answer: '',
    plan_status: 'waiting_confirmation',
    itinerary: { plan_version: 4, days: [], brief: { destination: '厦门' } },
  } as unknown as PlanResponse
  const onResponse = vi.fn()
  const onDraft = vi.fn()
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: {
      status: 'answered', result_kind: 'answer',
      final_answer: '草案 v4 的行程费用估算合计约 ¥1200。', itinerary: null,
    } } }
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChatDrawer
    mode="panel" conversationId="trip" hasItinerary
    pendingResponse={pendingResponse} processState={null}
    onResponse={onResponse} onDraft={onDraft} onDiscardPending={vi.fn()}
    onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
  />))

  expect(container.textContent).toContain('草案预算怎么算')
  expect(container.textContent).toContain('为什么这样安排')
  expect(container.textContent).not.toContain('第 2 天太挤了')
  const textarea = container.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
      .set!.call(textarea, '这份草案的预算估算怎么算的？')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => textarea.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
  ))

  expect(stream).toHaveBeenCalledTimes(1)
  expect(stream.mock.calls[0][2]).toMatchObject({ mode: 'read_only' })
  expect(onDraft).not.toHaveBeenCalled()
  expect(onResponse).toHaveBeenCalledWith(expect.objectContaining({ itinerary: null }))
  expect(container.textContent).toContain('有待应用修改 · v4')
})

it('移动端草案卡默认精简展示共享差异，展开后显示完整变化', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  const active = {
    plan_version: 12,
    brief: { start_date: '2026-10-10' },
    cost: { tickets: 0, meals: 0, lodging: 0, transit: 0 },
    days: [{
      day_index: 1, day_date: '2026-10-10', items: [
        { title: '西湖', kind: 'visit', start: '09:00', end: '10:00', poi: { poi_id: 'lake', name: '西湖' } },
        { title: '灵隐寺', kind: 'visit', start: '11:00', end: '12:00', poi: { poi_id: 'temple', name: '灵隐寺' } },
        { title: '旧景点', kind: 'visit', start: '13:00', end: '14:00', poi: { poi_id: 'old', name: '旧景点' } },
      ],
    }, { day_index: 2, day_date: '2026-10-11', items: [] }],
  } as unknown as Itinerary
  const draft = {
    ...active,
    plan_version: 13,
    days: [{
      day_index: 1, day_date: '2026-10-10', items: [
        { title: '灵隐寺', kind: 'visit', start: '10:00', end: '12:00', poi: { poi_id: 'temple', name: '灵隐寺' } },
        { title: '新午餐', kind: 'meal', start: '13:00', end: '14:00', poi: null },
      ],
    }, { day_index: 2, day_date: '2026-10-11', items: [
      { title: '西湖', kind: 'visit', start: '15:00', end: '16:00', poi: { poi_id: 'lake', name: '西湖' } },
    ] }],
  } as unknown as Itinerary
  const pendingResponse = {
    status: 'ready', final_answer: '', plan_status: 'waiting_confirmation',
    base_plan_version: 12, itinerary: draft,
  } as PlanResponse
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChatDrawer
    mode="drawer" isMobileFull open conversationId="trip" hasItinerary
    itinerary={active} pendingResponse={pendingResponse} processState={null}
    onResponse={vi.fn()} onDraft={vi.fn()} onDiscardPending={vi.fn()}
    onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
  />))

  expect(container.textContent).toContain('移动 1')
  expect(container.textContent).toContain('时段调整 1')
  expect(container.textContent).toContain('查看全部 4 条变化')
  expect(container.textContent).not.toContain('新午餐')
  const expand = [...container.querySelectorAll('button')]
    .find((button) => button.textContent?.includes('查看全部 4 条变化'))!
  await act(async () => expand.click())
  expect(container.textContent).toContain('新午餐')

  await act(async () => root.render(<TravelChatDrawer
    mode="drawer" isMobileFull open conversationId="trip" hasItinerary
    itinerary={active}
    pendingResponse={{ ...pendingResponse, itinerary: { ...draft, plan_version: 14 } }}
    processState={null} onResponse={vi.fn()} onDraft={vi.fn()} onDiscardPending={vi.fn()}
    onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
  />))
  expect(container.textContent).toContain('查看全部 4 条变化')
  expect(container.textContent).not.toContain('新午餐')
})

it('首份 confirmed 行程直接进入当前行程，不进入草案确认面板', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  const onResponse = vi.fn()
  const onDraft = vi.fn()
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: {
      status: 'ready', final_answer: '', plan_status: 'confirmed',
      itinerary: { plan_version: 1, days: [], brief: { destination: '杭州' } },
    } } }
  })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<TravelChatDrawer
    mode="panel" conversationId="trip" hasItinerary={false}
    pendingResponse={null} processState={null}
    onResponse={onResponse} onDraft={onDraft} onDiscardPending={vi.fn()}
    onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
  />))
  const textarea = container.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
      .set!.call(textarea, '帮我规划杭州')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => textarea.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
  ))
  expect(onResponse).toHaveBeenCalledWith(expect.objectContaining({ plan_status: 'confirmed' }))
  expect(onDraft).not.toHaveBeenCalled()
})

it('助手消息同时显示 Reporter 回复和结构化理由', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: {
      status: 'answered', final_answer: 'LLM 回复：已按要求整理福州行程。', itinerary: null,
      rationale: { headline: { days: 2, spots: 5, must_go: [] } },
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
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
      .set!.call(textarea, '帮我规划福州两天')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => textarea.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
  ))
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)) })

  const assistantMessages = container.querySelectorAll('ul[aria-label="旅行助手消息"] li')
  expect(assistantMessages.length).toBeGreaterThan(0)
  expect(assistantMessages[assistantMessages.length - 1].textContent)
    .toContain('LLM 回复：已按要求整理福州行程。')
  expect(assistantMessages[assistantMessages.length - 1].textContent)
    .toContain('行程已按你的需求排好')
})

it('结构化业务失败的说明仍显示为助手消息', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: {
      status: 'failed', final_answer: '福州天气查询暂不可用，请稍后重试。', itinerary: null,
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
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
      .set!.call(textarea, '帮我查福州天气')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => textarea.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
  ))
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)) })

  const assistantMessages = container.querySelectorAll('ul[aria-label="旅行助手消息"] li')
  expect(assistantMessages.length).toBeGreaterThan(0)
  expect(assistantMessages[assistantMessages.length - 1].textContent)
    .toContain('福州天气查询暂不可用，请稍后重试。')
  expect(assistantMessages[assistantMessages.length - 1].textContent).toContain('未完成')
})

it('同一用户和会话重挂后恢复消息，切换用户或会话不串线', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  window.sessionStorage.setItem('agent.user_info', JSON.stringify({
    userId: 'user-15', tenantId: 'tenant-a',
  }))
  stream.mockImplementation(async function* () {
    yield { event: 'done', data: { result: {
      status: 'answered', final_answer: '杭州天气晴朗', itinerary: null,
    } } }
  })

  const renderDrawer = async (conversationId: string, mode: 'panel' | 'drawer' = 'panel') => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    await act(async () => root.render(<TravelChatDrawer
      mode={mode} conversationId={conversationId} hasItinerary={false}
      pendingResponse={null} processState={null}
      onResponse={vi.fn()} onDraft={vi.fn()} onDiscardPending={vi.fn()}
      onProcessEvent={vi.fn()} onStartNewTrip={vi.fn()}
    />))
  }

  await renderDrawer('trip-1')
  const textarea = container.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!
      .set!.call(textarea, '帮我查杭州天气')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => textarea.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
  ))
  expect(container.textContent).toContain('帮我查杭州天气')
  const storedMessages = JSON.parse(window.sessionStorage.getItem(
    'travel:conversation:v1:tenant-a:user-15:trip-1',
  ) || '[]')
  expect(storedMessages).toEqual(expect.arrayContaining([
    expect.objectContaining({
      conversationId: 'trip-1',
      turnId: expect.any(String),
      id: expect.stringContaining(':user'),
      role: 'user',
      text: '帮我查杭州天气',
    }),
  ]))

  await act(async () => root.unmount())
  container.remove()
  await renderDrawer('trip-1', 'drawer')
  expect(container.textContent).toContain('帮我查杭州天气')

  await act(async () => root.unmount())
  container.remove()
  await renderDrawer('trip-2')
  expect(container.textContent).not.toContain('帮我查杭州天气')

  await act(async () => root.unmount())
  container.remove()
  window.sessionStorage.setItem('agent.user_info', JSON.stringify({
    userId: 'user-15', tenantId: 'tenant-b',
  }))
  await renderDrawer('trip-1')
  expect(container.textContent).not.toContain('帮我查杭州天气')

  await act(async () => root.unmount())
  container.remove()
  window.sessionStorage.setItem('agent.user_info', JSON.stringify({
    userId: 'user-16', tenantId: 'tenant-a',
  }))
  await renderDrawer('trip-1')
  expect(container.textContent).not.toContain('帮我查杭州天气')
})

/**
 * useSSE — 会话级模型覆盖透传（B.9 决策② / docs/model-config-admin-ui-design.md §14.1）
 *
 * 守卫两条不变量：
 *   1. 未设置会话覆盖时，请求体**不带** model（走后端全局默认）；
 *   2. 设置了会话覆盖时，请求体带 model —— 否则「选了模型却仍跑全局」。
 *
 * 项目未引入 @testing-library，按既有约定用 react-dom 直渲 + act。
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { useChatStore } from '@/store/chat'

vi.mock('@/api/chat', () => ({
  streamChat: vi.fn(async function * () { /* 立即结束：不 yield，直接收尾 */ }),
  abortChat: vi.fn(async () => undefined),
}))

import { streamChat } from '@/api/chat'
import { useAbortStreamOnUnmount, useSSE } from './useSSE'
import { abortChat } from '@/api/chat'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

type SSEApi = ReturnType<typeof useSSE>

function mount(): SSEApi {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const api: { current: SSEApi | null } = { current: null }
  act(() => root.render(<Harness onApi={(a) => { api.current = a }} />))
  mounted.push({ container, root })
  if (!api.current) throw new Error('useSSE 未挂载')
  return api.current
}

function Harness({ onApi }: { onApi: (api: SSEApi) => void }) {
  useAbortStreamOnUnmount()
  onApi(useSSE())
  return null
}

/** 冲掉 async generator / effect 引起的 microtask 队列 */
async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
}

afterEach(() => {
  vi.clearAllMocks()
  useChatStore.setState({ sessionModel: undefined, sessions: [], currentId: undefined })
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('useSSE 会话级模型覆盖', () => {
  it('长任务交接只保存任务编号，后续继续不重发原问题', async () => {
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield { event: 'done', data: { answer: '长任务已开始', elapsed: 0, task_id: 'task-1' } }
    })
    const sessionId = useChatStore.getState().newSession()
    const api = mount()
    await act(async () => { await api.startStream('写长报告', sessionId) })
    const message = useChatStore.getState().sessions.find((s) => s.id === sessionId)?.messages.at(-1)
    expect(message?.taskId).toBe('task-1')
    expect(message?.content).toBe('长任务已开始')
    expect(useChatStore.getState().isLoading).toBe(false)
    expect(streamChat).toHaveBeenCalledTimes(1)
  })

  it('主动停止保留已生成正文并标明可能不完整，晚到内容不能覆盖正文', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield { event: 'delta', data: { content: '已经生成的部分回答' } } as any
      await gate
      yield { event: 'delta', data: { content: '停止后晚到内容' } } as any
    })
    const sessionId = useChatStore.getState().newSession()
    const api = mount()
    let pending!: Promise<void>
    await act(async () => { pending = api.startStream('测试停止', sessionId) })
    await flush()
    expect(useChatStore.getState().deltaText).toBe('已经生成的部分回答')
    await act(async () => { await api.stopStream() })
    release()
    await act(async () => { await pending })
    const session = useChatStore.getState().sessions.find((s) => s.id === sessionId)!
    expect(session.messages.at(-1)?.content).toBe(
      '已经生成的部分回答\n\n> 已停止生成；以上内容可能不完整。',
    )
    expect(useChatStore.getState().isLoading).toBe(false)
  })

  it('首个回答片段到达前停止时也显示明确状态', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    vi.mocked(streamChat).mockImplementation(async function* () {
      await gate
      yield { event: 'delta', data: { content: '停止后的迟到回答' } } as any
    })
    const sessionId = useChatStore.getState().newSession()
    const api = mount()
    let pending!: Promise<void>
    await act(async () => { pending = api.startStream('尚未回答时停止', sessionId) })
    await flush()
    await act(async () => { await api.stopStream() })
    release()
    await act(async () => { await pending })
    const message = useChatStore.getState().sessions
      .find((session) => session.id === sessionId)?.messages.at(-1)
    expect(message?.content).toBe('已停止生成，尚未收到可展示的回复内容。')
  })

  it('切换会话时立即通知后端停止旧请求并保留已生成内容', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield { event: 'delta', data: { content: '已经生成的旧会话回答' } } as any
      await gate
      yield { event: 'delta', data: { content: '切换后不应出现的内容' } } as any
    })
    const oldSessionId = useChatStore.getState().newSession()
    const api = mount()
    let pending!: Promise<void>
    await act(async () => { pending = api.startStream('旧会话问题', oldSessionId) })
    await flush()

    const call = vi.mocked(streamChat).mock.calls[0]
    if (!call) throw new Error('流请求未创建')
    const [request, signal] = call
    if (!signal) throw new Error('流请求没有中止信号')
    expect(signal.aborted).toBe(false)
    let newSessionId = ''
    await act(async () => { newSessionId = useChatStore.getState().newSession() })

    expect(signal.aborted).toBe(true)
    expect(abortChat).toHaveBeenCalledWith(oldSessionId, request.request_id, 'agent')
    const oldMessage = useChatStore.getState().sessions
      .find((session) => session.id === oldSessionId)?.messages.at(-1)
    expect(oldMessage?.content).toBe(
      '已经生成的旧会话回答\n\n> 已停止生成；以上内容可能不完整。',
    )
    expect(oldMessage?.trace).toBeDefined()

    release()
    await act(async () => { await pending })
    expect(useChatStore.getState().currentId).toBe(newSessionId)
    expect(useChatStore.getState().sessions
      .find((session) => session.id === oldSessionId)?.messages.at(-1)?.content)
      .toBe('已经生成的旧会话回答\n\n> 已停止生成；以上内容可能不完整。')
  })

  it('离开企业助手页面时停止在途请求并保存当前回答', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield { event: 'delta', data: { content: '页面离开前的回答' } } as any
      await gate
      yield { event: 'delta', data: { content: '离开页面后的迟到内容' } } as any
    })
    const sessionId = useChatStore.getState().newSession()
    const api = mount()
    let pending!: Promise<void>
    await act(async () => { pending = api.startStream('页面卸载测试', sessionId) })
    await flush()

    const call = vi.mocked(streamChat).mock.calls[0]
    if (!call) throw new Error('流请求未创建')
    const [request, signal] = call
    if (!signal) throw new Error('流请求没有中止信号')
    const entry = mounted.pop()
    if (!entry) throw new Error('聊天主视图未挂载')
    act(() => entry.root.unmount())
    entry.container.remove()

    expect(signal.aborted).toBe(true)
    expect(abortChat).toHaveBeenCalledWith(sessionId, request.request_id, 'agent')
    expect(useChatStore.getState().sessions
      .find((session) => session.id === sessionId)?.messages.at(-1)?.content)
      .toBe('页面离开前的回答\n\n> 已停止生成；以上内容可能不完整。')

    release()
    await act(async () => { await pending })
  })

  it('收到 done 后切换会话仍保留服务端定稿且不再请求中止', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield { event: 'delta', data: { content: '流式预览' } } as any
      yield { event: 'done', data: {
        answer: '服务端定稿答案', elapsed: 1, sources: [],
      } } as any
      await gate
    })
    const oldSessionId = useChatStore.getState().newSession()
    const api = mount()
    let pending!: Promise<void>
    await act(async () => { pending = api.startStream('完成态切换测试', oldSessionId) })
    await flush()
    expect(useChatStore.getState().sessions
      .find((session) => session.id === oldSessionId)?.messages.at(-1)?.content)
      .toBe('服务端定稿答案')

    let newSessionId = ''
    await act(async () => { newSessionId = useChatStore.getState().newSession() })
    expect(useChatStore.getState().currentId).toBe(newSessionId)
    expect(useChatStore.getState().sessions
      .find((session) => session.id === oldSessionId)?.messages.at(-1)?.content)
      .toBe('服务端定稿答案')
    expect(abortChat).not.toHaveBeenCalled()

    release()
    await act(async () => { await pending })
  })

  it.each(['event', 'exception'])('切换会话后忽略旧请求晚到的%s错误', async (kind) => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    vi.mocked(streamChat).mockImplementation(async function* () {
      await gate
      if (kind === 'exception') throw new Error('旧请求连接异常')
      yield { event: 'error', data: { message: '旧请求失败', code: 'UNKNOWN' } } as any
    })
    const oldId = useChatStore.getState().newSession()
    const api = mount()
    let pending!: Promise<void>
    await act(async () => { pending = api.startStream('旧问题', oldId) })
    const currentId = useChatStore.getState().newSession()
    useChatStore.setState({ currentRequestId: 'new-request', isLoading: true })
    release()
    await act(async () => { await pending })
    expect(useChatStore.getState().currentId).toBe(currentId)
    expect(useChatStore.getState().error).toBeNull()
    expect(useChatStore.getState().currentRequestId).toBe('new-request')
    expect(useChatStore.getState().isLoading).toBe(true)
  })

  it('会话未设覆盖时，请求体不带 model 字段', async () => {
    useChatStore.setState({ sessionModel: null })
    const api = mount()

    await act(async () => { await api.startStream('问题', 's1') })
    await flush()

    const body = vi.mocked(streamChat).mock.calls[0][0] as unknown as Record<string, unknown>
    expect(body.model).toBeUndefined()
    // 幂等键仍需照常带上（并发会话引入的契约，不能被本次改动破坏）
    expect(body.idempotency_key).toBeTruthy()
  })

  it('会话设置覆盖后，请求体带该 model', async () => {
    useChatStore.setState({ sessionModel: 'model-b' })
    const api = mount()

    await act(async () => { await api.startStream('问题', 's1') })
    await flush()

    const body = vi.mocked(streamChat).mock.calls[0][0] as unknown as Record<string, unknown>
    expect(body.model).toBe('model-b')
  })

  it('done.answer 覆盖流式预览并成为最终展示正文', async () => {
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield { event: 'delta', data: { content: '临时流式片段', ts: 1 } } as any
      yield {
        event: 'done',
        data: {
          elapsed: 0,
          sources: [],
          answer: '最终权威答案',
          answer_source: 'reporter',
        },
      } as any
    })
    const sessionId = useChatStore.getState().newSession()
    const api = mount()

    await act(async () => { await api.startStream('问题', sessionId) })
    await flush()

    const session = useChatStore.getState().sessions.find((item) => item.id === sessionId)
    const messages = session?.messages ?? []
    expect(messages[messages.length - 1]?.content).toBe('最终权威答案')
  })

  it('done.sql_results 保存到助手消息供结果卡片展示', async () => {
    const sqlResults = [{
      title: '本月销售额', columns: ['月份', '销售额'],
      rows: [['2026-11', 12345]], total_rows: 1, omitted_columns: 0,
    }]
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield {
        event: 'done',
        data: { elapsed: 0, sources: [], answer: '本月销售额为 12,345。', sql_results: sqlResults },
      } as any
    })
    const sessionId = useChatStore.getState().newSession()
    const api = mount()

    await act(async () => { await api.startStream('查本月销售额', sessionId) })
    await flush()

    const message = useChatStore.getState().sessions.find((item) => item.id === sessionId)?.messages.at(-1)
    expect(message?.sqlResults).toEqual(sqlResults)
  })
})

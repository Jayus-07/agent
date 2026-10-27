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
import { useSSE } from './useSSE'

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

  it('主动停止保留已生成正文和过程，晚到内容不能覆盖正文', async () => {
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
    expect(session.messages.at(-1)?.content).toBe('已经生成的部分回答')
    expect(useChatStore.getState().isLoading).toBe(false)
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
})

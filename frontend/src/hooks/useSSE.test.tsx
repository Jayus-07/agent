/**
 * useSSE — 会话级模型覆盖透传（B.9 决策② / docs/model-config-admin-ui-design.md §16）
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
  useChatStore.setState({ sessionModel: null, sessions: [], currentId: null })
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('useSSE 会话级模型覆盖', () => {
  it('会话未设覆盖时，请求体不带 model 字段', async () => {
    useChatStore.setState({ sessionModel: null })
    const api = mount()

    await act(async () => { await api.startStream('问题', 's1') })
    await flush()

    const body = vi.mocked(streamChat).mock.calls[0][0] as Record<string, unknown>
    expect(body.model).toBeUndefined()
    // 幂等键仍需照常带上（并发会话引入的契约，不能被本次改动破坏）
    expect(body.idempotency_key).toBeTruthy()
  })

  it('会话设置覆盖后，请求体带该 model', async () => {
    useChatStore.setState({ sessionModel: 'model-b' })
    const api = mount()

    await act(async () => { await api.startStream('问题', 's1') })
    await flush()

    const body = vi.mocked(streamChat).mock.calls[0][0] as Record<string, unknown>
    expect(body.model).toBe('model-b')
  })
})

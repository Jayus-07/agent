import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getParticipants = vi.hoisted(() => vi.fn())
const streamRoundtable = vi.hoisted(() => vi.fn())

vi.mock('@/api/roundtable', () => ({
  roundtableService: { getParticipants, streamRoundtable },
}))
// MarkdownContent 依赖 react-markdown 全家桶，测试中直接透传文本
vi.mock('@/components/chat/MarkdownContent', () => ({
  default: ({ content }: { content: string }) => <span>{content}</span>,
}))

import RoundtablePage from './page'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const PARTICIPANTS = {
  limits: {
    min_participants: 2, max_participants: 5, max_rounds: 4,
    topic_max_chars: 200, max_total_llm_calls: 24,
  },
  participants: [
    { route_mode: 'travel', display_name: '行程规划师', label: '旅游规划', tools: ['travel.poi_search'] },
    { route_mode: 'customer_service', display_name: '客服专员', label: '智能客服', tools: ['rag.search'] },
    { route_mode: 'selection_funnel', display_name: '选品分析师', label: '智能选品', tools: ['web.search'] },
  ],
}

function scriptStream() {
  return (async function* () {
    yield { event: 'meta', data: { request_id: 'r1', topic: '测试话题', max_rounds: 1, participants: PARTICIPANTS.participants, ts: 1 } }
    yield { event: 'status', data: { stage: 'thinking', message: '行程规划师 正在思考…', ts: 2 } }
    yield { event: 'tool_result', data: { agent: 'travel', display_name: '行程规划师', capability: 'travel.poi_search', ok: true, summary: '0.1s · 10 字符', ts: 3 } }
    yield { event: 'message', data: { agent: 'travel', agent_label: '旅游规划', display_name: '行程规划师', round: 1, content: '规划师发言内容', ts: 4 } }
    yield { event: 'message', data: { agent: 'customer_service', agent_label: '智能客服', display_name: '客服专员', round: 1, content: '客服发言内容', ts: 5 } }
    yield { event: 'done', data: { elapsed: 1.5, rounds: 1, message_count: 2, ts: 6 } }
  })()
}

const mounted: { container: HTMLDivElement; root: Root }[] = []

async function mountPage() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted.push({ container, root })
  await act(async () => {
    root.render(<RoundtablePage />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

beforeEach(() => {
  sessionStorage.clear()
  sessionStorage.setItem('agent.user_info', JSON.stringify({ roles: ['admin'] }))
  getParticipants.mockResolvedValue(PARTICIPANTS)
  streamRoundtable.mockImplementation(scriptStream)
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  sessionStorage.clear()
  vi.clearAllMocks()
})

describe('Agent 圆桌页面', () => {
  it('viewer 无权限：只显示禁止卡片，不请求角色清单', async () => {
    sessionStorage.setItem('agent.user_info', JSON.stringify({ roles: ['viewer'] }))
    const container = await mountPage()
    expect(container.textContent).toContain('没有访问「Agent 圆桌」的权限')
    expect(getParticipants).not.toHaveBeenCalled()
    expect(container.textContent).not.toContain('讨论话题')
  })

  it('渲染配置面板：话题、角色清单、轮数与开始按钮', async () => {
    const container = await mountPage()
    expect(container.textContent).toContain('讨论话题')
    expect(container.textContent).toContain('行程规划师')
    expect(container.textContent).toContain('客服专员')
    expect(container.textContent).toContain('选品分析师')
    expect(container.textContent).toContain('开始圆桌')
    const checkboxes = container.querySelectorAll('input[type="checkbox"]')
    expect(checkboxes.length).toBe(3)
    // 默认预选前 3 个角色
    expect([...checkboxes].every((c) => (c as HTMLInputElement).checked)).toBe(true)
  })

  it('点击开始后消费 SSE 流：meta 条、消息气泡与 done 统计依次渲染', async () => {
    const container = await mountPage()
    const startButton = [...container.querySelectorAll('button')]
      .find((b) => b.textContent?.includes('开始圆桌'))
    expect(startButton).toBeTruthy()

    await act(async () => {
      startButton?.click()
      await new Promise((resolve) => setTimeout(resolve, 10))
    })

    expect(container.textContent).toContain('测试话题')
    expect(container.textContent).toContain('规划师发言内容')
    expect(container.textContent).toContain('客服发言内容')
    expect(container.textContent).toContain('travel.poi_search')
    expect(container.textContent).toContain('圆桌结束')
    expect(container.textContent).toContain('2 条发言')
  })

  it('流抛错时显示错误提示，不当成成功', async () => {
    streamRoundtable.mockImplementation(() => (async function* () {
      yield { event: 'meta', data: { request_id: 'r2', topic: 't', max_rounds: 1, participants: [], ts: 1 } }
      throw new Error('POST /api/roundtable/stream 失败 (500)')
    })())
    const container = await mountPage()
    const startButton = [...container.querySelectorAll('button')]
      .find((b) => b.textContent?.includes('开始圆桌'))

    await act(async () => {
      startButton?.click()
      await new Promise((resolve) => setTimeout(resolve, 10))
    })

    expect(container.textContent).toContain('POST /api/roundtable/stream 失败 (500)')
    expect(container.textContent).not.toContain('圆桌结束')
  })
})

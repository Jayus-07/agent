import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, expect, it } from 'vitest'
import type { SSEStreamEvent } from '@/lib/types'
import { useChatStore } from '@/store/chat'
import AgentTimeline, { streamPhaseLabel, streamProgressLabel } from './AgentTimeline'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mountTimeline(): HTMLElement {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<AgentTimeline collapsed={false} onToggle={() => {}} bare />))
  mounted.push({ container, root })
  return container
}

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
  useChatStore.setState({
    streamEvents: [],
    nodeLabels: {},
    currentStatus: '',
    isLoading: false,
  })
})

it('将结构化执行阶段翻译为用户可理解的过程标签', () => {
  expect(streamPhaseLabel('understanding')).toBe('需求理解')
  expect(streamPhaseLabel('tool_start')).toBe('Tool 调用')
  expect(streamPhaseLabel('tool_result')).toBe('Tool 返回')
  expect(streamPhaseLabel('other')).toBe('执行进度')
})

it('没有阶段事件时显示路由中的实时提示，而不是固定的规划文案', () => {
  expect(streamProgressLabel([], '', {})).toBe('正在识别问题并选择处理路径…')
})

it('将 RAG 检索阶段显示为当前实时进度', () => {
  const event: SSEStreamEvent = {
    event: 'log',
    data: {
      level: 'info',
      node: 'rag_skill',
      step_id: 'rag_search',
      message: '正在检索知识库',
      payload: { phase: 'rag_search', tool: 'rag.search' },
      ts: 1,
    },
  }

  expect(streamProgressLabel([event], 'rag_skill', {})).toBe('正在检索知识库并核验资料…')
})

it('将 SQL 真实阶段显示为当前实时进度', () => {
  const event: SSEStreamEvent = {
    event: 'log',
    data: {
      level: 'info',
      node: 'sql_generator',
      step_id: 'sql_generation',
      message: '正在生成查询语句',
      payload: { phase: 'sql_generation', tool: 'sql.query' },
      ts: 1,
    },
  }

  expect(streamProgressLabel([event], 'sql_generator', {})).toBe('正在生成 SQL 查询…')
})

it('Reporter 阶段更新为整理最终答复', () => {
  const event: SSEStreamEvent = {
    event: 'status',
    data: { node: 'reporter', ts: 1 },
  }

  expect(streamProgressLabel([event], 'reporter', {})).toBe('正在整理最终答复…')
})

it('把后端 SSE 阶段更新即时显示在聊天中的进度行', () => {
  useChatStore.setState({ isLoading: true, streamEvents: [], currentStatus: '' })
  const container = mountTimeline()
  expect(container.textContent).toContain('正在识别问题并选择处理路径…')

  const ragEvents: SSEStreamEvent[] = [
    { event: 'status', data: { node: 'rag_skill', ts: 1 } },
    {
      event: 'log',
      data: {
        level: 'info', node: 'rag_skill', step_id: 'rag_search',
        message: '正在检索知识库并核验资料',
        payload: { phase: 'rag_search', tool: 'rag.search' }, ts: 2,
      },
    },
  ]
  act(() => useChatStore.setState({ streamEvents: ragEvents, currentStatus: 'rag_skill' }))
  expect(container.textContent).toContain('正在检索知识库并核验资料…')

  const sqlEvents: SSEStreamEvent[] = [
    { event: 'status', data: { node: 'sql_skill', ts: 3 } },
    {
      event: 'log',
      data: {
        level: 'info', node: 'sql_generator', step_id: 'sql_generation',
        message: '正在生成查询语句',
        payload: { phase: 'sql_generation', tool: 'sql.query' }, ts: 4,
      },
    },
  ]
  act(() => useChatStore.setState({ streamEvents: sqlEvents, currentStatus: 'sql_skill' }))
  expect(container.textContent).toContain('正在生成 SQL 查询…')
})

it('按后端节点实测耗时显示阶段时长，不用总耗时反推末节点', () => {
  const events = [
    {
      event: 'status',
      data: {
        node: 'rag_skill',
        ts: 100,
        phase: 'started',
        execution_id: 'rag_skill:direct_1',
        started_at: 100,
      },
    },
    {
      event: 'status',
      data: {
        node: 'rag_skill',
        ts: 110.987,
        phase: 'completed',
        execution_id: 'rag_skill:direct_1',
        started_at: 100,
        finished_at: 110.987,
        duration_ms: 10987,
        status: 'done',
      },
    },
  ] as any as SSEStreamEvent[]
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(
    <AgentTimeline
      collapsed={false}
      onToggle={() => {}}
      events={events}
      nodeLabels={{ rag_skill: '知识库检索' }}
      totalElapsedHint={22.1}
      bare
    />,
  ))
  mounted.push({ container, root })

  expect(container.textContent).toContain('10.99s')
  expect(container.textContent).toContain('22.1s')
})

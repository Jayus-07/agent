import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import CSMessageList from './CSMessageList'
import type { CSMessage } from '@/store/csChat'

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  Element.prototype.scrollIntoView = () => undefined
})

describe('CSMessageList live progress', () => {
  let container: HTMLDivElement
  let root: Root

  afterEach(() => {
    if (root) act(() => root.unmount())
    container?.remove()
  })

  it('只把真实 status 节点时间线显示为处理中进度', () => {
    const messages: CSMessage[] = [
      { id: 'assistant-1', role: 'assistant', content: '正在查询订单', timestamp: 1 },
    ]
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)

    act(() => root.render(
      <CSMessageList
        messages={messages}
        isLoading
        currentNode="cs_query_expert"
        timeline={["cs_graph_node", "cs_query_expert"]}
      />,
    ))

    expect(container.querySelector('[data-testid="cs-live-progress"]')?.textContent)
      .toContain('本轮处理进度')
    expect(container.textContent).toContain('业务查询')
  })
})

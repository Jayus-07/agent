import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import InputOutputPanel from './InputOutputPanel'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
})

async function mountPanel(props: Parameters<typeof InputOutputPanel>[0]) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<InputOutputPanel {...props} />)
  })
  return container
}

describe('InputOutputPanel（问题与结果）', () => {
  it('展示用户问题与运行结果', async () => {
    const container = await mountPanel({
      question: '企业订单的发货时效是怎么规定的',
      answer: '现货商品24小时内发出。[E1]',
      answerLen: 520,
    })
    expect(container.textContent).toContain('企业订单的发货时效是怎么规定的')
    expect(container.textContent).toContain('现货商品24小时内发出。[E1]')
    expect(container.querySelector('section[aria-label="问题与结果"]')).toBeTruthy()
  })

  it('回答被截断存储时说明预览口径', async () => {
    const container = await mountPanel({
      question: 'q',
      answer: 'a'.repeat(200),
      answerLen: 1500,
    })
    expect(container.textContent).toContain('存储保留前 200 字预览')
    expect(container.textContent).toContain('1500')
  })

  it('展示错误码、错误类别与错误消息', async () => {
    const container = await mountPanel({
      question: 'q',
      answer: '',
      error: {
        code: 'SIMULATED_TIMEOUT',
        class: 'timeout',
        message: '模拟上游响应超时',
      },
    })
    expect(container.textContent).toContain('SIMULATED_TIMEOUT')
    expect(container.textContent).toContain('timeout')
    expect(container.textContent).toContain('模拟上游响应超时')
  })

  it('错误码不满足安全标签时不回显原文', async () => {
    const container = await mountPanel({
      question: 'q',
      answer: '',
      error: { code: '带 空格/<script> 的不安全码', message: '' },
    })
    expect(container.textContent).toContain('错误码文本已隐藏')
    expect(container.textContent).not.toContain('<script>')
    expect(container.textContent).toContain('错误消息未采集')
  })

  it('空值如实显示未采集，不伪造内容', async () => {
    const container = await mountPanel({ question: '', answer: '' })
    expect(container.textContent).toContain('未采集')
  })
})

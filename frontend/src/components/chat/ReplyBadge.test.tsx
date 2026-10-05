/** ReplyBadge 回复归因徽章回归 — 稳定码映射 + 渲染/未知码不渲染（2026-10-05 回复呈现规范一期） */
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import ReplyBadge, { replySourceMeta } from './ReplyBadge'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function renderBadge(source: string): HTMLElement {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted.push({ container, root })
  act(() => root.render(<ReplyBadge source={source} />))
  return container
}

afterEach(() => {
  for (const m of mounted) {
    act(() => m.root.unmount())
    m.container.remove()
  }
  mounted.length = 0
})

describe('replySourceMeta — 稳定码 → 徽章文案', () => {
  it('四种已知稳定码映射中文文案', () => {
    expect(replySourceMeta('knowledge_base')!.label).toBe('基于知识库回答')
    expect(replySourceMeta('data_analysis')!.label).toBe('基于业务数据分析')
    expect(replySourceMeta('realtime_query')!.label).toBe('基于实时数据查询')
    expect(replySourceMeta('system_notice')!.label).toBe('系统提示')
  })

  it('未知稳定码返回 null（前端静默不渲染，向后兼容）', () => {
    expect(replySourceMeta('unknown_code')).toBeNull()
    expect(replySourceMeta('')).toBeNull()
  })
})

describe('ReplyBadge 渲染', () => {
  it('已知稳定码渲染徽章文案', () => {
    const el = renderBadge('knowledge_base')
    const badge = el.querySelector('[data-testid="reply-badge"]')
    expect(badge).not.toBeNull()
    expect(badge!.textContent).toContain('基于知识库回答')
  })

  it('未知稳定码不渲染任何节点', () => {
    const el = renderBadge('agent') // 架构词不是稳定码，永不上用户面
    expect(el.querySelector('[data-testid="reply-badge"]')).toBeNull()
  })
})

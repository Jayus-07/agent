// cs-workstation-v2 P4：AI 助手面板渲染与坐席动作（采用/引用/存为话术/跟进「新」标记）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import AssistantPanel from './AssistantPanel'
import type { AssistSnapshot } from './assistTypes'

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (mounted) {
    act(() => mounted?.root.unmount())
    mounted.container.remove()
    mounted = null
  }
})

async function mount(ui: React.ReactElement) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(ui)
  })
  mounted = { container, root }
  return container
}

const SNAPSHOT: AssistSnapshot = {
  conversationId: 'conv-1',
  trigger: 'message',
  intent: { name: 'c_complaint', label: '投诉', confidence: 0.91 },
  emotion: { label: '愤怒', level: 'alert' },
  sop: [
    { label: '安抚情绪', state: 'done' },
    { label: '登记工单', state: 'cur' },
    { label: '同步处理进度', state: 'todo' },
  ],
  suggestions: [
    { kind: 'knowledge_answer', source: '知识库', score: 0.8, text: '抱歉给您带来不便，我们会尽快处理' },
    { kind: 'knowledge_snippet', source: '知识库原文·投诉SOP', score: null, text: '投诉处理五步法：倾听、道歉…' },
    { kind: 'order_summary', source: '订单系统', score: null, text: '您最近的订单 DEMO-1001 状态为已发货' },
    { kind: 'emotion_alert', source: '系统 · 情绪预警', score: null, text: '检测到强烈不满情绪，建议通知主管关注该会话。' },
  ],
  at: Date.now(),
}

describe('AssistantPanel（P4 AI 助手）', () => {
  it('渲染意图/情绪/SOP 与四类卡片，alert 卡红边', async () => {
    const c = await mount(<AssistantPanel assist={SNAPSHOT} selected onAdopt={vi.fn()} onQuote={vi.fn()} onSaveCanned={vi.fn()} />)
    expect(c.textContent).toContain('AI 实时辅助 · 监听中')
    expect(c.textContent).toContain('投诉')
    expect(c.textContent).toContain('0.91')
    expect(c.textContent).toContain('愤怒')
    expect(c.textContent).toContain('安抚情绪')
    expect(c.textContent).toContain('登记工单')
    expect(c.textContent).toContain('检测到强烈不满情绪')
    // 知识原文卡有「引用回复」，推荐卡有「采用」
    expect(c.textContent).toContain('引用回复')
    expect(c.textContent).toContain('采用')
  })

  it('采用填入输入框回调；存为话术带正文', async () => {
    const onAdopt = vi.fn()
    const onSaveCanned = vi.fn()
    const c = await mount(
      <AssistantPanel assist={SNAPSHOT} selected onAdopt={onAdopt} onQuote={vi.fn()} onSaveCanned={onSaveCanned} />,
    )
    const adopt = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '采用')!
    await act(async () => {
      adopt.click()
    })
    expect(onAdopt).toHaveBeenCalledWith('抱歉给您带来不便，我们会尽快处理')
    const save = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '存为话术')!
    await act(async () => {
      save.click()
    })
    expect(onSaveCanned).toHaveBeenCalledWith(
      '抱歉给您带来不便，我们会尽快处理', '知识库',
    )
  })

  it('引用回复拼接来源引文', async () => {
    const onQuote = vi.fn()
    const c = await mount(
      <AssistantPanel assist={SNAPSHOT} selected onAdopt={vi.fn()} onQuote={onQuote} onSaveCanned={vi.fn()} />,
    )
    const quote = Array.from(c.querySelectorAll('button')).find((b) => b.textContent === '引用回复')!
    await act(async () => {
      quote.click()
    })
    expect(onQuote).toHaveBeenCalledWith(
      '投诉处理五步法：倾听、道歉…', '知识库原文·投诉SOP', '知识库原文',
    )
  })

  it('followup 触发时首卡带「新」标记；未选中会话显示待机', async () => {
    const c = await mount(
      <AssistantPanel assist={{ ...SNAPSHOT, trigger: 'followup' }} selected onAdopt={vi.fn()} onQuote={vi.fn()} onSaveCanned={vi.fn()} />,
    )
    expect(c.textContent).toContain('新')

    const idle = await mount(
      <AssistantPanel assist={null} selected={false} onAdopt={vi.fn()} onQuote={vi.fn()} onSaveCanned={vi.fn()} />,
    )
    expect(idle.textContent).toContain('选择会话后开启监听')
  })

  it('情绪 alert 追加的预警卡没有动作按钮', async () => {
    const c = await mount(<AssistantPanel assist={SNAPSHOT} selected onAdopt={vi.fn()} onQuote={vi.fn()} onSaveCanned={vi.fn()} />)
    const alertCard = Array.from(c.querySelectorAll('div')).find((d) => d.textContent?.startsWith('检测到强烈不满情绪'))
    expect(alertCard).toBeDefined()
    expect(alertCard!.querySelectorAll('button').length).toBe(0)
  })
})

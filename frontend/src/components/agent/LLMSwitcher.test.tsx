/**
 * LLMSwitcher — 会话级切换（B.9 决策②）的守卫测试
 *
 * 核心不变量：在聊天组件里选模型**只写会话态**（store.sessionModel），
 * 绝不调用全局切换接口 switchLLM —— 全局默认已收敛到 admin（后端 /llm/switch 门禁）。
 *
 * 回归警报：若 handleSwitch 被改回调 switchLLM，用例 1 的
 * 「只写会话态」断言会失败；若有人重新引入全局写入，switchLLM 断言会捕获。
 *
 * 项目未引入 @testing-library，按既有约定用 react-dom 直渲 + act。
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { useChatStore } from '@/store/chat'

vi.mock('@/lib/api', () => ({
  listLLMModels: vi.fn(async () => ({
    models: [
      { name: 'model-a', display: '模型 A', description: '默认模型', provider: 'pa' },
      { name: 'model-b', display: '模型 B', description: '备选模型', provider: 'pb' },
    ],
  })),
  getCurrentLLM: vi.fn(async () => ({ model: 'model-a', provider: 'pa' })),
  getLLMBalance: vi.fn(async () => ({ ok: true, provider: 'pa', balance: '10.00' })),
  switchLLM: vi.fn(async () => ({ ok: true, model: 'model-b', provider: 'pb' })),
}))

import { switchLLM } from '@/lib/api'
import LLMSwitcher from './LLMSwitcher'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<LLMSwitcher />))
  mounted.push({ container, root })
  return container
}

/** 冲掉 refresh()/refreshBalance() 的 async effect */
async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
}

function buttons(container: HTMLElement): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll('button')) as HTMLButtonElement[]
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
  const btn = buttons(container).find((b) => (b.textContent || '').includes(text))
  if (!btn) throw new Error(`未找到含「${text}」的按钮`)
  return btn
}

afterEach(() => {
  vi.clearAllMocks()
  useChatStore.setState({ sessionModel: null })
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('LLMSwitcher 会话级模型切换', () => {
  it('选择模型只写会话态，不调用全局切换接口', async () => {
    useChatStore.setState({ sessionModel: null })
    const container = mount()
    await flush()

    // 打开下拉
    act(() => { buttons(container)[0].click() })

    // 点「模型 B」
    act(() => { findButton(container, '模型 B').click() })

    expect(useChatStore.getState().sessionModel).toBe('model-b')
    expect(switchLLM).not.toHaveBeenCalled()
  })

  it('选中项等于全局默认时存 null（不回写覆盖，保持状态干净）', async () => {
    useChatStore.setState({ sessionModel: 'model-b' })
    const container = mount()
    await flush()

    act(() => { buttons(container)[0].click() })
    act(() => { findButton(container, '模型 A').click() })

    expect(useChatStore.getState().sessionModel).toBeNull()
    expect(switchLLM).not.toHaveBeenCalled()
  })

  it('会话覆盖生效时触发器显示「· 本会话」，且可回到全局默认', async () => {
    useChatStore.setState({ sessionModel: 'model-b' })
    const container = mount()
    await flush()

    expect(container.textContent).toContain('本会话')

    act(() => { buttons(container)[0].click() })
    act(() => { findButton(container, '回到全局默认').click() })

    expect(useChatStore.getState().sessionModel).toBeNull()
    expect(switchLLM).not.toHaveBeenCalled()
  })
})

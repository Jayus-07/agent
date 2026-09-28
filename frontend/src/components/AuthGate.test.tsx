/**
 * AuthGate — 未登录守卫的回归测试
 *
 * 核心不变量：
 * 1. 公开页（/、/login、/register）不受守卫，直接渲染 children；
 * 2. 受保护页有 token → 放行；无 token 但静默续期成功 → 放行；
 * 3. 无 token 且续期失败 → window.location.assign('/') 弹回统一门户
 *    （产品口径：未登录一律回门户，而不是带 redirect 跳 /login）。
 *
 * 回归警报：若有人把兜底跳转改回 /login?redirect=…，用例 3/4 会失败；
 * 若把公开页清单收窄（如移除 /），用例 1 会失败。
 *
 * 项目未引入 @testing-library，按既有约定用 react-dom 直渲 + act。
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const nav = vi.hoisted(() => ({ pathname: '/' }))
const auth = vi.hoisted(() => ({
  getAccessToken: vi.fn<() => string | null>(() => null),
  tryRefreshOnce: vi.fn<() => Promise<boolean>>(async () => false),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
}))
vi.mock('@/lib/auth', () => auth)

import AuthGate from './AuthGate'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []
const assignSpy = vi.fn()

beforeEach(() => {
  nav.pathname = '/'
  auth.getAccessToken.mockReturnValue(null)
  auth.tryRefreshOnce.mockResolvedValue(false)
  vi.spyOn(window.location, 'assign').mockImplementation(assignSpy)
})

afterEach(() => {
  vi.restoreAllMocks()
  while (mounted.length) {
    const { container, root } = mounted.pop()!
    act(() => root.unmount())
    container.remove()
  }
})

function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(
    <AuthGate>
      <div id="guarded">protected-content</div>
    </AuthGate>,
  ))
  mounted.push({ container, root })
  return container
}

/** 冲掉 tryRefreshOnce().then 的 async effect */
async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
}

describe('AuthGate 公开页', () => {
  it.each(['/', '/login', '/register'])('%s 直接放行且不触发跳转', (pathname) => {
    nav.pathname = pathname
    const container = mount()
    expect(container.querySelector('#guarded')).not.toBeNull()
    expect(assignSpy).not.toHaveBeenCalled()
    expect(auth.tryRefreshOnce).not.toHaveBeenCalled()
  })
})

describe('AuthGate 受保护页', () => {
  it('有 access token → 放行且不跳转', () => {
    nav.pathname = '/agent'
    auth.getAccessToken.mockReturnValue('token-abc')
    const container = mount()
    expect(container.querySelector('#guarded')).not.toBeNull()
    expect(assignSpy).not.toHaveBeenCalled()
    expect(auth.tryRefreshOnce).not.toHaveBeenCalled()
  })

  it('无 token 但静默续期成功 → 放行且不跳转', async () => {
    nav.pathname = '/travel'
    auth.tryRefreshOnce.mockResolvedValue(true)
    const container = mount()
    // 检查期间渲染 null，避免受保护内容闪烁
    expect(container.querySelector('#guarded')).toBeNull()
    await flush()
    expect(container.querySelector('#guarded')).not.toBeNull()
    expect(assignSpy).not.toHaveBeenCalled()
  })

  it('无 token 且续期失败（/agent）→ 弹回统一门户 /', async () => {
    nav.pathname = '/agent'
    const container = mount()
    await flush()
    expect(container.querySelector('#guarded')).toBeNull()
    expect(assignSpy).toHaveBeenCalledTimes(1)
    expect(assignSpy).toHaveBeenCalledWith('/')
  })

  it('无 token 且续期失败（/travel）→ 弹回统一门户 /', async () => {
    nav.pathname = '/travel'
    mount()
    await flush()
    expect(assignSpy).toHaveBeenCalledWith('/')
  })
})

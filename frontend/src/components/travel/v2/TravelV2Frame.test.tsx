import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { TravelBottomNav, TravelTopBar } from './TravelV2Frame'
import { ASSISTANT_DRAWER_OPEN_EVENT } from '@/components/layout/MobileAssistantDrawer'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let container: HTMLDivElement | null = null

function render(element: React.ReactElement) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root?.render(element))
}

afterEach(() => {
  if (root) act(() => root?.unmount())
  container?.remove()
  root = null
  container = null
  document.body.innerHTML = ''
})

beforeEach(() => {
  window.localStorage.clear()
})

describe('TravelBottomNav', () => {
  it('四项布局：三个页面链接 + 「我的」为按钮（不开页面）', () => {
    render(createElement(TravelBottomNav, { active: 'home' }))
    const nav = container!.querySelector('nav[aria-label="手机导航"]')!
    const links = [...nav.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(links).toEqual(['/travel', '/travel/discover', '/travel/itineraries'])
    const me = nav.querySelector('button[aria-label="打开个人侧栏"]')
    expect(me).not.toBeNull()
    expect(me!.textContent).toContain('我的')
  })

  it('点击「我的」派发全局开门信号，不发生页面跳转', () => {
    render(createElement(TravelBottomNav, { active: 'home' }))
    const seen: string[] = []
    const spy = (event: Event) => seen.push(event.type)
    window.addEventListener(ASSISTANT_DRAWER_OPEN_EVENT, spy)
    try {
      act(() => {
        container!.querySelector<HTMLButtonElement>('button[aria-label="打开个人侧栏"]')!.click()
      })
    } finally {
      window.removeEventListener(ASSISTANT_DRAWER_OPEN_EVENT, spy)
    }
    expect(seen).toEqual([ASSISTANT_DRAWER_OPEN_EVENT])
  })
})

describe('TravelTopBar 抽屉（决策 4 导航）', () => {
  it('全局开门信号打开抽屉，导航为旅游三项 + 账号设置', () => {
    render(createElement(TravelTopBar, {}))
    act(() => {
      window.dispatchEvent(new CustomEvent(ASSISTANT_DRAWER_OPEN_EVENT))
    })
    const dialog = document.body.querySelector('[role="dialog"][aria-label="个人导航"]')
    expect(dialog).not.toBeNull()
    const hrefs = [...dialog!.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(hrefs).toEqual([
      '/travel',
      '/travel/templates',
      '/travel/itineraries',
      '/settings',
    ])
    expect(dialog!.textContent).toContain('旅游首页')
    expect(dialog!.textContent).toContain('旅行灵感')
    expect(dialog!.textContent).toContain('我的行程')
    expect(dialog!.textContent).toContain('账号设置')
  })

  it('关闭后导航链接退出 Tab 序（焦点管理约定）', () => {
    render(createElement(TravelTopBar, {}))
    act(() => {
      window.dispatchEvent(new CustomEvent(ASSISTANT_DRAWER_OPEN_EVENT))
    })
    const dialog = document.body.querySelector('[role="dialog"][aria-label="个人导航"]')!
    const linkWhenOpen = dialog.querySelector('a') as HTMLAnchorElement
    expect(linkWhenOpen.tabIndex).toBe(0)
    act(() => {
      ;(dialog.querySelector('button[aria-label="关闭个人侧栏"]') as HTMLButtonElement).click()
    })
    const navLinks = [...document.body.querySelectorAll('[aria-label="个人导航"] a')] as HTMLAnchorElement[]
    expect(navLinks.every((a) => a.tabIndex === -1)).toBe(true)
  })
})

describe('MobileAssistantDrawer 默认导航（助手页消费面回归）', () => {
  it('未传 navContent 时保持返回门户/记忆（chat 页等既有消费面不变）', async () => {
    const { default: MobileAssistantDrawer } = await import('@/components/layout/MobileAssistantDrawer')
    render(createElement(MobileAssistantDrawer, {}))
    act(() => {
      window.dispatchEvent(new CustomEvent(ASSISTANT_DRAWER_OPEN_EVENT))
    })
    const dialog = document.body.querySelector('[role="dialog"][aria-label="个人导航"]')!
    const hrefs = [...dialog.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(hrefs).toEqual(['/', '/settings#profile-memory'])
  })
})

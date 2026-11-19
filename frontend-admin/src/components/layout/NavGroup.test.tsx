import { act, type ComponentProps } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => '/data-explorer',
  useSearchParams: () => new URLSearchParams(''),
}))

import NavGroup from './NavGroup'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

function mount(showSections = false, alwaysOpen = false) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const props = {
    icon: <span data-testid="group-icon">▦</span>,
    label: '业务运营',
    collapsed: false,
    open: true,
    showSections,
    alwaysOpen,
    items: [
      { label: '数据查询', path: '/data-explorer', section: '业务洞察' },
      { label: '业务报告', path: '/reports', section: '业务洞察' },
    ],
  } as unknown as ComponentProps<typeof NavGroup>
  act(() => {
    root.render(<NavGroup {...props} />)
  })
  mounted.push({ container, root })
  return container
}

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('NavGroup', () => {
  it('只展示真实入口，分组说明不应伪装成菜单项', () => {
    const container = mount()

    expect(container.textContent).not.toContain('业务洞察')
    expect(container.textContent).not.toContain('选品运营')
    expect(container.querySelectorAll('a')).toHaveLength(2)
    expect(container.querySelector('button')?.getAttribute('aria-expanded')).toBe('true')
  })

  it('当前入口使用清晰的选中状态并标记当前页面', () => {
    const container = mount()
    const current = container.querySelector<HTMLAnchorElement>('a[href="/data-explorer"]')
    const inactive = container.querySelector<HTMLAnchorElement>('a[href="/reports"]')

    expect(current?.getAttribute('aria-current')).toBe('page')
    expect(current?.className).toContain('bg-accent-soft')
    expect(current?.className).toContain('font-semibold')
    expect(inactive?.className).toContain('text-text-secondary')
  })

  it('只在启用时显示分类标题，分类标题不应成为可点击入口', () => {
    const container = mount(true)

    expect(container.textContent).toContain('业务洞察')
    expect(container.textContent).not.toContain('选品运营')
    expect(container.querySelectorAll('a')).toHaveLength(2)
  })

  it('参考图模式常驻显示分类标题和全部页面入口，不显示折叠按钮', () => {
    const container = mount(false, true)

    expect(container.querySelector('[role="heading"]')?.textContent).toBe('业务运营')
    expect(container.querySelectorAll('a')).toHaveLength(2)
    expect(container.querySelector('button')).toBeNull()
  })
})

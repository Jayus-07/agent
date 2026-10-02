import { act, type ComponentProps } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('next/link', () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => '/selection-workbench',
}))

import NavGroup from './NavGroup'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

function mount(showSections = false) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const props = {
    icon: <span data-testid="group-icon">▦</span>,
    label: '业务运营',
    collapsed: false,
    open: true,
    showSections,
    items: [
      { label: '数据查询', path: '/data-explorer', section: '业务洞察' },
      { label: '竞品监控', path: '/competitors', section: '业务洞察' },
      { label: '选品工作台', path: '/selection-workbench', section: '选品运营' },
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
    expect(container.querySelectorAll('a')).toHaveLength(3)
    expect(container.querySelector('button')?.getAttribute('aria-expanded')).toBe('true')
  })

  it('当前入口使用清晰的选中状态并标记当前页面', () => {
    const container = mount()
    const current = container.querySelector<HTMLAnchorElement>('a[href="/selection-workbench"]')
    const inactive = container.querySelector<HTMLAnchorElement>('a[href="/data-explorer"]')

    expect(current?.getAttribute('aria-current')).toBe('page')
    expect(current?.className).toContain('bg-accent-soft')
    expect(current?.className).toContain('font-semibold')
    expect(inactive?.className).toContain('text-text-secondary')
  })

  it('只在启用时显示分类标题，分类标题不应成为可点击入口', () => {
    const container = mount(true)

    expect(container.textContent).toContain('业务洞察')
    expect(container.textContent).toContain('选品运营')
    expect(container.querySelectorAll('a')).toHaveLength(3)
  })
})

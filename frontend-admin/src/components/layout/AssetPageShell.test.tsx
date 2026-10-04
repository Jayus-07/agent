import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import {
  AssetActionButton,
  AssetPageShell,
  AssetSection,
  AssetStatCard,
} from './AssetPageShell'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const mounted: { container: HTMLDivElement; root: Root }[] = []

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('AI 资产页面共享视觉组件', () => {
  it('统一页面外壳、标题区和操作区', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    act(() => {
      root.render(
        <AssetPageShell
          title="测试页面"
          desc="页面说明"
          actions={<AssetActionButton>刷新</AssetActionButton>}
        >
          <div>内容</div>
        </AssetPageShell>,
      )
    })

    const shell = container.querySelector('[data-testid="asset-page-shell"]')
    expect(shell).toBeTruthy()
    expect(shell?.className).toContain('max-w-7xl')
    expect(shell?.className).toContain('px-6')
    expect(container.textContent).toContain('测试页面')
    expect(container.textContent).toContain('刷新')
    expect(container.querySelector('button')?.className).toContain('border-border-subtle')
  })

  it('统一区块标题与统计卡的层级', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted.push({ container, root })

    act(() => {
      root.render(
        <AssetSection title="能力清单" meta="17 条">
          <AssetStatCard label="总数" value="17" hint="已注册" />
        </AssetSection>,
      )
    })

    const section = container.querySelector('section')
    expect(section?.className).toContain('rounded-xl')
    expect(section?.className).toContain('shadow-card')
    expect(section?.querySelector(':scope > div')?.className).toContain('border-b')
    expect(container.textContent).toContain('能力清单')
    expect(container.textContent).toContain('17 条')
    expect(container.textContent).toContain('已注册')
  })
})

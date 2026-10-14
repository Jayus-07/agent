import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceFilter as FilterType } from '@/types/trace'
import TraceFilterBar from './TraceFilter'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
})

describe('TraceFilterBar', () => {
  it('将无法安全查询的运行中筛选标记为禁用和 BLOCKED', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    const filter: FilterType = {
      timeRange: '24h',
      status: 'all',
      keyword: '',
      page: 1,
      pageSize: 20,
      sortField: '',
      sortDir: 'desc',
    }

    await act(async () => {
      root.render(<TraceFilterBar filter={filter} onChange={vi.fn()} traces={[]} />)
    })

    const runningOption = container.querySelector<HTMLOptionElement>('option[value="running"]')
    expect(runningOption?.disabled).toBe(true)
    expect(runningOption?.textContent).toContain('BLOCKED')
  })
})

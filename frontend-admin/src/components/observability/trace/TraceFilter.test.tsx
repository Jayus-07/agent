import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { TraceFilter as FilterType, TraceFilterOptions } from '@/types/trace'
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
  it('运行中筛选解锁为可用选项（活跃端点已补资源级授权）', async () => {
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
    const options: TraceFilterOptions = {
      workflow_names: ['workflow-page-one', 'workflow-page-two'],
      models: ['model-page-one', 'model-page-two'],
      kb_ids: ['kb-page-one', 'kb-page-two'],
    }

    await act(async () => {
      root.render(<TraceFilterBar filter={filter} onChange={vi.fn()} options={options} />)
    })

    const runningOption = container.querySelector<HTMLOptionElement>('option[value="running"]')
    expect(runningOption?.disabled).toBe(false)
    expect(runningOption?.textContent).not.toContain('BLOCKED')
    expect(container.querySelector('select[aria-label="Agent 或 Workflow"] option[value="workflow-page-two"]')).toBeTruthy()
    expect(container.querySelector('select[aria-label="模型"] option[value="model-page-two"]')).toBeTruthy()
    expect(container.querySelector('select[aria-label="知识库"] option[value="kb-page-two"]')).toBeTruthy()
  })
})

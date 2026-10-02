import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { selectionDecisionApi } from '@/api/selectionDecision'
import SelectionDecisionPanel from './SelectionDecisionPanel'

const task = {
  id: 'task-1',
  status: 'success',
  verdict: 'go',
  trace_id: null,
  error: null,
  created_at: '2026-10-02 15:00',
  finished_at: '2026-10-02 15:01',
  inputs: { category: '蓝牙耳机', platforms: ['jd'] },
}

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeEach(() => {
  vi.spyOn(selectionDecisionApi, 'list').mockResolvedValue({ tasks: [task] })
})

afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
  vi.useRealTimers()
})

function mount(): HTMLDivElement {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  act(() => root.render(<SelectionDecisionPanel />))
  return container
}

describe('SelectionDecisionPanel', () => {
  it('展示任务列表和报告详情链接', async () => {
    const container = mount()

    await act(async () => {
      await Promise.resolve()
    })

    expect(container.textContent).toContain('蓝牙耳机')
    expect(container.querySelector('a[href="/selection-decision/task-1"]')?.textContent).toBe('查看')
  })

  it('卸载后停止任务轮询，不创建残留定时器', async () => {
    vi.useFakeTimers()
    const list = vi.mocked(selectionDecisionApi.list)
    const container = mount()

    await act(async () => {
      await Promise.resolve()
    })
    expect(list).toHaveBeenCalledTimes(1)

    act(() => mounted?.root.unmount())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000)
    })
    expect(list).toHaveBeenCalledTimes(1)
    container.remove()
  })
})

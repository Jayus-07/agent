// cs-workstation-v2 P5：退款审核工作台交互（队列/筛选/意见必填/终态锁）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const api = vi.hoisted(() => ({
  listRefundQueue: vi.fn(async () => ({
    items: [
      {
        request_id: 'req-1', request_no: 'RA-P5A0001', status: 'pending_review',
        status_display: '等待人工审核', order_no: 'DEMO-1001', amount: '398.00',
        reason: '商品破损', policy_version: 'policy-v1', user_id: 'user-a',
        review: null, created_at: '2026-10-31T10:00:00Z', updated_at: null,
      },
      {
        request_id: 'req-2', request_no: 'RA-P5A0002', status: 'approved',
        status_display: '审核通过，退款已退回钱包', order_no: 'DEMO-1002',
        amount: '129.00', reason: '七天无理由', policy_version: 'policy-v1',
        user_id: 'user-b',
        review: { decision: 'approved', opinion: '凭证齐全', reviewer_id: 'sup-1' },
        created_at: '2026-10-31T09:00:00Z', updated_at: null,
      },
    ],
    total: 2,
    mode: 'http',
  })),
  reviewRefund: vi.fn(async (_id: string, decision: string) => ({
    request_id: 'req-1',
    status: decision,
    status_display: decision === 'approved' ? '审核通过，退款已退回钱包' : '审核未通过',
  })),
}))

vi.mock('@/cs2/workspace/api', () => api)

import Cs2RefundsPage from '@/app/cs/refunds/page'

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
  vi.clearAllMocks()
})

async function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<Cs2RefundsPage />)
  })
  mounted = { container, root }
  return container
}

describe('Cs2RefundsPage（P5 退款审核）', () => {
  it('渲染队列两行与状态徽章；终态行详情显示审核记录', async () => {
    const c = await mount()
    await act(async () => {})
    expect(api.listRefundQueue).toHaveBeenCalledTimes(1)
    expect(c.textContent).toContain('RA-P5A0001')
    expect(c.textContent).toContain('待审核')
    expect(c.textContent).toContain('已通过')

    // 选中终态行 → 审核记录展示，无意见输入
    const rows = Array.from(c.querySelectorAll('button')).filter((b) =>
      b.textContent?.includes('RA-P5A0002'),
    )
    await act(async () => {
      rows[0]?.click()
    })
    expect(c.textContent).toContain('审核记录（终态不可回退）')
    expect(c.textContent).toContain('凭证齐全')
    expect(c.querySelector('#refund-opinion')).toBeNull()
  })

  it('意见为空时按钮禁用；填写后通过调用 API 并刷新', async () => {
    const c = await mount()
    await act(async () => {})
    const row = Array.from(c.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('RA-P5A0001'),
    )
    await act(async () => {
      row?.click()
    })
    const approve = Array.from(c.querySelectorAll('button')).find(
      (b) => b.textContent === '通过并退款',
    ) as HTMLButtonElement
    const reject = Array.from(c.querySelectorAll('button')).find(
      (b) => b.textContent === '驳回',
    ) as HTMLButtonElement
    expect(approve.disabled).toBe(true)
    expect(reject.disabled).toBe(true)

    const textarea = c.querySelector('#refund-opinion') as HTMLTextAreaElement
    const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      setValue.call(textarea, '凭证齐全，同意退款')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(approve.disabled).toBe(false)
    await act(async () => {
      approve.click()
    })
    expect(api.reviewRefund).toHaveBeenCalledWith('req-1', 'approved', '凭证齐全，同意退款')
    // 提交后刷新队列
    expect(api.listRefundQueue).toHaveBeenCalledTimes(2)
  })

  it('状态筛选传参', async () => {
    const c = await mount()
    await act(async () => {})
    const tab = Array.from(c.querySelectorAll('button')).find(
      (b) => b.textContent === '待审核',
    )!
    await act(async () => {
      tab.click()
    })
    expect(api.listRefundQueue).toHaveBeenLastCalledWith('pending_review')
  })
})

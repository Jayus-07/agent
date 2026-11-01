/**
 * BookingConfirmGate 交互契约（收编重构 §4.2 / 原型 B7 两段式确认门）。
 */
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  createQuote: vi.fn(),
  confirm: vi.fn(),
  status: vi.fn(),
}))

vi.mock('@/api/travelV2', () => ({
  createTravelBookingQuoteV2: mocks.createQuote,
  confirmTravelBookingV2: mocks.confirm,
  fetchTravelBookingStatusV2: mocks.status,
}))

import { BookingConfirmGate } from './BookingConfirmGate'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const QUOTE = {
  status: 'awaiting_confirmation' as const,
  trip_id: 'trip-1',
  booking_intent_id: 'bi-1',
  order_id: 'bo-1',
  quote_id: 'bq-1',
  amount: '640',
  currency: 'CNY',
  internal_expires_at: '2026-11-01T12:00:00+00:00',
  provider_expires_at: null,
  message: '确认卡文案',
}

const DONE = {
  result: 'booked',
  trip_id: 'trip-1',
  booking_intent_id: 'bi-1',
  order: { status: 'booked' },
  detail: '',
  message: '预订完成',
}

const base = {
  tripId: 'trip-1',
  commerceType: 'hotel' as const,
  searchParams: { city: '厦门', check_in: '2027-03-01', check_out: '2027-03-03' },
}

let container: HTMLElement
let root: Root

function renderGate() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => {
    root.render(createElement(BookingConfirmGate, base))
  })
}

function q(testid: string): HTMLElement | null {
  return container.querySelector(`[data-testid="${testid}"]`)
}

function click(testid: string) {
  const node = q(testid)
  if (!node) throw new Error(`missing ${testid}`)
  act(() => {
    node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

async function flush() {
  await act(async () => { await Promise.resolve() })
}

describe('BookingConfirmGate（预订确认门）', () => {
  beforeEach(() => {
    mocks.createQuote.mockReset()
    mocks.confirm.mockReset()
    mocks.status.mockReset()
  })

  afterEach(() => {
    act(() => { root.unmount() })
    container.remove()
    vi.clearAllMocks()
  })

  it('两段式：报价先出确认卡，用户点确认才执行', async () => {
    mocks.createQuote.mockResolvedValue(QUOTE)
    mocks.confirm.mockResolvedValue(DONE)
    renderGate()

    click('booking-start')
    await flush()

    const card = q('booking-quote-card')
    expect(card).not.toBeNull()
    expect(card!.textContent).toContain('640 CNY')
    expect(mocks.confirm).not.toHaveBeenCalled()   // 报价 ≠ 执行

    click('booking-confirm')
    await flush()

    const done = q('booking-done')
    expect(done).not.toBeNull()
    expect(done!.textContent).toContain('booked')
    expect(mocks.confirm).toHaveBeenCalledWith('trip-1', 'bi-1')
    expect(mocks.createQuote).toHaveBeenCalledWith('trip-1', {
      commerce_type: 'hotel',
      search_params: base.searchParams,
      selection: undefined,
    })
  })

  it('确认前可取消（不发起 confirm）', async () => {
    mocks.createQuote.mockResolvedValue(QUOTE)
    renderGate()

    click('booking-start')
    await flush()
    click('booking-cancel')
    await flush()

    expect(q('booking-quote-card')).toBeNull()
    expect(mocks.confirm).not.toHaveBeenCalled()
  })

  it('失败如实呈现（provider off 503 detail 透出）', async () => {
    mocks.createQuote.mockRejectedValue({
      detail: { code: 'BOOKING_PROVIDER_OFF', message: '预订功能未开通' },
    })
    renderGate()

    click('booking-start')
    await flush()

    const err = q('booking-error')
    expect(err).not.toBeNull()
    expect(err!.textContent).toContain('预订功能未开通')
    expect(q('booking-start')).not.toBeNull()  // 可重试
  })
})

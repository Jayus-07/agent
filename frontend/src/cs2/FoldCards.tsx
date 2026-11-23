'use client'

// cs-workstation-v2 P2：折叠卡 —— 我的退款申请（状态徽章；编号不代表已退款）
// 与我的工单（空态折叠）。P5 时退款状态与人工审核联动。
import { useEffect, useState } from 'react'
import {
  listMyTickets,
  listRefundRequests,
  type RefundRequestRow,
  type TicketRow,
} from './cs2Api'

const TICKETS_PAGE_SIZE = 10

const REFUND_BADGE: Record<string, string> = {
  pending_review: 'bg-amber-50 text-amber-600',
  reviewing: 'bg-blue-50 text-blue-600',
  approved: 'bg-blue-50 text-blue-600',
  rejected: 'bg-red-50 text-red-500',
  cancelled: 'bg-slate-100 text-slate-500',
}

export default function FoldCards({ refreshKey = 0 }: { refreshKey?: number }) {
  const [refunds, setRefunds] = useState<RefundRequestRow[] | null>(null)
  const [ticketPage, setTicketPage] = useState<{ items: TicketRow[]; total: number } | null>(null)
  const [ticketOffset, setTicketOffset] = useState(0)
  const [ticketLoading, setTicketLoading] = useState(true)
  const [ticketError, setTicketError] = useState(false)
  const [openRefunds, setOpenRefunds] = useState(false)
  const [openTickets, setOpenTickets] = useState(false)

  useEffect(() => {
    let cancelled = false
    listRefundRequests(10)
      .then((rows) => {
        if (!cancelled) setRefunds(rows)
      })
      .catch(() => {
        if (!cancelled) setRefunds([])
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey])

  useEffect(() => {
    let cancelled = false
    setTicketLoading(true)
    setTicketError(false)
    listMyTickets(TICKETS_PAGE_SIZE, ticketOffset)
      .then((page) => {
        if (cancelled) return
        if (ticketOffset > 0 && ticketOffset >= page.total) {
          setTicketOffset(Math.max(0, Math.floor((page.total - 1) / TICKETS_PAGE_SIZE) * TICKETS_PAGE_SIZE))
          return
        }
        setTicketPage(page)
      })
      .catch(() => {
        if (!cancelled) setTicketError(true)
      })
      .finally(() => {
        if (!cancelled) setTicketLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [refreshKey, ticketOffset])

  // P5：审核结果异步产生——30s 轻量轮询让「已通过/已驳回」状态可见
  useEffect(() => {
    const timer = setInterval(() => {
      listRefundRequests(10)
        .then((rows) => setRefunds(rows))
        .catch(() => {})
    }, 30_000)
    return () => clearInterval(timer)
  }, [])

  const tickets = ticketPage?.items ?? []
  if (refunds?.length === 0 && ticketPage?.total === 0) return null

  return (
    <div className="flex flex-col gap-2 px-4">
      {refunds && refunds.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white text-xs">
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-slate-500"
            onClick={() => setOpenRefunds((v) => !v)}
          >
            💰 我的退款申请（{refunds.length}）
            <span className="ml-auto text-[10.5px] text-slate-400">点击展开/收起</span>
          </button>
          {openRefunds && (
            <div className="px-3 pb-2.5">
              {refunds.map((r) => (
                <div key={r.request_id} className="flex items-center gap-2 border-b border-dashed border-slate-100 py-1.5 last:border-b-0">
                  <span className="min-w-0 flex-1 truncate text-slate-700">
                    {r.request_no} · 订单 {r.order_no} · ¥{Number(r.amount ?? 0).toFixed(2)}
                  </span>
                  <span className={`flex-none rounded-full px-2 py-0.5 text-[10.5px] ${REFUND_BADGE[r.status] ?? 'bg-slate-100 text-slate-500'}`}>
                    {r.status_display || r.status}
                  </span>
                </div>
              ))}
              <p className="mt-1.5 text-[10.5px] text-slate-400">
                申请编号尚不代表已退款；审核结果以人工审核为准。
              </p>
            </div>
          )}
        </div>
      )}
      {(ticketPage || ticketError) && (
        <div className="rounded-xl border border-slate-200 bg-white text-xs">
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-2 text-slate-500"
            onClick={() => setOpenTickets((v) => !v)}
          >
            🎫 我的工单{ticketPage ? `（${ticketPage.total}）` : ''}
            <span className="ml-auto text-[10.5px] text-slate-400">点击展开/收起</span>
          </button>
          {openTickets && ticketError && (
            <p className="px-3 pb-2.5 text-[11px] text-slate-500">工单加载失败，请稍后再试。</p>
          )}
          {openTickets && !ticketLoading && !ticketError && tickets.length === 0 && (
            <p className="px-3 pb-2.5 text-[11px] text-slate-400">暂无关联工单。</p>
          )}
          {openTickets && !ticketLoading && !ticketError && tickets.length > 0 && (
            <div className="px-3 pb-2.5">
              {tickets.map((t, i) => (
                <div key={t.ticket_no ?? t.ticket_id ?? i} className="flex items-center gap-2 border-b border-dashed border-slate-100 py-1.5 last:border-b-0">
                  <span className="min-w-0 flex-1 truncate text-slate-700">
                    {t.ticket_no ?? t.ticket_id} · {t.subject ?? t.title ?? '工单'}
                  </span>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10.5px] text-slate-500">
                    {t.status_display ?? t.status ?? '—'}
                  </span>
                </div>
              ))}
            </div>
          )}
          {openTickets && ticketPage && ticketPage.total > TICKETS_PAGE_SIZE && (
            <div className="flex gap-4 px-3 pb-2.5 text-slate-600">
              <button type="button" disabled={ticketLoading || ticketOffset === 0}
                onClick={() => setTicketOffset((offset) => offset - TICKETS_PAGE_SIZE)}>
                上一页
              </button>
              <button type="button"
                disabled={ticketLoading || ticketOffset + TICKETS_PAGE_SIZE >= ticketPage.total}
                onClick={() => setTicketOffset((offset) => offset + TICKETS_PAGE_SIZE)}>
                下一页
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

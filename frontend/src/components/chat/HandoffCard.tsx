'use client'

/**
 * HandoffCard — 主图域引导交接卡（多域隔离收官 M3）
 *
 * 数据源：SSE `handoff` AUX 帧（契约 @/types/handoff，与后端同构）。
 * 消息正文是后端下发的引导话术（reporter 直出），本卡是它的可操作形态：
 * 图标 + 一句话 + 带参跳转按钮。点击 = 埋点上报 + 跳转三专属入口：
 *   travel          → /travel?destination=…&days=…（旅游页解析参数预填 brief）
 *   selection_funnel → /selection-funnel?category=…&platform=…
 *   customer_service → 打开 CSDrawer 并预填 prefill_question（sessionStorage
 *                      + cs-drawer:open 事件，见 app/agent/page.tsx/CSDrawer）
 */
import { Compass, Headphones, ShoppingBag, ArrowRight } from 'lucide-react'
import { useRouter } from 'next/navigation'
import type { HandoffEvent } from '@/types/handoff'
import { requestSilent } from '@/lib/fetcher'

/** 域 → 卡片视觉（图标/按钮文案）。引导话术正文由后端下发，此处不复制。 */
const DOMAIN_META: Record<
  HandoffEvent['target_domain'],
  { icon: typeof Compass; button: string }
> = {
  travel: { icon: Compass, button: '去旅游规划页' },
  selection_funnel: { icon: ShoppingBag, button: '去智能选品页' },
  customer_service: { icon: Headphones, button: '打开智能客服' },
}

/** 点击埋点（§七退役指标数据源）：fire-and-forget，失败不影响跳转 */
function reportClick(targetDomain: HandoffEvent['target_domain']) {
  void requestSilent('/api/observability/handoff/click', {
    method: 'POST',
    body: { target_domain: targetDomain },
    timeout: 5000,
  })
}

function buildTravelQuery(params: HandoffEvent['params']): string {
  const qs = new URLSearchParams()
  if (params.destination) qs.set('destination', params.destination)
  if (params.days != null) qs.set('days', String(params.days))
  if (params.party_size != null) qs.set('party_size', String(params.party_size))
  if (params.budget_cny != null) qs.set('budget_cny', String(params.budget_cny))
  if (params.must_go && params.must_go.length > 0) {
    qs.set('must_go', params.must_go.join(','))
  }
  return qs.toString()
}

export default function HandoffCard({ event }: { event: HandoffEvent }) {
  const router = useRouter()
  const meta = DOMAIN_META[event.target_domain]
  const Icon = meta.icon

  const handleJump = () => {
    reportClick(event.target_domain)
    if (event.target_domain === 'travel') {
      const qs = buildTravelQuery(event.params)
      router.push(qs ? `/travel?${qs}` : '/travel')
      return
    }
    if (event.target_domain === 'selection_funnel') {
      const qs = new URLSearchParams()
      if (event.params.category) qs.set('category', event.params.category)
      if (event.params.platform) qs.set('platform', event.params.platform)
      router.push(qs.toString() ? `/selection-funnel?${qs.toString()}` : '/selection-funnel')
      return
    }
    // customer_service：主聊天页本就是 /agent，直接滑出抽屉并带预填问题
    try {
      if (event.params.prefill_question) {
        sessionStorage.setItem('cs_handoff_prefill', event.params.prefill_question)
      }
      window.dispatchEvent(new CustomEvent('cs-drawer:open'))
    } catch {
      /* sessionStorage 不可用时仅开抽屉 */
    }
  }

  return (
    <section
      data-testid="handoff-card"
      className="mx-5 my-3 rounded-2xl border border-sky-200 bg-sky-50/80 p-4 shadow-sm"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-sky-100 text-sky-700">
          <Icon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-relaxed text-sky-950">{event.text}</p>
          <button
            type="button"
            onClick={handleJump}
            className="mt-3 inline-flex items-center gap-1.5 rounded-xl border border-sky-300 bg-white
              px-3.5 py-2 text-sm font-medium text-sky-800 transition
              hover:border-sky-500 hover:bg-sky-100"
          >
            {meta.button}
            <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </section>
  )
}

/**
 * travelOverview.ts — 行程概览统计块取数（W2，桌面概览卡的单一事实源）
 *
 * 六行口径全部来自服务端 document 事实，不本地推算、不伪造（任务红线 5）：
 *   天数主题  = days.length + days[].title（空串 →（未命名），多天以 + 连接）
 *   安排      = Σ days[].items.length（跨 N 天）
 *   预算/节奏 = brief.budget.amount（null → 待设置）· paceLabel(brief.pace)
 *   预估花费  = totals.estimated_cost（null → —；有值标注「未核验」——规划期估算非报价）
 *   天气      = 契约位「出行日临近自动联动预报」（当前无实时预报数据源，如实占位）
 *   住宿      = arrangements.lodgings[0]（未安排 → 引导语，与原型 ws-ov 一致）
 */
import type { TravelTripV2 } from '@/api/travelV2'

export interface TripOverviewRow {
  label: string
  value: string
}

export function paceLabel(pace: string): string {
  return pace === 'relaxed' ? '轻松' : pace === 'intense' ? '紧凑' : '均衡'
}

export function buildTripOverview(trip: TravelTripV2): TripOverviewRow[] {
  const document = trip.document
  const days = document.days
  const totalItems = days.reduce((sum, day) => sum + day.items.length, 0)
  const themes = days.map((day) => (day.title || '').trim() || '（未命名）')
  const budgetAmount = document.brief.budget.amount
  const estimatedCost = document.totals.estimated_cost
  const lodging = document.arrangements.lodgings[0]
  return [
    { label: '天数主题', value: `${days.length} 天 · ${themes.join(' + ') || '—'}` },
    { label: '安排', value: `${totalItems} 个（跨 ${days.length} 天）` },
    {
      label: '预算 / 节奏',
      value: `${budgetAmount === null ? '待设置' : `¥${budgetAmount.toLocaleString()}`} · ${paceLabel(document.brief.pace)}`,
    },
    {
      label: '预估花费',
      value: estimatedCost === null ? '—' : `¥${estimatedCost.toLocaleString()}（未核验）`,
    },
    { label: '天气', value: '出行日临近自动联动预报' },
    {
      label: '住宿',
      value: lodging
        ? `${lodging.merchant.name} · ${lodging.check_in} 入住`
        : '未安排 —— 对话或备选确认后选入',
    },
  ]
}

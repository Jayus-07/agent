'use client'

/**
 * RationaleCard — 结构化「为什么这样排」（M2 验收反馈拍板形态）。
 *
 * 数据来源（零新造）：
 * - ①需求 chips：processState.requirement.brief
 * - ②数据来源：processState.tools 汇总（工具名+条数）
 * - ③排法步骤 + 取舍：PlanResponse.rationale（后端 reporter 产出）
 * - ④美食/⑥知识摘录：processState.tools 的 preview
 * - ⑤注意事项：itinerary.warnings + tradeoffs
 * 后端无 rationale（旧数据/降级）时调用方回退 Markdown 说明。
 */
import { useState } from 'react'
import {
  AlertTriangle, CheckCircle2, ChevronDown, Compass, Database, MapPin,
  MessageSquarePlus, Sparkles, UtensilsCrossed,
} from 'lucide-react'
import type { Itinerary } from '@/api/travel'
import type { TravelProcessState } from './travelRuntime'

import type { RationaleData } from '@/api/travel'
export type { RationaleData } from '@/api/travel'

const PACE_LABEL: Record<string, string> = { relaxed: '轻松', moderate: '适中', intense: '紧凑' }

/** 工具名 → 来源徽章（与 travelDisplay TRAVEL_TOOL_LABELS 对齐的精选子集） */
const SOURCE_META: Record<string, { tag: string; cls: string }> = {
  poi_search_tool: { tag: '实时检索', cls: 'bg-[#e2f0ee] text-[#087b73]' },
  'travel.poi_search': { tag: '实时检索', cls: 'bg-[#e2f0ee] text-[#087b73]' },
  web_search_tool: { tag: '知乎攻略', cls: 'bg-[#e8f0fe] text-[#2d5bd1]' },
  zhihu_search_tool: { tag: '知乎攻略', cls: 'bg-[#e8f0fe] text-[#2d5bd1]' },
  map_merchant_search_tool: { tag: '地图匹配', cls: 'bg-[#fdf1e0] text-[#b4690e]' },
  'travel.retrieve_knowledge': { tag: '知识库', cls: 'bg-[#f0e9fe] text-[#6d4bd1]' },
}

interface Props {
  rationale: RationaleData
  processState: TravelProcessState | null
  itinerary: Itinerary | null
  pace?: string
  onAsk: (text: string) => void
}

export default function RationaleCard({ rationale, processState, itinerary, pace, onAsk }: Props) {
  const [tradeOpen, setTradeOpen] = useState(false)
  const reqBrief = (processState?.requirement?.brief ?? {}) as Record<string, unknown>
  const tools = processState?.tools ?? []

  // ① 需求 chips
  const chips: Array<{ k: string; v: string; hot?: boolean }> = []
  if (reqBrief.destination) chips.push({ k: '目的地', v: String(reqBrief.destination) })
  if (reqBrief.days) chips.push({ k: '天数', v: `${reqBrief.days} 天` })
  if (reqBrief.party_size) chips.push({ k: '人数', v: `${reqBrief.party_size} 人` })
  if (reqBrief.budget_cny) {
    const budgetLabel = reqBrief.budget_constraint === 'soft' ? '约' : '≤'
    chips.push({ k: '预算', v: `${budgetLabel} ¥${reqBrief.budget_cny}` })
  }
  if (pace || reqBrief.pace) chips.push({ k: '节奏', v: PACE_LABEL[String(pace || reqBrief.pace)] ?? '适中' })
  ;(Array.isArray(reqBrief.preferences) ? reqBrief.preferences : []).forEach((p) =>
    chips.push({ k: '偏好', v: String(p), hot: true }))
  ;(Array.isArray(reqBrief.must_go) ? reqBrief.must_go : []).forEach((m) =>
    chips.push({ k: '必去', v: String(m), hot: true }))

  // ② 数据来源（有结果条数的检索类工具，≤4）
  const sources = tools
    .filter((t) => t.status === 'success' && (t.resultCount ?? 0) > 0)
    .slice(0, 4)

  // ④ 美食卡 / ⑥ 知识摘录
  const foodTool = [...tools].reverse().find((t) => t.category === 'food' && (t.preview?.length ?? 0) > 0)
  const knowTool = [...tools].reverse().find((t) => t.category === 'knowledge' && (t.preview?.length ?? 0) > 0)
  const trade = rationale.tradeoffs ?? {}
  const dropped = trade.dropped ?? []
  const unscheduled = trade.unscheduled ?? []
  const tradeCount = dropped.length + unscheduled.length + (trade.unscheduled_extra ?? 0)

  return (
    <div className="space-y-3">
      {/* 头部结论 */}
      <div className="flex items-center gap-2.5 rounded-xl border border-[#dae7e5] bg-white px-3 py-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[#087b73] text-white">
          <CheckCircle2 size={15} aria-hidden />
        </span>
        <p className="min-w-0 text-[11px] leading-relaxed text-[#183037]">
          <b className="text-[12.5px]">行程已按你的需求排好</b>
          <br />
          {rationale.headline?.days ?? itinerary?.days.length ?? '—'} 天 ·{' '}
          {rationale.headline?.spots ?? '—'} 个地点
          {(rationale.headline?.must_go ?? []).length > 0 && (
            <> · 必去「{(rationale.headline?.must_go ?? []).join('、')}」已排入</>
          )}
        </p>
      </div>

      {/* ① 我听到了什么 */}
      {chips.length > 0 && (
        <Section no={1} title="我听到了什么">
          <div className="flex flex-wrap gap-1.5">
            {chips.map((c, i) => (
              <span
                key={i}
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] ${
                  c.hot ? 'border-[#f0d9b8] bg-[#fdf1e0] text-[#b4690e]' : 'border-[#dae7e5] bg-[#f5faf9] text-[#183037]'
                }`}
              >
                <em className="not-italic text-[9.5px] text-[#8fa5a3]">{c.k}</em> {c.v}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* ② 数据从哪来 */}
      {sources.length > 0 && (
        <Section no={2} title="数据从哪来" right={`${sources.length} 个来源`}>
          <div className="space-y-1.5">
            {sources.map((t, i) => {
              const meta = SOURCE_META[t.tool] ?? { tag: '数据源', cls: 'bg-[#f5faf9] text-[#5c7074]' }
              return (
                <div key={i} className="flex items-center gap-2 text-[11px]">
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9.5px] font-bold ${meta.cls}`}>{meta.tag}</span>
                  <span className="min-w-0 flex-1 truncate text-[#5c7074]">{t.tool}</span>
                  <span className="shrink-0 font-bold text-[#183037]">{t.resultCount}</span>
                </div>
              )
            })}
          </div>
        </Section>
      )}

      {/* ③ 我是这样排的 + 取舍 */}
      <Section no={3} title="我是这样排的">
        <div className="space-y-3">
          <Step title="必去优先">{(rationale.headline?.must_go ?? []).length > 0 ? `你点名的「${(rationale.headline?.must_go ?? []).join('、')}」优先排入，永不被静默丢弃` : '没有指定必去；按候选质量直接排入'}</Step>
          <Step title="地理就近串联">把地点按位置就近串成每天动线，减少回头路</Step>
          <Step title="节奏约束">{`「${PACE_LABEL[String(pace || reqBrief.pace)] ?? '适中'}」档 ≈ ${rationale.pace_rule ?? '每天 4~5 个地点'}`}</Step>
          {tradeCount > 0 && (
            <div>
              <button
                type="button"
                onClick={() => setTradeOpen(!tradeOpen)}
                aria-expanded={tradeOpen}
                className="flex cursor-pointer items-center gap-1 text-[10.5px] text-[#5c7074] transition-colors hover:text-[#183037]"
              >
                <ChevronDown size={11} className={`transition-transform ${tradeOpen ? 'rotate-90' : ''} ${tradeOpen ? '' : '-rotate-90'}`} aria-hidden />
                我放弃了什么（{tradeCount} 处）
              </button>
              {tradeOpen && (
                <div className="mt-1.5 space-y-1">
                  {(trade.kept_required ?? []).map((k, i) => (
                    <p key={`k${i}`} className="flex gap-1.5 text-[10.5px] text-[#5c7074]">
                      <span className="font-bold text-[#087b73]">✓</span>保留「{k}」（你点名的必去）
                    </p>
                  ))}
                  {dropped.map((d, i) => (
                    <p key={`d${i}`} className="flex gap-1.5 text-[10.5px] text-[#5c7074]">
                      <span className="font-bold text-red-500">✕</span>移除「{d.name}」：{d.reason}
                    </p>
                  ))}
                  {unscheduled.length > 0 && (
                    <p className="flex gap-1.5 text-[10.5px] text-[#5c7074]">
                      <span className="font-bold text-[#8fa5a3]">—</span>
                      <span className="min-w-0">
                        未排入：{unscheduled.join('、')}
                        {(trade.unscheduled_extra ?? 0) > 0 ? ` 等 ${unscheduled.length + (trade.unscheduled_extra ?? 0)} 处` : ''}
                        （超节奏上限；想多玩可以说「改成紧凑节奏」）
                      </span>
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </Section>

      {/* ④ 美食推荐 */}
      {foodTool?.preview && (
        <Section no={4} title="美食推荐" right="按你的偏好匹配">
          <div className="relative">
            <div className="flex gap-2 overflow-x-auto pb-1 [scrollbar-width:thin]">
              {foodTool.preview.slice(0, 5).map((item, i) => {
                const name = String(item.name ?? `候选${i + 1}`)
                return (
                  <div key={i} className="w-[168px] shrink-0 overflow-hidden rounded-lg border border-[#f0dfc8] bg-[#fffdf9]">
                    <p className="truncate bg-[#f7e3cc] px-2 py-1 text-[11px] font-bold text-[#7c4a12]" title={name}>{name}</p>
                    <p className="truncate px-2 pt-1 text-[10px] text-[#8c7258]">
                      {item.rating != null && <span className="font-bold text-[#d9940c]">★{String(item.rating)}</span>}
                      {item.price != null ? ` · ${String(item.price)}` : ''}
                      {item.open_status != null ? ` · ${String(item.open_status)}` : ''}
                    </p>
                    <button
                      type="button"
                      onClick={() => onAsk(`帮我把「${name}」排进行程里合适的位置，其他安排尽量保持不变`)}
                      className="mx-1.5 mb-1.5 mt-1.5 flex w-[calc(100%-12px)] cursor-pointer items-center justify-center gap-1 rounded-md bg-[#087b73] py-1 text-[10px] font-medium text-white transition-colors hover:bg-[#06655f]"
                    >
                      <MessageSquarePlus size={10} aria-hidden /> 帮我排进行程
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        </Section>
      )}

      {/* ⑤ 你要注意的 */}
      <Section no={5} title="你要注意的">
        <div className="space-y-1.5">
          {dropped.length > 0 && (
            <Note>自动调整了 {dropped.length} 处安排（见第 3 节取舍记录），其余未经改动</Note>
          )}
          {(itinerary?.warnings ?? []).slice(0, 2).map((w, i) => (
            <Note key={i}>{w}</Note>
          ))}
          <Note icon="✎">想改任何一处，直接在下面说一句话就行</Note>
        </div>
      </Section>

      {/* ⑥ 本地攻略参考 */}
      {knowTool?.preview && (
        <Section no={6} title="本地攻略参考" right="知识库 · 原文摘录">
          <div className="space-y-1.5">
            {knowTool.preview.slice(0, 3).map((rec, i) => (
              <blockquote key={i} className="rounded-r-lg border-l-2 border-[#c3d6d2] bg-[#f5faf9] px-2.5 py-1.5 text-[10.5px] leading-relaxed text-[#183037]">
                {String(rec.text || '')}
                <span className="mt-0.5 block text-[9px] text-[#8fa5a3]">rag:travel · 时效未核实，以官方最新为准</span>
              </blockquote>
            ))}
          </div>
        </Section>
      )}

      {rationale.version_note && (
        <p className="text-center text-[9.5px] text-[#8fa5a3]">{rationale.version_note}</p>
      )}
    </div>
  )
}

function Section({ no, title, right, children }: {
  no: number
  title: string
  right?: string
  children: React.ReactNode
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-[#dae7e5] bg-white">
      <div className="flex items-center gap-1.5 border-b border-dashed border-[#e8f1ef] px-3 py-1.5 text-[11.5px] font-bold text-[#183037]">
        <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-[#087b73] font-mono text-[9px] text-white">{no}</span>
        {title}
        {right && <span className="ml-auto text-[9.5px] font-normal text-[#8fa5a3]">{right}</span>}
      </div>
      <div className="px-3 py-2.5">{children}</div>
    </div>
  )
}

function Step({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className="mt-1 h-3 w-3 shrink-0 rounded-full border-2 border-[#087b73] bg-white" aria-hidden />
      <p className="min-w-0 text-[11px] leading-relaxed text-[#183037]">
        <b>{title}</b>
        <span className="text-[#5c7074]"> —— {children}</span>
      </p>
    </div>
  )
}

function Note({ children, icon = '⚠' }: { children: React.ReactNode; icon?: string }) {
  return (
    <p className="flex gap-1.5 text-[10.5px] leading-relaxed text-[#5c7074]">
      <span className={`shrink-0 font-bold ${icon === '✎' ? 'text-[#087b73]' : 'text-[#b4690e]'}`}>{icon}</span>
      <span className="min-w-0">{children}</span>
    </p>
  )
}

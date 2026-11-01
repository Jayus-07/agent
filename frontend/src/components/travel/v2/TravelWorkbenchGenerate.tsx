'use client'

/**
 * TravelWorkbenchGenerate — 工作台生成态（P4a，/travel/itineraries/new）
 *
 * 原型 F1 语义：首页输入/模板/胶囊携 prompt 进入工作台——中列「正在生成
 * 新行程」面板（过程行 + 逐日草稿卡 + 保存/再调整），右列助手对话全程
 * 伴随（澄清选项/tool 卡片）。保存经服务端确认后 router.replace 进正式
 * 工作台编辑态（服务端行程为事实源，无缝衔接）。
 *
 * 生成引擎 = useTravelPlanGeneration（与 TravelChatPage 同一实现，写状态
 * 机/SSE 契约/幂等重试零复制）；草稿地图复用 TravelV2Map。
 */
import { Fragment, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { nanoid } from 'nanoid'
import { ArrowLeft, CalendarDays, CircleAlert, LoaderCircle, MapPin, Send, Sparkles } from 'lucide-react'
import type { TravelToolCard } from '@/api/travel'
import { travelProcessStatusLabel } from '@/components/travel/travelRuntime'
import ToolProcessRows from '@/components/travel/ToolProcessRows'
import { useTravelConversation } from '@/components/travel/useTravelConversation'
import { TravelBottomNav, TravelTopBar } from './TravelV2Frame'
import { ToolCardBlock } from './ToolCardBlock'
import TravelV2Map from './TravelV2Map'
import { travelV2WorkspaceHref } from './travelV2Conversation'
import { useTravelPlanGeneration, visibleTravelMessage } from './useTravelPlanGeneration'
import {
  travelWriteStatusLabel,
  travelWriteStatusRole,
  travelWriteStatusTone,
} from './travelV2WriteState'

export default function TravelWorkbenchGenerate({ initialPrompt = '', initialConversationId = '' }: { initialPrompt?: string; initialConversationId?: string }) {
  const router = useRouter()
  const [conversationId, setConversationId] = useState(() => initialConversationId || `travel-${nanoid(18)}`)
  const { messages, setMessages, syncError } = useTravelConversation(conversationId, { enabled: true })
  const orderedMessages = useMemo(() => messages, [messages])
  const [dayIndex, setDayIndex] = useState(0)

  const generation = useTravelPlanGeneration({
    conversationId,
    messages,
    setMessages,
    canSend: true,
    initialDraft: initialPrompt,
    onTripCreated: (savedTrip, { resolvedConversationId }) => {
      // 服务端已确认创建：replace 进正式工作台（编辑态），会话以服务端关联为权威
      router.replace(travelV2WorkspaceHref(savedTrip.trip_id, resolvedConversationId))
    },
  })
  const {
    draft, setDraft,
    loading, process, processLabel,
    error,
    draftPlan,
    clarifyOptions,
    cardsForTurn,
    sendText,
    saveDraftPlan,
    discardDraftPlan,
    writeFeedback,
    retryPendingPlanWrite,
    pendingPlanWriteRef,
  } = generation

  useEffect(() => {
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    if (url.searchParams.get('conversation_id') !== conversationId) {
      url.searchParams.set('conversation_id', conversationId)
      window.history.replaceState(window.history.state, '', url.toString())
    }
  }, [conversationId])

  const draftDocument = draftPlan?.input.document ?? null
  const draftDay = draftDocument ? draftDocument.days[Math.min(dayIndex, draftDocument.days.length - 1)] : null
  const brief = draftDocument?.brief ?? null

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void sendText(draft)
  }

  return (
    <main className="flex min-h-screen flex-col bg-[#f4f8fd] pb-[68px] text-[#17283f] lg:h-screen lg:min-h-0 lg:pb-0">
      <div className="hidden lg:block"><TravelTopBar /></div>
      <header className="sticky top-0 z-20 flex h-[58px] shrink-0 items-center justify-between border-b border-[#e6edf5] bg-white px-4 lg:mx-3 lg:mt-3 lg:h-[64px] lg:rounded-2xl lg:border">
        <a href="/travel" aria-label="返回旅游首页" className="flex h-9 w-9 items-center justify-center rounded-full text-[#6b7f98] hover:bg-[#f3f7fc]"><ArrowLeft size={18} aria-hidden /></a>
        <div className="min-w-0 text-center">
          <h1 className="truncate text-sm font-bold text-[#233a56]">正在生成新行程</h1>
          <p className="mt-0.5 truncate text-[9px] text-[#8493a6]">草稿 · 未保存 —— 确认后才创建正式行程，全程可与助手对话调整</p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[#f1f6fc] px-2.5 py-1 text-[9px] font-medium text-[#6380a0]">{loading ? '规划中' : draftPlan ? '待确认' : '描述需求'}</span>
      </header>
      <div className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[minmax(300px,.9fr)_minmax(340px,1.15fr)_minmax(360px,1fr)]">
        {/* 左列：草稿概览 + 草稿地图 */}
        <aside className="order-2 flex min-h-0 min-w-0 flex-col gap-3 rounded-[22px] border border-[#e4ebf4] bg-white p-3 shadow-[0_6px_24px_rgba(31,74,130,.05)] lg:order-1">
          <div className="flex items-center justify-between px-1"><div><h2 className="text-sm font-bold text-[#263b55]">行程草稿概览</h2><p className="mt-1 text-[9px] text-[#8a98aa]">生成后按天预览 · 保存后进入正式工作台</p></div><span className="rounded-full bg-[#fffbeb] px-2.5 py-1 text-[9px] font-medium text-[#b45309]">草稿</span></div>
          {brief ? (
            <>
              <section aria-label="草稿概览" className="shrink-0 rounded-xl border border-[#e2ecf7] bg-[#f6faff] p-3">
                <dl className="space-y-1.5">
                  {[['目的地', brief.destination], ['天数', `${brief.day_count} 天`], ['同行', `${brief.travelers.adults + brief.travelers.children} 人`], ['预算', brief.budget.amount === null ? '待设置' : `¥${brief.budget.amount.toLocaleString()}`], ['节奏', brief.pace === 'relaxed' ? '轻松' : brief.pace === 'intense' ? '紧凑' : '均衡']].map(([label, value]) => (
                    <div key={label} className="flex items-center justify-between gap-3 text-[10px] leading-4"><dt className="font-semibold text-[#52698a]">{label}</dt><dd className="min-w-0 flex-1 text-right text-[#42596f]">{value}</dd></div>
                  ))}
                </dl>
              </section>
              <div className="flex shrink-0 gap-2 overflow-x-auto pb-1">{draftDocument!.days.map((day, index) => <button key={day.day_id} type="button" aria-pressed={dayIndex === index} onClick={() => setDayIndex(index)} className={`min-w-[88px] rounded-xl border px-3 py-2 text-left ${dayIndex === index ? 'border-[#76aaf4] bg-[#f2f7ff] text-[#246bd2]' : 'border-[#e6edf5] bg-white text-[#6e8097]'}`}><span className="block truncate text-[10px] font-semibold">第 {index + 1} 天{day.title?.trim() ? ` · ${day.title.trim()}` : ''}</span><span className="mt-1 block text-[8px]">{day.date || '日期待定'}</span></button>)}</div>
              <div className="min-h-[220px] flex-1">{draftDay && <TravelV2Map day={draftDay} selectedItemId="" selectedLocation={null} onSelectItem={() => {}} />}</div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-[#cfdeef] bg-[#fbfdff] p-6 text-center"><div><MapPin size={22} className="mx-auto text-[#83a7d5]" aria-hidden /><p className="mt-2 text-xs font-semibold text-[#465e7a]">还没有草稿</p><p className="mt-1 text-[10px] leading-4 text-[#8a9bb0]">在右侧把目的地、天数和节奏告诉助手；<br />规划完成后这里会出现逐日草稿与地图。</p></div></div>
          )}
        </aside>
        {/* 中列：生成面板（过程行 + 逐日草稿卡 + 保存） */}
        <section className="order-1 flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)] lg:order-2">
          <header className="shrink-0 border-b border-[#edf1f6] px-4 pb-3 pt-3.5"><h2 className="text-sm font-bold text-[#263b55]">正在生成新行程</h2><p className="mt-1 text-[9px] text-[#8a98aa]">边分析边生成 · 完成的阶段可折叠展开 · 确认后才落正式行程</p></header>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3" aria-live="polite">
            {loading && <div className="rounded-xl border border-[#e4ebf3] bg-[#fbfdff] px-3 py-2.5 text-xs text-[#526984]"><div className="flex items-center gap-2"><LoaderCircle size={14} className="animate-spin text-[#2878f5]" aria-hidden />{processLabel || '正在连接旅游 Agent…'}</div>{process && Object.keys(process.stages).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(process.stages).map(([name, stage]) => <span key={name} className={`rounded-full px-2 py-1 text-[9px] ${stage.status === 'completed' ? 'bg-emerald-50 text-emerald-700' : stage.status === 'failed' ? 'bg-rose-50 text-rose-700' : 'bg-white text-[#6e8097]'}`}>{name}</span>)}</div>}{process && process.tools.length > 0 && <details open className="mt-2 rounded-xl border border-[#e4ebf3] bg-[#fbfdff] px-2.5 py-2 text-[9px] text-[#526984]"><summary className="cursor-pointer font-semibold">工具过程 · {process.tools.length} 项</summary><ul aria-label="Tool 与 Provider 状态" className="mt-1.5 space-y-1.5"><ToolProcessRows tools={process.tools.map((tool) => ({ ...tool, error: undefined }))} /></ul></details>}</div>}
            {!loading && !draftPlan && <div className="rounded-xl border border-dashed border-[#cfdeef] bg-[#fbfdff] p-6 text-center text-xs text-[#8797aa]">把旅行想法发给右侧助手，规划过程与逐日草稿会显示在这里。</div>}
            {draftPlan && <section aria-label="行程草稿预览" className="rounded-2xl border border-[#cfe0f5] bg-[#f6faff] p-3">
              <div className="flex items-center justify-between gap-2"><p className="text-xs font-semibold text-[#263c57]">草稿已生成 · 确认后保存为正式行程</p><span className="rounded-full bg-white px-2 py-0.5 text-[9px] font-medium text-[#6380a0]">草稿 · 未保存</span></div>
              <div className="mt-2 space-y-2">{draftPlan.input.document.days.map((day, index) => <div key={day.day_id} className="rounded-xl border border-[#e2ecf7] bg-white p-2.5"><p className="text-[11px] font-semibold text-[#2b405b]">第 {index + 1} 天 · {day.title || '行程安排'}</p><ul className="mt-1.5 space-y-1">{day.items.map((item) => <li key={item.item_id} className="flex items-baseline gap-2 text-[10px] leading-4 text-[#526984]"><span className="shrink-0 text-[#8190a3]">{item.start_time || '时间待定'}</span><span className="truncate">{item.title}</span><span className="shrink-0 text-[#93a0b1]">{item.duration_min} 分钟</span></li>)}</ul></div>)}</div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" onClick={() => void saveDraftPlan()} disabled={writeFeedback?.status === 'VALIDATING' || writeFeedback?.status === 'SAVING' || writeFeedback?.status === 'UNKNOWN'} className="min-h-10 rounded-xl bg-[#2878f5] px-4 text-xs font-semibold text-white transition hover:bg-[#1769e9] disabled:cursor-not-allowed disabled:opacity-60">保存为正式行程</button>
                <button type="button" onClick={discardDraftPlan} disabled={writeFeedback?.status === 'VALIDATING' || writeFeedback?.status === 'SAVING'} className="min-h-10 rounded-xl border border-[#dce6f1] bg-white px-4 text-xs font-medium text-[#5b718d] transition hover:border-[#b9d3fa] hover:text-[#2878f5] disabled:opacity-60">再调整一下</button>
              </div>
            </section>}
            {error && <div role="alert" className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-[10px] leading-5 text-rose-800"><CircleAlert size={14} className="mt-0.5 shrink-0" aria-hidden />{error}</div>}
            {writeFeedback && <div role={travelWriteStatusRole(writeFeedback.status)} className={`rounded-xl border px-3 py-2.5 text-sm leading-5 ${travelWriteStatusTone(writeFeedback.status)}`}>
              <p><span className="mr-1.5 font-semibold">{travelWriteStatusLabel(writeFeedback.status)}</span>{writeFeedback.message}</p>
              {writeFeedback.status === 'UNKNOWN' && pendingPlanWriteRef.current && <button type="button" onClick={() => void retryPendingPlanWrite()} className="mt-2 min-h-10 rounded-lg border border-amber-300 bg-white px-3 text-sm font-semibold text-amber-950">使用同一请求安全重试</button>}
            </div>}
            {syncError && <p role="status" className="text-[10px] text-amber-700">{syncError}</p>}
          </div>
        </section>
        {/* 右列：助手对话全程伴随 */}
        <aside className="order-3 flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]">
          <header className="shrink-0 border-b border-[#edf1f6] px-4 py-3"><div className="flex items-center gap-2"><span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={15} aria-hidden /></span><div><h2 className="text-sm font-bold text-[#263b55]">AI 旅行助手</h2><p className="mt-0.5 text-[9px] text-[#8a98aa]">全程伴随：澄清、调整和确认都在对话里</p></div></div></header>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3" aria-live="polite">
            {orderedMessages.length === 0 && <div className="flex items-start gap-3"><span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span><div className="max-w-[88%] rounded-2xl rounded-tl-md bg-[#f2f7fd] px-4 py-3 text-xs leading-6 text-[#405875]">告诉我这次旅行的目的地、天数、同行人数和节奏；生成过程随时可以补充或调整。</div></div>}
            {orderedMessages.map((message) => <Fragment key={message.id}><div className={`flex items-start gap-3 ${message.role === 'user' ? 'justify-end' : ''}`}>{message.role === 'assistant' && <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span>}<div className={`max-w-[88%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-xs leading-6 ${message.role === 'user' ? 'rounded-tr-md bg-[#2878f5] text-white' : 'rounded-tl-md bg-[#f2f7fd] text-[#405875]'}`}>{message.role === 'assistant' && message.tag && <span className={`mb-1 block text-[9px] font-semibold ${message.tone === 'warn' ? 'text-amber-700' : 'text-[#4882ca]'}`}>{message.tag}</span>}{visibleTravelMessage(message.text)}</div></div>{message.role === 'assistant' && cardsForTurn(message.turnId).length > 0 && <div className="pl-11"><ToolCardBlock cards={cardsForTurn(message.turnId)} onAsk={(text) => void sendText(text)} /></div>}</Fragment>)}
            {loading && cardsForTurn(process?.runId ?? '').length > 0 && <div className="pl-11"><ToolCardBlock cards={cardsForTurn(process?.runId ?? '')} onAsk={(text) => void sendText(text)} /></div>}
            {clarifyOptions && !loading && <div className="flex flex-wrap gap-2 pl-11" aria-label="槽位澄清选项">{clarifyOptions.options.map((option) => <button key={option.label} type="button" disabled={loading} onClick={() => void sendText(option.label)} className="rounded-full border border-[#a9c9f7] bg-[#eaf2ff] px-3 py-1.5 text-[11px] font-medium text-[#1f62c9] transition hover:bg-[#d9e9ff] disabled:opacity-50">{option.label}</button>)}</div>}
          </div>
          <form onSubmit={submit} className="shrink-0 border-t border-[#edf1f6] p-3"><label className="sr-only" htmlFor="workbench-generate-input">描述旅行需求</label><div className="flex items-end gap-2 rounded-2xl border border-[#dfe8f3] bg-[#fbfdff] p-2 focus-within:border-[#99bdfa] focus-within:ring-4 focus-within:ring-[#2878f5]/[.08]"><textarea id="workbench-generate-input" aria-label="描述旅行需求" rows={2} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={loading || writeFeedback?.status === 'UNKNOWN'} placeholder="描述目的地、天数、同行人和节奏…" className="max-h-32 min-h-11 flex-1 resize-none bg-transparent px-2 py-2 text-xs leading-5 text-[#344b67] outline-none placeholder:text-[#9aa8b8] disabled:opacity-60" /><button type="submit" aria-label="发送消息" disabled={!draft.trim() || loading || writeFeedback?.status === 'UNKNOWN'} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#2878f5] text-white transition hover:bg-[#1769e9] disabled:cursor-not-allowed disabled:bg-[#c7d7ed]"><Send size={16} aria-hidden /></button></div><p className="mt-2 px-1 text-[9px] text-[#9aa8b8]">消息提交至旅游 Agent · 新行程仅在服务端确认后创建</p></form>
        </aside>
      </div>
      <TravelBottomNav active="home" />
    </main>
  )
}

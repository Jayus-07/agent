'use client'

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { CircleAlert, LoaderCircle, Send, Sparkles } from 'lucide-react'
import { fetchTravelCandidates, streamTravelPlan, type TravelCandidate, type TravelCandidatesResponse, type TravelStreamEvent } from '@/api/travel'
import { initialTravelProcess, reduceTravelStreamEvent, travelProcessStatusLabel, type TravelProcessState } from '@/components/travel/travelRuntime'
import { useTravelConversation } from '@/components/travel/useTravelConversation'
import { resolveTravelStreamOutcome } from './travelLiveRuntime'

export default function TravelLiveAssistantPanel({ conversationId, onPlanUpdated }: {
  conversationId: string
  onPlanUpdated: () => void
}) {
  const { messages, setMessages, syncError } = useTravelConversation(conversationId)
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [process, setProcess] = useState<TravelProcessState | null>(null)
  const [error, setError] = useState('')
  const [candidateData, setCandidateData] = useState<TravelCandidatesResponse | null>(null)
  const [candidateError, setCandidateError] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  const loadCandidates = useCallback(() => {
    void fetchTravelCandidates(conversationId).then(setCandidateData).catch(() => {
      setCandidateError('候选池暂时不可用。先与 AI 对话后，旅游 Agent 会在规划过程中生成真实候选。')
    })
  }, [conversationId])

  useEffect(() => {
    loadCandidates()
    return () => abortRef.current?.abort()
  }, [loadCandidates])

  const send = useCallback(async (raw: string) => {
    const text = raw.trim()
    if (!text || loading) return
    const clientRunId = `client-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)
    setError('')
    setDraft('')
    setProcess(initialTravelProcess(clientRunId))
    setMessages((current) => [...current, {
      id: `${clientRunId}:user`, conversationId, turnId: clientRunId, role: 'user', text,
    }])
    const events: TravelStreamEvent[] = []
    try {
      for await (const event of streamTravelPlan(text, conversationId, {
        signal: controller.signal, clientRunId, mode: 'chat', source: 'manual',
      })) {
        events.push(event)
        setProcess((current) => current ? reduceTravelStreamEvent(current, event) : current)
      }
      const outcome = resolveTravelStreamOutcome(events)
      if (outcome.kind === 'failed') throw new Error(outcome.message)
      const response = outcome.response
      let message = response.final_answer?.trim() || '已收到，我可以继续帮你调整。'
      let tag = outcome.kind === 'clarification' ? '需要补充信息' : '旅游问答'
      let tone: 'ok' | 'warn' = 'ok'
      if (outcome.kind === 'draft') {
        tag = '待确认版本'
        tone = 'warn'
        message += '\n\n服务端当前将此修改标记为待确认版本，行程工作台仍展示已确认内容。'
      }
      setMessages((current) => [...current, {
        id: `${clientRunId}:assistant`, conversationId, turnId: clientRunId,
        role: 'assistant', text: message, tag, tone,
      }])
      if (response.itinerary) onPlanUpdated()
      loadCandidates()
    } catch (cause) {
      if (!controller.signal.aborted) {
        const message = cause instanceof Error ? cause.message : '旅游服务暂时不可用。'
        setError(message)
        setMessages((current) => [...current, {
          id: `${clientRunId}:assistant`, conversationId, turnId: clientRunId,
          role: 'assistant', text: `本次修改未完成：${message}`, tag: '请求失败', tone: 'warn',
        }])
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setLoading(false)
    }
  }, [conversationId, loadCandidates, loading, onPlanUpdated, setMessages])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void send(draft)
  }

  const candidates = Object.entries(candidateData?.groups ?? {}).flatMap(([category, items]) =>
    items.map((candidate) => ({ ...candidate, category: candidate.category || category })))

  return <section className="flex min-h-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]">
    <header className="shrink-0 border-b border-[#edf1f6] px-4 py-3"><div className="flex items-center gap-2"><span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={15} aria-hidden /></span><div><h2 className="text-sm font-bold text-[#263b55]">行程助手</h2><p className="mt-0.5 text-[9px] text-[#8a98aa]">对话结果通过旅游 Agent 返回</p></div></div></header>
    <div className="min-h-0 flex-1 overflow-y-auto p-3">
      {messages.length === 0 ? <p className="rounded-xl bg-[#f4f8fd] px-3 py-3 text-[10px] leading-5 text-[#6e8097]">可以问景点、天气和交通，也可以直接说“把某地点移到第二天”。只有服务端返回的行程会更新此工作台。</p> : <div className="space-y-2.5">{messages.map((message) => <div key={message.id} className={`rounded-xl px-3 py-2.5 text-[10px] leading-5 ${message.role === 'user' ? 'ml-5 bg-[#2878f5] text-white' : 'mr-2 bg-[#f2f7fd] text-[#49617e]'}`}>{message.role === 'assistant' && message.tag && <span className={`mb-1 block text-[9px] font-semibold ${message.tone === 'warn' ? 'text-amber-700' : 'text-[#4c81bd]'}`}>{message.tag}</span>}{message.text}</div>)}</div>}
      {loading && <div className="mt-3 rounded-xl bg-[#f2f7fd] px-3 py-2 text-[10px] text-[#526984]"><div className="flex items-center gap-2"><LoaderCircle size={13} className="animate-spin text-[#2878f5]" aria-hidden />{travelProcessStatusLabel({ loading, stopped: false, status: process?.status })}</div>{process && Object.entries(process.stages).length > 0 && <div className="mt-2 flex flex-wrap gap-1">{Object.entries(process.stages).map(([stage, status]) => <span key={stage} className="rounded-full bg-white px-2 py-0.5 text-[8px] text-[#71839a]">{stage}: {status.status === 'completed' ? '完成' : status.status === 'failed' ? '失败' : '进行中'}</span>)}</div>}</div>}
      {error && <p role="alert" className="mt-3 flex items-start gap-1.5 rounded-lg bg-rose-50 px-2.5 py-2 text-[9px] leading-4 text-rose-700"><CircleAlert size={12} className="mt-0.5 shrink-0" aria-hidden />{error}</p>}
      {syncError && <p role="status" className="mt-2 text-[9px] text-amber-700">{syncError}</p>}
      <div className="mt-4 border-t border-[#edf1f6] pt-3"><div className="mb-2 flex items-center justify-between"><h3 className="text-[10px] font-bold text-[#405874]">精选景点候选</h3>{candidateData && <span className="text-[8px] text-[#98a5b5]">行程版本 v{candidateData.plan_version}</span>}</div>{candidateData?.available && candidates.length > 0 ? <div className="space-y-1.5">{candidates.slice(0, 8).map((candidate) => <CandidateButton key={candidate.poi_id} candidate={candidate} onChoose={() => setDraft((value) => value || `请评估是否把${candidate.name}加入当前行程，并说明适合安排在哪一天。`)} />)}</div> : <p className="rounded-lg bg-[#f7faff] px-2.5 py-2 text-[9px] leading-4 text-[#8a98aa]">{candidateError || candidateData?.hint || '完成一次规划后，这里会显示旅游 Agent 返回的真实景点候选。'}</p>}</div>
    </div>
    <form onSubmit={submit} className="shrink-0 border-t border-[#edf1f6] p-2.5"><label htmlFor="trip-assistant-input" className="sr-only">给行程助手发消息</label><div className="flex items-end gap-1.5 rounded-xl border border-[#dfe8f3] bg-[#fbfdff] p-1.5"><textarea id="trip-assistant-input" rows={2} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={loading} placeholder="问问题或描述行程调整…" className="min-h-10 flex-1 resize-none bg-transparent px-1.5 py-1 text-[10px] leading-4 text-[#344b67] outline-none placeholder:text-[#9aa8b8]" /><button type="submit" aria-label="发送消息" disabled={!draft.trim() || loading} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#2878f5] text-white disabled:bg-[#c7d7ed]"><Send size={13} aria-hidden /></button></div></form>
  </section>
}

function CandidateButton({ candidate, onChoose }: { candidate: TravelCandidate; onChoose: () => void }) {
  return <button type="button" onClick={onChoose} className="w-full rounded-lg border border-[#e8eef5] bg-white px-2.5 py-2 text-left transition hover:border-[#b8d3f7] hover:bg-[#fbfdff]"><div className="flex items-center justify-between gap-2"><span className="truncate text-[10px] font-medium text-[#435a76]">{candidate.name}</span><span className="shrink-0 rounded-md bg-[#f1f6fc] px-1.5 py-0.5 text-[8px] text-[#738aa4]">{candidate.category}</span></div><p className="mt-1 line-clamp-2 text-[8px] leading-4 text-[#95a2b1]">{candidate.reason || `来源：${candidate.source || '旅游 Agent'}`}</p></button>
}

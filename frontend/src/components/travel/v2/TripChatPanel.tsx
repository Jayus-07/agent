'use client'

import { useState, type FormEvent } from 'react'
import { MapPin, Send, Sparkles } from 'lucide-react'
import { TRAVEL_CANDIDATES } from './travelV2Data'
import { DemoNotice } from './TravelV2Frame'

export default function TripChatPanel({ onAddSuggestion }: { onAddSuggestion: (candidateId: string) => void }) {
  const [draft, setDraft] = useState('')
  const [sent, setSent] = useState<string[]>([])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft.trim()) return
    setSent((items) => [...items, draft.trim()])
    setDraft('')
  }

  return (
    <aside className="flex min-h-0 flex-col overflow-hidden rounded-[22px] border border-[#e4ebf4] bg-white shadow-[0_6px_24px_rgba(31,74,130,.05)]">
      <header className="flex items-center gap-2.5 border-b border-[#edf1f6] px-4 py-3.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[#eaf2ff] text-[#2878f5]"><Sparkles size={16} aria-hidden /></span>
        <div className="min-w-0 flex-1"><h2 className="text-xs font-bold text-[#2a405d]">AI 旅行助手</h2><p className="mt-0.5 text-[9px] text-[#92a0b1]">和行程一起讨论</p></div>
        <span className="rounded-full bg-[#f0f6ff] px-2 py-1 text-[8px] font-medium text-[#5380b5]">演示</span>
      </header>
      <div className="border-b border-[#edf1f6] p-3"><DemoNotice compact /></div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3.5">
        <div className="max-w-[95%] rounded-2xl rounded-tl-md bg-[#f2f7fd] px-3 py-2.5 text-[10px] leading-5 text-[#506984]">我把杭州三天安排成轻松节奏：灵隐寺、茶园和西湖都有时间慢慢逛。你可以继续调整地点和交通。</div>
        <div className="ml-auto max-w-[90%] rounded-2xl rounded-tr-md bg-[#eaf2ff] px-3 py-2.5 text-[10px] leading-5 text-[#416b9b]">第二天想少走一点，多留些休息时间。</div>
        <div className="max-w-[95%] rounded-2xl rounded-tl-md bg-[#f2f7fd] px-3 py-2.5 text-[10px] leading-5 text-[#506984]">这段是示例对话，尚未发送给旅游 Agent。编辑行程可通过卡片菜单预览本地操作。</div>
        {sent.map((text, index) => <div key={`${index}-${text}`} className="ml-auto max-w-[90%] rounded-2xl rounded-tr-md bg-[#eaf2ff] px-3 py-2.5 text-[10px] leading-5 text-[#416b9b]">{text}<span className="mt-1 block text-[8px] text-[#7896ba]">仅本地演示，未提交</span></div>)}
      </div>
      <div className="border-t border-[#edf1f6] p-3">
        <form onSubmit={submit} className="flex items-center gap-2 rounded-xl border border-[#e3eaf3] bg-[#fbfdff] p-1.5">
          <label className="sr-only" htmlFor="trip-chat-draft">输入行程调整</label>
          <input id="trip-chat-draft" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="继续聊聊你的想法…" className="min-w-0 flex-1 bg-transparent px-2 text-[10px] outline-none placeholder:text-[#a0adbd]" />
          <button type="submit" aria-label="发送演示消息" disabled={!draft.trim()} className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#2878f5] text-white disabled:bg-[#c7d7ed]"><Send size={13} aria-hidden /></button>
        </form>
        <div className="mb-2 mt-3 flex items-center justify-between"><h3 className="text-[10px] font-semibold text-[#516984]">附近灵感</h3><span className="text-[8px] text-[#9aa7b6]">示例候选</span></div>
        <div className="space-y-2">
          {TRAVEL_CANDIDATES.slice(0, 2).map((candidate) => <div key={candidate.id} className="flex items-center gap-2 rounded-xl border border-[#edf1f6] p-2">
            <img src={candidate.image} alt="" loading="lazy" className="h-9 w-10 rounded-lg object-cover" />
            <div className="min-w-0 flex-1"><p className="truncate text-[9px] font-semibold text-[#526781]">{candidate.title}</p><p className="mt-0.5 truncate text-[8px] text-[#95a2b1]">{candidate.category} · {candidate.duration}分钟</p></div>
            <button type="button" onClick={() => onAddSuggestion(candidate.id)} aria-label={`演示添加${candidate.title}`} className="flex h-7 w-7 items-center justify-center rounded-full bg-[#eff6ff] text-[#2878f5]"><MapPin size={13} aria-hidden /></button>
          </div>)}
        </div>
      </div>
    </aside>
  )
}

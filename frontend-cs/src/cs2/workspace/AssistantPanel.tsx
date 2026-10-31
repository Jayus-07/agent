'use client'

// cs-workstation-v2 P4：AI 助手面板（右栏第三 Tab，五块）。
// 数据源 = assist.suggestion 瞬态事件（意图/情绪/SOP/推荐/知识）；
// 坐席动作：采用（填输入框）/ 引用回复（追加引文）/ 存为话术（个人话术）。
import type { AssistSnapshot } from './assistTypes'

interface Props {
  assist: AssistSnapshot | null
  selected: boolean
  onAdopt: (text: string) => void
  onQuote: (text: string, source: string, title: string) => void
  onSaveCanned: (text: string, source: string) => void
}

const REPLY_KINDS = new Set(['knowledge_answer', 'order_summary', 'emotion_alert'])

function ScoreBadge({ score }: { score: number | null | undefined }) {
  if (score == null) return null
  return (
    <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-600">
      置信 {score}
    </span>
  )
}

function EmotionBadge({ emotion }: { emotion?: AssistSnapshot['emotion'] }) {
  if (!emotion) return <span className="text-xs text-slate-400">—</span>
  const cls
    = emotion.level === 'alert'
      ? 'bg-red-50 text-red-600'
      : emotion.level === 'warn'
        ? 'bg-amber-50 text-amber-600'
        : 'bg-emerald-50 text-emerald-600'
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] ${cls}`}>
      {emotion.label}
    </span>
  )
}

export default function AssistantPanel({
  assist,
  selected,
  onAdopt,
  onQuote,
  onSaveCanned,
}: Props) {
  const replies = (assist?.suggestions ?? []).filter((s) => REPLY_KINDS.has(s.kind))
  const knowledge = (assist?.suggestions ?? []).filter((s) => s.kind === 'knowledge_snippet')

  return (
    <div className="space-y-3 p-2 text-xs">
      <div className="flex items-center gap-1.5 text-[11px] text-slate-500">
        <span
          className={
            selected
              ? 'inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500'
              : 'inline-block h-1.5 w-1.5 rounded-full bg-slate-300'
          }
          aria-hidden
        />
        {selected ? 'AI 实时辅助 · 监听中' : '选择会话后开启监听'}
      </div>

      <div className="rounded-lg border border-slate-100 p-2">
        <p className="mb-1 text-[11px] text-slate-400">意图识别</p>
        {assist?.intent ? (
          <p className="flex items-center gap-1.5">
            <span className="text-xs text-slate-800">{assist.intent.label}</span>
            {assist.intent.confidence != null && (
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-600">
                {assist.intent.confidence}
              </span>
            )}
          </p>
        ) : (
          <p className="text-xs text-slate-400">—</p>
        )}
        <p className="mb-1 mt-2 text-[11px] text-slate-400">情绪检测</p>
        <EmotionBadge emotion={assist?.emotion} />
      </div>

      <div>
        <p className="mb-1 text-[11px] text-slate-400">AI 推荐回复</p>
        {!assist && (
          <p className="rounded-lg bg-slate-50 p-2 text-[11px] text-slate-400">
            AI 正在分析最新消息…
          </p>
        )}
        {assist && replies.length === 0 && (
          <p className="p-1 text-[11px] text-slate-400">暂无推荐</p>
        )}
        {replies.map((s, i) => (
          <div
            key={`${s.kind}-${i}`}
            className={
              s.kind === 'emotion_alert'
                ? 'mb-1.5 rounded-lg border border-red-200 bg-red-50/40 p-2'
                : 'mb-1.5 rounded-lg border border-slate-100 p-2'
            }
          >
            {assist?.trigger === 'followup' && i === 0 && (
              <span className="mb-1 mr-1 inline-block rounded-full bg-red-500 px-1.5 py-0.5 text-[10px] text-white">
                新
              </span>
            )}
            <p className="text-[11px] leading-relaxed text-slate-700">{s.text}</p>
            <p className="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-slate-400">
              <span>来源：{s.source}</span>
              <ScoreBadge score={s.score} />
            </p>
            {s.kind !== 'emotion_alert' && (
              <p className="mt-1.5 flex gap-1.5">
                <button
                  className="rounded-lg bg-indigo-600 px-2 py-1 text-[11px] text-white hover:opacity-90"
                  onClick={() => onAdopt(s.text)}
                >
                  采用
                </button>
                <button
                  className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
                  onClick={() => onSaveCanned(s.text, s.source)}
                >
                  存为话术
                </button>
              </p>
            )}
          </div>
        ))}
      </div>

      <div>
        <p className="mb-1 text-[11px] text-slate-400">知识推荐</p>
        {knowledge.length === 0 && (
          <p className="p-1 text-[11px] text-slate-400">暂无命中知识</p>
        )}
        {knowledge.map((s, i) => (
          <div key={`kb-${i}`} className="mb-1.5 rounded-lg border border-slate-100 p-2">
            <p className="flex flex-wrap items-center gap-1">
              <b className="text-[11px] text-slate-800">知识库原文</b>
              <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                {s.source}
              </span>
              <ScoreBadge score={s.score} />
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-600">{s.text}</p>
            <p className="mt-1.5 flex gap-1.5">
              <button
                className="rounded-lg bg-indigo-600 px-2 py-1 text-[11px] text-white hover:opacity-90"
                onClick={() => onQuote(s.text, s.source, '知识库原文')}
              >
                引用回复
              </button>
              <button
                className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
                onClick={() => onSaveCanned(s.text, s.source)}
              >
                存为话术
              </button>
            </p>
          </div>
        ))}
      </div>

      <div>
        <p className="mb-1 text-[11px] text-slate-400">SOP 下一步</p>
        {(assist?.sop ?? []).length > 0 ? (
          <ul className="space-y-1">
            {(assist?.sop ?? []).map((step) => (
              <li key={step.label} className="flex items-center gap-1.5">
                {step.state === 'done' && (
                  <span className="text-emerald-600" aria-hidden>✓</span>
                )}
                {step.state === 'cur' && (
                  <span className="text-indigo-600" aria-hidden>•</span>
                )}
                {step.state === 'todo' && (
                  <span className="text-slate-300" aria-hidden>○</span>
                )}
                <span
                  className={
                    step.state === 'todo' ? 'text-slate-400' : 'text-slate-700'
                  }
                >
                  {step.label}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="p-1 text-[11px] text-slate-400">—</p>
        )}
      </div>
    </div>
  )
}

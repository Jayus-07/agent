'use client'

/**
 * TravelOnboarding — 新用户旅游偏好引导问卷（2026-10-08 拍板）。
 *
 * 触发：进入 /travel 且 GET /api/travel/preferences 为全空（新用户）。
 * 形态：4 题单选选项卡，趣味话术；答完自动 PUT 保存并收起。
 * 价值：origin/pace/diet/transport 直接进规划参数（slot_filler 预填），
 * 老用户不弹（设置里改偏好的能力走既有表单）。
 */
import { useState } from 'react'
import { Compass } from 'lucide-react'
import {
  saveMyPreferences, type TravelPrefs,
} from '@/api/travel'

interface Question {
  key: 'origin' | 'pace' | 'diet' | 'transport'
  emoji: string
  title: string
  options: { label: string; value: string }[]
}

const QUESTIONS: Question[] = [
  {
    key: 'origin',
    emoji: '🎒',
    title: '从哪儿出发呀？',
    options: [
      { label: '福州', value: '福州' },
      { label: '厦门', value: '厦门' },
      { label: '泉州', value: '泉州' },
      { label: '杭州', value: '杭州' },
    ],
  },
  {
    key: 'pace',
    emoji: '🐾',
    title: '旅途节奏你喜欢哪种？',
    options: [
      { label: '慢悠悠逛吃', value: 'relaxed' },
      { label: '张弛有度', value: 'moderate' },
      { label: '特种兵打卡', value: 'intense' },
    ],
  },
  {
    key: 'diet',
    emoji: '🍜',
    title: '有什么不吃的吗？',
    options: [
      { label: '不忌口，啥都吃', value: '' },
      { label: '不吃辣', value: '不吃辣' },
      { label: '素食为主', value: '素食' },
      { label: '少吃海鲜', value: '少吃海鲜' },
    ],
  },
  {
    key: 'transport',
    emoji: '🚄',
    title: '出门怎么走？',
    options: [
      { label: '高铁优先', value: '高铁' },
      { label: '自驾', value: '自驾' },
      { label: '飞机', value: '飞机' },
      { label: '都行，看方便', value: '' },
    ],
  },
]

export default function TravelOnboarding({
  onDone,
  onSkip,
}: {
  /** 提交成功（答完全部 4 题）后回调——页面把偏好并入表单 */
  onDone: (prefs: TravelPrefs) => void
  /** 用户点「跳过」 */
  onSkip: () => void
}) {
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const q = QUESTIONS[step]
  const pick = async (opt: { label: string; value: string }) => {
    const next = { ...answers, [q.key]: opt.value }
    setAnswers(next)
    if (step < QUESTIONS.length - 1) {
      setStep(step + 1)
      return
    }
    // 最后一题：保存（diet/transport 的「无偏好」存空串，与后端契约一致）
    setSaving(true)
    const prefs: TravelPrefs = {
      origin: next.origin ?? '',
      preferences: [],
      pace: (next.pace ?? '') as TravelPrefs['pace'],
      diet: next.diet ?? '',
      lodging: '',
      transport: next.transport ?? '',
    }
    const ok = await saveMyPreferences(prefs)
    setSaving(false)
    if (ok) onDone(prefs)
    else onSkip() // 保存失败不拦路：引导是增强，不是前置门
  }

  return (
    <section
      className="animate-fade-in mx-auto max-w-[680px] rounded-2xl border border-[#c9dcd7] bg-white p-5 shadow-card sm:p-6"
      aria-label="旅行偏好问卷"
    >
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#087b73]/10">
          <Compass size={17} className="text-[#087b73]" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-[#183037]">
            出发前先认识你一下 {q.emoji}
          </p>
          <p className="mt-0.5 text-[11px] text-[#5c7074]">
            4 个小问题，之后的行程会自动合你的口味（第 {step + 1}/{QUESTIONS.length} 题）
          </p>
        </div>
      </div>

      <p className="mt-4 text-[15px] font-medium text-[#183037]">{q.title}</p>
      <div className="mt-2.5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {q.options.map((opt) => (
          <button
            key={opt.label}
            type="button"
            disabled={saving}
            onClick={() => void pick(opt)}
            className="rounded-xl border border-[#dae7e5] bg-[#f4faf8] px-3 py-2.5 text-[13px]
              font-medium text-[#183037] transition-colors hover:border-[#087b73]/50
              hover:bg-[#e2f0ee] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {opt.label}
          </button>
        ))}
      </div>

      <div className="mt-3 flex items-center justify-between">
        <div className="flex gap-1">
          {QUESTIONS.map((_, i) => (
            <span
              key={i}
              className={`h-1.5 w-6 rounded-full ${i <= step ? 'bg-[#087b73]' : 'bg-[#e2efec]'}`}
            />
          ))}
        </div>
        <button
          type="button"
          onClick={onSkip}
          className="text-[11px] text-[#8fa5a3] hover:text-[#5c7074]"
        >
          跳过，直接规划 →
        </button>
      </div>
    </section>
  )
}

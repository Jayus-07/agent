'use client'

import { Hotel, Search, TrainFront, Utensils } from 'lucide-react'
import { ToolPreviewBody } from '@/components/travel/ToolPreviews'
import type { TravelToolCard } from '@/api/travel'

/**
 * F18 对话查询结果卡（tool.card）共享渲染块。
 *
 * 条目渲染复用 V1 ToolPreviewBody（商户/车次同一套实现，避免两处漂移）；
 * 卡片只描述查询事实，动作（帮我排/替换/选入）由调用方注入回调并直通
 * 既有提交链（聊天管线 / ai-edit / selections），本组件不建写路径。
 */

const KIND_LABELS: Record<TravelToolCard['kind'], string> = {
  train: '车次查询',
  hotel: '酒店查询',
  food: '餐饮推荐',
  poi: '景点查询',
}

const KIND_ICONS: Record<TravelToolCard['kind'], typeof Search> = {
  train: TrainFront,
  hotel: Hotel,
  food: Utensils,
  poi: Search,
}

/** onAsk：把条目交给助手继续编排（聊天管线或 ai-edit 既有写路径）。 */
export function ToolCardBlock({
  cards,
  onAsk,
}: {
  cards: TravelToolCard[]
  onAsk?: (text: string) => void
}) {
  if (cards.length === 0) return null
  return (
    <div className="mt-2 space-y-2">
      {cards.map((card, index) => {
        const Icon = KIND_ICONS[card.kind]
        return (
          <section
            key={`${card.kind}:${index}`}
            aria-label={KIND_LABELS[card.kind]}
            className="rounded-xl border border-[#e2ecf7] bg-[#fbfdff] p-2.5"
          >
            <p className="flex items-center gap-1.5 text-[10px] font-semibold text-[#263c57]">
              <span className="flex h-5 w-5 items-center justify-center rounded-md bg-[#eaf2ff] text-[#2878f5]"><Icon size={12} aria-hidden /></span>
              {card.title || KIND_LABELS[card.kind]}
              <span className="ml-auto font-normal text-[#93a0b1]">{card.items.length} 条</span>
            </p>
            <ToolPreviewBody
              preview={card.items}
              category={card.kind}
              onAsk={onAsk}
            />
            {card.disclosure && (
              <p className="mt-1.5 text-[9px] leading-4 text-[#8fa5a3]">{card.disclosure}</p>
            )}
          </section>
        )
      })}
    </div>
  )
}

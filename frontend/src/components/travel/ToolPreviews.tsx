"use client";

import { AlertCircle, Hotel, Utensils } from "lucide-react";

/**
 * Tool 三类可视化卡（2026-10-03 自 page.tsx 迁出为共享组件）：
 * 生成中进度卡与完成后的「Tool 执行记录」卡都要渲染同一批 preview，
 * 抽成单一实现避免两处漂移。条目来自后端 tool.result 帧 preview 字段
 * （后端已截断 ≤6 条），缺失字段不补造。
 */

/** 知乎攻略主题 → 徽章文案（与后端 GUIDE_TOPIC_LABELS 对齐） */
const GUIDE_TOPIC_LABELS: Record<string, string> = {
  attraction: "景点",
  food: "美食",
  city: "城市特色",
};

export function MerchantPreview({ preview, category }: { preview: Array<Record<string, unknown>>; category?: string }) {
  return (
    <div className="mt-2 grid gap-2 sm:grid-cols-2">
      {preview.slice(0, 4).map((item, index) => (
        <article key={`${String(item.id ?? item.name ?? index)}`} className="rounded-lg border border-[#f0dfc8] bg-[#fffdf9] p-2.5">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 rounded-md bg-[#f5e5d1] p-1.5 text-[#b36a2d]">{category === 'hotel' ? <Hotel size={13} aria-hidden /> : <Utensils size={13} aria-hidden />}</span>
            <div className="min-w-0">
              <h3 className="truncate text-xs font-semibold text-[#183037]">{String(item.name ?? '未命名商户')}</h3>
              <p className="mt-0.5 truncate text-[10px] text-[#7a6b5d]">{String(item.category ?? item.address ?? '地址待核实')}</p>
            </div>
          </div>
          <p className="mt-2 truncate text-[10px] text-[#6c5948]">{String(item.address ?? '地址待核实')}</p>
          <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 text-[10px] text-[#8c7258]">
            {item.rating != null && <span>评分 {String(item.rating)}</span>}
            {item.price != null && <span>{String(item.price)}</span>}
            {item.open_status != null && <span>{String(item.open_status)}</span>}
          </div>
        </article>
      ))}
    </div>
  )
}

export function TrainPreview({ preview }: { preview: Array<Record<string, unknown>> }) {
  /** 席别价格统一成「¥83」形态：数值/数字串补 ¥，其余原样透传，缺失显示 -- */
  const formatPrice = (value: unknown): string => {
    if (value == null || value === '') return '--'
    if (typeof value === 'number' && Number.isFinite(value)) return `¥${value}`
    const raw = String(value).trim()
    if (raw.startsWith('¥') || raw.startsWith('￥')) return raw
    return /^\d+(\.\d+)?$/.test(raw) ? `¥${raw}` : raw
  }
  return (
    <div className="mt-2 overflow-x-auto rounded-lg border border-[#f0dfc8]">
      <table className="min-w-full text-left text-[10px] text-[#6c5948]">
        <thead className="bg-[#fff3e3] text-[#8c7258]"><tr><th className="px-2 py-1.5 font-medium">车次</th><th className="px-2 py-1.5 font-medium">出发</th><th className="px-2 py-1.5 font-medium">到达</th><th className="px-2 py-1.5 font-medium">历时</th><th className="px-2 py-1.5 font-medium">余票</th><th className="px-2 py-1.5 font-medium">票价</th></tr></thead>
        <tbody>
          {preview.slice(0, 6).map((item, index) => {
            const seats = item.seats
            const seatsText = seats && typeof seats === 'object' && !Array.isArray(seats)
              ? Object.entries(seats as Record<string, unknown>).slice(0, 3)
                  .map(([seat, left]) => `${seat} ${String(left)}`).join(' / ')
              : ''
            const prices = item.prices
            const priceText = prices && typeof prices === 'object' && !Array.isArray(prices)
              ? Object.entries(prices as Record<string, unknown>).slice(0, 2)
                  .map(([seat, price]) => `${seat} ${formatPrice(price)}`).join(' / ')
              : ''
            return (
              <tr key={`${String(item.train_no ?? 'unknown')}-${String(item.start_time ?? index)}-${String(item.arrive_time ?? '')}-${index}`} className="border-t border-[#f4e6d3]">
                <td className="px-2 py-1.5 font-medium text-[#183037]">{String(item.train_no ?? '未知')}</td>
                <td className="px-2 py-1.5">{String(item.start_time ?? '--')}</td>
                <td className="px-2 py-1.5">{String(item.arrive_time ?? '--')}</td>
                <td className="px-2 py-1.5">{String(item.duration ?? '--')}</td>
                <td className="px-2 py-1.5">{seatsText || <span className="text-[#b3a48f]">--</span>}</td>
                <td className="px-2 py-1.5">{priceText || <span className="text-[#b3a48f]">--</span>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="px-2 py-1.5 text-[10px] text-[#8c7258]">余票与票价来自 12306 非官方聚合源，可能延迟；票价仅实时查询前 2 个车次，其余显示 --，出行前请以 12306 官方为准。</p>
    </div>
  )
}

/**
 * GuidePreview — 知乎攻略卡（📝景点怎么玩/美食必吃/城市特色，软内容参考）。
 * 与 MerchantPreview（📍结构化商户）并存分工；条目来自知乎官方 MCP 归一化
 * 字段（title/url/summary/author_name/vote_up_count/comment_count），多主题
 * 检索（2026-10-03）时每条带 topic 标记，缺失不补造。title 带原文链接。
 */
export function GuidePreview({ preview }: { preview: Array<Record<string, unknown>> }) {
  return (
    <ul className="mt-2 space-y-2">
      {preview.slice(0, 6).map((item, index) => {
        const title = String(item.title ?? '未命名内容')
        const url = typeof item.url === 'string' && item.url.startsWith('http') ? item.url : ''
        const summary = typeof item.summary === 'string' ? item.summary : ''
        const topic = typeof item.topic === 'string' ? GUIDE_TOPIC_LABELS[item.topic] : ''
        const votes = item.vote_up_count
        const comments = item.comment_count
        return (
          <li key={`${title}-${index}`} className="rounded-lg border border-[#e3e9f5] bg-[#fbfcff] p-2.5">
            {url ? (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="block truncate text-xs font-semibold text-[#2d5bd1] hover:underline"
              >
                {title}
              </a>
            ) : (
              <p className="truncate text-xs font-semibold text-[#183037]">{title}</p>
            )}
            {summary && <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-[#5c7074]">{summary}</p>}
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-[#8c7258]">
              <span className="rounded bg-[#e8f0fe] px-1.5 py-0.5 font-medium text-[#2d5bd1]">知乎</span>
              {topic && <span className="rounded bg-[#e2f0ee] px-1.5 py-0.5 font-medium text-[#087b73]">{topic}</span>}
              {typeof item.author_name === 'string' && item.author_name && <span>{item.author_name}</span>}
              {typeof votes === 'number' && votes > 0 && <span>{votes} 赞同</span>}
              {typeof comments === 'number' && comments > 0 && <span>{comments} 评论</span>}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

/** 按 category 分发 preview 卡（单一分发口径，两类调用方共用）。 */
export function ToolPreviewBody({ preview, category }: { preview: Array<Record<string, unknown>>; category?: string }) {
  if (category === 'train') return <TrainPreview preview={preview} />
  if (category === 'guide') return <GuidePreview preview={preview} />
  return <MerchantPreview preview={preview} category={category} />
}

/** 失败行的固定文案（与「未用假数据补齐」口径一致，两处共用）。 */
export function ToolFailedBody({ error }: { error?: string }) {
  return (
    <p className="break-words text-[11px] leading-relaxed text-red-600">
      {error || '该步骤执行失败，未用假数据补齐；其他步骤的结果仍然有效。'}
    </p>
  )
}

export { AlertCircle as ToolFailureIcon }

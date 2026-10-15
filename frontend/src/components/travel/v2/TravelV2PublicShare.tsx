'use client'

import { useEffect, useRef, useState } from 'react'
import { ApiError } from '@/api/client'
import {
  downloadSharedTravelTripICSv2,
  fetchPublicTravelTripShareV2,
  type PublicTravelTripShareV2,
} from '@/api/travelV2'
import { CalendarDays, Clock3, MapPin, TrainFront } from 'lucide-react'
import { saveTravelICSDownload } from './travelV2Download'

interface TravelV2PublicShareProps {
  shareId: string
}

export default function TravelV2PublicShare({ shareId }: TravelV2PublicShareProps) {
  const tokenRef = useRef<{ shareId: string; token: string } | null>(null)
  const [share, setShare] = useState<PublicTravelTripShareV2 | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [downloadBusy, setDownloadBusy] = useState(false)
  const [downloadError, setDownloadError] = useState('')

  useEffect(() => {
    let cancelled = false
    const tokenFromFragment = new URLSearchParams(window.location.hash.slice(1)).get('token') || ''
    // Strict Mode 开发态会立即重放 effect；fragment 已清理后，同一路由可用内存副本恢复。
    const token = tokenFromFragment || (tokenRef.current?.shareId === shareId ? tokenRef.current.token : '')
    tokenRef.current = token ? { shareId, token } : null
    // 令牌仅从 fragment 读取一次；移除地址栏副本，后续请求只通过指定请求头发送。
    window.history.replaceState(window.history.state, '', window.location.pathname)
    setShare(null)
    setLoading(Boolean(token))
    setError('')
    setDownloadError('')

    if (!token || !isShareId(shareId)) {
      setLoading(false)
      setError('此分享链接无效、已撤销或已过期。请向分享者索取新链接。')
      return () => { cancelled = true }
    }

    void fetchPublicTravelTripShareV2(shareId, token).then((result) => {
      if (!cancelled) {
        if (result.share_id !== shareId || !Number.isSafeInteger(result.revision)) {
          throw new Error('分享内容与链接不匹配。')
        }
        setShare(result)
      }
    }).catch((cause: unknown) => {
      if (!cancelled) {
        setError(cause instanceof ApiError && cause.status === 404
          ? '此分享链接无效、已撤销或已过期。请向分享者索取新链接。'
          : '暂时无法读取这份分享。请检查网络后重试。')
      }
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })

    return () => { cancelled = true }
  }, [shareId])

  async function downloadICS() {
    const token = tokenRef.current?.shareId === shareId ? tokenRef.current.token : ''
    if (!share || !token || downloadBusy) return
    setDownloadBusy(true)
    setDownloadError('')
    try {
      saveTravelICSDownload(await downloadSharedTravelTripICSv2(share.share_id, token))
    } catch (cause) {
      setDownloadError(cause instanceof ApiError && cause.status === 404
        ? '此分享链接已撤销或已过期。'
        : cause instanceof ApiError && cause.status === 422
          ? '这份行程缺少日历所需的有效日期或时刻，暂时无法导出。'
          : '日历文件暂时无法下载，请稍后重试。')
    } finally {
      setDownloadBusy(false)
    }
  }

  return <main className="min-h-screen bg-[#f4f8fd] px-4 py-8 text-[#17283f] sm:px-6 sm:py-12">
    <div className="mx-auto max-w-4xl">
      <header className="mb-6 flex items-start justify-between gap-4 rounded-[22px] border border-[#e4ebf4] bg-white px-5 py-5 shadow-[0_4px_18px_rgba(31,74,130,.04)] sm:px-7 sm:py-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[#2878f5]"><MapPin size={16} aria-hidden /><span className="text-sm font-semibold">旅行行程分享</span></div>
          <h1 className="mt-2 text-xl font-bold leading-7 text-[#233a56] sm:text-2xl">{share?.title || '查看一份旅行计划'}</h1>
          {share && <p className="mt-2 text-sm leading-6 text-[#71839b]">
            {share.brief.destination} · {share.brief.day_count} 天
            {share.brief.start_date ? ` · ${share.brief.start_date} 出发` : ''}
            {' · '}固定版本 v{share.revision}
          </p>}
        </div>
        {share && <span className="shrink-0 rounded-full bg-[#eaf2ff] px-3 py-1.5 text-xs font-semibold text-[#246bd2]">只读分享</span>}
      </header>

      {loading && <p role="status" className="rounded-xl border border-[#dce8f5] bg-white px-4 py-6 text-center text-sm text-[#71839b]">正在读取固定版本…</p>}
      {!loading && error && <section role="alert" className="rounded-xl border border-rose-200 bg-white px-5 py-6 text-sm leading-6 text-rose-800">
        <h2 className="font-semibold">无法打开这份分享</h2><p className="mt-1">{error}</p>
      </section>}

      {share && <>
        <section className="mb-5 flex flex-col gap-3 rounded-xl border border-[#cfe0f5] bg-[#eff6ff] px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <p className="text-sm leading-6 text-[#315f91]">这里展示创建分享时的 v{share.revision} 快照。行程之后的修改不会改变这份内容。</p>
          <button
            type="button"
            disabled={downloadBusy}
            onClick={() => void downloadICS()}
            className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-[#2878f5] px-4 text-sm font-semibold text-white hover:bg-[#1f68dc] disabled:cursor-wait disabled:opacity-60"
          ><CalendarDays size={16} aria-hidden />{downloadBusy ? '正在准备日历…' : '下载此版本 ICS'}</button>
        </section>
        {downloadError && <p role="alert" className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">{downloadError}</p>}

        <section className="rounded-[22px] border border-[#e4ebf4] bg-white px-4 py-5 shadow-[0_4px_18px_rgba(31,74,130,.04)] sm:px-7 sm:py-7">
          <div className="mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-[#e8edf4] pb-4 text-sm text-[#637891]">
            <span className="inline-flex items-center gap-2"><CalendarDays size={15} aria-hidden />{share.brief.start_date || '出发日期待定'}</span>
            <span className="inline-flex items-center gap-2"><Clock3 size={15} aria-hidden />{share.brief.timezone}</span>
          </div>
          <ol className="relative space-y-5 before:absolute before:bottom-5 before:left-[15px] before:top-4 before:w-px before:bg-[#dce6f2]">
            {share.days.map((day, dayIndex) => <li key={`${day.date || 'day'}-${dayIndex}`} className="relative pl-11">
              <span className="absolute left-0 top-0 flex h-8 w-8 items-center justify-center rounded-full border-2 border-[#8db6f8] bg-white text-xs font-bold text-[#2878f5]" aria-hidden>{dayIndex + 1}</span>
              <article className="rounded-xl border border-[#e8edf4] bg-white p-4 sm:p-5">
                <header className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                  <h2 className="font-semibold text-[#2d435e]">{day.title || `第 ${dayIndex + 1} 天`}</h2>
                  <time className="text-xs text-[#71839b]">{day.date || '日期待定'}</time>
                </header>
                {day.items.length > 0 ? <ol className="space-y-3">
                  {day.items.map((item, itemIndex) => <li key={`${item.title}-${itemIndex}`} className="flex gap-3">
                    <span className="w-12 shrink-0 pt-0.5 text-xs font-medium tabular-nums text-[#6380a0]">{item.start_time || '待定'}</span>
                    <div className="min-w-0 border-l border-[#dce6f2] pl-3">
                      <p className="text-sm font-medium text-[#2d435e]">{item.title}</p>
                      {item.place?.address && <p className="mt-1 text-xs leading-5 text-[#71839b]">{item.place.address}</p>}
                      {item.duration_min > 0 && <p className="mt-1 text-xs text-[#8a99ab]">安排 {item.duration_min} 分钟</p>}
                    </div>
                  </li>)}
                </ol> : <p className="rounded-lg bg-[#f6faff] px-3 py-3 text-sm text-[#71839b]">这一天暂未安排活动。</p>}
                {day.legs.length > 0 && <ul className="mt-4 space-y-1 border-t border-[#eef2f7] pt-3">
                  {day.legs.map((leg, legIndex) => <li key={`${leg.from_title}-${leg.to_title}-${legIndex}`} className="text-xs leading-5 text-[#71839b]">
                    {leg.from_title} 至 {leg.to_title} · {travelModeLabel(leg.selected_mode)}
                    {leg.duration_min !== null ? ` · 约 ${leg.duration_min} 分钟` : ''}
                  </li>)}
                </ul>}
              </article>
            </li>)}
          </ol>
        </section>

        {(share.arrangements.lodgings.length > 0 || share.arrangements.intercity_trains.length > 0) && <section className="mt-5 rounded-[22px] border border-[#e4ebf4] bg-white px-4 py-5 sm:px-7">
          <h2 className="mb-4 text-base font-semibold text-[#2d435e]">住宿与城际交通参考</h2>
          {share.arrangements.lodgings.length > 0 && <div className="space-y-3">
            {share.arrangements.lodgings.map((lodging, index) => <article key={`${lodging.name}-${index}`} className="rounded-xl border border-[#e8edf4] p-4">
              <h3 className="text-sm font-semibold text-[#2d435e]">{lodging.name}</h3>
              {lodging.address && <p className="mt-1 text-sm text-[#71839b]">{lodging.address}</p>}
              <p className="mt-2 text-xs text-[#71839b]">入住 {lodging.check_in} · 离店 {lodging.check_out} · 未预订</p>
            </article>)}
          </div>}
          {share.arrangements.intercity_trains.length > 0 && <div className="mt-3 space-y-3">
            {share.arrangements.intercity_trains.map((train, index) => <article key={`${train.train_code}-${train.travel_date}-${index}`} className="rounded-xl border border-[#e8edf4] p-4">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-[#2d435e]"><TrainFront size={15} aria-hidden />{train.train_code}</h3>
              <p className="mt-2 text-sm text-[#536a85]">{train.departure_station} {train.departure_time} → {train.arrival_station} {train.arrival_time}</p>
              <p className="mt-1 text-xs text-[#71839b]">{train.travel_date} · {train.duration_min} 分钟 · 未购票</p>
            </article>)}
          </div>}
        </section>}
        <p className="mt-5 text-center text-xs leading-5 text-[#8a99ab]">分享于 {formatShareExpiry(share.expires_at)} 到期。行程内容由分享者提供，请出行前再次核对。</p>
      </>}
    </div>
  </main>
}

function isShareId(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

function travelModeLabel(mode: string): string {
  return { walk: '步行', drive: '驾车', transit: '公共交通', taxi: '出租车' }[mode] || '交通'
}

function formatShareExpiry(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

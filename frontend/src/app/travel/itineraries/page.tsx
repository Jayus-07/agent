'use client'

import { useEffect, useRef, useState, type MouseEvent, type TouchEvent } from 'react'
import { ArrowRight, CalendarDays, MapPin, Trash2, Users } from 'lucide-react'
import { archiveTravelTripV2, fetchTravelTripsV2, type TravelTripV2 } from '@/api/travelV2'
import { TRAVEL_PHOTOS } from '@/components/travel/v2/travelV2Data'
import { TravelBottomNav, TravelTopBar } from '@/components/travel/v2/TravelV2Frame'

export default function TravelItinerariesPage() {
  const [trips, setTrips] = useState<TravelTripV2[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [openSwipeId, setOpenSwipeId] = useState('')
  const [pendingDelete, setPendingDelete] = useState<TravelTripV2 | null>(null)
  const [deleting, setDeleting] = useState(false)
  const touchRef = useRef<{ id: string; x: number; y: number } | null>(null)
  const swipedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    void fetchTravelTripsV2().then((data) => {
      if (!cancelled) setTrips(data.trips ?? [])
    }).catch(() => {
      if (!cancelled) setError('行程列表暂时无法加载，请检查登录状态后重试。')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  function touchStart(id: string, event: TouchEvent<HTMLAnchorElement>) {
    const touch = event.touches[0]
    if (touch) touchRef.current = { id, x: touch.clientX, y: touch.clientY }
  }

  function touchEnd(id: string, event: TouchEvent<HTMLAnchorElement>) {
    const start = touchRef.current
    const touch = event.changedTouches[0]
    touchRef.current = null
    if (!start || start.id !== id || !touch) return
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    if (Math.abs(dx) < Math.abs(dy) || Math.abs(dx) < 42) return
    setOpenSwipeId(dx < 0 ? id : '')
    swipedRef.current = true
  }

  function openTrip(event: MouseEvent<HTMLAnchorElement>, id: string) {
    if (swipedRef.current) {
      event.preventDefault()
      event.stopPropagation()
      swipedRef.current = false
      return
    }
    if (openSwipeId && openSwipeId !== id) setOpenSwipeId('')
  }

  async function confirmDelete() {
    if (!pendingDelete || deleting) return
    const tripId = pendingDelete.trip_id
    setDeleting(true)
    setError('')
    try {
      await archiveTravelTripV2(tripId)
      setTrips((current) => current.filter((trip) => trip.trip_id !== tripId))
      setOpenSwipeId('')
      setFeedback('行程已从你的列表中删除。')
      setPendingDelete(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '行程删除失败，请稍后重试。')
    } finally {
      setDeleting(false)
    }
  }

  return <main className="min-h-screen bg-[#f4f8fd] pb-24 text-[#17283f] md:pb-10"><TravelTopBar active="trips" /><div className="mx-auto max-w-[1040px] px-4 pb-8 pt-2 md:px-8"><div className="mb-5"><p className="text-[10px] font-semibold text-[#4a82c5]">你的 V2 旅行记录</p><h1 className="mt-1 text-2xl font-bold tracking-tight">我的行程</h1><p className="mt-2 text-xs text-[#8292a6]">这里仅显示新版旅游助手保存的行程。手机端左滑卡片可删除。</p></div>
    {error && !pendingDelete && <p role="alert" className="mb-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900">{error}</p>}{feedback && <p role="status" className="mb-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800">{feedback}</p>}
    {loading ? <p role="status" className="rounded-xl border border-[#e6edf5] bg-white p-8 text-center text-xs text-[#8191a6]">正在读取行程…</p> : !error && trips.length === 0 ? <div className="rounded-[22px] border border-dashed border-[#cfdeef] bg-white/80 p-10 text-center"><MapPin size={24} className="mx-auto text-[#82a7d5]" aria-hidden /><h2 className="mt-3 text-sm font-semibold text-[#465e7a]">还没有已保存的行程</h2><p className="mt-1 text-xs text-[#8a9bb0]">描述旅行需求，或从精选模板复制一份行程。</p><a href="/travel" className="mt-4 inline-flex h-9 items-center gap-2 rounded-xl bg-[#2878f5] px-4 text-xs font-semibold text-white">开始规划 <ArrowRight size={14} aria-hidden /></a></div> : <div className="space-y-3">{trips.map((trip) => <article key={trip.trip_id} className="relative overflow-hidden rounded-[20px] border border-[#e6edf5] bg-rose-50 shadow-[0_4px_16px_rgba(34,74,122,.04)]"><button type="button" aria-label={`删除${trip.title || trip.document.brief.destination}行程`} onClick={() => { setError(''); setPendingDelete(trip) }} className="absolute inset-y-0 right-0 flex w-[78px] flex-col items-center justify-center gap-1 bg-rose-500 text-white md:static md:float-right md:w-auto md:flex-row md:px-4"><Trash2 size={16} aria-hidden /><span className="text-[9px] font-semibold">删除</span></button><a href={`/travel/itineraries/${encodeURIComponent(trip.trip_id)}`} onTouchStart={(event) => touchStart(trip.trip_id, event)} onTouchEnd={(event) => touchEnd(trip.trip_id, event)} onClick={(event) => openTrip(event, trip.trip_id)} className={`relative z-10 flex items-center gap-3 border border-[#e6edf5] bg-white p-3 shadow-[0_4px_16px_rgba(34,74,122,.04)] transition-transform duration-200 sm:gap-4 sm:p-4 md:translate-x-0 ${openSwipeId === trip.trip_id ? '-translate-x-[78px]' : 'translate-x-0'}`}><img src={destinationImage(trip.document.brief.destination)} alt={`${trip.document.brief.destination}旅行`} className="h-[76px] w-[96px] shrink-0 rounded-[14px] object-cover sm:h-[90px] sm:w-[132px]" /><span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-2"><span className="truncate text-sm font-semibold text-[#20344e]">{trip.title || trip.document.brief.destination || '未命名行程'}</span><span className="rounded-full bg-[#f0f6ff] px-2 py-0.5 text-[9px] font-medium text-[#4380c9]">已保存</span></span><span className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-[#8191a6]"><span className="inline-flex items-center gap-1"><CalendarDays size={12} aria-hidden />{new Date(trip.updated_at).toLocaleDateString('zh-CN')}</span><span>·</span><span className="inline-flex items-center gap-1"><Users size={12} aria-hidden />{trip.document.brief.travelers.adults + trip.document.brief.travelers.children} 人</span></span><span className="mt-1.5 block truncate text-[10px] text-[#9aa8b8]">{trip.document.brief.day_count} 天 · 最近更新于 v{trip.revision}</span></span><ArrowRight size={17} className="mr-1 shrink-0 text-[#96a9c0]" aria-hidden /></a></article>)}</div>}
    </div><TravelBottomNav active="trips" />
    {pendingDelete && <div className="fixed inset-0 z-50 flex items-end justify-center bg-[#17283f]/35 p-3 sm:items-center" role="presentation" onClick={() => !deleting && setPendingDelete(null)}><section role="alertdialog" aria-modal="true" aria-labelledby="delete-trip-title" className="w-full max-w-sm rounded-[22px] bg-white p-5 shadow-2xl" onClick={(event) => event.stopPropagation()}><h2 id="delete-trip-title" className="text-sm font-bold text-[#263b55]">删除这条行程？</h2><p className="mt-2 text-xs leading-5 text-[#718198]">“{pendingDelete.title || pendingDelete.document.brief.destination || '未命名行程'}”将从你的行程列表移除。</p>{error && <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[10px] leading-4 text-rose-700">{error}</p>}<div className="mt-5 flex gap-2"><button type="button" disabled={deleting} onClick={() => setPendingDelete(null)} className="h-10 flex-1 rounded-xl border border-[#e2eaf3] text-xs font-medium text-[#647991]">取消</button><button type="button" disabled={deleting} onClick={() => void confirmDelete()} className="h-10 flex-1 rounded-xl bg-rose-500 text-xs font-semibold text-white disabled:opacity-60">{deleting ? '正在删除…' : '删除行程'}</button></div></section></div>}
  </main>
}

function destinationImage(destination: string): string {
  return /厦门|海|青岛|三亚/.test(destination) ? TRAVEL_PHOTOS.coast : /苏州|园林/.test(destination) ? TRAVEL_PHOTOS.garden : /杭州|西湖/.test(destination) ? TRAVEL_PHOTOS.westLake : TRAVEL_PHOTOS.city
}

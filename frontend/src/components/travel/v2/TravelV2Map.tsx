'use client'

/**
 * TravelV2Map — 行程地图（Leaflet + 高德瓦片）。
 *
 * 2026-11-03 用户口径：展示全部天数的地点，同一天的标记按动线顺序用
 * 折线相连，不同天不同颜色（天色清单见 travelV2MapData）——一眼看出
 * 每天的动线与覆盖范围。坐标核验信息（已核验/估算/未知）保留在
 * marker tooltip 里，不再占用标记配色；「查询候选」仍是独立形态。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import 'leaflet/dist/leaflet.css'
import type { TripDayV2 } from '@/api/travelV2'
import {
  getAllTravelMapPoints,
  travelMapDayColor,
  type TemporaryTravelMapLocation,
  type TravelMapDayPoint,
} from './travelV2MapData'

function verificationText(point: TravelMapDayPoint): string {
  const confidence = point.verification === 'verified'
    ? '已核验坐标'
    : point.verification === 'estimated' ? '估算坐标' : '坐标可信度未知'
  const dayLabel = `第 ${point.dayIndex + 1} 天`
  return point.candidate
    ? `${confidence} · 查询候选，尚未加入正式行程`
    : `${dayLabel} · ${confidence}`
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character] ?? character)
}

export default function TravelV2Map({
  days,
  activeDayIndex,
  selectedItemId,
  selectedLocation,
  onSelectItem,
}: {
  days: TripDayV2[]
  activeDayIndex: number
  selectedItemId: string
  selectedLocation: TemporaryTravelMapLocation | null
  onSelectItem: (id: string) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<import('leaflet').Map | null>(null)
  const leafletRef = useRef<typeof import('leaflet') | null>(null)
  const layerRef = useRef<import('leaflet').LayerGroup | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const points = useMemo(
    () => getAllTravelMapPoints(days, activeDayIndex, selectedLocation),
    [days, activeDayIndex, selectedLocation],
  )
  const hasPoints = points.length > 0

  useEffect(() => {
    let cancelled = false
    let map: import('leaflet').Map | null = null

    if (!hasPoints) {
      setReady(false)
      setFailed(false)
      return () => { cancelled = true }
    }

    setFailed(false)
    void import('leaflet').then((L) => {
      if (cancelled || !containerRef.current || !points[0]) return
      map = L.map(containerRef.current, { zoomControl: true, scrollWheelZoom: true })
      map.setView([points[0].lat, points[0].lng], 13)
      L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
        subdomains: '1234', maxZoom: 18, attribution: '© 高德地图',
      }).addTo(map)
      map.attributionControl.setPrefix(false)
      mapRef.current = map
      leafletRef.current = L
      setReady(true)
    }).catch(() => { if (!cancelled) setFailed(true) })

    return () => {
      cancelled = true
      map?.remove()
      if (mapRef.current === map) mapRef.current = null
      leafletRef.current = null
      layerRef.current = null
    }
  }, [hasPoints])

  useEffect(() => {
    const map = mapRef.current
    const L = leafletRef.current
    if (!ready || !map || !L || points.length === 0) return
    layerRef.current?.remove()
    const layer = L.layerGroup()
    const bounds: [number, number][] = []

    // 同一天的动线折线（垫在 marker 下层）：只连已保存地点，跳过候选
    const dayIndexes = [...new Set(points.filter((point) => !point.candidate).map((point) => point.dayIndex))]
    for (const dayIndex of dayIndexes) {
      const dayPoints = points.filter((point) => !point.candidate && point.dayIndex === dayIndex)
      if (dayPoints.length < 2) continue
      L.polyline(
        dayPoints.map((point) => [point.lat, point.lng] as [number, number]),
        { color: travelMapDayColor(dayIndex), weight: 2, opacity: 0.65 },
      ).addTo(layer)
    }

    points.forEach((point) => {
      const dayMarkers = points.filter((item) => item.dayIndex === point.dayIndex && !item.candidate)
      const withinDayIndex = dayMarkers.findIndex((item) => item.id === point.id)
      const active = point.candidate
        ? selectedLocation !== null
        : selectedLocation === null && point.id === selectedItemId
      // 配色 = 天色（用户口径）；候选保持独立灰底形态
      const color = point.candidate ? '#667b94' : travelMapDayColor(point.dayIndex)
      const label = point.candidate ? '候选' : String(withinDayIndex + 1)
      const width = point.candidate ? 48 : 28
      const dimmed = !point.candidate && point.dayIndex !== activeDayIndex
      const shadow = active
        ? '0 0 0 3px rgba(23,79,181,.28),0 2px 8px rgba(15,45,90,.3)'
        : '0 2px 8px rgba(15,45,90,.3)'
      const icon = L.divIcon({
        className: 'travel-v2-marker',
        html: `<span style="display:flex;align-items:center;justify-content:center;width:${width}px;height:28px;border-radius:${point.candidate ? '8px' : '9999px'};background:${color};color:#fff;font:700 11px/1 system-ui;border:2px solid #fff;box-shadow:${shadow};opacity:${dimmed ? 0.82 : 1}">${label}</span>`,
        iconSize: [width, 28],
        iconAnchor: [width / 2, 14],
      })
      const marker = L.marker([point.lat, point.lng], {
        icon,
        title: `${verificationText(point)}：${point.name}`,
        interactive: !point.candidate,
        keyboard: !point.candidate,
      })
      if (!point.candidate) {
        marker
          .bindTooltip(escapeHtml(`${verificationText(point)}：${point.name}`), { direction: 'top', offset: [0, -12] })
          .on('click', () => onSelectItem(point.id))
      }
      marker.addTo(layer)
      bounds.push([point.lat, point.lng])
    })

    layer.addTo(map)
    layerRef.current = layer
    map.fitBounds(bounds, { padding: [32, 32], maxZoom: 15 })
  }, [activeDayIndex, onSelectItem, points, ready, selectedItemId, selectedLocation])

  useEffect(() => {
    const container = containerRef.current
    const map = mapRef.current
    if (!ready || !container || !map || typeof ResizeObserver === 'undefined') return
    // 隐藏时 Leaflet 读到零尺寸；断点恢复后按实际容器重算，避免点位移出地图。
    const observer = new ResizeObserver(() => {
      if (!container.clientWidth || !container.clientHeight || points.length === 0) return
      map.invalidateSize({ pan: false, animate: false })
      map.fitBounds(points.map((point) => [point.lat, point.lng] as [number, number]), {
        padding: [32, 32], maxZoom: 15, animate: false,
      })
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [points, ready])

  if (failed) return <div className="flex h-full min-h-[220px] items-center justify-center rounded-[18px] bg-[#f5f8fc] px-5 text-center text-sm text-[#667b94] md:min-h-[320px]">地图暂时无法加载，请检查地图服务连接。</div>

  const savedPointCount = points.filter((point) => !point.candidate).length
  const emptyMessage = savedPointCount
    ? '已有安排的地点缺少有效坐标，暂时无法定位。'
    : '这一天还没有行程地点。选择其他日期或查看查询候选后，地图会显示对应位置。'

  return <div className="relative isolate h-full min-h-[220px] overflow-hidden rounded-[18px] border border-[#e5edf6] bg-[#f5f8fc] md:min-h-[320px]">
    <div ref={containerRef} className="absolute inset-0" role="img" aria-label="行程地图，各天地点按天配色并连成动线" />
    {!hasPoints && <div role="status" className="absolute inset-0 z-[400] flex items-center justify-center px-5 text-center text-sm leading-6 text-[#667b94]">
      <p>{emptyMessage}</p>
    </div>}
    {hasPoints && <div className="pointer-events-none absolute bottom-3 left-3 z-[400] max-w-[calc(100%-24px)] rounded-lg bg-white/95 px-3 py-2 text-xs leading-5 text-[#4f647e] shadow-sm">
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {days.map((_, index) => <span key={index} className="inline-flex items-center gap-1"><span aria-hidden className="inline-block h-2 w-2 rounded-full" style={{ background: travelMapDayColor(index) }} />第 {index + 1} 天</span>)}
      </span>
      {points.some((point) => point.candidate) && <span className="block font-medium text-[#975716]">“候选”尚未加入正式行程</span>}
    </div>}
  </div>
}

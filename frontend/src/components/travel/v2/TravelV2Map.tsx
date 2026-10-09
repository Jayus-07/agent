'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import 'leaflet/dist/leaflet.css'
import type { TripDayV2, TripItemV2 } from '@/api/travelV2'

type MarkerPoint = { id: string; name: string; lat: number; lng: number }

export default function TravelV2Map({
  day,
  selectedItemId,
  selectedLocation,
  onSelectItem,
}: {
  day: TripDayV2 | null
  selectedItemId: string
  selectedLocation: { name: string; lat: number; lng: number } | null
  onSelectItem: (id: string) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<import('leaflet').Map | null>(null)
  const leafletRef = useRef<typeof import('leaflet') | null>(null)
  const layerRef = useRef<import('leaflet').LayerGroup | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const points = useMemo<MarkerPoint[]>(() => {
    const stops: MarkerPoint[] = (day?.items ?? []).flatMap((item: TripItemV2) => {
      const place = item.place
      if (!place || place.lat === null || place.lng === null) return []
      return [{ id: item.item_id, name: item.title, lat: place.lat, lng: place.lng }]
    })
    if (selectedLocation) stops.push({ id: 'search-result', ...selectedLocation })
    return stops
  }, [day, selectedLocation])

  useEffect(() => {
    let cancelled = false
    let map: import('leaflet').Map | null = null
    void import('leaflet').then((L) => {
      if (cancelled || !containerRef.current) return
      map = L.map(containerRef.current, { zoomControl: true, scrollWheelZoom: true })
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
      mapRef.current = null
      layerRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    const L = leafletRef.current
    if (!ready || !map || !L) return
    layerRef.current?.remove()
    const layer = L.layerGroup()
    const bounds: [number, number][] = []
    points.forEach((point, index) => {
      const active = point.id === selectedItemId || point.id === 'search-result'
      const icon = L.divIcon({
        className: 'travel-v2-marker',
        html: `<span style="display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:9999px;background:${active ? '#174fb5' : '#2878f5'};color:#fff;font:700 12px/1 system-ui;border:2px solid #fff;box-shadow:0 2px 8px rgba(15,45,90,.3)">${index + 1}</span>`,
        iconSize: [28, 28], iconAnchor: [14, 14],
      })
      L.marker([point.lat, point.lng], { icon, title: point.name })
        .bindTooltip(point.name, { direction: 'top', offset: [0, -12] })
        .on('click', () => point.id !== 'search-result' && onSelectItem(point.id))
        .addTo(layer)
      bounds.push([point.lat, point.lng])
    })
    layer.addTo(map)
    layerRef.current = layer
    if (bounds.length) map.fitBounds(bounds, { padding: [32, 32], maxZoom: 15 })
    else map.setView([30.246, 120.15], 11)
  }, [onSelectItem, points, ready, selectedItemId])

  if (failed) return <div className="flex h-full min-h-[220px] items-center justify-center rounded-[18px] bg-[#f5f8fc] px-5 text-center text-xs text-[#8191a6] md:min-h-[320px]">地图暂时无法加载，请检查地图服务连接。</div>
  return <div className="relative h-full min-h-[220px] overflow-hidden rounded-[18px] border border-[#e5edf6] bg-[#f5f8fc] md:min-h-[320px]">
    <div ref={containerRef} className="absolute inset-0" role="img" aria-label="行程地图，点击编号可定位行程地点" />
    {points.length === 0 && <div className="pointer-events-none absolute inset-x-3 top-3 z-[400] rounded-lg bg-white/90 px-3 py-2 text-[10px] text-[#70839b] shadow-sm">选中景点或查询结果后，地图会显示对应位置</div>}
    {points.length > 0 && <div className="pointer-events-none absolute bottom-3 left-3 z-[400] rounded-lg bg-white/90 px-3 py-2 text-[9px] text-[#6c8099] shadow-sm">地点编号与时间轴联动 · 地图位置来自现有地点或实时查询</div>}
  </div>
}

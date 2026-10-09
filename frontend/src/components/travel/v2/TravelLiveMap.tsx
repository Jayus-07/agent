'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import type { ItineraryDay, ItineraryItem } from '@/api/travel'
import { classifyFact } from '@/components/travel/travelRuntime'
import 'leaflet/dist/leaflet.css'

type LeafletMap = import('leaflet').Map
type MapStop = { id: string; title: string; lat: number; lng: number }

function coordinateIsVerified(item: ItineraryItem): boolean {
  const poi = item.poi
  if (!poi || !Number.isFinite(poi.lat) || !Number.isFinite(poi.lng)) return false
  return poi.location_status === 'verified'
    || classifyFact({ source: poi.source, verification_status: poi.verification_status }) === 'verified'
}

export default function TravelLiveMap({ day, selectedId, onSelect }: {
  day: ItineraryDay
  selectedId: string
  onSelect: (id: string) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<LeafletMap | null>(null)
  const leafletRef = useRef<typeof import('leaflet') | null>(null)
  const layerRef = useRef<import('leaflet').LayerGroup | null>(null)
  const markersRef = useRef(new Map<string, import('leaflet').Marker>())
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const stops = useMemo<MapStop[]>(() => day.items.flatMap((item, index) => (
    item.kind === 'visit' && coordinateIsVerified(item) && item.poi
      ? [{ id: `${day.day_index}:${index}:${item.title}`, title: item.title, lat: item.poi.lat, lng: item.poi.lng }]
      : []
  )), [day])

  useEffect(() => {
    let cancelled = false
    let map: LeafletMap | null = null
    void import('leaflet').then((L) => {
      if (cancelled || !containerRef.current) return
      map = L.map(containerRef.current, { zoomControl: true, scrollWheelZoom: true, attributionControl: true })
      L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
        subdomains: '1234', maxZoom: 18, attribution: '© 高德地图',
      }).addTo(map)
      map.attributionControl.setPrefix(false)
      leafletRef.current = L
      mapRef.current = map
      setReady(true)
    }).catch(() => { if (!cancelled) setFailed(true) })
    return () => {
      cancelled = true
      map?.remove()
      mapRef.current = null
      layerRef.current = null
      markersRef.current.clear()
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    const L = leafletRef.current
    if (!ready || !map || !L) return
    layerRef.current?.remove()
    markersRef.current.clear()
    const group = L.layerGroup()
    const bounds: [number, number][] = []
    if (stops.length > 1) {
      L.polyline(stops.map((stop) => [stop.lat, stop.lng] as [number, number]), {
        color: '#2878f5', weight: 3, dashArray: '7 6', opacity: 0.8,
      }).addTo(group)
    }
    stops.forEach((stop, index) => {
      const icon = L.divIcon({
        className: 'travel-live-marker',
        html: `<span style="display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:9999px;background:${stop.id === selectedId ? '#174fb5' : '#2878f5'};color:#fff;font:700 12px/1 system-ui;border:2px solid #fff;box-shadow:0 2px 6px rgba(15,45,90,.3)">${index + 1}</span>`,
        iconSize: [26, 26], iconAnchor: [13, 13],
      })
      const marker = L.marker([stop.lat, stop.lng], { icon, title: stop.title })
        .bindTooltip(stop.title, { direction: 'top', offset: [0, -12] })
        .on('click', () => onSelect(stop.id))
        .addTo(group)
      markersRef.current.set(stop.id, marker)
      bounds.push([stop.lat, stop.lng])
    })
    group.addTo(map)
    layerRef.current = group
    if (bounds.length > 0) map.fitBounds(bounds, { padding: [28, 28], maxZoom: 15 })
  }, [onSelect, ready, selectedId, stops])

  useEffect(() => {
    const map = mapRef.current
    const marker = markersRef.current.get(selectedId)
    const stop = stops.find((item) => item.id === selectedId)
    if (!map || !marker || !stop) return
    map.flyTo([stop.lat, stop.lng], Math.max(map.getZoom(), 14), { duration: 0.35 })
    marker.openTooltip()
  }, [ready, selectedId, stops])

  if (failed || stops.length === 0) {
    return <div className="flex h-[300px] items-center justify-center rounded-[18px] border border-[#e5edf6] bg-[#f7faff] px-5 text-center"><div><p className="text-sm font-semibold text-[#536a86]">{failed ? '地图暂时无法加载' : '本日没有已核实坐标的地点'}</p><p className="mt-1 text-[10px] leading-5 text-[#8998aa]">{failed ? '地图服务连接失败，可稍后重试。' : '模板或未核实地点不会显示地图标记，补充真实坐标后会出现在这里。'}</p></div></div>
  }

  return <figure className="overflow-hidden rounded-[18px] border border-[#e5edf6] bg-white"><figcaption className="border-b border-[#edf1f6] px-3 py-2 text-[10px] font-medium text-[#647991]">第 {day.day_index} 天地图 · 编号与行程地点对应</figcaption><div ref={containerRef} className="h-[300px] w-full bg-[#f4f8fd]" role="img" aria-label="真实行程地点地图" /><p className="border-t border-[#edf1f6] px-3 py-2 text-[9px] text-[#96a3b2]">虚线只表示到访顺序，不代表实际道路导航</p></figure>
}

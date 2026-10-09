'use client'

import { Navigation } from 'lucide-react'
import type { DemoTripStop } from './travelV2State'

const MARKER_POINTS = [
  [114, 342], [224, 235], [345, 296], [465, 190], [558, 275], [420, 390], [245, 410], [620, 130],
]

export default function TravelRouteMap({ stops, selectedStopId, onSelectStop, dayLabel = '第 1 天', compact = false }: {
  stops: DemoTripStop[]
  selectedStopId?: string
  onSelectStop?: (stopId: string) => void
  dayLabel?: string
  compact?: boolean
}) {
  const visible = stops.slice(0, MARKER_POINTS.length)
  const route = visible.map((_, index) => MARKER_POINTS[index].join(',')).join(' ')

  return (
    <div className={`relative overflow-hidden rounded-[20px] border border-[#dce8e3] bg-[#eaf2ed] ${compact ? 'h-[190px] sm:h-[220px]' : 'h-[300px] xl:h-[350px]'}`}>
      <svg viewBox="0 0 700 500" preserveAspectRatio="none" role="img" aria-label="杭州行程示意地图，路线连线不是实际道路导航" className="h-full w-full">
        <rect width="700" height="500" fill="#eaf2ed" />
        <path d="M0 70 C88 106 70 177 20 226 C-2 258 24 333 0 373 L0 500 145 500 C136 430 190 392 174 328 C155 255 213 220 178 157 C160 124 191 81 156 0 L0 0Z" fill="#dcebdc" />
        <path d="M466 0 C420 74 481 116 453 170 C430 218 479 253 449 310 C421 368 463 415 432 500 L700 500 700 0Z" fill="#dcebdc" />
        <path d="M0 289 C95 249 118 317 189 299 C260 280 294 223 360 237 C438 254 486 310 557 278 C614 252 634 222 700 227" fill="none" stroke="#c3e5ec" strokeWidth="36" />
        <path d="M0 289 C95 249 118 317 189 299 C260 280 294 223 360 237 C438 254 486 310 557 278 C614 252 634 222 700 227" fill="none" stroke="#d5f0f3" strokeWidth="25" />
        <g fill="none" stroke="#ffffff" strokeWidth="6" opacity=".85">
          <path d="M-30 104 C135 133 230 34 405 75 S621 139 733 72" />
          <path d="M-30 417 C135 365 220 472 359 408 S586 363 733 443" />
          <path d="M90 -20 C119 122 69 227 102 346 S132 450 95 525" />
          <path d="M306 -20 C270 106 354 168 316 285 S269 410 310 524" />
          <path d="M586 -20 C551 115 626 214 587 330 S565 447 621 520" />
        </g>
        <g fill="none" stroke="#d1d9d3" strokeWidth="2" opacity=".8">
          <path d="M-20 180 C96 155 150 185 253 149 S464 116 731 168" />
          <path d="M-20 355 C108 313 212 351 300 323 S509 293 731 334" />
          <path d="M211 -20 C198 93 235 157 211 248 S201 429 226 520" />
          <path d="M516 -20 C493 107 542 182 512 260 S504 420 534 520" />
        </g>
        <g fill="#c8ddc9" opacity=".95">
          <path d="M263 57 320 41 346 68 332 102 278 111 248 84Z" />
          <path d="M78 49 128 30 160 51 149 93 94 101 65 79Z" />
          <path d="M516 387 584 362 622 393 607 438 541 446 506 418Z" />
          <path d="M365 342 414 321 447 343 438 378 391 390 356 367Z" />
        </g>
        <text x="46" y="263" fill="#829d8a" fontSize="13">西湖风景区</text>
        <text x="464" y="112" fill="#829d8a" fontSize="12">灵隐山林</text>
        <text x="487" y="330" fill="#829d8a" fontSize="12">城市街区</text>
        {visible.length > 1 && <polyline points={route} fill="none" stroke="#2878f5" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" opacity=".92" />}
        {visible.map((stop, index) => {
          const [x, y] = MARKER_POINTS[index]
          const selected = stop.id === selectedStopId
          return (
            <g key={stop.id} role="button" tabIndex={0} aria-label={`查看第 ${index + 1} 站：${stop.title}`} onClick={() => onSelectStop?.(stop.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onSelectStop?.(stop.id) }} className="cursor-pointer">
              {selected && <circle cx={x} cy={y} r="23" fill="#2878f5" opacity=".15" />}
              <circle cx={x} cy={y} r="17" fill={selected ? '#155bc3' : '#2878f5'} stroke="white" strokeWidth="4" />
              <text x={x} y={y + 5} textAnchor="middle" fill="white" fontSize="13" fontWeight="700">{index + 1}</text>
              {!compact && index < 5 && <text x={x + 24} y={y + 4} fill="#344d67" fontSize="11" fontWeight="600">{stop.title.slice(0, 7)}</text>}
            </g>
          )
        })}
      </svg>
      <div className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full border border-white/70 bg-white/90 px-2.5 py-1.5 text-[9px] font-medium text-[#4f7091] shadow-sm"><Navigation size={12} className="text-[#2878f5]" aria-hidden />杭州 · {dayLabel}</div>
      <div className="absolute bottom-3 right-3 rounded-lg border border-white/60 bg-white/85 px-2 py-1 text-[8px] text-[#71849a] shadow-sm">示意连线 · 非实际道路导航</div>
    </div>
  )
}

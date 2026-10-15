import type { TravelICSDownloadV2 } from '@/api/travelV2'

export function saveTravelICSDownload(download: TravelICSDownloadV2): void {
  const objectUrl = URL.createObjectURL(download.blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = download.filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000)
}

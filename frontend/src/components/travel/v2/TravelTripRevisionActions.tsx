'use client'

import { useEffect, useState } from 'react'
import { ApiError } from '@/api/client'
import {
  createTravelTripShareV2,
  createTravelV2IdempotencyKey,
  downloadTravelTripRevisionICSv2,
  revokeTravelTripShareV2,
} from '@/api/travelV2'
import { CalendarDays, Check, Copy, Link2, Share2, X } from 'lucide-react'
import { saveTravelICSDownload } from './travelV2Download'

interface StoredShareIntent {
  key: string
  expires_in_days: number
  share_id?: string
  expires_at?: string
}

interface TravelTripRevisionActionsProps {
  tripId: string
  revision: number
  writeBlockedMessage?: string
}

const SHARE_DURATIONS = [7, 30, 90, 365]
const SESSION_PREFIX = 'travel-v2-share-intent'

export default function TravelTripRevisionActions({
  tripId,
  revision,
  writeBlockedMessage = '',
}: TravelTripRevisionActionsProps) {
  const storageKey = `${SESSION_PREFIX}:${tripId}:${revision}`
  const [shareIntent, setShareIntent] = useState<StoredShareIntent | null>(null)
  const [shareUrl, setShareUrl] = useState('')
  const [expiresInDays, setExpiresInDays] = useState(30)
  const [shareBusy, setShareBusy] = useState(false)
  const [copyMessage, setCopyMessage] = useState('')
  const [shareError, setShareError] = useState('')
  const [confirmingRevoke, setConfirmingRevoke] = useState(false)
  const [revokeBusy, setRevokeBusy] = useState(false)
  const [revokeError, setRevokeError] = useState('')
  const [downloadBusy, setDownloadBusy] = useState(false)
  const [downloadError, setDownloadError] = useState('')

  useEffect(() => {
    try {
      const raw = window.sessionStorage.getItem(storageKey)
      if (!raw) return
      const parsed = JSON.parse(raw) as Partial<StoredShareIntent>
      if (
        typeof parsed.key !== 'string'
        || !parsed.key
        || !SHARE_DURATIONS.includes(parsed.expires_in_days ?? 0)
      ) return
      const restored: StoredShareIntent = {
        key: parsed.key,
        expires_in_days: parsed.expires_in_days!,
        ...(typeof parsed.share_id === 'string' ? { share_id: parsed.share_id } : {}),
        ...(typeof parsed.expires_at === 'string' ? { expires_at: parsed.expires_at } : {}),
      }
      setShareIntent(restored)
      setExpiresInDays(restored.expires_in_days)
    } catch {
      // 存储不可用时仍可在当前面板内安全重试，不读取或保存分享令牌。
    }
  }, [storageKey])

  function persistIntent(value: StoredShareIntent | null) {
    setShareIntent(value)
    try {
      if (value) window.sessionStorage.setItem(storageKey, JSON.stringify(value))
      else window.sessionStorage.removeItem(storageKey)
    } catch {
      // 幂等键只在本次挂载期间保留；不影响服务端身份与权限校验。
    }
  }

  async function createOrRecoverShare() {
    if (shareBusy || writeBlockedMessage) return
    const unexpired = shareIntent?.expires_at
      ? Date.parse(shareIntent.expires_at) > Date.now()
      : true
    const sameIntent = shareIntent
      && shareIntent.expires_in_days === expiresInDays
      && unexpired
    const intent = sameIntent
      ? shareIntent
      : { key: createTravelV2IdempotencyKey(), expires_in_days: expiresInDays }
    persistIntent(intent)
    setShareBusy(true)
    setShareError('')
    setCopyMessage('')
    try {
      const created = await createTravelTripShareV2(
        tripId,
        { revision, expires_in_days: intent.expires_in_days },
        intent.key,
      )
      const expectedPath = `/travel/share/${created.share_id}`
      const parsedUrl = new URL(created.share_url, window.location.origin)
      if (
        created.trip_id !== tripId
        || created.revision !== revision
        || parsedUrl.origin !== window.location.origin
        || parsedUrl.pathname !== expectedPath
        || parsedUrl.search
        || !parsedUrl.hash.startsWith('#token=')
      ) {
        throw new Error('分享服务返回了不匹配的固定版本链接。')
      }
      const completed: StoredShareIntent = {
        ...intent,
        share_id: created.share_id,
        expires_at: created.expires_at,
      }
      persistIntent(completed)
      setShareUrl(created.share_url)
      setCopyMessage('链接已创建。令牌只保存在当前页面内。')
    } catch (cause) {
      setShareError(shareFailureMessage(cause))
      setCopyMessage('创建结果尚未确认。本页面已保留请求标识，安全重试会复用它。')
    } finally {
      setShareBusy(false)
    }
  }

  async function copyShareLink() {
    if (!shareUrl) return
    const fullUrl = new URL(shareUrl, window.location.origin).toString()
    try {
      await navigator.clipboard.writeText(fullUrl)
      setCopyMessage('分享链接已复制。')
    } catch {
      const input = document.getElementById(`travel-share-link-${tripId}-${revision}`) as HTMLInputElement | null
      input?.focus()
      input?.select()
      setCopyMessage('链接已选中，请复制并妥善保存。')
    }
  }

  async function revokeShare() {
    if (!shareIntent?.share_id || revokeBusy) return
    setRevokeBusy(true)
    setRevokeError('')
    try {
      await revokeTravelTripShareV2(tripId, shareIntent.share_id)
      persistIntent(null)
      setShareUrl('')
      setConfirmingRevoke(false)
      setCopyMessage('分享已撤销，原链接立即失效。')
    } catch (cause) {
      setRevokeError(revokeFailureMessage(cause))
    } finally {
      setRevokeBusy(false)
    }
  }

  async function downloadICS() {
    if (downloadBusy) return
    setDownloadBusy(true)
    setDownloadError('')
    try {
      saveTravelICSDownload(await downloadTravelTripRevisionICSv2(tripId, revision))
    } catch (cause) {
      setDownloadError(icsFailureMessage(cause))
    } finally {
      setDownloadBusy(false)
    }
  }

  const shareExpired = Boolean(
    shareIntent?.expires_at && Date.parse(shareIntent.expires_at) <= Date.now(),
  )
  const canRecover = Boolean(shareIntent?.share_id && !shareExpired)

  return <section className="mt-3 border-t border-[#e8edf4] pt-3" aria-label={`版本 v${revision} 分享与导出`}>
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={downloadBusy}
        onClick={() => void downloadICS()}
        className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-[#d5e2f1] bg-white px-3 text-sm font-medium text-[#496783] hover:border-[#8db6f8] hover:text-[#246bd2] disabled:cursor-wait disabled:opacity-60"
      ><CalendarDays size={15} aria-hidden />{downloadBusy ? '正在准备日历…' : '下载此版本 ICS'}</button>
      {canRecover && !shareUrl && <button
        type="button"
        disabled={shareBusy}
        onClick={() => void createOrRecoverShare()}
        className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-[#2878f5] px-3 text-sm font-semibold text-white hover:bg-[#1f68dc] disabled:cursor-wait disabled:opacity-60"
      ><Link2 size={15} aria-hidden />{shareBusy ? '正在恢复链接…' : '恢复分享链接'}</button>}
      {!canRecover && <>
        <label className="sr-only" htmlFor={`travel-share-duration-${tripId}-${revision}`}>分享链接有效期</label>
        <select
          id={`travel-share-duration-${tripId}-${revision}`}
          value={expiresInDays}
          onChange={(event) => setExpiresInDays(Number(event.target.value))}
          className="min-h-10 rounded-lg border border-[#d5e2f1] bg-white px-2.5 text-sm text-[#496783] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#2878f5]"
        >{SHARE_DURATIONS.map((days) => <option key={days} value={days}>{days} 天有效</option>)}</select>
        <button
          type="button"
          disabled={shareBusy || Boolean(writeBlockedMessage)}
          onClick={() => void createOrRecoverShare()}
          title={writeBlockedMessage || undefined}
          className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-[#2878f5] px-3 text-sm font-semibold text-white hover:bg-[#1f68dc] disabled:cursor-not-allowed disabled:opacity-50"
        ><Share2 size={15} aria-hidden />{shareBusy ? '正在创建…' : shareIntent?.share_id ? '创建新链接' : shareIntent ? '安全重试' : '创建分享链接'}</button>
      </>}
    </div>

    {shareIntent?.expires_at && <p className={`mt-2 text-xs ${shareExpired ? 'text-amber-800' : 'text-[#71839b]'}`}>
      {shareExpired ? '此版本的分享链接已过期。' : `此版本的分享链接将在 ${formatShareExpiry(shareIntent.expires_at)} 到期。`}
    </p>}
    {shareUrl && <div className="mt-3 rounded-xl border border-[#dce8f5] bg-[#f6faff] p-3">
      <label htmlFor={`travel-share-link-${tripId}-${revision}`} className="text-xs font-semibold text-[#405b79]">固定版本 v{revision} 分享链接</label>
      <input
        id={`travel-share-link-${tripId}-${revision}`}
        type="url"
        readOnly
        autoComplete="off"
        spellCheck={false}
        value={new URL(shareUrl, typeof window === 'undefined' ? 'http://localhost' : window.location.origin).toString()}
        className="mt-1.5 min-h-10 w-full rounded-lg border border-[#d5e2f1] bg-white px-2.5 text-xs text-[#405b79]"
      />
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" onClick={() => void copyShareLink()} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-[#d5e2f1] bg-white px-3 text-sm font-medium text-[#496783] hover:border-[#8db6f8]"><Copy size={14} aria-hidden />复制链接</button>
        {!confirmingRevoke && <button type="button" disabled={revokeBusy} onClick={() => setConfirmingRevoke(true)} className="inline-flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50"><X size={14} aria-hidden />撤销链接</button>}
      </div>
      {confirmingRevoke && <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3" role="group" aria-label="确认撤销分享链接">
        <p className="text-sm leading-5 text-amber-950">撤销后，持有此链接的人将无法继续查看 v{revision}。</p>
        <div className="mt-2 flex gap-2">
          <button type="button" disabled={revokeBusy} onClick={() => void revokeShare()} className="min-h-10 rounded-lg bg-rose-700 px-3 text-sm font-semibold text-white disabled:opacity-60">{revokeBusy ? '正在撤销…' : '确认撤销'}</button>
          <button type="button" disabled={revokeBusy} onClick={() => setConfirmingRevoke(false)} className="min-h-10 rounded-lg px-3 text-sm font-medium text-[#637891] hover:bg-white disabled:opacity-60">保留链接</button>
        </div>
      </div>}
    </div>}
    {shareIntent?.share_id && !shareUrl && <div className="mt-3 rounded-xl border border-[#e8edf4] bg-[#f8fafc] p-3">
      <p className="text-xs leading-5 text-[#637891]">此版本已有分享记录；恢复链接或撤销都只作用于 v{revision}。</p>
      {!confirmingRevoke && <button type="button" disabled={revokeBusy} onClick={() => setConfirmingRevoke(true)} className="mt-2 inline-flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50"><X size={14} aria-hidden />撤销此分享</button>}
      {confirmingRevoke && <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3" role="group" aria-label="确认撤销分享链接">
        <p className="text-sm leading-5 text-amber-950">撤销后，持有此链接的人将无法继续查看 v{revision}。</p>
        <div className="mt-2 flex gap-2">
          <button type="button" disabled={revokeBusy} onClick={() => void revokeShare()} className="min-h-10 rounded-lg bg-rose-700 px-3 text-sm font-semibold text-white disabled:opacity-60">{revokeBusy ? '正在撤销…' : '确认撤销'}</button>
          <button type="button" disabled={revokeBusy} onClick={() => setConfirmingRevoke(false)} className="min-h-10 rounded-lg px-3 text-sm font-medium text-[#637891] hover:bg-white disabled:opacity-60">保留链接</button>
        </div>
      </div>}
    </div>}
    {copyMessage && <p className="mt-2 flex items-center gap-1.5 text-xs text-[#315f91]" role="status"><Check size={13} aria-hidden />{copyMessage}</p>}
    {(shareError || revokeError || downloadError) && <div className="mt-2 space-y-1" role="alert">
      {shareError && <p className="text-xs leading-5 text-rose-700">{shareError}</p>}
      {revokeError && <p className="text-xs leading-5 text-rose-700">{revokeError}</p>}
      {downloadError && <p className="text-xs leading-5 text-rose-700">{downloadError}</p>}
    </div>}
  </section>
}

function shareFailureMessage(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 409) return '这次创建请求与服务器记录不一致。请检查有效期后再开始新的创建。'
  if (cause instanceof ApiError && cause.status === 404) return '找不到所选行程版本，请刷新版本历史后重试。'
  if (cause instanceof ApiError && cause.status === 422) return '服务器无法接受这次分享设置，请检查后重试。'
  if (cause instanceof ApiError && cause.status >= 500) return '分享服务暂不可用。本页面已保留请求标识，可稍后安全重试。'
  return '分享未能确认。本页面已保留请求标识，可检查网络后安全重试。'
}

function revokeFailureMessage(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 404) return '找不到此分享，或它已被撤销。'
  if (cause instanceof ApiError && cause.status === 403) return '只有创建者可以撤销此分享。'
  if (cause instanceof ApiError && cause.status >= 500) return '撤销服务暂不可用。链接状态尚未确认，请稍后重试。'
  return '撤销未能确认。链接状态尚未确认，请稍后重试。'
}

function icsFailureMessage(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 422) return '此版本缺少日历所需的有效日期或时刻，暂时无法导出 ICS。'
  if (cause instanceof ApiError && cause.status === 404) return '找不到此正式版本。请刷新版本历史后重试。'
  if (cause instanceof ApiError && cause.status >= 500) return '导出服务暂不可用，请稍后重试。'
  return '日历文件暂时无法下载，请检查网络后重试。'
}

function formatShareExpiry(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

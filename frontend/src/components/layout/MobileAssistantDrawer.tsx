'use client'

import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import Link from 'next/link'
import { createPortal } from 'react-dom'
import {
  Bot,
  Brain,
  Check,
  Compass,
  History,
  House,
  Leaf,
  Mountain,
  Pencil,
  Sparkles,
  X,
  type LucideIcon,
} from 'lucide-react'
import { getCachedUser } from '@/lib/auth'

const AVATAR_PRESETS = [
  { id: 'initial', label: '账号首字', Icon: null, color: 'bg-accent/10 text-accent' },
  { id: 'sparkles', label: '灵感', Icon: Sparkles, color: 'bg-amber-50 text-amber-600' },
  { id: 'bot', label: '智能', Icon: Bot, color: 'bg-sky-50 text-sky-600' },
  { id: 'leaf', label: '自然', Icon: Leaf, color: 'bg-emerald-50 text-emerald-700' },
  { id: 'compass', label: '旅行', Icon: Compass, color: 'bg-orange-50 text-orange-600' },
  { id: 'mountain', label: '山野', Icon: Mountain, color: 'bg-violet-50 text-violet-600' },
] as const satisfies readonly { id: string; label: string; Icon: LucideIcon | null; color: string }[]

type AvatarPresetId = (typeof AVATAR_PRESETS)[number]['id']

const AVATAR_EVENT = 'assistant-avatar-preset-change'

function isAvatarPresetId(value: string | null): value is AvatarPresetId {
  return AVATAR_PRESETS.some((preset) => preset.id === value)
}

interface MobileAssistantDrawerProps {
  /** 仅由支持会话历史的页面注入；选中历史后关闭侧栏。 */
  historyContent?: (onSelectSession: () => void) => ReactNode
}

/** 三个助手页面共用的手机端个人侧栏。 */
export default function MobileAssistantDrawer({ historyContent }: MobileAssistantDrawerProps) {
  const [open, setOpen] = useState(false)
  const [avatarPickerOpen, setAvatarPickerOpen] = useState(false)
  const [username, setUsername] = useState('已登录用户')
  const [avatarPresetId, setAvatarPresetId] = useState<AvatarPresetId>('initial')
  const [avatarStorageKey, setAvatarStorageKey] = useState('')
  const [portalRoot, setPortalRoot] = useState<HTMLElement | null>(null)
  const drawerId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const avatarPickerOpenRef = useRef(false)
  const avatarInitial = username.slice(0, 1).toUpperCase()
  const avatarPreset = AVATAR_PRESETS.find((preset) => preset.id === avatarPresetId) ?? AVATAR_PRESETS[0]
  const AvatarIcon = avatarPreset.Icon

  const renderAvatar = (className: string, iconSize: number) => (
    <span className={`flex shrink-0 items-center justify-center rounded-full font-semibold ${avatarPreset.color} ${className}`}>
      {AvatarIcon ? <AvatarIcon size={iconSize} aria-hidden /> : avatarInitial}
    </span>
  )

  useEffect(() => {
    const user = getCachedUser()
    const accountName = user?.username || '已登录用户'
    const accountId = String(user?.userId || user?.username || 'anonymous')
    const storageKey = `agent.avatar-preset.${encodeURIComponent(accountId)}`

    setUsername(accountName)
    setAvatarStorageKey(storageKey)
    try {
      const savedPreset = window.localStorage.getItem(storageKey)
      if (isAvatarPresetId(savedPreset)) setAvatarPresetId(savedPreset)
    } catch {
      // 浏览器禁用本地存储时仍允许本次会话切换头像。
    }
    setPortalRoot(document.body)

    const handleStorage = (event: StorageEvent) => {
      if (event.key === storageKey && isAvatarPresetId(event.newValue)) {
        setAvatarPresetId(event.newValue)
      }
    }
    const handleAvatarChange = (event: Event) => {
      const detail = (event as CustomEvent<{ storageKey?: string; presetId?: string }>).detail
      const presetId = detail?.presetId
      if (detail?.storageKey === storageKey && presetId && isAvatarPresetId(presetId)) {
        setAvatarPresetId(presetId)
      }
    }
    window.addEventListener('storage', handleStorage)
    window.addEventListener(AVATAR_EVENT, handleAvatarChange)

    return () => {
      window.removeEventListener('storage', handleStorage)
      window.removeEventListener(AVATAR_EVENT, handleAvatarChange)
    }
  }, [])

  useEffect(() => {
    avatarPickerOpenRef.current = avatarPickerOpen
  }, [avatarPickerOpen])

  useEffect(() => {
    if (!open) return

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (avatarPickerOpenRef.current) {
          setAvatarPickerOpen(false)
          return
        }
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    closeRef.current?.focus()

    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  const close = () => {
    setAvatarPickerOpen(false)
    setOpen(false)
    triggerRef.current?.focus()
  }

  const selectAvatar = (presetId: AvatarPresetId) => {
    setAvatarPresetId(presetId)
    setAvatarPickerOpen(false)
    if (!avatarStorageKey) return

    try {
      window.localStorage.setItem(avatarStorageKey, presetId)
    } catch {
      // 当前页面仍使用刚选中的头像；下次打开时会回到默认头像。
    }
    window.dispatchEvent(new CustomEvent(AVATAR_EVENT, {
      detail: { storageKey: avatarStorageKey, presetId },
    }))
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-label="打开个人侧栏"
        aria-expanded={open}
        aria-controls={drawerId}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent/10
          text-[13px] font-semibold text-accent transition-colors hover:bg-accent/20"
      >
        {renderAvatar('h-full w-full text-[13px]', 16)}
      </button>

      {portalRoot && createPortal(
        <div
          className={`fixed inset-0 z-[80] ${open ? 'pointer-events-auto' : 'pointer-events-none'}`}
          aria-hidden={!open}
        >
          <button
            type="button"
            tabIndex={open ? 0 : -1}
            aria-label="关闭个人侧栏"
            onClick={close}
            className={`absolute inset-0 h-full w-full bg-black/35 transition-opacity duration-300 ${open ? 'opacity-100' : 'opacity-0'}`}
          />
          <aside
            id={drawerId}
            role={open ? 'dialog' : undefined}
            aria-modal={open || undefined}
            aria-label="个人导航"
            aria-hidden={!open}
            className={`absolute inset-y-0 right-0 flex w-[min(86vw,340px)] flex-col bg-white shadow-2xl
              transition-transform duration-300 ease-out ${open ? 'translate-x-0' : 'translate-x-full'}`}
          >
            <div
              className="flex min-h-0 flex-1 flex-col px-5"
              style={{
                paddingTop: 'calc(env(safe-area-inset-top) + 2rem)',
                paddingBottom: 'calc(env(safe-area-inset-bottom) + 1.25rem)',
              }}
            >
              <div className="relative flex flex-col items-center border-b border-black/[0.06] pb-7 pt-4 text-center">
                <button
                  ref={closeRef}
                  type="button"
                  onClick={close}
                  aria-label="关闭个人侧栏"
                  tabIndex={open ? 0 : -1}
                  className="absolute right-0 top-0 flex h-9 w-9 items-center justify-center rounded-full
                    text-text-muted transition-colors hover:bg-black/5 hover:text-text-primary"
                >
                  <X size={18} />
                </button>
                <button
                  type="button"
                  onClick={() => setAvatarPickerOpen(true)}
                  tabIndex={open ? 0 : -1}
                  aria-label="选择预设头像"
                  className="group relative rounded-full outline-none ring-offset-4 transition focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {renderAvatar('h-16 w-16 text-xl', 25)}
                  <span className="absolute bottom-0 right-0 flex h-6 w-6 items-center justify-center rounded-full
                    border-2 border-white bg-text-primary text-white shadow-sm transition group-hover:scale-105">
                    <Pencil size={11} aria-hidden />
                  </span>
                </button>
                <p className="mt-3 max-w-full truncate text-sm font-semibold text-text-primary">{username}</p>
                <p className="mt-1 text-xs text-text-muted">点头像，换个形象</p>
              </div>

              {historyContent && open && (
                <section className="flex min-h-0 flex-1 flex-col border-b border-black/[0.06] py-4">
                  <h2 className="mb-2 flex shrink-0 items-center gap-2 px-2 text-xs font-semibold text-text-secondary">
                    <History size={15} className="text-text-muted" aria-hidden />
                    历史会话
                  </h2>
                  <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-1">
                    {historyContent(close)}
                  </div>
                </section>
              )}

              <nav
                aria-label="个人导航"
                className={`space-y-1 ${historyContent ? 'shrink-0 py-3' : 'flex-1 py-5'}`}
              >
                <Link
                  href="/"
                  onClick={close}
                  tabIndex={open ? 0 : -1}
                  className="flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium text-text-primary
                    transition-colors hover:bg-accent/5 hover:text-accent"
                >
                  <House size={18} className="text-text-muted" aria-hidden />
                  返回门户
                </Link>
                <Link
                  href="/settings#profile-memory"
                  onClick={close}
                  tabIndex={open ? 0 : -1}
                  className="flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium text-text-primary
                    transition-colors hover:bg-accent/5 hover:text-accent"
                >
                  <Brain size={18} className="text-text-muted" aria-hidden />
                  记忆
                </Link>
              </nav>
            </div>
          </aside>
          {avatarPickerOpen && open && (
            <div
              className="absolute inset-0 z-10 flex items-end justify-center bg-black/40 p-4 sm:items-center"
              onClick={() => setAvatarPickerOpen(false)}
            >
              <section
                role="dialog"
                aria-modal="true"
                aria-labelledby={`${drawerId}-avatar-title`}
                onClick={(event) => event.stopPropagation()}
                className="w-full max-w-sm rounded-3xl bg-white p-5 shadow-2xl"
                style={{ marginBottom: 'env(safe-area-inset-bottom)' }}
              >
                <div className="mb-4 flex items-start justify-between gap-4">
                  <div>
                    <h2 id={`${drawerId}-avatar-title`} className="text-base font-semibold text-text-primary">
                      挑一张头像
                    </h2>
                    <p className="mt-1 text-xs text-text-muted">选个顺眼的，继续和助手聊</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setAvatarPickerOpen(false)}
                    aria-label="关闭头像选择"
                    className="flex h-8 w-8 items-center justify-center rounded-full text-text-muted transition hover:bg-black/5"
                  >
                    <X size={17} />
                  </button>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {AVATAR_PRESETS.map((preset) => {
                    const PresetIcon = preset.Icon
                    const selected = avatarPresetId === preset.id
                    return (
                      <button
                        key={preset.id}
                        type="button"
                        onClick={() => selectAvatar(preset.id)}
                        aria-pressed={selected}
                        className={`relative flex min-h-[92px] flex-col items-center justify-center gap-2 rounded-2xl border
                          transition ${selected ? 'border-accent bg-accent/[0.04]' : 'border-black/[0.07] hover:border-black/20'}`}
                      >
                        <span className={`flex h-12 w-12 items-center justify-center rounded-full text-lg font-semibold ${preset.color}`}>
                          {PresetIcon ? <PresetIcon size={21} aria-hidden /> : avatarInitial}
                        </span>
                        <span className="text-xs font-medium text-text-secondary">{preset.label}</span>
                        {selected && (
                          <span className="absolute right-2 top-2 flex h-4 w-4 items-center justify-center rounded-full bg-accent text-white">
                            <Check size={11} aria-hidden />
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
              </section>
            </div>
          )}
        </div>,
        portalRoot,
      )}
    </>
  )
}

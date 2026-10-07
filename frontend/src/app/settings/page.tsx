'use client'

/**
 * /settings — 设置页（2026-10-07 移动端改版新增）
 *
 * 入口：助手页 /agent 顶栏用户头像（两端都显示）。
 * 结构：账号卡 → 用户画像记忆（GET /api/memory/profile，只读）→ 退出登录。
 * 历史会话不在本页：AI 助手在顶栏「历史」按钮、旅游页在顶栏「历史规划」，
 * 各自主页直达（用户拍板：历史会话放各自主页面）。
 *
 * 错误处理对齐项目「不吞错」约定：接口失败展示错误态 + 重试，不降级成空列表。
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowLeft, LogOut, RefreshCw } from 'lucide-react'
import { listProfileMemories, type ProfileMemory } from '@/api/memory'
import { getCachedUser, logout } from '@/lib/auth'
import { useToast } from '@/components/shared/Toast'

/** memory_type → 中文标签 + 配色（长期记忆四类，见 backend/memory/long_term.py） */
const TYPE_LABELS: Record<string, { label: string; cls: string }> = {
  user_fact: { label: '事实', cls: 'bg-blue-50 text-blue-800' },
  preference: { label: '偏好', cls: 'bg-emerald-50 text-emerald-800' },
  decision: { label: '决策', cls: 'bg-violet-50 text-violet-800' },
  knowledge: { label: '知识', cls: 'bg-amber-50 text-amber-800' },
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function SettingsPage() {
  const router = useRouter()
  const toast = useToast()
  const username = getCachedUser()?.username || '本地用户'
  const avatarInitial = username.slice(0, 1).toUpperCase()

  const [records, setRecords] = useState<ProfileMemory[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setRecords(await listProfileMemories())
      setLoadError(null)
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : '加载画像记忆失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const handleLogout = async () => {
    if (!window.confirm('确定退出登录吗？')) return
    try {
      await logout()
    } finally {
      router.replace('/login')
    }
  }

  return (
    <div className="min-h-screen bg-bg-root">
      {/* 顶栏：返回 + 标题 */}
      <header className="sticky top-0 z-10 flex h-14 items-center gap-2 border-b border-black/5 bg-surface-base/90 px-4 backdrop-blur">
        <button
          onClick={() => router.back()}
          className="p-1.5 rounded-lg hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
          aria-label="返回"
          title="返回"
        >
          <ArrowLeft size={18} />
        </button>
        <h1 className="text-[15px] font-semibold text-text-primary">设置</h1>
      </header>

      <div className="mx-auto w-full max-w-2xl px-4 py-4 space-y-4">
        {/* 账号卡 */}
        <section className="flex items-center gap-3 rounded-xl border border-black/5 bg-surface-base p-4">
          <div className="w-12 h-12 rounded-full bg-accent/10 flex items-center justify-center shrink-0">
            <span className="text-lg font-medium text-accent">{avatarInitial}</span>
          </div>
          <div className="min-w-0">
            <p className="text-[15px] font-medium text-text-primary truncate">{username}</p>
            <p className="text-xs text-text-muted mt-0.5">普通用户</p>
          </div>
        </section>

        {/* 用户画像记忆 */}
        <section>
          <div className="flex items-center justify-between px-1 pb-2">
            <h2 className="text-xs text-text-muted">用户画像 · 长期记忆</h2>
            <button
              onClick={() => void refresh()}
              className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
              aria-label="刷新画像记忆"
              title="刷新"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>

          <div className="rounded-xl border border-black/5 bg-surface-base divide-y divide-black/[0.04]">
            {loading && records.length === 0 ? (
              <p className="px-4 py-8 text-center text-xs text-text-muted">加载中...</p>
            ) : loadError ? (
              <div className="px-4 py-8 text-center">
                <p className="text-xs text-red-500 break-words">画像记忆加载失败</p>
                <p className="mt-1 text-[10px] text-text-muted break-words">{loadError}</p>
                <button onClick={() => void refresh()} className="mt-2 text-xs text-accent hover:underline">
                  重试
                </button>
              </div>
            ) : records.length === 0 ? (
              <p className="px-4 py-8 text-center text-xs text-text-muted">
                还没有画像记忆，多聊几句我会慢慢记住你的偏好
              </p>
            ) : (
              records.map((r, idx) => {
                const meta = TYPE_LABELS[r.memory_type] ?? { label: r.memory_type, cls: 'bg-black/5 text-text-secondary' }
                return (
                  <div key={`${r.created_at ?? 'na'}-${idx}`} className="flex items-start gap-2.5 px-4 py-3">
                    <span className={`shrink-0 mt-0.5 inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ${meta.cls}`}>
                      {meta.label}
                    </span>
                    <p className="min-w-0 flex-1 text-[13px] text-text-primary break-words">{r.content}</p>
                    <span className="shrink-0 text-[11px] text-text-muted mt-0.5">{formatDate(r.created_at)}</span>
                  </div>
                )
              })
            )}
          </div>
          <p className="px-1 pt-2 text-[11px] text-text-muted">
            记忆在对话中自动积累，仅自己可见
          </p>
        </section>

        {/* 退出登录 */}
        <button
          onClick={handleLogout}
          className="w-full flex items-center justify-center gap-1.5 rounded-xl border border-red-200 bg-red-50
            px-4 py-3 text-sm font-medium text-red-700 hover:bg-red-100 active:scale-[0.99] transition-all"
        >
          <LogOut size={15} />
          退出登录
        </button>
      </div>
    </div>
  )
}

'use client'

/**
 * ChatHeader — 任务模式顶部标题栏（h-14）
 * 左：任务栏开关 + 当前会话标题；右：新建任务 + 分享 + 退出登录。
 * 模型切换已下移到输入框工具栏（ComposerToolbar），此处不再重复。
 * 全局控制台导航在 /agent 下不渲染（见 app/layout.tsx）。
 */
import { useRouter } from 'next/navigation'
import { Headphones, History, LogOut, PanelLeft, PanelLeftClose, Plus, Share2 } from 'lucide-react'
import { useToast } from '@/components/shared/Toast'
import { getCachedUser, logout } from '@/lib/auth'

interface Props {
  title: string
  sidebarVisible: boolean
  onToggleSidebar: () => void
  onNewTask?: () => void
  /** 当前会话 id，用于生成分享深链 */
  sessionId?: string
  /** 打开智能客服抽屉（WorkBuddy 式右上胶囊入口；不传则不渲染） */
  onOpenCS?: () => void
  /** 打开历史会话浮层（移动端 <md 专用按钮；桌面走左侧任务栏） */
  onOpenHistory?: () => void
}

export default function ChatHeader({
  title, sidebarVisible, onToggleSidebar, onNewTask, sessionId, onOpenCS, onOpenHistory,
}: Props) {
  const toast = useToast()
  const router = useRouter()
  const username = getCachedUser()?.username || ''
  const avatarInitial = username ? username.slice(0, 1).toUpperCase() : 'U'

  const handleShare = async () => {
    const url = sessionId
      ? `${window.location.origin}/agent?session=${encodeURIComponent(sessionId)}`
      : window.location.href
    try {
      await navigator.clipboard.writeText(url)
      toast.success('会话链接已复制到剪贴板')
    } catch {
      toast.warning(`复制失败，请手动复制：${url}`)
    }
  }

  // 退出登录（2026-09-23 D1-8）：吊销后端 refresh 会话（HttpOnly cookie
  // 由服务端 Set-Cookie 失效）+ 清本地 access/userInfo，无论请求成败都跳登录页
  const handleLogout = async () => {
    try {
      await logout()
    } finally {
      router.replace('/login')
    }
  }

  return (
    <header className="shrink-0 h-14 flex items-center gap-2 px-4 bg-surface-base/80">
      {/* 历史会话（移动端 <md）：用户拍板 2026-10-07——手机顶栏左侧=历史，右侧=头像 */}
      {onOpenHistory && (
        <button
          onClick={onOpenHistory}
          className="md:hidden shrink-0 flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs text-text-secondary
            hover:text-text-primary hover:bg-black/5 transition-colors"
          aria-label="历史会话"
          title="历史会话"
        >
          <History size={17} />
        </button>
      )}

      {/* 任务栏开关（桌面；窄视口下任务栏本就隐藏，开关无意义） */}
      <button
        onClick={onToggleSidebar}
        className="hidden md:block p-1.5 rounded-lg hover:bg-black/5 text-text-muted hover:text-text-primary transition-colors"
        aria-label={sidebarVisible ? '收起任务栏' : '展开任务栏'}
        title={sidebarVisible ? '收起任务栏' : '展开任务栏'}
      >
        {sidebarVisible ? <PanelLeftClose size={17} /> : <PanelLeft size={17} />}
      </button>

      <h1 className="flex-1 min-w-0 truncate text-[15px] font-semibold text-text-primary" title={title}>
        {title}
      </h1>

      {/* 智能客服（桌面入口）：移动端 2026-10-07 起挪进底部导航第三 tab，顶栏收掉 */}
      {onOpenCS && (
        <button
          onClick={onOpenCS}
          className="hidden md:flex shrink-0 items-center gap-1.5 px-3 py-1.5 rounded-full border border-border-subtle bg-white
            text-xs text-text-secondary shadow-[0_1px_2px_rgba(0,0,0,0.04)]
            hover:text-text-primary hover:border-black/15 hover:shadow
            focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent/40
            transition-colors"
          aria-label="打开智能客服"
          title="智能客服"
        >
          <Headphones size={13} />
          智能客服
        </button>
      )}

      {onNewTask && (
        <button
          onClick={onNewTask}
          className="hidden md:flex shrink-0 items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-text-secondary
            hover:text-text-primary hover:bg-black/5 transition-colors"
          aria-label="新建任务"
          title="新建任务"
        >
          <Plus size={16} />
        </button>
      )}

      {/* 分享 / 退出登录：桌面保留；移动端收进设置页（用户拍板 2026-10-07） */}
      <button
        onClick={handleShare}
        className="hidden md:flex shrink-0 items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-text-secondary
          hover:text-text-primary hover:bg-black/5 transition-colors"
        aria-label="分享会话链接"
        title="分享会话链接"
      >
        <Share2 size={16} />
      </button>

      <button
        onClick={handleLogout}
        className="hidden md:flex shrink-0 items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-text-secondary
          hover:text-text-primary hover:bg-black/5 transition-colors"
        aria-label="退出登录"
        title="退出登录"
      >
        <LogOut size={16} />
      </button>

      {/* 用户头像（两端，最右）：点击进设置页（画像记忆 / 登出）。
          移动端顶栏唯一右侧元素——登出/分享已收进设置页 */}
      <button
        onClick={() => router.push('/settings')}
        className="ml-auto shrink-0 w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center
          text-[13px] font-medium text-accent hover:bg-accent/20 transition-colors"
        aria-label="打开设置"
        title="设置"
      >
        {avatarInitial}
      </button>
    </header>
  )
}

'use client'

/**
 * ChatHeader — 任务模式顶部标题栏（h-14）
 * 左：任务栏开关 + 当前会话标题；右：新建任务 + 分享。
 * 模型切换已下移到输入框工具栏（ComposerToolbar），此处不再重复。
 * 全局控制台导航在 /agent 下不渲染（见 app/layout.tsx）。
 */
import { PanelLeft, PanelLeftClose, Plus, Share2 } from 'lucide-react'
import { useToast } from '@/components/shared/Toast'

interface Props {
  title: string
  sidebarVisible: boolean
  onToggleSidebar: () => void
  onNewTask?: () => void
  /** 当前会话 id，用于生成分享深链 */
  sessionId?: string
}

export default function ChatHeader({
  title, sidebarVisible, onToggleSidebar, onNewTask, sessionId,
}: Props) {
  const toast = useToast()

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

  return (
    <header className="shrink-0 h-14 flex items-center gap-2 px-4 bg-surface-base/80">
      {/* 任务栏开关（窄视口下任务栏本就隐藏，开关无意义） */}
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

      {onNewTask && (
        <button
          onClick={onNewTask}
          className="shrink-0 flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-text-secondary
            hover:text-text-primary hover:bg-black/5 transition-colors"
          aria-label="新建任务"
          title="新建任务"
        >
          <Plus size={16} />
        </button>
      )}

      <button
        onClick={handleShare}
        className="shrink-0 flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-text-secondary
          hover:text-text-primary hover:bg-black/5 transition-colors"
        aria-label="分享会话链接"
        title="分享会话链接"
      >
        <Share2 size={16} />
      </button>
    </header>
  )
}

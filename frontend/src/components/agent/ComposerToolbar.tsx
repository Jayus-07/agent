'use client'

/**
 * ComposerToolbar — 输入框下方工具栏（两段式输入框的下半段）
 *
 * 2026-10-07 移动端改版精简：移除 预算圆圈 / 当前部门 / 附件占位 / 模型切换，
 * 只保留 发送/停止。理由：部门是授权属性不是操作项、附件未上线、模型与余额
 * 对移动端用户是噪音（用户拍板「模型和部门都不要显示，附件也不要，余额也不要」）。
 *  - 发送/停止：生成中按钮变为停止按钮，点击中止本轮流式（原有行为不变）。
 *  - LLMSwitcher / BudgetRing 组件保留在原位未删：能力还在，后续要恢复只接回来。
 */
import { ArrowUp, Square } from 'lucide-react'

interface Props {
  disabled?: boolean
  canSend: boolean
  onSend: () => void
  /** 生成中时发送按钮变为停止按钮，点击中止本轮流式 */
  onStop?: () => void
  /** 兼容旧签名（ChatView → ChatInput 透传）；精简后不再渲染预算圆圈 */
  budgetStatus?: unknown
}

export default function ComposerToolbar({
  disabled = false, canSend, onSend, onStop,
}: Props) {
  return (
    <div className="flex items-center justify-end pt-2">
      {/* 发送 / 停止：生成中按钮切换为停止，随时可点（不随 canSend 置灰）。
          注意停止态背景用默认调色板，项目的 surface/text 令牌在 bg- 前缀下不可用 */}
      <button
        type="button"
        onClick={disabled ? onStop : onSend}
        disabled={disabled ? !onStop : !canSend}
        aria-label={disabled ? '停止生成' : '发送'}
        title={disabled ? '停止生成' : '发送'}
        className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center
          transition-all duration-200 active:scale-95 shadow-sm
          ${disabled
            ? 'bg-neutral-800 text-white hover:bg-neutral-700'
            : 'bg-accent text-white hover:bg-accent-hover disabled:opacity-20 disabled:cursor-not-allowed'}`}
      >
        {disabled ? <Square size={12} className="fill-current" /> : <ArrowUp size={16} strokeWidth={2.5} />}
      </button>
    </div>
  )
}

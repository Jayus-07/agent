'use client'

/**
 * ComposerToolbar — 输入框下方工具栏（两段式输入框的下半段）
 *
 * 从左到右：部门选择 · 附件 · （弹性空隙）· 模型切换 · 发送/停止。
 *  - 部门选择：决定 RAG 检索授权范围（不是装饰，逻辑与原 ChatInput 一致）
 *  - 附件：P0 占位，点击提示能力开发中
 *  - 模型切换：LLMSwitcher 从 ChatHeader 下移至此
 */
import { ArrowUp, Building2, Paperclip, Square } from 'lucide-react'
import { DEPARTMENTS } from '@/lib/department'
import { useToast } from '@/components/shared/Toast'
import LLMSwitcher from '@/components/agent/LLMSwitcher'

interface Props {
  department: string
  onDepartmentChange: (value: string) => void
  disabled?: boolean
  canSend: boolean
  onSend: () => void
  /** 生成中时发送按钮变为停止按钮，点击中止本轮流式 */
  onStop?: () => void
}

export default function ComposerToolbar({
  department, onDepartmentChange, disabled = false, canSend, onSend, onStop,
}: Props) {
  const toast = useToast()

  return (
    // flex-wrap：320px 级窄屏下放不下单行（部门+附件+模型胶囊+权限+发送 ≈ 380px），
    // 模型切换器整组换行而不是溢出；≥sm 视口仍单行。
    // 左簇（部门+附件）与右簇（模型+权限+发送）用 ml-auto 分开，行内垂直统一居中
    <div className="flex flex-wrap items-center gap-1.5 gap-y-1 pt-2">
      {/* 左簇：部门选择 + 附件 */}
      <div className="flex items-center gap-0.5 shrink-0">
        <div
          className="min-w-0 flex items-center gap-1 rounded-lg hover:bg-black/[0.05]
            transition-colors duration-200 px-2 py-1.5"
          title="选择部门以获得对应知识库的检索范围；未选择按对客最严格范围处理"
        >
          <Building2 size={14} className="text-text-muted" aria-hidden />
          <select
            value={department}
            onChange={(e) => onDepartmentChange(e.target.value)}
            disabled={disabled}
            aria-label="选择部门（检索授权范围）"
            className="bg-transparent outline-none text-xs text-text-secondary cursor-pointer
              disabled:opacity-40 max-w-[92px] appearance-none"
          >
            <option value="">未选择部门</option>
            {DEPARTMENTS.map((d) => (
              <option key={d.id} value={d.id}>{d.label}</option>
            ))}
          </select>
        </div>

        {/* 附件（P0 占位） */}
        <button
          type="button"
          onClick={() => toast.info('附件上传能力开发中，敬请期待')}
          className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-black/[0.05]
            transition-colors"
          aria-label="添加附件"
          title="添加附件"
        >
          <Paperclip size={15} />
        </button>
      </div>

      {/* 右簇：模型切换 + 发送/停止；窄屏下模型胶囊整组换行，由 wrap 兜底 */}
      <div className="flex items-center gap-1 ml-auto basis-full sm:basis-auto">
        <div className="flex-1 sm:flex-none flex justify-end sm:block min-w-0">
          <LLMSwitcher />
        </div>

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
    </div>
  )
}

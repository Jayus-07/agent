'use client'

/**
 * ComposerToolbar — 输入框下方工具栏（两段式输入框的下半段）
 *
 * 从左到右：部门选择 · 附件 · （弹性空隙）· 模型切换 · 权限开关 · 发送。
 *  - 部门选择：决定 RAG 检索授权范围（不是装饰，逻辑与原 ChatInput 一致）
 *  - 附件：P0 占位，点击提示能力开发中
 *  - 模型切换：LLMSwitcher 从 ChatHeader 下移至此
 *  - 权限开关：localStorage 占位（键 chat_permission），P1 接后端
 */
import { useEffect, useState } from 'react'
import { ArrowUp, Building2, Paperclip, ShieldCheck, ShieldOff } from 'lucide-react'
import { DEPARTMENTS } from '@/lib/department'
import { useToast } from '@/components/shared/Toast'
import LLMSwitcher from '@/components/LLMSwitcher'

const PERMISSION_KEY = 'chat_permission'

function readPermission(): boolean {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(PERMISSION_KEY) === '1'
  } catch {
    return false
  }
}

function writePermission(on: boolean): void {
  try {
    if (on) window.localStorage.setItem(PERMISSION_KEY, '1')
    else window.localStorage.removeItem(PERMISSION_KEY)
  } catch {
    // localStorage 不可用（隐私模式等）：静默降级
  }
}

interface Props {
  department: string
  onDepartmentChange: (value: string) => void
  disabled?: boolean
  canSend: boolean
  onSend: () => void
}

export default function ComposerToolbar({
  department, onDepartmentChange, disabled = false, canSend, onSend,
}: Props) {
  const toast = useToast()
  const [permission, setPermission] = useState(false)

  // localStorage 仅客户端可读，挂载后再取，避免 SSR 水合不一致
  useEffect(() => { setPermission(readPermission()) }, [])

  const togglePermission = () => {
    const next = !permission
    setPermission(next)
    writePermission(next)
    toast.info(next ? '已开启严格权限模式（P0 占位，暂不生效）' : '已关闭严格权限模式')
  }

  return (
    // flex-wrap：320px 级窄屏下放不下单行（部门+附件+模型胶囊+权限+发送 ≈ 380px），
    // 模型切换器整组换行而不是溢出；≥sm 视口仍单行
    <div className="flex flex-wrap items-center gap-1.5 gap-y-1 pt-2">
      {/* 部门选择：决定检索授权范围（空 = 按对客最严格集合） */}
      <div
        className="shrink-0 min-w-0 flex items-center gap-1 rounded-lg hover:bg-black/[0.05]
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
        className="shrink-0 p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-black/[0.05]
          transition-colors"
        aria-label="添加附件"
        title="添加附件"
      >
        <Paperclip size={15} />
      </button>

      {/* 模型切换（从 ChatHeader 下移）；窄屏下胶囊占整行宽度，由 wrap 兜底 */}
      <div className="flex-1 basis-full sm:basis-0 sm:flex-none order-last sm:order-none flex justify-end sm:block">
        <LLMSwitcher />
      </div>

      {/* 权限开关（P0 占位） */}
      <button
        type="button"
        onClick={togglePermission}
        aria-pressed={permission}
        aria-label="切换严格权限模式"
        title={permission ? '严格权限模式：开（P0 占位）' : '严格权限模式：关'}
        className={`shrink-0 p-1.5 rounded-lg transition-colors ${
          permission
            ? 'text-accent bg-accent/8 hover:bg-accent/15'
            : 'text-text-muted hover:text-text-primary hover:bg-black/[0.05]'
        }`}
      >
        {permission ? <ShieldCheck size={15} /> : <ShieldOff size={15} />}
      </button>

      {/* 发送 */}
      <button
        type="button"
        onClick={onSend}
        disabled={!canSend}
        className="shrink-0 w-8 h-8 rounded-full bg-accent text-white flex items-center justify-center
          hover:bg-accent-hover disabled:opacity-20 disabled:cursor-not-allowed
          transition-all duration-200 active:scale-95 shadow-sm"
        aria-label="发送"
      >
        <ArrowUp size={16} strokeWidth={2.5} />
      </button>
    </div>
  )
}

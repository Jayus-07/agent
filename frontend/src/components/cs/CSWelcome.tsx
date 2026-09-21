'use client'

import { Headphones } from 'lucide-react'
import { CS_QUICK_PROMPTS } from './constants'

const DEMO_MODE = process.env.NEXT_PUBLIC_CS_DEMO_MODE === 'true'

interface Props {
  onQuickPrompt: (prompt: string) => void
  onRequestHandoff: () => void
  handoffDisabled?: boolean
  handoffPending?: boolean
}

export default function CSWelcome({
  onQuickPrompt,
  onRequestHandoff,
  handoffDisabled = false,
  handoffPending = false,
}: Props) {
  return (
    // 内容整体略上移（pb 大于 pt）：视觉重心对齐，不在抽屉竖向正中「发飘」
    <div className="flex-1 flex items-center justify-center px-5 pt-4 pb-8 overflow-y-auto">
      <div className="text-center w-full max-w-[360px]">
        <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-accent/10 mb-3.5">
          <Headphones size={28} className="text-accent" />
        </div>
        <h2 className="text-base font-semibold text-text-primary mb-1.5">智能客服中心</h2>
        <p className="text-xs text-text-muted leading-relaxed mb-5">
          您好，我是 AI 客服助手，可以帮您查询订单、追踪物流、处理退款等。请问有什么可以帮您？
        </p>
        {DEMO_MODE && (
          <p className="text-[10px] text-amber-600 dark:text-amber-400 mb-4">
            🧪 演示模式：以下对话使用模拟数据（DEMO- 前缀订单），非真实订单
          </p>
        )}
        {/* 两列网格：440px 抽屉下恰好两列排满。
            「转接人工」是唯一会走出常规意图流的动作（进人工链路），
            单独横跨整行并加分隔，避免被读成「第 5 个普通入口」的落单项 */}
        <div className="grid grid-cols-2 gap-2">
          {CS_QUICK_PROMPTS.filter((qp) => qp.label !== '转接人工').map((qp) => (
            <button
              key={qp.label}
              onClick={() => onQuickPrompt(qp.prompt)}
              title={qp.prompt}
              className="flex items-center gap-1.5 px-2.5 py-2 text-xs rounded-lg min-w-0
                border border-border-subtle bg-surface-base
                text-text-secondary hover:text-text-primary hover:border-accent/40
                hover:shadow-sm transition-all"
            >
              <span className="shrink-0">{qp.icon}</span>
              <span className="truncate">{qp.label}</span>
            </button>
          ))}
        </div>

        {/* 转人工：独立横条，与常规快捷入口在语义上分开 */}
        {CS_QUICK_PROMPTS.filter((qp) => qp.label === '转接人工').map((qp) => (
          <button
            key={qp.label}
            onClick={onRequestHandoff}
            disabled={handoffDisabled || handoffPending}
            title={handoffDisabled ? '请先发送一条消息，再请求人工客服' : qp.prompt}
            aria-label="转接人工客服"
            className="mt-2 w-full flex items-center justify-center gap-1.5 px-2.5 py-2 text-xs rounded-lg min-w-0
              border border-border-subtle bg-surface-base
              text-text-secondary hover:text-text-primary hover:border-accent/40
              disabled:cursor-not-allowed disabled:opacity-50
              hover:shadow-sm transition-all"
          >
            <span className="shrink-0">{qp.icon}</span>
            <span className="truncate">
              {handoffPending ? '正在请求人工客服…' : qp.label}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

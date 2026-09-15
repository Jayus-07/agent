'use client'

import { Headphones } from 'lucide-react'
import { CS_QUICK_PROMPTS } from './constants'

interface Props {
  onQuickPrompt: (prompt: string) => void
}

export default function CSWelcome({ onQuickPrompt }: Props) {
  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center max-w-md">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-accent/10 mb-4">
          <Headphones size={32} className="text-accent" />
        </div>
        <h2 className="text-lg font-semibold text-text-primary mb-1">智能客服中心</h2>
        <p className="text-xs text-text-muted mb-6">
          您好，我是 AI 客服助手，可以帮您查询订单、追踪物流、处理退款等。请问有什么可以帮您？
        </p>
        <div className="flex flex-wrap justify-center gap-2">
          {CS_QUICK_PROMPTS.map((qp) => (
            <button
              key={qp.label}
              onClick={() => onQuickPrompt(qp.prompt)}
              className="flex items-center gap-1.5 px-3 py-2 text-xs rounded-lg
                border border-border-subtle bg-surface-base
                text-text-secondary hover:text-text-primary hover:border-accent/40
                hover:shadow-sm transition-all"
            >
              <span>{qp.icon}</span>
              <span>{qp.label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

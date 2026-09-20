'use client'

/**
 * LLMSwitcher — Google 风格 LLM 切换器 + 余额显示
 *
 * 设计参考: Google AI Studio / Google Search
 *   - 单一胶囊状触发器（合并"余额 + 模型"）
 *   - 白色背景 + 1px 浅灰边框 + subtle 阴影
 *   - 黑色文字 (#1f1f1f) + Google Blue (#1a73e8) 强调
 *   - 圆角 20px (胶囊) / 16px (下拉)
 *   - 状态用"勾号 ✓" + 500ms 反馈，不滥用转圈
 *
 * B.9 决策②（2026-09-19）—— 受控化，语义变更：
 *   - 选模型 = **会话级临时覆盖**（只写 store.sessionModel，**不调 switchLLM**）
 *   - 「当前模型」= 会话覆盖 ?? 全局默认（全局默认只读）
 *   - 会话覆盖生效时触发器加「· 本会话」后缀，避免误以为动了全局
 *   - 写全局的入口已收敛到 admin（POST /llm/switch 有 require_admin_user 门禁），
 *     本组件（web 端无角色体系）不再提供
 */

import { useEffect, useState, useCallback, useRef } from 'react'
import { Sparkles, ChevronDown, Check, Loader2, AlertCircle, X, RotateCcw } from 'lucide-react'
import {
  listLLMModels,
  getCurrentLLM,
  getLLMBalance,
  type LLMModel,
  type LLMBalance,
} from '@/lib/api'
import { useChatStore } from '@/store/chat'

export default function LLMSwitcher() {
  const [models, setModels] = useState<LLMModel[]>([])
  /** 全局默认模型（只读）：仅用于展示与「回到全局默认」的目标名 */
  const [globalModel, setGlobalModel] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [justSwitched, setJustSwitched] = useState(false)  // 切换成功的瞬间反馈
  const [error, setError] = useState<string>('')
  const [balance, setBalance] = useState<LLMBalance | null>(null)
  const [balanceLoading, setBalanceLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)

  // 会话级模型覆盖（B.9）：null = 跟随全局默认，与 sessionId 同生命周期
  const sessionModel = useChatStore((s) => s.sessionModel)
  const setSessionModel = useChatStore((s) => s.setSessionModel)

  // 实际生效的模型 = 会话覆盖 ?? 全局默认
  const current = sessionModel || globalModel

  // 加载模型列表 + 全局默认模型
  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [list, cur] = await Promise.all([listLLMModels(), getCurrentLLM()])
      setModels(list.models)
      setGlobalModel(cur.model)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  // 加载余额
  const refreshBalance = useCallback(async (modelName?: string) => {
    setBalanceLoading(true)
    try {
      const target = models.find((m) => m.name === (modelName || current))
      const result = await getLLMBalance(target?.provider)
      setBalance(result)
    } catch (e) {
      setBalance({ ok: false, error: e instanceof Error ? e.message : '查询失败' })
    } finally {
      setBalanceLoading(false)
    }
  }, [models, current])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { if (current) refreshBalance(current) }, [current, refreshBalance])

  // 点击外部关闭下拉
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const flashSwitched = () => {
    setJustSwitched(true)
    setTimeout(() => setJustSwitched(false), 1500)
  }

  /** 选择**本次会话**使用的模型（B.9）：只写会话态，不触碰全局默认。
   *  选中项若等于全局默认值，则等价于「清除覆盖」，存 null 以保持状态干净。 */
  const handleSwitch = (modelName: string) => {
    if (modelName === current) return
    setError('')
    setOpen(false)
    setSessionModel(modelName === globalModel ? null : modelName)
    flashSwitched()
  }

  /** 清除会话覆盖 → 回到全局默认 */
  const clearOverride = () => {
    setSessionModel(null)
    setOpen(false)
    flashSwitched()
  }

  const currentModel = models.find((m) => m.name === current)
  const globalModelDisplay = models.find((m) => m.name === globalModel)?.display || globalModel

  // 余额徽章内容
  const renderBalance = () => {
    if (balanceLoading) {
      return <Loader2 className="w-3.5 h-3.5 animate-spin text-[#5f6368]" />
    }
    if (balance?.ok && balance.provider === 'ollama') {
      return (
        <span className="text-[13px] text-[#1f1f1f] font-medium tracking-tight">
          ∞ 本地
        </span>
      )
    }
    if (balance?.ok && balance.balance) {
      return (
        <span className="text-[13px] text-[#1f1f1f] font-medium tabular-nums tracking-tight">
          ¥ {balance.balance}
        </span>
      )
    }
    if (balance?.error && balance.provider !== 'ollama') {
      return (
        <span className="text-[13px] text-[#d93025] font-medium" title={balance.error}>
          未配置
        </span>
      )
    }
    return <span className="text-[13px] text-[#5f6368]">—</span>
  }

  return (
    <div className="relative" ref={popoverRef}>
      {/* 触发器：胶囊形 + Google 风格 */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={loading}
        className={`
          group flex items-center gap-2.5 h-9 pl-3 pr-3
          bg-white border border-[#dadce0] rounded-full
          text-[13px] text-[#1f1f1f] font-medium
          hover:bg-[#f8f9fa] hover:shadow-[0_1px_2px_rgba(60,64,67,0.1),0_1px_3px_rgba(60,64,67,0.08)]
          active:bg-[#f1f3f4]
          transition-all duration-150
          disabled:opacity-60 disabled:cursor-wait
          ${open ? 'shadow-[0_1px_2px_rgba(60,64,67,0.1),0_1px_3px_rgba(60,64,67,0.08)] border-[#1a73e8]' : ''}
        `}
      >
        <Sparkles className="w-4 h-4 text-[#1a73e8]" strokeWidth={2} />

        {justSwitched ? (
          <Check className="w-3.5 h-3.5 text-[#1e8e3e]" strokeWidth={2.5} />
        ) : (
          <span className="max-w-[110px] sm:max-w-[180px] truncate">
            {currentModel?.display || current || '加载中'}
          </span>
        )}

        {/* 会话级覆盖生效标记：明确"改的是本会话，不是全局" */}
        {sessionModel && !justSwitched && (
          <span className="text-[11px] text-[#1a73e8] font-normal shrink-0">· 本会话</span>
        )}

        {/* 分隔点（subtle 风格） */}
        <span className="w-px h-4 bg-[#dadce0]" />

        {renderBalance()}

        <ChevronDown
          className={`w-3.5 h-3.5 text-[#5f6368] transition-transform duration-200 ${
            open ? 'rotate-180' : ''
          }`}
          strokeWidth={2}
        />
      </button>

      {/* 下拉面板 — 向上弹出，紧凑 */}
      {open && (
        <div
          className="
            absolute right-0 bottom-full mb-2 w-[260px] z-50
            bg-white border border-[#dadce0] rounded-xl
            shadow-[0_-4px_8px_3px_rgba(60,64,67,0.15),0_-1px_3px_rgba(60,64,67,0.08)]
            overflow-hidden
          "
        >
          {/* 标题栏 */}
          <div className="px-3 pt-2.5 pb-1.5 border-b border-[#f1f3f4]">
            <div className="text-[12px] font-medium text-[#1f1f1f]">选择本次会话的模型</div>
            <div className="text-[11px] text-[#5f6368] mt-0.5 leading-snug">
              只影响本会话；全局默认由管理员控制
            </div>
          </div>

          {/* 回到全局默认（仅覆盖生效时出现） */}
          {sessionModel && (
            <div className="p-1 border-b border-[#f1f3f4]">
              <button
                type="button"
                onClick={clearOverride}
                className="
                  w-full px-2 py-1.5 rounded-lg text-left
                  flex items-center gap-2 text-[12px] text-[#1a73e8]
                  hover:bg-[#f8f9fa] active:bg-[#f1f3f4] transition-colors duration-100
                "
              >
                <RotateCcw className="w-3.5 h-3.5 shrink-0" strokeWidth={2} />
                <span className="truncate">回到全局默认（{globalModelDisplay || '—'}）</span>
              </button>
            </div>
          )}

          {/* 模型列表 */}
          <div className="max-h-[240px] overflow-y-auto py-0.5">
            {models.map((m) => {
              const isCurrent = m.name === current
              const isGlobal = m.name === globalModel
              return (
                <button
                  type="button"
                  key={m.name}
                  onClick={(e) => { e.preventDefault(); handleSwitch(m.name) }}
                  className={`
                    w-full px-3 py-2 text-left flex items-start gap-2.5
                    transition-colors duration-100
                    ${isCurrent
                      ? 'bg-[#e8f0fe]'
                      : 'hover:bg-[#f8f9fa] active:bg-[#f1f3f4]'
                    }
                  `}
                >
                  <div className="mt-0.5 shrink-0">
                    <div
                      className={`
                        w-3.5 h-3.5 rounded-full border-2 flex items-center justify-center
                        ${isCurrent
                          ? 'border-[#1a73e8]'
                          : 'border-[#dadce0]'
                        }
                      `}
                    >
                      {isCurrent && (
                        <div className="w-1.5 h-1.5 rounded-full bg-[#1a73e8]" />
                      )}
                    </div>
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium text-[#1f1f1f] flex items-center gap-1.5">
                      <span className="truncate">{m.display}</span>
                      {isGlobal && (
                        <span className="shrink-0 text-[10px] font-normal text-[#5f6368] bg-[#f1f3f4] rounded px-1 py-px">
                          全局
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-[#5f6368] mt-0.5 leading-snug">
                      {m.description}
                    </div>
                  </div>

                  {isCurrent && (
                    <Check className="w-3.5 h-3.5 text-[#1a73e8] shrink-0 mt-0.5" strokeWidth={2.5} />
                  )}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="absolute right-0 top-full mt-1.5 w-[260px] z-50
          bg-red-50 border border-red-200 rounded-lg px-3 py-2
          flex items-start gap-2 text-xs text-red-700 shadow-sm">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span className="flex-1">{error}</span>
          <button type="button" onClick={() => setError('')} className="shrink-0 text-red-400 hover:text-red-600">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  )
}

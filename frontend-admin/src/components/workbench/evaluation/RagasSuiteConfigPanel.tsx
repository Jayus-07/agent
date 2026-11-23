'use client'

import React from 'react'
import { RAG_CORE_METRICS, RAGAS_OPTIONAL_METRICS } from './ragMetrics'

export interface RagasSuiteConfig {
  /** 是否启用核心五维指标（默认 true） */
  enableCore: boolean
  /** 是否启用联合 Ragas 进阶可选评测 */
  enableRagas: boolean
  /** Ragas 评测深度 */
  ragasLevel: 'basic' | 'standard' | 'full'
  /** 额外勾选的可选指标 keys */
  selectedOptionalKeys: string[]
}

export interface RagasSuiteConfigPanelProps {
  value: RagasSuiteConfig
  onChange: (next: RagasSuiteConfig) => void
  disabled?: boolean
}

export const DEFAULT_RAGAS_SUITE_CONFIG: RagasSuiteConfig = {
  enableCore: true,
  enableRagas: false,
  ragasLevel: 'standard',
  selectedOptionalKeys: ['semantic_similarity', 'context_utilization'],
}

export default function RagasSuiteConfigPanel({
  value,
  onChange,
  disabled = false,
}: RagasSuiteConfigPanelProps) {
  const handleToggleRagas = (enabled: boolean) => {
    onChange({
      ...value,
      enableRagas: enabled,
    })
  }

  const handleLevelChange = (level: 'basic' | 'standard' | 'full') => {
    let nextKeys = [...value.selectedOptionalKeys]
    if (level === 'basic') {
      nextKeys = []
    } else if (level === 'standard') {
      nextKeys = ['semantic_similarity', 'context_utilization']
    } else if (level === 'full') {
      nextKeys = RAGAS_OPTIONAL_METRICS.map(m => m.key)
    }
    onChange({
      ...value,
      ragasLevel: level,
      selectedOptionalKeys: nextKeys,
    })
  }

  return (
    <div className="rounded-xl border border-border-subtle bg-surface-base p-4">
      <div className="flex items-center justify-between border-b border-border-subtle pb-3">
        <div>
          <h4 className="text-sm font-semibold text-text-primary">评测指标与评估套件</h4>
          <p className="text-xs text-text-muted">按需组合 RAG 五维黄金指标与 Ragas 进阶官方评测能力</p>
        </div>
        <span className="rounded-md bg-bg-elevated px-2 py-0.5 text-[11px] text-text-secondary">
          双轨评测架构
        </span>
      </div>

      <div className="mt-3.5 space-y-4">
        {/* 基础五维黄金指标 */}
        <div className="rounded-lg border border-border-subtle bg-bg-elevated p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/10 text-xs font-bold text-emerald-600 dark:text-emerald-400">
                ✓
              </span>
              <span className="text-xs font-medium text-text-primary">
                RAG 五维核心黄金量化指标（推荐默认必评）
              </span>
            </div>
            <span className="text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
              默认常驻
            </span>
          </div>
          <p className="mt-1 text-[11px] text-text-muted">
            全面覆盖检索召回（Context Recall）、检索精度（Context Precision）、忠实度无幻觉（Faithfulness）、意图相关度（Answer Relevance）与事实正确性（Answer Correctness）。
          </p>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {RAG_CORE_METRICS.map(m => (
              <span
                key={m.key}
                className="inline-flex items-center rounded-md border border-border-subtle bg-surface-base px-2 py-0.5 text-[10px] text-text-secondary"
              >
                {m.label}
              </span>
            ))}
          </div>
        </div>

        {/* 联合 Ragas 可选评测配置 */}
        <div
          className={`rounded-lg border transition-colors p-3 ${
            value.enableRagas
              ? 'border-indigo-500/30 bg-indigo-50/20 dark:bg-indigo-950/10'
              : 'border-border-subtle bg-bg-elevated'
          }`}
        >
          <div className="flex items-center justify-between">
            <div>
              <span className="text-xs font-medium text-text-primary">
                联合 Ragas 深度评测套件
              </span>
              <span className="ml-1.5 rounded-full bg-indigo-500/10 px-2 py-0.5 text-[10px] font-medium text-indigo-600 dark:text-indigo-400">
                进阶可选
              </span>
            </div>
            <label className="relative inline-flex cursor-pointer items-center">
              <input
                type="checkbox"
                className="peer sr-only"
                checked={value.enableRagas}
                disabled={disabled}
                onChange={e => handleToggleRagas(e.target.checked)}
              />
              <div className="peer h-5 w-9 rounded-full bg-slate-300 after:absolute after:top-[2px] after:left-[2px] after:h-4 after:w-4 after:rounded-full after:bg-white after:transition-all after:content-[''] peer-checked:bg-indigo-600 peer-checked:after:translate-x-full peer-focus:outline-none dark:bg-slate-700" />
            </label>
          </div>

          <p className="mt-1 text-[11px] text-text-muted">
            调用官方 Ragas 框架执行深度上下文实体挖掘、表述精炼度及无害合规性质检。需消耗少量额外推理 Token 与评测时延。
          </p>

          {value.enableRagas && (
            <div className="mt-3.5 space-y-3 border-t border-border-subtle/80 pt-3">
              <div>
                <label className="text-xs font-medium text-text-secondary">评测深度等级</label>
                <div className="mt-1.5 grid grid-cols-3 gap-2">
                  {(['basic', 'standard', 'full'] as const).map(lvl => {
                    const active = value.ragasLevel === lvl
                    const title =
                      lvl === 'basic' ? '基础 (Basic)' : lvl === 'standard' ? '标准 (Standard)' : '全量 (Full)'
                    const hint =
                      lvl === 'basic'
                        ? '快速双检'
                        : lvl === 'standard'
                        ? '推荐基线'
                        : '全维度深度质检'

                    return (
                      <button
                        key={lvl}
                        type="button"
                        disabled={disabled}
                        onClick={() => handleLevelChange(lvl)}
                        className={`rounded-lg border p-2 text-left transition-colors ${
                          active
                            ? 'border-indigo-600 bg-white shadow-xs dark:bg-indigo-900/40'
                            : 'border-border-subtle bg-surface-base hover:bg-bg-hover'
                        }`}
                      >
                        <p className={`text-xs font-semibold ${active ? 'text-indigo-600 dark:text-indigo-400' : 'text-text-primary'}`}>
                          {title}
                        </p>
                        <p className="mt-0.5 text-[10px] text-text-muted">{hint}</p>
                      </button>
                    )
                  })}
                </div>
              </div>

              <div>
                <p className="text-[11px] text-text-muted">包含的进阶指标：</p>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {RAGAS_OPTIONAL_METRICS.map(m => (
                    <span
                      key={m.key}
                      className="inline-flex items-center gap-1 rounded-md border border-indigo-500/20 bg-indigo-50/50 px-2 py-0.5 text-[10px] text-indigo-700 dark:bg-indigo-950/30 dark:text-indigo-300"
                    >
                      <span>⚡</span>
                      <span>{m.label}</span>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

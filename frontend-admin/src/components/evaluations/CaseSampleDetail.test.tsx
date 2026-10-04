import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import CaseSampleDetail from '@/components/evaluations/CaseSampleDetail'
import type { EvalResultDetail } from '@/api/evaluation'

// happy-dom 环境全局
beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

const baseResult = {
  case_id: 'RC-001',
  module: 'rag',
  status: 'pass' as const,
  expected: { expected_answer: '答案' },
  actual: {
    question: 'q',
    generated_answer: '生成的答案内容',
    details: [
      { doc_id: 'doc-a', title: '文档A', rerank_score: 0.91 },
      { doc_id: 'doc-b', title: '文档B', rerank_score: 0.72 },
    ],
    trace: { trace_id: 'tr-123', total_spans: 8 },
    router: { captured: false, reason: 'rag_eval_direct_no_router' },
  },
  metrics: {
    'recall@5': 1.0,
    'sem_faithfulness': 0.88,
    'ragas_faithfulness': 0.92,
    'ragas_context_recall': 0.85,
    'judge_total': 0.86,
  },
  duration_ms: 1200,
  error_msg: null,
  error_stage: null,
} as unknown as EvalResultDetail

let container: HTMLDivElement
let root: Root

const renderView = (ui: React.ReactNode) => {
  act(() => {
    root.render(ui)
  })
}

describe('CaseSampleDetail（C8 样本级证据链）', () => {
  afterEach(() => {
    act(() => {
      root.unmount()
    })
  })

  it('渲染检索命中表（doc_id + rerank 分）+ RAGAS/judge/自研 三区分离', () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    renderView(<CaseSampleDetail result={baseResult} />)
    const text = container.textContent || ''
    // C8-1：检索命中表
    expect(text).toContain('检索命中（2）')
    expect(text).toContain('doc-a')
    expect(text).toContain('0.9100')
    // C8-3/RAGAS-12：RAGAS 独立区块，不与 sem_* 混排
    expect(text).toContain('RAGAS 指标（独立口径）')
    expect(text).toContain('faithfulness: 0.9200')
    expect(text).toContain('自研语义指标')
    expect(text).toContain('sem_faithfulness: 0.8800')
    // C8-3/EVD-07：judge 明细独立区块（0.86*5=4.30/5）
    expect(text).toContain('Judge 评分（LLM-as-Judge 四维）')
    expect(text).toContain('total: 4.30/5')
    // C8-2/TRACE-09：trace 链接
    const link = container.querySelector('a[href="/observability/traces/tr-123"]')
    expect(link).not.toBeNull()
    // C8-5/EVD-02：router 诚实口径
    expect(text).toContain('未捕获（rag_eval_direct_no_router')
  })

  it('error_stage 渲染徽标；原始 JSON 折叠、点击展开（UI-05）', () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    const failed = { ...baseResult, error_stage: 'ragas' } as unknown as EvalResultDetail
    renderView(<CaseSampleDetail result={failed} />)
    let text = container.textContent || ''
    expect(text).toContain('失败阶段: ragas')
    // 默认折叠：看不到 Expected 原文区
    expect(text).not.toContain('"expected_answer"')
    const toggle = Array.from(container.querySelectorAll('button')).find(
      b => (b.textContent || '').includes('查看原文'),
    )
    expect(toggle).toBeDefined()
    act(() => {
      toggle!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    text = container.textContent || ''
    expect(text).toContain('"expected_answer"')
  })
})

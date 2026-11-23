import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeAll, describe, expect, it } from 'vitest'
import MetricRadarCard from './MetricRadarCard'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

describe('MetricRadarCard', () => {
  it('正确渲染五维指标雷达图与各项得分', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)

    const scores = {
      context_recall: 0.92,
      context_precision: 0.85,
      faithfulness: 0.95,
      answer_relevance: 0.88,
      answer_correctness: 0.81,
    }

    act(() => {
      root.render(<MetricRadarCard scores={scores} />)
    })

    expect(container.textContent).toContain('RAG 五维核心质量雷达')
    expect(container.textContent).toContain('上下文召回率')
    expect(container.textContent).toContain('忠实度 / 无幻觉率')
    expect(container.textContent).toContain('92.0%')
    expect(container.textContent).toContain('95.0%')
    expect(container.querySelector('svg')).not.toBeNull()

    act(() => root.unmount())
    container.remove()
  })

  it('在包含 Ragas 进阶指标时动态渲染可选指标区域', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)

    const scores = {
      context_recall: 0.9,
      ragas_context_entity_recall: 0.78,
      ragas_aspect_critic_conciseness: 0.84,
    }

    act(() => {
      root.render(<MetricRadarCard scores={scores} showRagasOptional={true} />)
    })

    expect(container.textContent).toContain('Ragas 联合测评已激活')
    expect(container.textContent).toContain('上下文实体召回率')
    expect(container.textContent).toContain('78.0%')
    expect(container.textContent).toContain('表达精炼度')
    expect(container.textContent).toContain('84.0%')

    act(() => root.unmount())
    container.remove()
  })
})

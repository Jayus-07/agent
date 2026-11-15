import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import RagAnswerHint from './RagAnswerHint'

describe('RagAnswerHint', () => {
  it('points to the source card only when one is present', () => {
    const withSources = renderToStaticMarkup(createElement(RagAnswerHint, {
      confidence: 0.42,
      hasSources: true,
    }))
    const withoutSources = renderToStaticMarkup(createElement(RagAnswerHint, {
      confidence: 0.42,
    }))

    expect(withSources).toContain('点击下方来源核实原文')
    expect(withoutSources).toContain('暂时没有可查看的引用来源')
    expect(withoutSources).not.toContain('点击下方来源')
  })

  it('keeps the status-specific action when a retrieval failure has no sources', () => {
    const html = renderToStaticMarkup(createElement(RagAnswerHint, {
      answerStatus: 'rag_retrieval_degraded',
      confidence: 0.2,
    }))

    expect(html).toContain('请稍后重试')
    expect(html).not.toContain('暂时没有可查看的引用来源')
  })

  it('still shows a low-confidence warning for a non-rejection status', () => {
    const html = renderToStaticMarkup(createElement(RagAnswerHint, {
      answerStatus: 'ok',
      confidence: 0.42,
      hasSources: true,
    }))

    expect(html).toContain('回答置信度 42%')
    expect(html).toContain('点击下方来源核实原文')
  })
})

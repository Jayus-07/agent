import { describe, expect, it } from 'vitest'
import { presentAssistantAnswer } from './assistantAnswerPresentation'

describe('presentAssistantAnswer', () => {
  it('removes internal document markers from visible text', () => {
    expect(presentAssistantAnswer('规则说明 <!--doc:71a1ad2d5f-->')).toBe('规则说明')
  })

  it('uses structured source cards instead of a duplicate reference appendix', () => {
    const answer = '结论见上文。<!--doc:doc-1-->\n\n---\n\n### 参考文献\n\n1. **退款规则.md**'

    expect(presentAssistantAnswer(answer, true)).toBe('结论见上文。')
  })

  it('keeps the reference appendix when there are no structured sources', () => {
    const answer = '结论见上文。\n\n### 参考文献\n\n1. **退款规则.md**'

    expect(presentAssistantAnswer(answer)).toBe(answer)
  })
})

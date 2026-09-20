import { describe, expect, it } from 'vitest'
import { lastUserQuestion } from './chatRetry'

describe('lastUserQuestion', () => {
  it('从消息尾部找到最近一条非空用户问题', () => {
    expect(lastUserQuestion([
      { role: 'user', content: '第一问' },
      { role: 'assistant', content: '失败' },
      { role: 'user', content: '  第二问  ' },
      { role: 'assistant', content: '' },
    ])).toBe('第二问')
  })

  it('没有可重试的用户问题时返回 null', () => {
    expect(lastUserQuestion([{ role: 'assistant', content: '失败' }])).toBeNull()
  })
})

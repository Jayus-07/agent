import { describe, expect, it } from 'vitest'
import {
  CHAT_INPUT_MAX_CHARS,
  chatInputOverLimit,
  chatInputShowCounter,
} from './chatInputLimit'

describe('chatInputLimit', () => {
  it('上限与后端 CHAT_INPUT_MAX_CHARS 对齐（20000）', () => {
    expect(CHAT_INPUT_MAX_CHARS).toBe(20000)
  })

  it('chatInputOverLimit 边界：= 上限不超、> 上限超', () => {
    expect(chatInputOverLimit('a'.repeat(20000))).toBe(false)
    expect(chatInputOverLimit('a'.repeat(20001))).toBe(true)
  })

  it('计数器只在达到 80% 阈值后显示', () => {
    expect(chatInputShowCounter('a'.repeat(10000))).toBe(false)
    expect(chatInputShowCounter('a'.repeat(16000))).toBe(true)
  })
})

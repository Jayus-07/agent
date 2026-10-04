import { describe, expect, it } from 'vitest'
import { PROMPT_RELEASE_EXECUTOR, PROMPT_RELEASE_SUITE } from './promptRelease'

describe('Prompt 发布评测配置', () => {
  it('默认使用 8 条发布短门禁集', () => {
    expect(PROMPT_RELEASE_SUITE).toBe('pr_smoke')
  })

  it('默认使用 GitHub Actions 作为正式发布评测执行器', () => {
    expect(PROMPT_RELEASE_EXECUTOR).toBe('github')
  })
})

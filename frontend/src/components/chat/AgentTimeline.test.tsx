import { expect, it } from 'vitest'
import { streamPhaseLabel } from './AgentTimeline'

it('将结构化执行阶段翻译为用户可理解的过程标签', () => {
  expect(streamPhaseLabel('understanding')).toBe('需求理解')
  expect(streamPhaseLabel('tool_start')).toBe('Tool 调用')
  expect(streamPhaseLabel('tool_result')).toBe('Tool 返回')
  expect(streamPhaseLabel('other')).toBe('执行进度')
})

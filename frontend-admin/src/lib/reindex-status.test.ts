import { describe, expect, it } from 'vitest'

import { getReindexTerminalState } from './reindex-status'

describe('getReindexTerminalState', () => {
  const submittedAtMs = 1_700_000_000_000

  it('任务仍在运行时忽略同一文档上一次运行的完成进度', () => {
    expect(getReindexTerminalState({
      taskStatus: 'RUNNING',
      progressStage: 'done',
      progressUpdatedAt: '1699999000.0',
      submittedAtMs,
    })).toBeNull()
  })

  it('有任务状态时以任务终态为准', () => {
    expect(getReindexTerminalState({
      taskStatus: 'SUCCESS',
      progressStage: 'uploading',
      submittedAtMs,
    })).toBe('success')
    expect(getReindexTerminalState({
      taskStatus: 'FAILED',
      progressStage: 'done',
      submittedAtMs,
    })).toBe('failure')
  })

  it('没有任务记录时只接受本次提交之后写入的终态进度', () => {
    expect(getReindexTerminalState({
      progressStage: 'done',
      progressUpdatedAt: String(submittedAtMs / 1000),
      submittedAtMs,
    })).toBe('success')
    expect(getReindexTerminalState({
      progressStage: 'done',
      progressUpdatedAt: String((submittedAtMs - 5000) / 1000),
      submittedAtMs,
    })).toBeNull()
  })
})

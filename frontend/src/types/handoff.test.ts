/**
 * handoff.test.ts — 前后端契约 fixture 对齐测试（多域隔离收官 M1）
 *
 * 消费 backend/tests/fixtures/handoff_payload_v1.json（唯一契约样例，
 * 后端 test_handoff_contract.py 校验同一份文件）——两端任一侧字段改名/
 * 改语义，这里立即红。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parseHandoffEvent, type HandoffPayloadV1 } from './handoff'

const FIXTURE_PATH = resolve(
  __dirname,
  '../../../backend/tests/fixtures/handoff_payload_v1.json',
)

describe('handoff 契约 fixture 对齐', () => {
  const raw = JSON.parse(readFileSync(FIXTURE_PATH, 'utf-8')) as Record<
    string,
    unknown
  >

  it('fixture 顶层字段与前端类型一一对应', () => {
    expect(Object.keys(raw).sort()).toEqual(
      ['params', 'reason', 'target_domain', 'text', 'v'].sort(),
    )
    expect(raw.v).toBe(1)
  })

  it('parseHandoffEvent 接受契约样例且字段保真', () => {
    const evt = parseHandoffEvent({ ...raw, ts: 1700000000 })
    expect(evt).not.toBeNull()
    const payload = evt as HandoffPayloadV1
    expect(payload.target_domain).toBe('travel')
    expect(payload.params.destination).toBe('福州')
    expect(payload.params.days).toBe(2)
    expect(payload.params.party_size).toBe(3)
    expect(payload.params.budget_cny).toBe(2000)
    expect(payload.params.must_go).toEqual(['三坊七巷'])
    expect(payload.text).toContain('旅游规划')
  })

  it('非法形状拒绝：未知目标域 / 缺 v / 非 dict', () => {
    expect(parseHandoffEvent({ ...raw, target_domain: 'ghost' })).toBeNull()
    expect(parseHandoffEvent({ ...raw, v: 2 })).toBeNull()
    expect(parseHandoffEvent(null)).toBeNull()
    expect(parseHandoffEvent('handoff')).toBeNull()
  })

  it('容忍缺省：无 reason/params/text 时按空值解析（向后兼容演进）', () => {
    const evt = parseHandoffEvent({ v: 1, target_domain: 'customer_service' })
    expect(evt).not.toBeNull()
    expect(evt!.reason).toBe('')
    expect(evt!.params).toEqual({})
  })
})

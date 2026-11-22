import { describe, expect, it } from 'vitest'
import {
  ALL_DOMAIN,
  EVAL_DOMAINS,
  domainLabel,
  isDomainFilter,
  moduleToDomain,
  promptKeyToDomain,
} from './domains'

describe('评测域归堆规则', () => {
  it('prompt_key 前缀映射到对应域', () => {
    expect(promptKeyToDomain('rag.qa')).toBe('rag')
    expect(promptKeyToDomain('rag.preprocessing.summary')).toBe('rag')
    expect(promptKeyToDomain('sql.generator')).toBe('sql')
    expect(promptKeyToDomain('customer_service.answer')).toBe('cs')
    expect(promptKeyToDomain('travel.turn_decision')).toBe('travel')
  })
  it('评测器与未登记 key 归编排智能体域兜底', () => {
    expect(promptKeyToDomain('evaluation.judge.cs_forbidden')).toBe('agent')
    expect(promptKeyToDomain('planner.system')).toBe('agent')
    expect(promptKeyToDomain('router.llm')).toBe('agent')
    expect(promptKeyToDomain('memory.session.summary')).toBe('agent')
    expect(promptKeyToDomain('business_report.polish')).toBe('agent')
    expect(promptKeyToDomain('')).toBe('agent')
    expect(promptKeyToDomain(null)).toBe('agent')
  })
  it('module 字段映射到对应域，customer_service 别名归客服', () => {
    expect(moduleToDomain('rag')).toBe('rag')
    expect(moduleToDomain('sql')).toBe('sql')
    expect(moduleToDomain('cs')).toBe('cs')
    expect(moduleToDomain('customer_service')).toBe('cs')
    expect(moduleToDomain('travel')).toBe('travel')
    expect(moduleToDomain('unknown-module')).toBe('agent')
    expect(moduleToDomain(undefined)).toBe('agent')
  })
  it('域清单含五个域且标签齐备', () => {
    expect(EVAL_DOMAINS.map(item => item.key)).toEqual(['rag', 'sql', 'cs', 'travel', 'agent'])
    expect(domainLabel('cs')).toBe('客服')
    expect(domainLabel('agent')).toBe('编排智能体')
  })
  it('URL domain 参数合法性', () => {
    expect(isDomainFilter('sql')).toBe(true)
    expect(isDomainFilter(ALL_DOMAIN)).toBe(true)
    expect(isDomainFilter('bogus')).toBe(false)
    expect(isDomainFilter(null)).toBe(false)
  })
})

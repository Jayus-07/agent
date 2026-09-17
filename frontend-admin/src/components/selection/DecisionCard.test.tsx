/**
 * DecisionCard 纯函数测试（UX P0-③ / B1 拍板闭环）
 *
 * 锁三件事：
 *   1. 拍板徽章语义：三态各得其色（中国惯例涨红跌绿），未拍板灰态；
 *   2. 表单校验：candidate_id 必填、decision 必须三值之一；
 *   3. 表现回填 metrics 构造：空槽位丢弃、非法数值丢弃、备注保留。
 */
import { describe, expect, it } from 'vitest'

import { buildMetrics, decisionBadge, validateDecideInput } from './DecisionCard'

describe('decisionBadge — 拍板徽章', () => {
  it('三态各自成色：采纳红 / 放弃绿 / 暂缓琥珀（涨红跌绿惯例）', () => {
    expect(decisionBadge('adopted')).toMatchObject({ text: '已拍板·采纳' })
    expect(decisionBadge('adopted').className).toContain('red')
    expect(decisionBadge('rejected').className).toContain('green')
    expect(decisionBadge('deferred').className).toContain('amber')
  })

  it('未拍板 → 灰态「未拍板」', () => {
    expect(decisionBadge(null)).toMatchObject({ text: '未拍板' })
    expect(decisionBadge(null).className).toContain('gray')
  })
})

describe('validateDecideInput — 拍板表单校验', () => {
  it('candidate_id 为空/纯空白 → 报错文案', () => {
    expect(validateDecideInput('', 'adopted')).toContain('候选商品标识')
    expect(validateDecideInput('   ', 'adopted')).toContain('候选商品标识')
  })

  it('合法输入 → null（放行）', () => {
    expect(validateDecideInput('jd:10001', 'adopted')).toBeNull()
    expect(validateDecideInput('jd:10001', 'rejected')).toBeNull()
    expect(validateDecideInput('jd:10001', 'deferred')).toBeNull()
  })
})

describe('buildMetrics — 表现回填构造', () => {
  it('合法数值进 metrics，空槽位丢弃', () => {
    expect(buildMetrics('420', '4.6', '旺季表现')).toEqual({
      sales_30d: 420,
      rating: 4.6,
      note: '旺季表现',
    })
  })

  it('全空 → 空 metrics（后端 dict 仍可回填但等价于无信息）', () => {
    expect(buildMetrics('', '', '')).toEqual({})
  })

  it('非法数值丢弃，备注保留', () => {
    expect(buildMetrics('abc', 'NaN', 'only-note')).toEqual({ note: 'only-note' })
  })

  it('备注只 trim 不改写', () => {
    expect(buildMetrics('', '', '  有空格  ')).toEqual({ note: '有空格' })
  })
})

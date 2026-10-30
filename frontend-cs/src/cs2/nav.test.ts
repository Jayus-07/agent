import { describe, expect, it } from 'vitest'
import { CS2_NAV_ITEMS } from './nav'

describe('cs2 导航配置', () => {
  it('覆盖原型侧栏的全部七个视图', () => {
    expect(CS2_NAV_ITEMS.map((item) => item.href)).toEqual([
      '/cs2/workspace',
      '/cs2/conversations',
      '/cs2/tickets',
      '/cs2/refunds',
      '/cs2/qa',
      '/cs2/dashboard',
      '/cs2/knowledge',
    ])
  })

  it('退款审核与数据看板标记为 supervisor 视角', () => {
    const supervisorOnly = CS2_NAV_ITEMS
      .filter((item) => item.supervisorOnly)
      .map((item) => item.href)
    expect(supervisorOnly).toEqual(['/cs2/refunds', '/cs2/dashboard'])
  })
})

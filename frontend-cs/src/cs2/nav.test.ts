import { describe, expect, it } from 'vitest'
import { CS2_NAV_ITEMS } from './nav'

describe('cs 导航配置（P8 切换后路由）', () => {
  it('覆盖原型侧栏的全部七个视图', () => {
    expect(CS2_NAV_ITEMS.map((item) => item.href)).toEqual([
      '/cs/workspace',
      '/cs/conversations',
      '/cs/tickets',
      '/cs/refunds',
      '/cs/qa',
      '/cs/dashboard',
      '/cs/knowledge',
    ])
  })

  it('退款审核与数据看板标记为 supervisor 视角', () => {
    const supervisorOnly = CS2_NAV_ITEMS
      .filter((item) => item.supervisorOnly)
      .map((item) => item.href)
    expect(supervisorOnly).toEqual(['/cs/refunds', '/cs/dashboard'])
  })
})

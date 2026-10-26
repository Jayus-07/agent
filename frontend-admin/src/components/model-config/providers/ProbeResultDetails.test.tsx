import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import ProbeResultDetails from './ProbeResultDetails'

it('默认展示后端中文结论和下一步，技术原文保持折叠', () => {
  const html = renderToStaticMarkup(<ProbeResultDetails result={{
    ok: false, provider: 'p', draft: false, network_scope: 'public', target: 'https://example.org',
    userMessage: '供应商账户额度不足', nextAction: '请检查账户余额、套餐和可用额度。',
    summary: '旧文案', steps: [{ grade: 'L2', status: 'fail', summary: '旧归因', raw: 'raw-error' }],
  }} />)
  expect(html).toContain('供应商账户额度不足')
  expect(html).toContain('请检查账户余额')
  expect(html).toContain('查看技术详情')
  expect(html).not.toContain('<details open')
  expect(html).toContain('raw-error')
})

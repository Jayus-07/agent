// @ts-nocheck
import { test, expect } from '@playwright/test'

const pending = {
  proposal_id: 'proposal-browser-1',
  version: 2,
  action_type: 'refund',
  masked_target: '订单尾号 ****1234',
  summary: '为订单申请退款 120 元',
  expires_at: '2026-10-09T12:00:00+08:00',
  state: 'pending',
}
const otherPending = {
  ...pending,
  proposal_id: 'proposal-browser-2',
  version: 5,
  summary: '第二个会话的退款申请',
}
const latestPending = {
  ...pending,
  version: 3,
  summary: '刷新后的退款摘要',
}

const viewports = [
  { name: '390px 手机', width: 390, height: 844 },
  { name: '中间断点', width: 768, height: 900 },
  { name: '桌面', width: 1280, height: 900 },
]
const baseURL = process.env.CS_E2E_BASE_URL || 'http://127.0.0.1:3000'

for (const viewport of viewports) {
  test.describe(`客服待确认卡 · ${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } })

    test.beforeEach(async ({ page }) => {
      await page.addInitScript(() => {
        sessionStorage.setItem('agent.access_token', 'cs-e2e-token')
      })
      await page.route('**/api/cs/conversations/my?*', (route) => route.fulfill({
        json: {
          items: [
            {
              conversation_id: 'conversation-e2e-1', summary: '退款咨询',
              conversation_status: 'active', handling_mode: 'ai',
              created_at: '2026-10-08T10:00:00+08:00',
              last_activity_at: '2026-10-08T10:05:00+08:00',
              messages: [{
                message_id: 'message-e2e-1', sender_type: 'assistant',
                content: '请确认退款申请。', created_at: '2026-10-08T10:05:00+08:00',
              }],
            },
            {
              conversation_id: 'conversation-e2e-2', summary: '另一个退款咨询',
              conversation_status: 'active', handling_mode: 'ai',
              created_at: '2026-10-08T09:00:00+08:00',
              last_activity_at: '2026-10-08T09:05:00+08:00',
              messages: [{
                message_id: 'message-e2e-2', sender_type: 'assistant',
                content: '另一个待确认申请。', created_at: '2026-10-08T09:05:00+08:00',
              }],
            },
          ],
        },
      }))
      await page.route('**/api/cs/conversations/my/*/pending', (route) => {
        const conversationId = new URL(route.request().url()).pathname.split('/').at(-2)
        return route.fulfill({
          json: {
            pending_action: conversationId === 'conversation-e2e-2' ? otherPending : pending,
          },
        })
      })
      await page.route('**/api/cs/conversations/my/*/messages?*', (route) => {
        const conversationId = new URL(route.request().url()).pathname.split('/').at(-2)
        return route.fulfill({
          json: {
            conversation_id: conversationId, handoff_state: 'none',
            last_id: 0, messages: [], agent_typing: false,
          },
        })
      })
      await page.route('**/api/cs/tickets?*', (route) => route.fulfill({ json: { items: [], total: 0 } }))
      await page.route('**/api/cs/confirm', (route) => {
        const body = route.request().postDataJSON()
        const cancelled = body.decision === 'cancel'
        return route.fulfill({
          json: {
            status: cancelled ? 'cancelled' : 'success',
            answer: cancelled ? '退款申请已取消。' : '退款申请已提交。',
            confirmation_state: cancelled ? 'user_cancelled' : 'success',
            proposal_id: body.proposal_id, version: body.expected_version,
          },
        })
      })
    })

    test('展示安全摘要且页面没有横向溢出', async ({ page }, testInfo) => {
      await page.goto(`${baseURL}/agent?cs=1`)
      await expect(page.getByTestId('cs-confirm-card')).toBeVisible()
      await expect(page.getByText('为订单申请退款 120 元')).toBeVisible()
      await expect(page.getByText('订单尾号 ****1234')).toBeVisible()
      await page.screenshot({ path: testInfo.outputPath('pending-card.png'), fullPage: true })
      const hasHorizontalOverflow = await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
      )
      expect(hasHorizontalOverflow).toBe(false)
    })

    test('提交一次版本化确认并显示服务端结果', async ({ page }) => {
      let postedBody
      await page.route('**/api/cs/confirm', async (route) => {
        postedBody = route.request().postDataJSON()
        await route.fulfill({
          json: {
            status: 'success', answer: '退款申请已提交。',
            confirmation_state: 'success', proposal_id: pending.proposal_id, version: pending.version,
          },
        })
      })
      await page.goto(`${baseURL}/agent?cs=1`)
      await page.getByTestId('cs-confirm-submit').click()
      await expect(page.getByText('退款申请已提交。')).toBeVisible()
      expect(postedBody).toMatchObject({
        session_id: 'conversation-e2e-1',
        proposal_id: pending.proposal_id,
        expected_version: pending.version,
        decision: 'confirm',
      })
      expect(postedBody.client_action_id).toBeTruthy()
    })

    test('切换会话并刷新后恢复各自的 Pending', async ({ page }) => {
      await page.goto(`${baseURL}/agent?cs=1`)
      await expect(page.getByTestId('cs-confirm-card')).toBeVisible()
      const sessionSelect = page.getByRole('combobox', { name: '切换客服会话' })
      await sessionSelect.selectOption('conversation-e2e-2')
      await expect(page.getByText('第二个会话的退款申请')).toBeVisible()
      await sessionSelect.selectOption('conversation-e2e-1')
      await expect(page.getByText('为订单申请退款 120 元')).toBeVisible()

      await page.reload()
      await expect(page.getByTestId('cs-confirm-card')).toBeVisible()
      await expect(page.getByText('为订单申请退款 120 元')).toBeVisible()
    })

    test('取消操作提交 cancel 并呈现服务端答复', async ({ page }) => {
      await page.goto(`${baseURL}/agent?cs=1`)
      await page.getByTestId('cs-confirm-cancel').click()
      await expect(page.getByText('退款申请已取消。')).toBeVisible()
    })

    test('409 后刷新最新状态并向用户解释', async ({ page }) => {
      await page.route('**/api/cs/conversations/my/*/pending', (route) => route.fulfill({
        json: { pending_action: latestPending },
      }))
      await page.route('**/api/cs/confirm', (route) => route.fulfill({
        status: 409,
        json: { detail: 'proposal version changed' },
      }))
      await page.goto(`${baseURL}/agent?cs=1`)
      await page.getByTestId('cs-confirm-submit').click()
      await expect(page.getByText('刷新后的退款摘要')).toBeVisible()
      await expect(page.getByText('确认信息已更新')).toBeVisible()
    })
  })
}

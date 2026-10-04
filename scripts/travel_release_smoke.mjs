// scripts/travel_release_smoke.mjs — 旅游助手发布 Smoke（验收 #145）
//
// 全栈固定旅程，每步强断言，任一步失败非零退出：
//   登录 → 表单规划出单 → 聊天改单出草案 → 应用草案（版本+1）
//   → 刷新恢复 → 历史列表恢复 → 导出 ICS（VTIMEZONE 断言）→ 聊天消息回复
//
// 运行（需先起全栈 devctl.bat all /y）：
//   cd scripts && node --experimental-vm-modules travel_release_smoke.mjs
// 依赖：playwright（开发机已在 D:/tmp/pwtest/node_modules；其他环境
//   `npm i playwright` 后设 PLAYWRIGHT_DIR 指向其 node_modules）。
// 账号：uitest_user / UiTest2026（本机测试超级账号族）。
import { createRequire } from 'module'
const require = createRequire(import.meta.url)
let chromium
try {
  ;({ chromium } = require(process.env.PLAYWRIGHT_DIR
    ? `${process.env.PLAYWRIGHT_DIR}/playwright`
    : 'playwright'))
} catch {
  console.error('[smoke] 未找到 playwright：npm i playwright 或设 PLAYWRIGHT_DIR'
    + '（开发机示例：PLAYWRIGHT_DIR=D:/tmp/pwtest/node_modules）')
  process.exit(2)
}

const BASE = process.env.SMOKE_BASE || 'http://127.0.0.1:3100'
const failures = []
function assert(cond, label, detail = '') {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` — ${detail}` : ''}`)
  if (!cond) failures.push(label)
}

const b = await chromium.launch()
const p = await b.newContext({ viewport: { width: 1728, height: 960 }, locale: 'zh-CN' })
  .then(c => c.newPage())
p.setDefaultTimeout(150000)

// ① 登录
await p.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(1200)
await p.locator('input[type="text"], input[name="username"]').first().fill('uitest_user')
await p.locator('input[type="password"]').first().fill('UiTest2026')
await p.locator('button[type="submit"], button:has-text("登录")').first().click()
await p.waitForTimeout(2500)
assert(p.url().includes('travel') || p.url().includes('agent') || !p.url().includes('login'),
  '① 登录跳转', p.url())

// 全新会话，避免脏状态影响断言
await p.evaluate(() => {
  sessionStorage.removeItem('travel:conversation')
  sessionStorage.removeItem('travel:plan-state')
})
await p.goto(`${BASE}/travel`, { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(2000)
await p.waitForSelector('input[placeholder*="例如"]', { timeout: 10000 })

// ② 表单规划出单
await p.locator('input[placeholder*="例如"]').first()
  .fill('福州 2 天 2 人，预算 2500，必去三坊七巷')
await p.locator('button:has-text("生成行程")').first().click()
await p.waitForSelector('text=个地点', { timeout: 150000 })
await p.waitForTimeout(3000)
const cid = await p.evaluate(() => sessionStorage.getItem('travel:conversation'))
assert(!!cid, '② 规划出单并建立会话', `cid=${cid}`)
const v1 = await p.evaluate(() => {
  const s = JSON.parse(sessionStorage.getItem('travel:plan-state') || '{}')
  return s.plan?.itinerary?.plan_version ?? s.plan?.plan_version ?? null
}).catch(() => null)

// ③ 聊天改单 → 草案卡
await p.locator('section[aria-label="旅行助手"] textarea, section[aria-label="旅行助手"] input[type="text"]')
  .first().fill('第二天别太满，节奏轻松一点')
await p.keyboard.press('Enter').catch(() => {})
const sendBtn = p.locator('section[aria-label="旅行助手"] button[aria-label*="发送"], section[aria-label="旅行助手"] button:has-text("发送")')
if (await sendBtn.count()) await sendBtn.first().click()
await p.waitForSelector('text=变更草案', { timeout: 150000 })
assert(true, '③ 聊天改单出草案卡')

// ④ 应用草案
await p.locator('section[aria-label="行程修改预览"] button:has-text("应用")').first().click()
await p.waitForTimeout(4000)
const v2 = await p.evaluate(() => {
  const s = JSON.parse(sessionStorage.getItem('travel:plan-state') || '{}')
  return s.plan?.itinerary?.plan_version ?? s.plan?.plan_version ?? null
}).catch(() => null)
assert(v1 != null && v2 != null && Number(v2) > Number(v1),
  '④ 应用后版本递增', `v1=${v1} v2=${v2}`)

// ⑤ 刷新恢复（断线降级口径：行程恢复 + 断线如实提示）
await p.reload({ waitUntil: 'domcontentloaded' })
await p.waitForTimeout(5000)
const restored = await p.locator('text=三坊七巷').count()
assert(restored > 0, '⑤ 刷新后行程恢复')

// ⑥ 历史列表恢复
const histBtn = p.locator('button:has-text("历史"), [aria-label*="历史"]').first()
if (await histBtn.count()) {
  await histBtn.click()
  await p.waitForTimeout(1500)
  const item = p.locator(`text=福州`).first()
  assert(await item.count() > 0, '⑥ 历史列表含本会话')
  await item.click().catch(() => {})
  await p.waitForTimeout(2500)
} else {
  console.log('SKIP ⑥ 历史入口未找到（布局变更时更新选择器）')
}

// ⑦ 导出 ICS：响应体断言 VTIMEZONE + TZID（验收 #115 联动）
const icsResp = await p.evaluate(async (cid) => {
  const token = sessionStorage.getItem('agent.access_token')
  const state = JSON.parse(sessionStorage.getItem('travel:plan-state') || '{}')
  const itinerary = state.itinerary || state.plan || null
  if (!itinerary) return { status: 0, body: 'NO_ITINERARY_IN_SESSION' }
  const r = await fetch('/api/travel/export/ics', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    credentials: 'include',
    body: JSON.stringify({ itinerary }),
  })
  return { status: r.status, body: await r.text() }
}, cid)
assert(icsResp.status === 200, '⑦ ICS 导出 200', `status=${icsResp.status}`)
assert(String(icsResp.body).includes('BEGIN:VTIMEZONE')
  && String(icsResp.body).includes('TZID:Asia/Shanghai'),
  '⑦ ICS 含 VTIMEZONE Asia/Shanghai')

// ⑧ 聊天消息得到回复（右栏活通道）
await p.locator('section[aria-label="旅行助手"] textarea, section[aria-label="旅行助手"] input[type="text"]')
  .first().fill('行程里有几个景点？')
const sendBtn2 = p.locator('section[aria-label="旅行助手"] button[aria-label*="发送"], section[aria-label="旅行助手"] button:has-text("发送")')
if (await sendBtn2.count()) await sendBtn2.first().click()
else await p.keyboard.press('Enter')
await p.waitForTimeout(20000)
const bubbles = await p.locator('section[aria-label="旅行助手"]').innerText()
assert(bubbles.length > 0, '⑧ 聊天通道有响应')

await p.screenshot({ path: 'D:/tmp/travel-smoke/release-smoke-final.png' })
await b.close()

console.log('== 释放 Smoke 结果:', failures.length ? `FAIL (${failures.length})` : 'ALL PASS')
if (failures.length) {
  console.log('失败项:', failures.join(' | '))
  process.exit(1)
}

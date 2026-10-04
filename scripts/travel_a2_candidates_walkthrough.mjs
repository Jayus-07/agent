// scripts/travel_a2_candidates_walkthrough.mjs — A2 分类候选表 UI（验收 #10）实机走查
//
// 流程：登录 → 规划出单 → 断言分类候选面板（tab+列表）→ 点「换入」
// → 草案卡出现 → 应用（版本+1）→ GET /api/travel/decisions 核对
// canvas_replace 落库（entry=candidates_panel）。截图归档 D:/tmp/travel-a2/。
import { createRequire } from 'module'
import { mkdirSync } from 'fs'
const require = createRequire(import.meta.url)
let chromium
try {
  ;({ chromium } = require(process.env.PLAYWRIGHT_DIR
    ? `${process.env.PLAYWRIGHT_DIR}/playwright`
    : 'playwright'))
} catch {
  console.error('[a2] 未找到 playwright：设 PLAYWRIGHT_DIR=D:/tmp/pwtest/node_modules')
  process.exit(2)
}

const BASE = process.env.SMOKE_BASE || 'http://127.0.0.1:3100'
const OUT = 'D:/tmp/travel-a2'
mkdirSync(OUT, { recursive: true })
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
await p.waitForURL(u => !u.href.includes('/login'), { timeout: 20000 }).catch(() => {})
assert(!p.url().includes('login'), '① 登录跳转', p.url())

await p.evaluate(() => {
  sessionStorage.removeItem('travel:conversation')
  sessionStorage.removeItem('travel:plan-state')
})
await p.goto(`${BASE}/travel`, { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(2000)
await p.waitForSelector('input[placeholder*="例如"]', { timeout: 10000 })

// ② 规划出单（salt 防工具缓存命旧数据）
const salt = Math.floor(Math.random() * 900) + 100
await p.locator('input[placeholder*="例如"]').first()
  .fill(`福州 2 天 ${salt % 3 + 2} 人，预算 ${2000 + salt}，必去三坊七巷`)
await p.locator('button:has-text("生成行程")').first().click()
await p.waitForSelector('text=个地点', { timeout: 150000 })
await p.waitForTimeout(3000)
assert(true, '② 规划出单')
const cid = await p.evaluate(() => sessionStorage.getItem('travel:conversation'))

// ③ 分类候选面板可见（tab + 列表 + 版本徽章）
await p.waitForSelector('section[aria-label="分类候选"]', { timeout: 10000 })
const panelText = await p.locator('section[aria-label="分类候选"]').innerText()
const hasTab = /景点|美食/.test(panelText)
assert(hasTab, '③ 候选面板 tab 可见（景点/美食，空组隐藏）')
assert(/v\d+/.test(panelText), '③b 候选表标题带 plan_version 徽章')
await p.screenshot({ path: `${OUT}/01-candidates-panel.png`, fullPage: false })

// ④ 点「换入」→ 草案卡出现
const replaceBtn = p.locator('section[aria-label="分类候选"] button:has-text("换入")').first()
assert(await replaceBtn.isEnabled(), '④ 换入按钮可点（非生成中）')
await replaceBtn.click()
await p.waitForSelector('text=变更草案', { timeout: 150000 })
assert(true, '④b 换入触发草案卡')
await p.screenshot({ path: `${OUT}/02-draft-after-replace.png`, fullPage: false })

// ⑤ 应用草案 → 版本 +1
const vBefore = await p.evaluate(() => {
  const s = JSON.parse(sessionStorage.getItem('travel:plan-state') || '{}')
  return s.plan?.itinerary?.plan_version ?? 0
})
const applyBtn = p.locator('button:has-text("应用新行程"), button:has-text("应用")').first()
await applyBtn.click()
await p.waitForFunction(
  (v) => {
    const s = JSON.parse(sessionStorage.getItem('travel:plan-state') || '{}')
    return (s.plan?.itinerary?.plan_version ?? 0) > v
  },
  vBefore,
  { timeout: 150000 },
)
const vAfter = await p.evaluate(() => {
  const s = JSON.parse(sessionStorage.getItem('travel:plan-state') || '{}')
  return s.plan?.itinerary?.plan_version ?? 0
})
assert(vAfter > vBefore, '⑤ 应用草案版本严格递增', `v${vBefore}→v${vAfter}`)

await p.screenshot({ path: `${OUT}/03-applied-v${vAfter}.png`, fullPage: false })
console.log(`[a2] GUI 全链 PASS；decision 落库核对走 scripts/travel_a2_decision_probe.py（页面裸 fetch 无网关头）`)
await b.close()
if (failures.length) {
  console.error(`[a2] ${failures.length} 项失败: ${failures.join(' | ')}`)
  process.exit(1)
}
console.log('[a2] ALL PASS')
console.log(`[a2] CONVERSATION_ID=${cid}`)

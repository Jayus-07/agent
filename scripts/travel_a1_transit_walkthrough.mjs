// scripts/travel_a1_transit_walkthrough.mjs — A1 市内公交候选（验收 #41）实机走查
//
// 流程：登录 → 规划福州 2 天（短途段触发公交候选）→ 等出单 → 逐段找
// 「公交」展开钮 → 展开截图 → 断言主时间轴口径不变（时刻表与主导出一致）。
// 截图归档 D:/tmp/travel-a1/。
import { createRequire } from 'module'
import { mkdirSync } from 'fs'
const require = createRequire(import.meta.url)
let chromium
try {
  ;({ chromium } = require(process.env.PLAYWRIGHT_DIR
    ? `${process.env.PLAYWRIGHT_DIR}/playwright`
    : 'playwright'))
} catch {
  console.error('[a1] 未找到 playwright：设 PLAYWRIGHT_DIR=D:/tmp/pwtest/node_modules')
  process.exit(2)
}

const BASE = process.env.SMOKE_BASE || 'http://127.0.0.1:3100'
const OUT = 'D:/tmp/travel-a1'
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

// ① 登录（401/502 重试一次——多会话共享账号）
await p.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(1200)
await p.locator('input[type="text"], input[name="username"]').first().fill('uitest_user')
await p.locator('input[type="password"]').first().fill('UiTest2026')
await p.locator('button[type="submit"], button:has-text("登录")').first().click()
await p.waitForURL(u => !u.href.includes('/login'), { timeout: 20000 }).catch(() => {})
assert(!p.url().includes('login'), '① 登录跳转', p.url())

// 全新会话
await p.evaluate(() => {
  sessionStorage.removeItem('travel:conversation')
  sessionStorage.removeItem('travel:plan-state')
})
await p.goto(`${BASE}/travel`, { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(2000)
await p.waitForSelector('input[placeholder*="例如"]', { timeout: 10000 })

// ② 规划（近端日期 → 实时公交口径可用；参数带随机扰动绕开 24h tool_cache——
// 缓存命中的是 transit_option 尚未存在的旧行程，属正确缓存行为）
const salt = Math.floor(Math.random() * 900) + 100
await p.locator('input[placeholder*="例如"]').first()
  .fill(`福州 2 天 ${salt % 4 + 1} 人，预算 ${2000 + salt}，必去三坊七巷`)
await p.locator('button:has-text("生成行程")').first().click()
await p.waitForSelector('text=个地点', { timeout: 150000 })
await p.waitForTimeout(3000)
assert(true, '② 规划出单')
await p.screenshot({ path: `${OUT}/01-plan-overview.png`, fullPage: false })

// ③ 抓行程 legs 数据（含 transit_option）——直接读页面 plan-state（与 UI 同源）
const legEvidence = await p.evaluate(() => {
  const raw = sessionStorage.getItem('travel:plan-state')
  if (!raw) return { error: 'no plan-state' }
  const s = JSON.parse(raw)
  const it = s.plan?.itinerary || s.plan || s.itinerary
  if (!it) return { error: 'no itinerary', keys: Object.keys(s) }
  const legs = []
  for (const day of it.days || []) {
    for (const leg of day.legs || []) {
      legs.push({
        day: day.day_index, from: leg.from_title, to: leg.to_title,
        mode: leg.mode, minutes: leg.minutes,
        transit_option: leg.transit_option || null,
      })
    }
  }
  return { plan_version: it.plan_version, legs }
})
console.log('[a1] legs evidence:', JSON.stringify(legEvidence, null, 2).slice(0, 1600))
const withOption = (legEvidence.legs || []).filter(l => l.transit_option)
assert((legEvidence.legs || []).length > 0, '③ API 行程 legs 可取', `legs=${(legEvidence.legs || []).length}`)
assert(withOption.length > 0, '③b 至少一段带 transit_option', `带候选段数=${withOption.length}`)

// ④ UI 面：找带「公交」钮的交通段，点开截图
const transitButtons = p.locator('button:has-text("公交")')
const btnCount = await transitButtons.count()
assert(btnCount > 0, '④ 时间轴存在公交候选展开钮', `按钮数=${btnCount}`)
if (btnCount > 0) {
  await transitButtons.first().click()
  await p.waitForTimeout(800)
  // 展开区出现摘要文本（地铁/步行/公交关键词）
  const summaryText = await p.locator('text=/地铁|公交|步行 \\d+m/').first()
    .textContent({ timeout: 5000 }).catch(() => '')
  assert(!!summaryText, '④b 展开区显示乘坐摘要', String(summaryText).slice(0, 60))
  await p.screenshot({ path: `${OUT}/02-transit-option-expanded.png`, fullPage: false })
}

// ⑤ 主时间轴口径不变：transit_option 展开不改变时刻表文本
const timelineBefore = await p.locator('ol li[data-item-kind]').allTextContents()
await p.locator('button:has-text("公交")').first().click() // 再点收起
await p.waitForTimeout(400)
const timelineAfter = await p.locator('ol li[data-item-kind]').allTextContents()
assert(JSON.stringify(timelineBefore) === JSON.stringify(timelineAfter),
  '⑤ 主时间轴口径不变（展开/收起前后一致）')

await p.screenshot({ path: `${OUT}/03-final-state.png`, fullPage: false })
console.log(`[a1] 截图已归档 ${OUT}/（共 3 张）`)
await b.close()
if (failures.length) {
  console.error(`[a1] ${failures.length} 项失败: ${failures.join(' | ')}`)
  process.exit(1)
}
console.log('[a1] ALL PASS')

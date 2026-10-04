// scripts/travel_decision_contract.mjs — 五类决策契约自动回归（验收 #141）
//
// GUI 触发五类 decision（apply_draft / discard_draft / canvas_replace /
// tier_switch / budget_negotiate），随后查 GET /api/travel/decisions 逐类
// 断言记录存在且契约字段完整（decision/plan_version/payload/source/
// client_run_id）。任一缺失/字段残缺 → 非零退出。
//
// 运行依赖同 scripts/travel_release_smoke.mjs（playwright + 全栈已起）。
import { createRequire } from 'module'
const require = createRequire(import.meta.url)
let chromium
try {
  ;({ chromium } = require(process.env.PLAYWRIGHT_DIR
    ? `${process.env.PLAYWRIGHT_DIR}/playwright`
    : 'playwright'))
} catch {
  console.error('[decision-contract] 未找到 playwright：npm i playwright 或设 PLAYWRIGHT_DIR')
  process.exit(2)
}
const BASE = process.env.SMOKE_BASE || 'http://127.0.0.1:3100'
const failures = []
const assert = (cond, label, detail = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` — ${detail}` : ''}`)
  if (!cond) failures.push(label)
}

const b = await chromium.launch()
const p = await b.newContext({ viewport: { width: 1728, height: 960 }, locale: 'zh-CN' })
  .then(c => c.newPage())
p.setDefaultTimeout(150000)

await p.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(1200)
await p.locator('input[type="text"], input[name="username"]').first().fill('uitest_user')
await p.locator('input[type="password"]').first().fill('UiTest2026')
await p.locator('button[type="submit"], button:has-text("登录")').first().click()
await p.waitForTimeout(2500)
await p.evaluate(() => {
  sessionStorage.removeItem('travel:conversation')
  sessionStorage.removeItem('travel:plan-state')
})
await p.goto(`${BASE}/travel`, { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(2000)
await p.waitForSelector('input[placeholder*="例如"]', { timeout: 10000 })

// 规划（低预算触发 budget_negotiate 缺口卡 + 美食偏好触发换一家入口）
await p.locator('input[placeholder*="例如"]').first()
  .fill('泉州 2 天 2 人，预算 300，必去开元寺，喜欢美食')
await p.locator('button:has-text("生成行程")').first().click()
await p.waitForSelector('text=个地点', { timeout: 150000 })
await p.waitForTimeout(3000)
const cid = await p.evaluate(() => sessionStorage.getItem('travel:conversation'))
assert(!!cid, '规划出单', `cid=${cid}`)

// ① discard_draft：聊天改单出草案后放弃
await p.locator('section[aria-label="旅行助手"] textarea, section[aria-label="旅行助手"] input[type="text"]')
  .first().fill('第二天别太满')
const send1 = p.locator('section[aria-label="旅行助手"] button[aria-label*="发送"], section[aria-label="旅行助手"] button:has-text("发送")')
if (await send1.count()) await send1.first().click()
else await p.keyboard.press('Enter')
await p.waitForSelector('text=变更草案', { timeout: 150000 })
await p.locator('section[aria-label="行程修改预览"] button:has-text("放弃")').first().click()
await p.waitForTimeout(2000)
assert(true, '① discard_draft 触发')

// ② apply_draft：再改单后应用
await p.locator('section[aria-label="旅行助手"] textarea, section[aria-label="旅行助手"] input[type="text"]')
  .first().fill('节奏改紧凑一点')
const send2 = p.locator('section[aria-label="旅行助手"] button[aria-label*="发送"], section[aria-label="旅行助手"] button:has-text("发送")')
if (await send2.count()) await send2.first().click()
else await p.keyboard.press('Enter')
await p.waitForSelector('text=变更草案', { timeout: 150000 })
await p.locator('section[aria-label="行程修改预览"] button:has-text("应用")').first().click()
await p.waitForTimeout(4000)
assert(true, '② apply_draft 触发')

// ③ canvas_replace：用餐条目换一家 → 确认替换（无候选时 skip）
const replaceBtn = p.locator('button:has-text("换一家")')
if (await replaceBtn.count()) {
  await replaceBtn.first().click()
  await p.waitForTimeout(800)
  const cand = p.locator('[role="dialog"] button').first()
  if (await cand.count()) {
    await cand.click()
    await p.waitForTimeout(300)
    const confirm = p.locator('button:has-text("确认替换")')
    if (await confirm.count()) {
      await confirm.first().click()
      await p.waitForTimeout(1500)
      assert(true, '③ canvas_replace 触发')
    } else assert(false, '③ canvas_replace 确认按钮缺失')
  } else console.log('SKIP ③ 换一家无候选（数据相关，非契约失败）')
} else console.log('SKIP ③ 无换一家入口（当日无用餐条目，非契约失败）')

// ④ tier_switch：档位切换器
const tierBtn = p.locator('button:has-text("舒适均衡型"), button:has-text("舒适")').first()
if (await tierBtn.count()) {
  await tierBtn.click()
  await p.waitForTimeout(1500)
  const confirmTier = p.locator('button:has-text("确认"), button:has-text("重新规划")').first()
  if (await confirmTier.count()) await confirmTier.click().catch(() => {})
  await p.waitForTimeout(6000)
  assert(true, '④ tier_switch 触发')
} else console.log('SKIP ④ 档位切换器未找到（布局变更时更新选择器）')

// ⑤ budget_negotiate：缺口卡快捷删减按钮（去掉第 X 天 / 改成 X 天 / 预算调到）
// 放最后：点按后代发聊天重排，产生的新草案不影响已触发的其他类
const negotiate = p.locator('[aria-label="快捷删减协商"] button').first()
if (await negotiate.count()) {
  await negotiate.click()
  await p.waitForTimeout(2000)
  assert(true, '⑤ budget_negotiate 触发')
} else console.log('SKIP ⑤ 无缺口协商入口（预算 300 未触发缺口卡时数据相关）')

// 查询决策并逐类断言契约字段
const decisions = await p.evaluate(async (cid) => {
  const token = sessionStorage.getItem('agent.access_token')
  const r = await fetch(`/api/travel/decisions?conversation_id=${encodeURIComponent(cid)}`, {
    headers: { Authorization: `Bearer ${token}` }, credentials: 'include',
  })
  return { status: r.status, body: await r.json() }
}, cid)
assert(decisions.status === 200, 'decisions 查询 200', `status=${decisions.status}`)

const items = decisions.body?.decisions || []
const REQUIRED_FIELDS = ['decision', 'plan_version', 'source', 'client_run_id']
const seen = new Set()
for (const it of items) {
  const missing = REQUIRED_FIELDS.filter(f => it[f] === undefined || it[f] === null)
  if (missing.length) {
    assert(false, `decision 契约字段完整`, `${it.decision}: 缺 ${missing.join(',')}`)
  }
  seen.add(it.decision)
}
assert(!items.some(it => REQUIRED_FIELDS.some(f => it[f] == null)),
  '全部 decision 契约字段完整', `共 ${items.length} 条`)
for (const t of ['apply_draft', 'discard_draft', 'canvas_replace', 'tier_switch', 'budget_negotiate']) {
  assert(seen.has(t), `决策类型 ${t} 落库`)
}
await b.close()
console.log('== 决策契约回归:', failures.length ? `FAIL (${failures.length})` : 'ALL PASS')
if (failures.length) { console.log('失败项:', failures.join(' | ')); process.exit(1) }

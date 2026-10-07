// obsrecon_ui_walk.mjs — Phase 7 Grafana 7 张 Dashboard 浏览器实机走查（Node playwright）
// 真实 Chromium 渲染；输出截图 + walk_report.json（console 错误 / panel error 计数）。
// 用法：GRAFANA_PWD=xxx node scripts/obsrecon_ui_walk.mjs [--base http://localhost:3001]
import fs from "node:fs";
import { chromium } from "playwright";

const BASE = process.argv.includes("--base")
  ? process.argv[process.argv.indexOf("--base") + 1]
  : "http://localhost:3001";
const PWD = process.env.GRAFANA_PWD;
if (!PWD) { console.error("需要 GRAFANA_PWD 环境变量"); process.exit(1); }
const WAIT = 18000;
const OUT = "d:/tmp/obsrecon_ui";
fs.mkdirSync(OUT, { recursive: true });

const DASHBOARDS = [
  ["agent-01-platform-overview", "01 平台总览"],
  ["agent-02-router-runtime", "02 Agent 运行时与路由"],
  ["agent-03-tool-runtime", "03 Tool / MCP 运行时"],
  ["agent-04-rag-knowledge", "04 RAG 与知识"],
  ["agent-05-llm-cost-context", "05 LLM / 成本 / 上下文"],
  ["agent-06-domain-business", "06 业务域成效"],
  ["agent-07-infra-async", "07 基础设施与异步"],
];

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 }, locale: "zh-CN" });
const page = await ctx.newPage();
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 200)); });

// 登录
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill('input[name="user"]', "admin");
await page.fill('input[name="password"]', PWD);
await page.evaluate("document.querySelector('button[type=submit]')?.click()");
await page.waitForLoadState("networkidle");
await page.waitForTimeout(3000);
console.log("login ->", page.url);

const report = [];
for (const [uid, label] of DASHBOARDS) {
  const errsBefore = consoleErrors.length;
  await page.goto(`${BASE}/d/${uid}?orgId=1&from=now-24h&to=now&refresh=30s`,
    { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(WAIT);
  const nPanels = await page.evaluate(
    'document.querySelectorAll(\'[data-testid="data-testid Panel container"]\').length');
  const nNoData = await page.evaluate(
    'document.querySelectorAll(\'[data-testid="data-testid Panel status code 2"]\').length');
  const title = await page.title();
  const shot = `${OUT}/${uid}.png`;
  await page.screenshot({ path: shot, fullPage: true });
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.waitForTimeout(4000);
  const narrow = `${OUT}/${uid}_narrow.png`;
  await page.screenshot({ path: narrow, fullPage: false });
  await page.setViewportSize({ width: 1600, height: 900 });
  const entry = {
    uid, label, title, panels_rendered: nPanels, panel_errors: nNoData,
    new_console_errors: consoleErrors.slice(errsBefore),
    screenshot: shot, narrow,
  };
  report.push(entry);
  console.log(`${uid}: panels=${nPanels} panel_errors=${nNoData} console+=${entry.new_console_errors.length}`);
}
await browser.close();
fs.writeFileSync(`${OUT}/walk_report.json`, JSON.stringify(report, null, 1));
console.log("saved", `${OUT}/walk_report.json`);

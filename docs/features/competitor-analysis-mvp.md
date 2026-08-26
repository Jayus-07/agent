# 竞品分析功能 — MVP 实现说明（2026-08-21）

## 功能概述

对话式竞品分析最小闭环：**抓取竞品页面 → 结构化抽取（价格/促销/评价/库存）→ 快照存档 → 变价对比**。用户在对话里发一个竞品链接即可获得分析结果；支持监控列表与价格历史。

## 用户操作方式

| 说法（对话里直接说） | 触发动作 |
|---|---|
| "帮我分析这个竞品 `<URL>`" | 立即抓取 + 抽取 + 存快照 + 对比上次 |
| "竞品最近降价了吗 `<URL>`" | 同上（路由到 competitor.analyze） |
| "巡检一下所有监控的竞品" | 扫描监控列表全部条目 |
| "这个竞品的价格历史 `<URL>`" | 输出价格历史表格与区间 |
| （action=add 加入监控） | URL 进 watchlist，自动抓基线快照 |

路由：`rule_router.py` 新增竞品强信号（竞品/比价/降价/商品域名等关键词 + URL），在 SQL/RAG 规则之前判断。

## 数据来源

- **公开页面定点抓取**：用户提供的竞品商品页/官网 URL（crawl4ai 无头浏览器）
- **搜索引擎动态发现**：复用现有 `web_search`（DuckDuckGo）找行业资讯/竞品链接
- **自有数据对比**：复用现有 `sql` Skill 查业务库
- 合规边界：只抓公开页面，不破反爬、不碰登录数据（京东详情页目前有反爬拦截，需代理池/正式接入时处理）

## 新增/修改文件

```
backend/competitor/                  新模块
  store.py          SQLite 存储（data/competitor.db: watchlist + snapshots 两表）
  adapters.py       平台识别 + 正则抽取（LLM 兜底）+ 货币符号识别
  extractor.py      LLM 结构化抽取（JSON schema，失败降级正则）
  pipeline.py       analyze_url / scan_watchlist / history_report 管线
backend/tools/competitor.py          competitor_analyze_tool（LangChain Tool）
backend/tools/crawler_runtime.py     常驻 crawl4ai 运行时（见"重要修复"）
backend/skills/competitor_analysis/  Skill（capabilities: competitor.analyze/watch/history）
scripts/demo_competitor.py           演示脚本

修改：
backend/skills/registry.py           注册 CompetitorAnalysisSkill
backend/tools/__init__.py            导出新 tool
backend/orchestration/tools.py       导出新 tool（向后兼容路径）
backend/orchestration/router/rule_router.py  竞品路由强信号
backend/tools/web.py                 web_crawl_tool 切换到安全运行时
```

## 重要修复：web_crawl 存量崩溃 bug

排查发现（2026-08-21）：本机 crawl4ai 0.9.2 在 `AsyncWebCrawler.__aexit__` 清理阶段触发 Playwright 硬崩溃（进程级 exit，无法捕获），导致原 `web_crawl_tool` **每次调用都会杀死宿主进程**（抓取结果拿到了但调用方拿不到，FastAPI 服务里用一次即崩）。

修复：新增 `backend/tools/crawler_runtime.py` — 后台线程持有专用事件循环 + 单例浏览器（官方推荐复用模式），永不调用 aclose。收益：崩溃消除 + 二次抓取免 ~10s 浏览器冷启动。

## 效果演示

```
.venv/Scripts/python.exe scripts/demo_competitor.py
```

输出示例（真实抓取 books.toscrape.com 商品页）：

```
## 竞品分析: A Light in the Attic
- 现价: £51.77
- 价格变化: ⚠️ 降价: £59.99 → £51.77（-8.22，-13.7%）
- 卖点: 经典诗歌与绘画合集,20周年纪念版,...（LLM 抽取）
*快照 #5 已存档 | 抽取方式: llm*
```

对话主链路已验证：`MultiAgentSystem.ask("竞品 <URL> 最近价格怎么样")` → Router（竞品强信号）→ SkillExecutor → competitor_analysis → Reporter 生成完整分析报告。

## 回归验证

全量测试：**1072 passed, 3 skipped**（`pytest tests/ backend/tests -o addopts=""`）。

## 已知限制与后续迭代

1. **京东/淘宝详情页反爬**：结构性拦截（无 body），需代理池 + cookie 配置，属 Phase 2 数据源适配器工作
2. 定时巡检（frequency 字段已预留）待接入 WorkflowScheduler
3. 变价告警邮件推送待接 email Skill + 告警规则字段
4. 竞品 vs 自家数据对比（my_sku 字段已预留）待接 sql.query

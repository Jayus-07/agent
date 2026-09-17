# 选品漏斗域（selection_funnel）落地与接线记录（2026-09-17）

> 会话产物交接。本域为**纯新增**实施，未触碰 capability_registry 重构会话
> 持有的任何在途文件（动手前 git status 复核：40+ 未提交改动全部避开）。
> **2026-09-17 11:40 更新**：重构会话已落地提交（246add1），三步接线已执行，
> 海选数据源经用户拍板改为「批量导入通道」（见第七节）。

## 一、为什么做（背景一句话）

旧 `selection_decision` workflow 是四个工作流里唯一没跑通的（交接汇总 D4：
卡在真实竞品 URL 抓取被反爬阻断 + watchlist 数据不一致）。用户拍板重写。
新思路：**域图吃候选池数据源，不依赖外站爬取**，从根上绕开反爬。
海选主源 = 批量导入通道（见第七节）；watchlist 降级为兜底源；
关键词榜单（批次 5 / C2 的 RankingFetcher、product_candidates）就绪后
在 pool_builder 层接入，接口不变。

## 二、已落地（全部为新增文件，17 测试全绿）

| 文件 | 职责 |
|---|---|
| `backend/config/selection_funnel.py` | 阈值集中（`SELECTION_FUNNEL_ENABLED` 默认 **false**）；类目差异化阈值走 `SELECTION_FUNNEL_CATEGORY_RULES` JSON |
| `backend/selection_funnel/graph_state.py` | 节点常量 + TypedDict + `new_selection_funnel_graph_input()`（只放本轮输入） |
| `backend/selection_funnel/models/brief.py` | 需求契约：category 必填，platform/价格带/成本/毛利率可选 |
| `backend/selection_funnel/brief_node.py` | P0 纯规则槽位抽取（无 LLM）；缺类目 → need_info 追问 |
| `backend/selection_funnel/stages/pool_builder.py` | 层二建池：竞品监控快照 → 类目/平台/价格带过滤 |
| `backend/selection_funnel/stages/screener.py` | 层三初筛：rating/reviews/价格带硬阈值；**缺数据保留并披露，不静默丢** |
| `backend/selection_funnel/stages/verifier.py` | 层四验证：**复用 selection/scoring.py 五维潜力分**（不造第二套评分）+ 促销依赖度痛点信号 |
| `backend/selection_funnel/economics.py` | 单位经济纯函数（售价−成本−扣点−物流−推广） |
| `backend/selection_funnel/stages/economist.py` | 层五利润淘汰：淘汰理由含完整数字明细，可复算 |
| `backend/selection_funnel/stages/ranker.py` | 层六排序：分数↓→毛利↓→url 兜底（同分确定性） |
| `backend/selection_funnel/reporter.py` | 报告：漏斗计数表 + 淘汰明细 + 数据缺口 + 测款建议 |
| `backend/selection_funnel/graph_builder.py` | 线性漏斗 + 条件短路（淘空→reporter 如实收尾；无 supervisor 循环）；无 checkpointer（单轮任务） |
| `backend/selection_funnel/register.py` | DomainGraph 注册（name=`selection_funnel`） |
| `backend/orchestration/graph/selection_funnel_graph_node.py` | 适配器：异常降级兜底回复 + funnel 埋点 |
| `backend/orchestration/graph/selection_funnel_prefilter.py` | 预过滤纯函数；**刻意排除「选品决策/值不值得做/能不能上」**（让给 selection_decision workflow，两边不抢路由） |
| `backend/tests/selection_funnel/` | conftest 工厂 + 17 用例（正常漏斗/缺槽追问/淘空/确定性/预过滤/注册） |

改既有文件仅一处一行：`backend/domains/__init__.py` 加 `import backend.selection_funnel.register`。

**验证**：`backend/tests/selection_funnel` 17 passed；`test_registry_consistency + test_layer_consistency` 25 passed。

## 三、接线状态：✅ 已完成（2026-09-17，重构 246add1 落地后）

1. ✅ `backend/orchestration/graph/router_node.py` — 旅游预过滤后插入选品预过滤
   （顺序：CS 规则预判 → CS 预过滤 → 旅游预过滤 → **选品预过滤** → CS 语义兜底 → 三层 Router）。
2. ✅ `backend/orchestration/graph/direct_executor.py` — `_USER_CAP_LABELS` 已加
   `"selection_funnel": "智能选品漏斗"`。
3. ✅ `.env` 已置 `SELECTION_FUNNEL_ENABLED=true`（第 326 行）。
4. ✅ 回归：选品域 46 + 守护 + 路由 = **79 passed**（`--no-cov`）。

## 四、验收命令

```bash
./.venv/Scripts/python.exe -m pytest backend/tests/selection_funnel/ backend/tests/test_registry_consistency.py backend/tests/test_layer_consistency.py backend/tests/orchestration/router/ -q --no-cov
```

## 五、旧 selection_decision workflow 的处置（待用户拍板）

- 现状：workflow 仍注册且可被向量路由命中（「选品决策/值不值得做」类提问），
  与新域**语义互补不重叠**：漏斗产 Top-N 候选，workflow 对单品做值不值得做的决策。
- 选项 A（推荐）：保留 workflow，二期把漏斗 Top-N 作为其候选输入（financing/panel/report 模块可复用）。
- 选项 B：下线 workflow（需同步改 `capabilities.yaml` workflows 段 +
  `orchestration/workflows/__init__.py::register_all()` + `app/server.py`；
  重构 246add1 已落地，server.py 不再在途，B 现在可执行）。

## 六、海选数据源拍板与导入通道（2026-09-17 第二轮）

**用户质疑成立**：监控池受反爬预算硬约束（`anti_ban.GLOBAL_DAILY_BUDGET=40` 次/天），
只能养 1-2 个对象，撑不起漏斗「海选」语义（盯盘 1-2 个 vs 海选几十上百）。
拍板 A 方案：**批量导入通道为主源**，watchlist 降级兜底，榜单采集（C2）二期。

新增文件：
| 文件 | 职责 |
|---|---|
| `backend/selection_funnel/import_pool.py` | 表格解析（CSV/TSV/XLSX，中文表头宽松映射）+ SQLite 候选池（`data/selection_import.db`）；销量不冒充评价数；无效行剔除 |
| `backend/app/api/routes/selection_funnel.py` | REST 导入通道：`POST /import/file`（CSV/XLSX 上传）、`POST /import/text`（Excel Ctrl+C 粘贴 TSV）、`GET /import/candidates`、`DELETE /import/batch/{id}` |
| `backend/tests/selection_funnel/test_import_pool.py` | 解析/映射/清洗/存储 18 用例 |
| `backend/tests/selection_funnel/test_pool_sources.py` | 多源建池：导入优先/兜底/去重/降级 + **导入 8 行走完整漏斗出 Top-5 图级验收** |
| `backend/tests/selection_funnel/test_api_import.py` | API 冒烟 3 用例（TestClient 子应用，不拉全家桶） |

改动既有文件：
- `backend/selection_funnel/stages/pool_builder.py` — 重构为多源（`SELECTION_FUNNEL_POOL_SOURCES`，默认 `import,watchlist`，顺序即优先级；url 去重前源优先；单源失败跳过不炸）
- `backend/config/selection_funnel.py` — 加 `SELECTION_FUNNEL_POOL_SOURCES`
- `backend/app/api/router.py` — 注册 selection_funnel 路由（两行）
- `backend/tests/selection_funnel/conftest.py` — 加 autouse `isolated_import_store`（临时库隔离，不碰真实 data/）

运营用法：生意参谋/竞品工具导出表格（表头含「标题/价格/评分/评价数/平台/类目/链接」等任意组合，
缺列保留 None 由漏斗披露）→ `POST /api/selection-funnel/import/file` 上传或
`/import/text` 粘贴 → 对话里说「给 XX 做一次智能选品」即走漏斗。


## 七、知识层 P0 落地（2026-09-17 第三轮，原架构「知识层」补齐 1→3/3）

用户拍板「动手」后实施，三件套（`backend/selection_funnel/knowledge.py`）：

1. **极限词扫描**（纯规则零依赖）：广告法禁用语词组级扫 Top-N 候选的
   title/promo_text/highlights，误报可控，报告措辞「疑似，请人工复核」；
2. **平台合规要点**：内置清单（淘宝/天猫/拼多多/京东/抖音各 2 条 + 通用 3 条），
   标注以平台最新规则为准；
3. **RAG 检索增强**：桥接 `get_rag_pipeline().retrieve_knowledge()`（轻量检索 3-5s），
   知识库有合规/案例文档时注入报告，没有则降级为空 + note 披露（绝不静默、绝不炸漏斗）。

挂点：`reporter_node` 正常路径调 `compliance_review()` → 报告新增
「### 合规与知识层提示」段。**纪律：知识层只富化不淘汰**——合规风险交人工复核，不自动毙品。

测试：`test_knowledge.py` 13 用例 + conftest 加 autouse `isolated_rag`
（测试绝不拉真实 RAG pipeline，首轮实测 162s → 隔离后 9s）。
选品域合计 **59 passed**（含路由回归 64 passed）。

二期余量（原架构灰块）：大盘趋势 + 拉词建池（待 C2 榜单数据源拍板）、
差评实证（待评论抓取通道）、评估层（待选品→上架→动销回流闭环）。

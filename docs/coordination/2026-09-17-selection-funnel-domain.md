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

## 五、旧 selection_decision workflow 的处置（✅ 已拍板选项 A 并落地，2026-09-17）

- 现状：workflow 仍注册且可被向量路由命中（「选品决策/值不值得做」类提问），
  与新域**语义互补不重叠**：漏斗产 Top-N 候选，workflow 对单品做值不值得做的决策。
- 选项 A（✅ 已实施，d6d6a0a）：漏斗 Top-N 作为 workflow 候选输入——
  `funnel_result.top` 补齐 rating/review_count/highlights；主图
  `_build_workflow_inputs` 在 selection_decision 且同会话有 funnel_context.top
  时注入 `funnel_candidates`；`candidates_from_funnel` 归一，`competitor_data`
  优先漏斗候选、空则回落 watchlist；决策报告披露「候选来源」。financing/panel
  模块经 `ctx.inputs` 通道天然复用。用法：同会话先跑漏斗，再说
  「对 Top-1 跑选品决策」。
- ~~选项 B：下线 workflow~~（不采纳——语义互补已兑现，保留）。

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


## 八、C2 榜单与差评实证的上传文件化（2026-09-17 第四轮）

用户拍板：二期灰块「大盘趋势 / 拉词 / 差评实证」不等数据源，先与选品导入同形态——
**上传文件通道**（market_data.py，与商品导入池同库 selection_import.db，三张表分工）：

| kind | 表头示例 | 漏斗用途 |
|---|---|---|
| products | 标题/价格/评分/评价数/销量（已有） | 候选池主源 |
| keywords | 关键词/搜索人气/点击率/转化率/竞争度 | 报告「赛道画像」：top 词 + 机会词（人气名次−竞争名次差） |
| reviews | 商品标题/评论内容/星级 | 报告「痛点机会」：规则桶聚类（质量/物流/尺寸/气味/描述/服务），星级≤3 计差评 |

- screener 补丁：榜单数据常有销量没评价数——热度线以销量代判，口径写进披露
- 挂点：reporter 注入两段；无数据不渲染段；画像与痛点只富化不淘汰
- API：/import/{file,text} 加 kind 分发；GET /market/snapshot、/reviews/pain-points
- 测试 71 passed（选品域，+12）+ 守护/路由 30 passed
- 二期仅剩：评估层（待动销回流）；关键词榜的「拉词搜索建池」仍需商品榜配合上传
  （关键词级数据不直接产商品候选，正解依旧是 C2 榜单源直连）


## 九、管理端选品漏斗工作台 + mock 样例（2026-09-17 第五轮，f0e2d10）

用户双任务：「缺前端上传页（体验缺口不堵功能）在管理端加一个」+「mock 一份商品榜」。

### 前端工作台（业务分析 → 选品漏斗）
- `frontend-admin/src/api/selectionFunnel.ts`：service 对接 BFF `/api/selection-funnel/*`
  （importFile/importText/listCandidates/marketSnapshot/painPoints/clearBatch），
  BFF catch-all 自动注入 X-API-Key，无需 rewrite
- `frontend-admin/src/app/selection-funnel/page.tsx`：三卡上传（商品榜/关键词榜/差评，
  各带表头说明 + 文件/粘贴双模式 + 类目输入）→ 赛道画像卡（关键词 Top10 + 机会词徽章）
  → 候选池表（价格/评分/评价数/销量/类目，痛点展开行，按批次清除）
- navConfig 业务分析组插入「选品漏斗」（智能选品与选品决策之间）
- 验证：**3200 端口有其他会话的 dev server（PID 24112），按纪律不跑 next build**
  （会清写 .next 打断对方），改 `tsc --noEmit` 全量类型检查通过（EXIT=0）

### mock 样例（docs/samples/，gen_samples.py 生成）
三份 utf-8-sig CSV：样例-商品榜-宠物零食.csv（30 行）、样例-关键词榜（12 行）、
样例-差评（15 行）。商品榜刻意混脏格式——¥ 前缀、「1.5万」「4.8分」、3 行缺评价人数、
2 行极限词（「全网最低价」「销量第一」）——全被真实链路正确解析/识别。

### 两次端到端实测（同一批 30 品）
1. 默认阈值（min_margin 0.30）：pool 30 → screen 30 → econ 0 → **empty_pool**，
   每条淘汰带可复算数字（宠物零食实际毛利 15-28%，30% 线全灭）——正确行为不是 bug，
   暴露阈值校准需求
2. `SELECTION_FUNNEL_CATEGORY_RULES='{"宠物零食":{"min_margin":0.18,"min_reviews":1000}}'`：
   30 → screen 24（6 淘汰）→ econ 14（10 淘汰）→ **Top-5**，
   Top-1 冻干鹌鹑 66.7 分 / 毛利 23.6%，报告含合规提示（2 条极限词命中）、
   赛道画像、痛点机会三段

⚠️ **mock 数据已真实入库**（data/selection_import.db）：批次
`imp-20260917123800`（商品）/ `kw-20260917123801`（关键词）/ `rv-20260917123801`（差评），
页面候选池可按批次清除，勿与真实数据混判。


## 十、全流程实测：上传→漏斗→Top-5 走通 + 批次清除缺口修复（2026-09-17 第六轮，8546ce1）

用户要求「你自己上传来走一遍全流程」。实测发现并修复一个真缺口，全链路走通。

### 发现 1：容器镜像是旧的，页面链路暂不可用
agent-app-1 容器（healthy）内无 selection_funnel 代码——routes 只有 selection/selection_decision。
BFF(3200)→APISIX(9080)→容器(8000) 链路对 /api/selection-funnel/* 全部 404。
**页面要真正可用需重建容器**（会打断容器内使用，需用户拍板/例行重建窗口）。
实测用宿主机临时 uvicorn（8100，只挂本域 router，用完即停）走真实 HTTP multipart。

### 发现 2：批次清除只覆盖商品表（真 bug，已修 8546ce1）
页面「按批次清除」承诺三卡数据可清，实际 DELETE /import/batch/{id} 只查
import_candidates——kw-/rv- 批次必 404（MarketStore 根本没有删除方法）。
修复：MarketStore.clear_batch（双表 DELETE）+ 端点按前缀分发
（imp-→商品表；kw-/rv-→市场库）。测试 71→**73 passed**。

### 发现 3：brief 类目抽取会把动词吸进类目
「帮我**做**宠物零食选品」→ 类目=「做宠物零食」→ 池 0 命中（empty_pool 诚实披露）。
工程规避：`funnel_context={"category": "宠物零食"}` 显式传（brief_node 优先级高于消息抽取）。
抽取健壮性留作待办（不改本轮）。

### 最终跑通（HTTP 上传三份 CSV 后同库）
pool 30→**11**（平台=淘宝 + 价格带过滤 19 淘汰）→ screen **10** → verify 10 →
econ **7**（毛利线 18%）→ rank **Top-5**：冻干鸡肉 67.0 / 洁齿骨 64.9 / 鸡胸肉干 64.4 /
冻干混合装 60.6 / 鸭胸肉冻干 58.9。
报告四段齐全：漏斗计数表、**极限词「销量第一」被合规层识别**、
赛道画像（12 词 + 机会词）、痛点实证；RAG 桥接 0 chunks 降级空披露（设计路径实测）。
跑后测试数据全清（3 批次，库回到 0 行）。


## 十一、结果合理性复算 + RAG 知识库真实命中（2026-09-17 第七轮，04d4cfa）

用户问「结果是合理的吗」+「做一个有结果命中的」。两层回应：

### 结果合理性（每层淘汰可对样例数据复算）
- pool 30→11：funnel_context 显式 platform=淘宝，样例 30 品中淘宝恰 11 品
  （拼多多 8/天猫 6/京东 5/抖音 4 全被平台过滤），价格带全保留
- screen 11→10：min_reviews=1000，淘宝 11 品中仅驯鹿骨（评价 350）不达线 → 淘汰 1
- econ 10→7：毛利线 18% 淘汰 3（低价低毛利款）
- rank 7→5：综合分排序；Top-1 冻干鸡肉（¥59.9/4.8 分/评价 12000）居首合理
- 极限词「销量第一」（Top-5 内鸭胸肉冻干）只富化不淘汰（设计纪律）

### RAG 知识库真实命中（从「0 chunks 降级」到「命中注入」）
灌库：data/docs/policy_general/（第一级子目录名=kb_id，实测踩坑：
放其他子目录会派生错 kb）新增《淘宝平台广告合规管理制度-宠物食品类目》，
启动期增量索引 17 chunks，doc_type=compliance 正确析出。

发现**双重死锁**（rag_enhance 原 query「宠物零食 淘宝 …广告法…」必空命中）：
1. QueryAnalyzer 把「淘宝」析出 person_names → 硬过滤（严格相等），
   而文档侧 person_names 是 jieba 逗号串含噪声（「严重者, 许可证…」）→ 必假阴性
2. 「广告法」触发 business_domain=advertising，文档侧规则析出 product → 单值漂移

桥接层修复（04d4cfa）：rag_enhance query 改「{类目} 平台合规规定 禁限售 选品案例」，
析出仅剩 doc_type $in [policy,compliance]（与文档对齐）。
**RAG 层待办**（留给 RAG 侧会话）：to_metadata_filter 对 person_names/organization
的启发式析出不应做硬过滤，可仿 domains 的 $in+阈值改进；doc_registry 表无
person_names 列（chunk metadata 有值，registry 无列，一致性核查可留）。

命中结果：报告「合规与知识层提示」新增第三段**「知识库相关片段（知识库）」**，
命中的正是知识库案例《宠物零食类目文案合规整改》——「销量第一」被判违规罚款的
整改案例，与本次 Top-5 极限词扫描发现**互相印证**（闭环演示）。
文档演示后已删，索引靠启动期 sync 自愈（delta.deleted → _remove_document）。
测试 73 passed。

## 十二、六步法缺口完善：退款损耗口径 + 竞争格局 + 测款计划 + 决策草案（2026-09-17 第八轮，a94f3e7）

用户拿六步选品法对照盘点漏斗差距后拍板「这个先完善」。范围=六格中不依赖
新数据源、不动其他会话在途域的部分，全部落地：

1. **④经济口径补全——退款损耗**：`economics.calc_unit_economics` 加
   `refund_ratio=0.03`（`SELECTION_FUNNEL_REFUND_RATIO` env 可覆盖，0 退回旧
   口径），公式 = 售价 − 成本 − 扣点 − 物流 − 推广 − **退款损耗**；
   淘汰理由追加「− 退款损耗 N」保持可复算纪律。副作用（真实口径收紧）：
   纯估计成本（45% 售价）的低价款不再达 30% 线——e2e 夹具补「成本60元以内」
   需求约束，129/139 元两款在 econ 层被如实淘汰（dropped=2 断言锁定）。
2. **②竞争格局分析**：`market_data.competition_structure(pool)` 零新数据源，
   纯建池全量计算：CR5（评论数代销量，缺则 sales 兜底）/价格分位
   P25/P50/P75（线性插值）/评价比异常（>0.5 刷评嫌疑；<0.02 且销量≥1000
   销量存疑）/品牌桶（`BRAND_WORDS` 默认 旗舰店/官方/专卖/直营，
   `SELECTION_FUNNEL_BRAND_WORDS` env 按类目扩充，不写死品牌名）。
   报告新段「竞争格局（商品榜）」，含 CR5 集中度判读、价格带打法提示、
   品牌占比过半时白牌避战建议；pool 快照缺失时以存活候选近似并披露。
3. **③痛点对策映射**：`PAIN_ACTIONS` 六桶（质量做工/物流包装/尺寸规格/
   气味口感/描述不符/售后服务）→ 可执行对策，痛点段逐候选附「对策」行。
4. **⑤测款计划卡**：报告新段「测款计划（小额试错）」——Top1-2 逐款
   ¥300-500/天 × 14 天、点击率 > 行业 1.2 倍且加购率 > 8% 加预算、
   30% 预算无加购止损、14 天 ROI<1 复盘或放弃；
   `funnel_context.supply.moq` 机会式读取，有 MOQ 则估首批投入（MOQ×成本），
   缺则披露「补齐后可算首批资金占用」。
5. **⑥决策草案**：报告新段「决策草案（规则初判）」——Top-3 逐款
   做（缓冲 ≥5pp 且成本实测无痛点）/条件做（缓冲 2-5pp 控首单、成本估计值
   先询价、有痛点先落对策）/放弃（<2pp 贴线无缓冲），依据全部来自漏斗已算
   数字，标注「供人工拍板」。
6. reporter_node 接线：`state["pool"]`（竞争格局看全池）、
   `funnel_context.supply.moq`、类目毛利线（brief.target_margin >
   类目 rules > 默认 0.30）。「下一步建议」与测款段去重。

测试 73→81（economics 精确断言 34.5→31.5、refund=0 兼容路径、退款损耗进
理由、竞争结构/异常/品牌桶、三段渲染、草案分档、候选近似回退）。
**遗留给新数据源/他会话域**：C2 榜单直连（②的真实数据源）、供给端 MOQ 上游、
RAG 层 to_metadata_filter 正解、漏斗→决策任务打通。

## 十三、/selection 智能选品页面下线（2026-09-17 第九轮，2d28f8e）

用户拍板「/selection 智能选品 这个删掉」。管理端导航「业务分析」组从
竞品监控/智能选品/选品漏斗/选品决策/报告中心 → 收敛为
**竞品监控/选品漏斗/选品决策/报告中心** 四项。

- 删 `frontend-admin/src/app/selection/`（page.tsx：监控池推荐+趋势面板+
  评分权重配置；TrendPanels.tsx 懒加载组件，无其他引用方）。
- **保留 `src/api/selection.ts` 与后端 `/selection` API**：competitors
  竞品对比（batchScores）与 CompareModal（compare）仍在消费，
  surface.test.ts 契约不破。
- 副作用：评分权重配置（/selection/weights）暂无 UI 入口，后端 API 未动，
  将来选品决策页接权重策略可直接复用。
- 验证：tsc --noEmit 零错误（`.next`/`.next-p0-rolegate` 里指向已删页面的
  过期类型存根需清子目录）；navConfig+surface 契约测试 28 passed。
- 此前遗留「/selection 改名需与他会话对齐」随下线自动消掉；
  「漏斗 Top-5 → 决策任务打通」仍是漏斗侧下一步。

## 十四、历史页快览透出：报告正文入 trace + 来源健康/规则版本列（2026-09-17，eae7005）

用户确认「后端功能前端要体现」→ 按「快览 + 报告查看」落地：

- 适配器成功路径埋点 `funnel_report`（报告正文 → trace.metadata；failed 路径无报告不写）。
- 漏斗历史页加「来源健康」（pool.sources 徽章：ok 中性 / empty 琥珀 / error 红）与
  「规则版本」（funnel_config.rules_version）两列——数据此前已入 trace，纯前端透出。
- 行内「查看报告」展开行：渲染报告全文（数据新鲜度披露 / 分层汇总 / 来源健康 / 价格趋势），
  免回会话翻记录。
- 验证：selection_funnel 套件 114 passed；前端 tsc --noEmit 0 错误。

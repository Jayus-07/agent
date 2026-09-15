# Skill 集成项目进度报告（2026-09-15 晚）

> 范围：SkillHub 六 skill 评审采纳 → 六批次集成实施（计划书 v2：`docs/superpowers/plans/2026-09-15-skill-integration-plan.md`）
> 视角：本会话（Skill 集成线）+ 并发会话（RAG/网关/前端线）当日合计进展
> 状态：批次 1/2/3 已完成，批次 4/5/6 待实施

---

## 一、结论速览

**六批次已完成三个（1/2/3），全部经真实验收；批次 4-6 待计划实施。**
当日两条会话线合计提交 25+ 个 commit，无冲突。 Skill 集成线的核心成果：
EmailSkill 四能力升级、market_research 证据管线 Workflow、选品决策的评估/决策职责分离 + decision_log 反馈闭环——「智能选品」与「竞品分析」两大功能域各补齐一个层级。

---

## 二、已完成 ✅

### 批次 1：Agently Mail 升级 EmailSkill（提交 d3f9f19）

| 项 | 内容 |
|---|---|
| 能力 | `email.send`（引擎切换 smtp\|agently）+ 新增 `email.search` / `email.read` / `email.watch` |
| 文件 | `backend/tools/agently.py`（新增，CLI 包装 + exit 0-8 语义）、`backend/tools/email.py`、`backend/skills/email/skill.py`、`capabilities.yaml` |
| 设计决策 | 发送审批沿用项目 `ensure_approved` 门，通过后 `--confirmed` 直发——不叠加 CLI 两阶段；幂等指纹缓存双引擎共用 |
| 验收 | CLI 安装 + OAuth 通过（mint1614@agent.qq.com）；**真实搜索经包装层验证 ok:true**；定向测试 26 例 |
| 待办 | 发送侧真实验收并入首个业务场景 |

### 批次 2：market_research 品类调研 Workflow（提交 e9dc554 + e6aefa7）

| 项 | 内容 |
|---|---|
| 架构 | 五段式证据管线（非逐章 DAG）：采集 → 清洗/标准化/来源分级 → 三组章节分析并行（LLM 只许引用 evidence_id）→ 统一事实锁定 → 12 章节 Markdown 报告 + Source Index |
| 失败语义 | 有效证据 <5 条 fail-fast；单组 LLM 失败重试 2 次后降级骨架，不中止整体 |
| 验收 | **端到端真实验收通过**（并发会话完成，e6aefa7）：API + 浏览器双通道 success（156s，7 证据/15 章节）；含 Bing 兜底/SSRF 防护/问句抽品类/摘要降级四处修复 |
| 遗留 | 长 workflow 的 chat SSE 断流 P0 已由并发会话修复（e582731 心跳保活） |

### 批次 3：评估/决策职责分离 + decision_log（提交 b6626a9）

| 项 | 内容 |
|---|---|
| 拆分 | `market_assess` → `market_evidence_assess`（只判证据资格 sufficient/partial/insufficient）+ `selection_decision_gate`（市场门控）——**insufficient 硬禁 go**，partial 证据推荐上限"谨慎" |
| decision_log | 十字段表 + 快照不可变（SQLite 触发器 RAISE ABORT 双保险）；同一候选自增 decision_version；痛点 Top5 → 监控关键词建议 |
| 验收 | 定向 21 + 关联面 422 passed；**golden 评测复验全绿**：106 passed/4 skipped，拒答准确率 1.0、分层 smoke/core/hard 全 100%（07:34） |

### 当日并行线成果（另一会话，与本计划协同）

- APISIX 网关迁移 B0-B4 完成并切流 9080；项目分离（移除 Java 组件，py 自建用户体系）
- SSE 心跳保活修复长 workflow 断流（P0）；启动预热消除首请求 30s 冷启
- 评测数据集 V3 结构对齐；RAG 答案缓存越权污染等安全修复；认证 P3 落地

---

## 三、进行中 🔄（收尾项，代码均已就绪）

| # | 事项 | 卡点 | 预估 |
|---|---|---|---|
| 1 | **批次 3 全量回归门禁** | 前两次被关机/并发重构污染（114 failed 为污染样本，auth_middleware 单跑全过）；**干净门禁已于 23:36 重新挂起后台** | 跑完即关 |
| 2 | **decision_log 回填 API** | `set_user_decision`/`set_feedback` 存储方法已有，缺 API 路由 + 前端入口——反馈闭环最后一米 | 半天 |
| 3 | **批次 1 发送侧验收** | Agently 真实发信未发生，等首个业务场景（报告推送/告警）顺带验收 | 随批次 6 |

---

## 四、待计划 ⬜

| 批次 | 内容 | 依赖 |
|---|---|---|
| 4 | CPR 吸收：结论五元组（类型/SRC/时间/置信度/依据）进 prompt 资产、SWOT 逐条分型、八维先定评分标准；四节点 golden 评测集（market_evidence_assess / selection_decision_gate / differentiation / review_panel 各 10-20 条） | 无，可立即开始 |
| 5 | 数据源欠账：榜单采集 RankingFetcher + product_candidates + discover/candidates 端点；评论区实证（review_pain 升级）；Postgres 业务库接 finance_model；my_sku 对标 | 批次 3 已就绪 |
| 6 | CloakBrowser 生产引入（六项风控清单：钉版本/许可审查/兼容差异清单/进程隔离/熔断不豁免/实测四指标）+ 主动通知闭环（选品事件 → email/automation） | email.watch 已就绪 |

---

## 五、风险与注意事项

1. **并发会话协作**：两条线同仓并行，纪律是"动文件前看 mtime/status"，进度看板双方各自维护自己批次，目前无冲突
2. **门禁污染教训**：在大重构进行中不要跑全量回归——白天那次 114 failed 全是并发改动的中间态，auth_middleware 单跑即绿
3. **关机风险**：长跑门禁两次被关机杀掉，建议夜间挂机时确认电源设置
4. **阿里云欠费**：已充值解决；golden 评测恢复正常

---

## 六、关键数据

- 全量基线：3560+ passed / 29 skipped（含今日全部新能力）
- 测试增量：本计划新增/修订约 70 个用例（email 26 + decision_log 9 + smoke/report 修订 + market_research smoke）
- 端到端验证：3 次真实链路（Agently 搜索 / market_research 联网调研 / golden 评测）

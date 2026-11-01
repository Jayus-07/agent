# PROGRESS — cs-evaluation-upgrade（客服评测集升级）

- 任务文件：同目录 TASK.md（唯一事实源）；评估由上一会话完成，本文件承接实施进度
- 更新时间：2026-11-01
- 当前状态：**评估完成，P1–P5 待实施**（P1 可直接开工；启动清单 TASK §8）

## 阶段状态总表

| 项 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| 评估 | 评测集满足度评估（资产/消费/接线三层 + 320 条回放基线） | **已完成**（上一会话，结论入 TASK §1） | — | scripts/cs_dataset_probe.py 可复现 |
| P1 | 离线回放 runner 入 CI + v2/ 分片定案 | 待实施（可开工） | — | — |
| P2 | 数据集修订三类口径 + 升 v2.1 | 待实施（依赖 P1） | — | — |
| P3 | 词表/规则能力补强（cs-gate 护航） | 待实施（依赖 P1） | — | — |
| P4 | 管理端评测中心接线（offline 先行；live 需用户拍板预算） | 待实施 | — | — |
| P5 | 坏例回流运营闭环 | 长期 | — | — |

状态取值：待实施 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每项完成后回填：分支与 PR、实际测试命令与 PASS/FAIL、基线数字对比、遗留事项。

## 环境

- Worktree：`D:\Program Files\workplace\agent-gov`（复用；停靠 docs/cap-gov-owner-confirm 已合并，新分支自 origin/main）
- 基线时点：origin/main@dce04e4 之后（并行会话 travel-v2/ci-debt 持续推进，开工时先 fetch 对齐）
- 测试解释器：`D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（worktree 无 .venv，需从主仓库拷 .env）；真库 5433
- frontend-admin node_modules：junction 复用主仓库（如涉评测中心前端改动）
- 关键口径：回放链 CSRouter.route 完整入口；词表门禁 vocab_gate fail-closed 勿绕过；锁版修订流程见 datasets/README.md
- 环境坑：TASK §2.3（Mimosa hook / GitHub 抖动重试 / compose 等价原命令 / MSYS_NO_PATHCONV / connect_timeout / 并行会话纪律）——开工必读

## 日志

### 2026-11-01（一）—— 评估会话产出（本任务立项依据）

- **评估触发**：用户指令「客服相关评测集要升级：评估是否满足现有客服系统、结合管理端评测，根据结果结合真实项目优化」。
- **探明结论**（详见 TASK §1/§2）：
  - 资产成熟：v2.0 锁版 320 条 + cs-gate 416 条词表门禁（CI 已生效，勿动）；
  - 消费断链：320 条锁版断言零执行（唯一消费者是存在性测试）；管理端评测平台 9 个数据集全 rag、cs=0；live 接线为 manifest 遗留的后续任务；
  - 首次全量离线回放（CSRouter.route 完整入口，零 LLM）：**intent 40.6% / target 52.8% / risk 63.4%**；失败样例暴露三类系统性口径问题（k_policy/k_faq 边界、域判定门槛 vs 自然表达、safety/multi_turn 断言口径）+ 数据集治理悬空（v2/ 304 条修订分片与锁版 320 并行未定案）；
  - 分工红线：工程正确性（幂等/资金/状态）归 119 个既有单测，评测集只管行为质量。
- **产物**：探针脚本持久化至 `scripts/cs_dataset_probe.py`（可复现基线）；本任务 TASK/PROGRESS 落稿。
- **同会话顺带产出**（关联但非本任务范围）：capability-governance G1–G6 全部合入关闭（#198/#199/#200/#202/#203/#204/#207/#211/#216）；owner 归属确认（单一团队，platform-team 即实值）；G5 翻正连带项移交 travel-v2 E5；客服域投产就绪度评估（结论见 TASK §2.4 关联上下文——frontend-cs 生产定义已存在、资金后端/压测/渗透测试为硬缺口、渠道=web 已确认）。
- **待办**：下一会话从 P1 开始（启动清单 TASK §8）。

### 关联：客服域投产检查清单（同日评估遗留，供投产会话使用）

1. frontend-cs 生产构建验证：`COMPOSE_PROJECT_NAME=agent docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build frontend-cs && up -d frontend-cs`，验证 :3300/login 200——上一会话 build 被中断未完成（服务定义已在 local.yml:223，纯验证动作）；
2. 渠道决策落档：真实渠道=web（已确认）——建议同步 `docs/domains/customer-service.md` 渠道段；
3. 资金功能首期范围决策：不含资金则 CS_BUSINESS_GATEWAY_MODE/CS_REFUND_REQUEST_MODE 保持 disabled 并在投产检查记录；
4. 压测（多坐席容量）与安全渗透测试：公开全量投产前必做；
5. 共享库 govtest/probe 测试残留：确认非活跃后经管理端 DELETE 清理。

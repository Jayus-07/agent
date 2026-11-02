# PROGRESS — cs-public-launch（客服域公开投产实施）

- 任务文件：同目录 TASK.md（唯一事实源）；计划由 2026-11-02 会话立项（承接 cs-evaluation-upgrade 收官）
- 更新时间：2026-11-02
- 当前状态：**计划已立项，Q1 待实施**

## 阶段状态总表

| 期 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| Q1 | 上线巡检小件（管理端选项处理 / frontend-cs 构建验证 / 渠道资金落档 / 残留清理） | 待实施 | feat/cs-prod-q1（计划） | TASK §3 Q1 |
| Q2 | cs_faq BM25 索引补建 + live 冒烟对比 | 待实施 | feat/cs-prod-q2（计划） | TASK §3 Q2 |
| Q3 | live 质量缺口决策与实施（4 个拍板项） | 待实施 | 视拍板范围 | TASK §3 Q3 |
| Q4 | 多坐席容量压测 | 待实施 | feat/cs-prod-q4（计划） | TASK §3 Q4 |
| Q5 | 安全渗透自检（Mimosa + 边界复核） | 待实施 | feat/cs-prod-q5（计划） | TASK §3 Q5 |
| Q6 | 资金范围决策落档 | 待实施 | docs（计划） | TASK §3 Q6 |

状态取值：待实施 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每期完成后回填：分支与 PR、实际测试命令与 PASS/FAIL、关键数字（如「系统繁忙」比例、压测 P95）、遗留事项。

## 环境

- Worktree：`D:\Program Files\workplace\agent-cs-prod`（新建，分支自最新 origin/main；worktree 无 .env 从主仓库拷贝）
- 测试解释器：`D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（cwd 在 worktree 根）；真库 5433
- frontend-admin node_modules：junction 复用主仓库
- 宿主执行三件套（NO_PROXY / 注册表刷新 / 8088 模型服务）：cs-evaluation-upgrade PROGRESS 2026-11-02（六）
- 身份链（user:279 super_admin JWT + agent-auth-redis + X-API-Key 经 :9080）：cs-evaluation-upgrade PROGRESS 2026-11-02（四）
- 环境坑：cs-evaluation-upgrade TASK §2.3（Mimosa hook / GitHub 抖动退避 / compose 等价原命令 / MSYS_NO_PATHCONV / 并行会话纪律）——开工必读
- 关键参照：锁版数据集与门禁基线 cs-evaluation-upgrade PROGRESS 2026-11-02（三）；live 基线与移交清单 2026-11-02（六）；P7/P8 运营口径 2026-11-02（七）（八）

## 日志

### 2026-11-02（一）—— 立项

- cs-evaluation-upgrade P1–P8 全部收官（评测运营线达成受控试用口径）；用户拍板「写公开投产实施计划，下一会话处理」。
- 本任务立项：Q1–Q6 六期，覆盖管理端测评误导选项、frontend-cs 构建验证、渠道/资金落档、共享库残留清理、BM25 索引、live 质量缺口拍板、容量压测、渗透自检、资金决策。
- 门槛口径：Q1+Q2 = 受控试用放量；Q1–Q6 全绿 = 全量公开投产。

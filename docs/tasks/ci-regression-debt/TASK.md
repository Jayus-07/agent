# CI 全量回归暗债清偿

> 任务 ID：ci-regression-debt
> 创建：2026-10-30；最近交接：2026-10-31（第二批会话）
> 状态：IN_PROGRESS（v2 的 61 例已全部定性并修复；等待全量基线 v3 实证）

## 目标

把每周 Full Regression（`.github/workflows/full_regression.yml`，13 suite matrix，约 12545 用例）的失败清单收敛到 0：逐周下降直至绿。

## 背景

- 基线 v1（run 40948443665，2026-10-29）：108 失败。
- **基线 v2（run 41120872440，2026-10-31 凌晨）：61 失败（净减 47），api / tools-security-obs / sql-infra / orchestration（dispatch 复验）清零**。
- 本会话（2026-10-31 晚）把 v2 剩余 61 例全部定性并修复（PR #153/#154/#158），等待全量 v3（run 41151145995）实证。

各 suite 明细与逐批实证见 [PROGRESS.md](PROGRESS.md)。

## v2 61 例的定性总表（本会话完成，供复核）

定性分布：**xdist 竞争 2 类（booking 互踩 / p5 时序）+ CI 环境差异 3 类（域开关 env 缺失 / 认证 fail-closed / INVENTORY 库漂移）+ 测试资产漂移（对齐新契约约 20 例）+ 退役功能测试 4 例（删除）**。无一需要"修产品 bug"——与前序批次结论一致（测试资产漂移是主旋律）。

| suite | v2 失败 | 根因 | 修复 PR |
|---|---|---|---|
| orchestration | 7 | CI 无 .env：CS/TRAVEL 总闸 sys_config 缺省 false，fixture patch 的 config.CS_ENABLED 非 M16 判定源；selection GET 缺认证头（router 级 resolve_operator_role → fail-closed 503） | #153 |
| misc | 22 | inventory 10：CI env 未设 INVENTORY_DB_PGDATABASE，测试表落进无后缀兼容库且 drop 清理扫描不到 → 同 worker 行残留互踩；kafka 2：lock 漏包；skills 3：b6b7973 校验收敛契约；tool_runtime 3：7e64152 错误映射契约 / lock 过期 / rbac 守卫改名；domain_runtime 2：M16 同款漂移 + 权威 Handoff 前置键；business_report 1：API-Key fail-closed 503 | #153 + #154 |
| travel | 13 | decision_audit 4：V1 决策留痕退役（删除测试）；booking 5：xdist worker 互踩共享表（advisory lock 串行化）；event_stream 1：V1 端点 404 迁 V2；provider_contracts 2：xdist 类（本地 xdist 全绿） | #158 |
| customer_service | 4 | d18：store「每会话一条当前确认」语义（save 整行覆盖）与测试构造不兼容；cs_experts：290d2b7 refund_request 专用真实链路；p5 1：CI 时序竞争（本地 1495 passed 未复现） | #158 |
| prompts-context | 2 | auto_compact：fixture 12 轮太短，重建后固定 policy 开销反超压缩收益；collapse：本地 xdist 467 passed 未复现 | #158 |
| evaluation | 2 | 37ae046 temperature 0.1→0；8a97a63 数据集版本 candidate.4（loader 对齐 manifest 权威） | #158 |
| repo-tests | 1 | a9184e4 同 request_id 重启流 409（stream_registry 进程级单例跨用例存活） | #158 |
| （workflow） | — | Regression summary 恒红：toJSON(needs.*) 单依赖序列化为数组（全部历史 run 实证） | #158 |

## 验收

- 每周 Full Regression step summary 失败数逐周下降至 0（v1 108 → v2 61 → v3 待证）。
- Regression summary 表格修复后，suite 全绿时 run 才能真正变绿。
- 台账与 PR 描述如实记录；容器级部署验证 UNVERIFIED 必须显式标注。

## 纪律

- 所有改动一律走 PR 合 main；提交用 `git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定。
- 不扩大 mock、不删断言、不 skip/xfail 掩盖失败；未实际执行的验证不得报通过。
- 本会话三批全部为测试资产 + CI workflow 改动，未动产品代码，无需 rebuild。
- 数据文件（suite 声明 candidate.1 vs manifest candidate.4）的不一致属 DATA-01 数据链流程，未在本任务扩范围（测试注释已标注）。

## 关联

- 前序：PR #126–#132、#136、#139–#145、#149、#150（任务文档）。
- 本会话已合 PR：#153（orchestration 7 + inventory 10）、#154（misc 12 + lock 补 kafka-python）、#158（第三批 22 例 + summary）。
- 本任务文档：[PROGRESS.md](PROGRESS.md)（含 v1→v2→v3 曲线、实证数据、坑位记录）。

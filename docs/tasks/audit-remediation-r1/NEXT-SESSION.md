# audit-remediation-r1 — 下一会话接手实施计划（NEXT-SESSION）

> 写于 2026-11-03 会话收尾。前七轮修复已全部合并（PR #283/#284/#285/#287/#288/#290/#293/#294），
> 本文件是剩余工作的接手计划。恢复任务时先核对：main 最新 SHA、并行任务档案（docs/tasks/ 全量 ls）
> 防撞车、本文件与 PROGRESS.md。

## 一、总计划（剩余工作全景）

前七轮已完成：P0×1、P1×5 全部修复；P2 实施完成（唯一例外 competitor 租户列已裁决暂缓）。
剩余为 **P3 收官批**（低风险、多为分诊与小修）+ 两个运维项 + 一个多租户前置项：

| 批次 | 内容 | 类型 | 优先级 |
| --- | --- | --- | --- |
| A1 | 依赖 advisory 人工核对（13 包 / 63 条公告，两轮 Mimosa 扫描均未展开明细） | 分诊 | 中 |
| A2 | SQL 注入告警分诊：`backend/rag/indexing/operation_log_pg.py`、`backend/observability/{llm_usage_store_pg,trace_store_pg,analytics_store_pg}.py`、`backend/rag/preprocessing/keyword_store_pg.py`、`backend/shared/idempotency.py:1415` 附近的动态标识符拼接点逐条确认 | 分诊+小修 | 中 |
| B1 | xml_parser 对齐 pptx 的 `_parse_ooxml_xml` 形态（消掉最后 1 条 CWE-776 告警；守卫已在，纯清扫扫描器盲区） | 小修 | 低 |
| B2 | 列表分页收口：`tasks.py` 列表仅 limit 无 offset、`agent_chat_tasks.py` 固定 limit=100、`memory.py:284` limit 无上限（对齐 `cs_admin.py:767` 的 cursor 分页样板） | 小修 | 中 |
| B3 | `/admin/email/channel` 挂 `require_admin_user`（现向 viewer 暴露 SMTP host/账号存在性） | 小修 | 低 |
| B4 | tenant 空值回退 "default" 改 fail-closed：`plan_revisions.py:52-56`、`travel.py:909`（对齐 `cs_admin._resolve_tenant_scope` 写法；网关对 JWT 必注入 tenant_id，仅旧令牌/异常路径可达） | 小修 | 中 |
| C1 | 生产配置检查清单文档：`GATEWAY_SESSION_CHECK=enforce`、`JWT_SESSION_GUARD_MODE`（audit 默认的告警/拒绝策略）、9080 不出宿主机/安全组、依赖豁免记录落档 | 文档 | 中 |
| D | （仅当用户明确要启用多租户）competitor 租户列改造：watch/snapshot/config/events 四表迁移 + url 全局 UNIQUE 改 (tenant_id,url) 联合唯一 + 爬虫管线扫描语义全链路重构 | 大改 | 默认不实施 |

裁决记录（勿翻案）：competitor 租户列在单租户现实下暂缓；防爆破「不锁不存在账号」契约保留；
bm25 pickle 有 SHA256 sidecar 校验为已知可接受项；OCR 视觉调用不走 proxy 为既有裁决。

## 二、工作树

- **复用** `D:\Program Files\workplace\agent-wt-audit-r1`（七轮分支已全部合并，工作区干净）。
- 开工前：`git fetch origin` → 确认 main 最新 → 每批次从 `origin/main` 新建分支
  （命名沿用 `fix/audit-p3-<batch>`）。
- worktree 内已有 `.env`（拷贝自主仓）；若缺失按 AGENTS.md 从主仓补拷。
- 主仓 `D:\Program Files\workplace\agent` 仅供 pull/重建部署，不直接改代码。
- 网络注意：本机直连 GitHub 失败，推送/PR 用 `HTTPS_PROXY=http://127.0.0.1:7897`（命令级，不改全局）。

## 三、进度基线（接手时快照）

- main：`5b6309f`（PR #295，并行线客服作答均衡；含本任务七轮合并）。
- 密封扫描两份（Mimosa，产物在 `C:\Users\wh\.mimosa\security-scans\project-7c22c5c336fedd2b5b9d1cb1\`）：
  - 基线 `scan-2026-11-02T22-27-15.492Z-7e692965b766`，seal `sha256:d31b06...c8e81`，292 findings；
  - 收尾 `scan-2026-11-03T15-11-19.830Z-b5fe43c69829`，seal `sha256:2e12c4...9ba3`，286 findings
    （对照结论：XML 实体 6→1、CWE-330 26→25、跨文件污点 43→35；data.py:93 / auth_local:792-804 /
    xml_parser:32 三处残留标记已人工核实为守卫后误报或非安全用途）。
- 测试新增 10 个回归文件（见下节）；任务档案 PROGRESS.md 七轮证据齐全。

## 四、测试与验证约定

- 既有回归资产（改这些区域必须保持全绿）：
  `test_data_upload_path_guard.py`、`test_pg_saver_pool.py`、`test_competitor_authz_gate.py`、
  `test_apisix_b4_direct_routes.py`、`test_authn_hardening.py`、`test_engineering_p2_fixes.py`、
  `test_p2_wrapup_fixes.py`、`test_auth_local.py`（含不锁定契约）、
  `test_checkpointer_degrade_policy.py`、`test_stream_events_flow.py`。
- 每项新修带回归测试（不扩大 mock、不 skip）；定向跑受影响面，不跑全量（testing-guide T0-T2）。
- 分诊类（A1/A2）产出核对表写入 PROGRESS.md：每条标记 误报(附理由)/需修(附修复与测试)。
- A2 若确认需修：动态标识符一律改参数化或白名单校验，附负例测试。

## 五、验收标准

- **批次 A**：A1 产出 13 包逐包结论（可升级/豁免+理由/需处理）；A2 告警清单逐条有结论，
  需修项修复后对应告警在新扫描中消失或标注扫描器盲区理由。
- **批次 B**：B1 后 CWE-776 告警归零；B2 三个端点分页/上限齐备且既有契约测试通过；
  B3 非 admin 访问 403（audit 灰度放行语义不变）；B4 空租户请求 403 而非落 default，
  旧令牌路径负例测试覆盖。
- **批次 C**：清单文档落在 `docs/operations/`（或并入既有 release-quality/commands 相关节），
  每项含 检查方法/期望值/当前状态（UNVERIFIED 项如实标注）。
- 通用：每批次独立 PR → CI 全绿 → 合并 → 主仓 pull → 受影响服务重建 → `/health` 200。
  未实际执行的验证不得报告为通过。

## 六、下一会话启动提示词

见仓库内本文件第六节（与对话中给出的提示词一致，以文件为准）。

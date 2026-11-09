# RAG 生命周期审计收口（2026-11-09）

## 目标

处理 `NEEDS-VALIDATION.md` 中 A 档三条 RAG 生命周期线索：先验证，再按 owner 拍板的契约收口。

## 已确认决策

1. #61（下线/送审 vs 排队发布）：CAS 加生命周期状态守卫 **且** 撤销迁移时取消在途索引运行。
2. #62（chunk 详情读取）：非 active 文档对普通可读主体隐藏正文，管理员保留诊断读取。
3. #65（发布前授权）：发布段按运行记录 actor/tenant 复验当前授权，失败按 superseded 终止。

## 范围

- `register_published` 增加认领状态守卫（`base_status` 随运行记录固化）。
- 生命周期服务在下线/送审成功后取消该文件的在途运行。
- `/rag/chunks/{doc_id}/detail` 增加发布状态门（管理员豁免）。
- 索引发布前授权复验（复用 `resolve_task_authorization`）。

## 验收

- 每条带双租户/无凭据对照测试，加入 `rag_smoke.yml` 定向清单。
- 本机按重建流程验证健康；CI 全绿后合并 main。
- 不修改 `C:\Users\wh\security-audit-skill\` 下的审计产物。

## 不在本任务范围

- B–F 档审计线索（另立任务）。
- 生产 ECS 发布（本批改动尚未发布）。

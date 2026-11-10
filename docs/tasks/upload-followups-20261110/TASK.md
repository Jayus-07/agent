# TASK: 上传链路后续排期（2026-11-10 评审遗留）

> 源自 rag-upload-review-fix-20261110 评审，已修复部分见该任务 PROGRESS.md。
> 本文件为遗留项排期：优先级 P1 > P2 > P3；建议批次见各条目。
>
> **进度更新（2026-11-10 batch1/batch2）**：
> - batch1（PR #430，已合并部署）：真实链路冒烟（批次头透传实证、SSE、损坏拦截、49MB 流式）；
>   发现并修复流式透传下 51MB 上传连接重置回归（BFF 路径级上限三方同源）；#2 cs BFF 同步完成；
>   #4 delete/reindex 错误可见性完成。合并后复验：51MB 经 BFF 413+人话 detail（0.05s）、49MB 合法上传正常。
> - batch2（PR #432）：上传窗口专项评审——发现 web 端整个上传栈零消费方（死代码，同 cs），已删除；
>   admin 活 UI 三处交互修复（input value 重置 / 运行中阶段中文标签+消息渲染 / 未知 stage 兜底）。
> - #5 语义变更：web 死代码删除后原「web 组件测试平移」改为「admin behavior 测试扩充」（已有 5 用例，见下方 P3）。

## 遗留项与排期

### P2 — 排入常规迭代

| # | 事项 | 范围 | 备注 |
|---|------|------|------|
| 3 | 上传错误状态码口径统一 | 后端 rag_upload.py + admin 前端解析 | 现状 200+`ok:false` / 422 / 409 / 403 混用；破坏性 API 契约变更，需版本化（新路径或 Accept 头协商），先写 ADR，前端解析同步迁移 |
| 6 | 前端 50MB 上限与后端 env 联动 | config 暴露面 | admin UploadDialog 的 `MAX_UPLOAD_MB` 与 BFF 预检常量（50MiB+16KiB）均为前端写死，后端 `RAG_MAX_FILE_SIZE` 改 env 时漂移；可选方案：/rag/health 返回上限供两处读取 |

### P3 — 机会成本驱动

| # | 事项 | 范围 | 备注 |
|---|------|------|------|
| 7 | admin 上传窗口可用性增强 | frontend-admin UploadDialog | 上传中 beforeunload 拦截（防误刷新丢进度）；ESC/遮罩点击关闭（非上传中）；批量文件数上限提示（如 >20 提示分批） |
| 8 | SSE 断连重试 | admin knowledge.ts uploadDocument | 后端已支持 Redis 镜像重订阅（#426 Fix 8），前端 SSE `fail()` 前重连一次再判「进度连接中断」，降低误报失败 |
| 9 | admin behavior 测试扩充 | UploadDialog.behavior.test.tsx | 补后台转传后重开窗口渲染失败原因、duplicate 计入成功统计、KB 加载失败态 |
| 10 | lib/api multi-query 函数清理 | frontend/src/lib/api.ts | web MultiQueryToggle 已删，`getMultiQueryMode/setMultiQueryMode` 若确认无其他消费方一并移除（需先全仓核查，含 cs2 命名空间） |

## 验收口径

- 每项完成需带对应回归测试；契约变更（#3）必须先写 ADR 说明迁移路径。
- cs 端（已对齐）启用任何上传/批量功能前，先补同款代理契约测试（已有 8 用例基线）。

## 执行批次计划（2026-11-11 排期，接手会话：docs/tasks/upload-rereview-20261111/）

| 批次 | 内容 | 前置 |
|------|------|------|
| batch3 | **全链路复审**（新鲜眼，前后端 + 三端 BFF + 网关），产出评审报告；发现项按风险插入后续批次 | 无——新会话首轮任务 |
| batch4 | #6 env 联动（/rag/health 返回上限，前端两处消费）+ #7 窗口可用性（beforeunload/ESC/批量上限提示）+ #8 SSE 断连重试 + #9 测试扩充；batch3 发现的快修项并入 | batch3 报告 |
| batch5 | #10 lib/api multi-query 核查清理（独立小 PR） | 全仓核查消费方 |
| batch6 | #3 状态码口径版本化（ADR 先行 → 后端新契约 → admin 解析迁移 → 旧契约保留一个发布周期） | ADR 评审通过 |

> 批次划分原则：batch3 只评审不改码（评审与修复分离）；batch4 聚合小项降低 PR 碎片度；#3 体量与风险独立成批。

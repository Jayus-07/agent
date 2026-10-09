# 旅游助手 V2 Batch 2 进度

更新日期：2026-10-10（Asia/Shanghai）

## 状态

Batch 2 的认证态 V2 API、SSE 生成保存和主要浏览器路径已完成复验。用户随后继续任务并完成 Batch 3；当前状态与待确认项以总任务 [PROGRESS.md](../travel-assistant-v2-redesign/PROGRESS.md) 为准。完整 Docker 依赖镜像重建曾受外部 Debian 镜像 500/502 影响。

## 工作区与运行环境

- 此文档初版曾记录目录无法执行 Git 检查；后续恢复时确认当前项目根目录是 Git 仓库，`git status --short` 可运行。工作区仍有大量未提交改动，不视为干净，也未因该历史误报而重置或清理文件。
- 找到并保留既有补丁备份：`D:\tmp\agent-workspace-backup-20261009-travel-v2\working-tree.patch`、`D:\tmp\agent-travel-v2-tools-20261009\working-tree.patch`。
- 3220 为新版 Next.js 预览，3100 的旧预览保持运行。获用户批准后只重建/替换了 `agent-app-1`；未重建 3100、3220、APISIX 或其他容器。应用健康检查为 200。
- 完整 `docker compose build app` 受 Debian 包镜像 HTTP 500/502 阻断。为避免重建其他服务，保留旧镜像标签并基于其构建仅含后端路由改动的 overlay，再只重建 app。此结果验证了运行镜像更新，但完整 Dockerfile 全量构建仍未验证。

## 实现与 API 证据

- `backend/app/api/routes/travel.py` 增加受身份认证保护的 `/api/travel/v2/plan/stream`，复用现有规划图和 SSE；V2 对话使用独立命名空间，不写入已退役的 V1 存储/版本表。
- `frontend/src/api/travel.ts`、`frontend/src/components/travel/v2/TravelChatPage.tsx` 与 `travelLiveRuntime.ts` 接通 V2 SSE，生成成功后自动保存 V2 行程。
- 浏览器第一次遇到过期登录态时，正常 `/api/auth/refresh` 返回 200，随后模板与行程 GET 返回 200；未绕过认证。未带凭据的 V2 SSE 请求返回 401。
- 聊天原先请求已退役的 V1 SSE 路径并得到 404；改接 V2 路径后，登录用户生成并保存了一条真实杭州两日 V2 行程，随后从 V2 API 打开正式工作台。没有使用演示行程或伪造保存结果。

## 浏览器验收与差异

- 首页、聊天、桌面三栏工作台、手机行程详情和编辑入口均在真实登录态下检查。桌面结构符合左侧 AI、中间时间轴、右侧地图；手机包含行程概览、日程切换、地图、时间轴和底部导航。
- 真实模板列表为空，首页展示空状态；静态模板详情 `hangzhou-slow` 返回 404。因此没有用演示模板填充验收。
- 酒店和美食查询从现有 Provider 返回真实商户结果。酒店结果明确没有房态/实价，价格待核实。
- 动车查询向现有 12306 聚合 Provider 请求时失败，页面未展示模拟车次。代码/定向测试覆盖 `no_results`、`provider_unavailable`、`network_timeout` 状态区分，但这三种状态没有全部在本次实时浏览器中逐一触发。
- 桌面 AI 面板提示行程对话修改尚未接入 V2，输入不会修改正式行程；地图没有路线 polyline。手机“搜索并添加地点”当前转到美食查询，结构化编辑控件及排序/删除保存路径尚未接通。初次生成成功后没有可见的撤销入口；已有版本撤销路径只覆盖已保存后的部分变更。
- 保存前查询保持只读；本次未执行酒店/美食/动车结果写入正式行程，因此没有验证 Provider 结果选择后的实际写入流程。

## 截图

- 桌面真实 V2 工作台，1440×1000：`D:\tmp\travel-v2-screenshots-20261009\travel-v2-desktop-1440x1000.png`
- 手机真实 V2 行程页，390×844：`D:\tmp\travel-v2-screenshots-20261009\travel-v2-mobile-390x844.png`
- 原演示页截图另存为 `travel-v2-desktop-1440x1000-demo-backup.png` 和 `travel-v2-mobile-390x844-demo-backup.png`，未丢弃。

## 验证记录

- 前端定向测试：`npm test -- --run src/components/travel/v2 src/app/travel src/api/travel.test.ts src/api/travelV2.test.ts src/api/client.test.ts src/api/bff-proxy.test.ts` —— 10 个文件、71 条测试通过。
- 类型检查：`npx tsc --noEmit` —— 通过。
- Next.js 独立目录生产构建 —— 通过。
- 后端定向测试：`D:/Python/python.exe -m pytest backend/tests/api/test_travel_request_execution.py backend/tests/travel_v2/test_legacy_persistence_retired.py -q --no-cov -k "v2_stream or legacy_trip_persistence_api_routes_are_not_mounted or legacy_storage_does_not_create_retired_tables"` —— 5 通过、3 条按筛选条件排除。
- 更广的未筛选旧版与 V2 测试运行有 2 条旧版用例因测试 Plan Store 未初始化而失败（`PlanStoreUnavailable`）；不能据此声称整个后端测试集通过。
- 完整 Docker 镜像构建未通过，原因是 Debian 包镜像返回 HTTP 500/502；线上开发容器实际运行的是旧基础镜像加仅含路由改动的 overlay。

## Batch 2 后续状态

此文档保留 Batch 2 完成时的验收证据。模板数据为空、自然语言 AI 改行程等未接功能仍是已记录限制；结构化编辑已经在 Batch 3 完成，其他边界以总任务进度为准。

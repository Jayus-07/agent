# 旅游规划助手 V2 重构进度

更新日期：2026-10-10（Asia/Shanghai）

## 当前状态

Batch 1、Batch 2 与 Batch 3 均已完成。Batch 3 已通过真实登录态编辑、刷新持久化和撤销恢复验证；现在报告并暂停，等待用户确认后再进入 Batch 4。

## 工作区与运行服务

- 当前目录是 Git 仓库，`git status --short` 可运行，工作区仍有大量其他业务改动；不视为干净。本阶段仅改旅游 V2 编辑代码和任务文档，未重置或清理其他改动。此前工作区补丁备份仍保留：`D:\tmp\agent-workspace-backup-20261009-travel-v2\working-tree.patch`、`D:\tmp\agent-travel-v2-tools-20261009\working-tree.patch`。
- `http://localhost:3220/travel` 是新版 Next.js 预览；3100 的旧预览保留运行。按用户先前批准，仅以 Batch 3 V2 后端 overlay 替换 `agent-app-1`；替换前后只有该容器 ID 改变，现为 healthy。未停止 3100/3220/APISIX 或其他容器。
- 既有桌面/手机真实 V2 截图：`D:\tmp\travel-v2-screenshots-20261009\travel-v2-desktop-1440x1000.png`（1440×1000）、`D:\tmp\travel-v2-screenshots-20261009\travel-v2-mobile-390x844.png`（390×844）。原演示截图另存为 `*-demo-backup.png`。

## Batch 1：完成

- 已有 V2 首页、模板预览、聊天、桌面左 AI/中时间轴/右地图工作台、手机行程和搜索面板，均位于现有 Next.js 项目。
- 页面曾在 3220 真实登录态下浏览；上述截图指向真正 V2 行程工作台和手机详情，不是 `/travel/itineraries/demo` 演示页。
- 前端定向测试 10 个文件、71 条通过；`npx tsc --noEmit` 通过；Next.js 独立目录生产构建通过。命令详见 [Batch 2 验收记录](../travel-assistant-v2-batch-2/PROGRESS.md)。
- 视觉和功能差异已记录：模板 API 返回空列表；行程对话修改、结构化编辑、地点替换/搜索、路段交通写入仍有入口未接通；地图位置可联动但没有真实道路导航线。

## Batch 2：完成

- V2 SSE 路由复用现有旅游图并独立命名空间；V2 Trip 查询、生成后保存和 revision CAS 更新使用服务端身份上下文。
- 已登录浏览器正常刷新认证后成功读取真实 V2 API 并生成保存真实行程；未登录请求仍返回 401。没有绕过鉴权或使用演示数据充当已保存行程。
- 酒店、美食查询返回真实商户结果且酒店价格/房态未伪装；动车 Provider 本次实时查询失败，没有展示模拟车次。
- 详细测试通过/失败、服务部署边界和浏览器状态见 [Batch 2 验收记录](../travel-assistant-v2-batch-2/PROGRESS.md)。完整 Docker 依赖镜像构建因 Debian 镜像 500/502 未通过；运行容器使用了获准的 app-only overlay。

## Batch 3：完成

- 已在既有 `TripEditService` 增加结构化操作 API；操作在 Trip 行锁内基于当前文档变换，并沿用 CAS、Revision 快照与幂等记录。
- 已加入自定义活动、真实腾讯 POI 候选的添加/替换、行程项时间/停留/说明、删除、同日排序、跨日移动、增删天数、交通方式切换。
- 交通切换及变更后的新路段清除原有时长/距离/费用事实；服务端校验锁定/固定项、时间冲突、已核实营业时间，并在费用分项不足时留下预算复核提示。
- V2 工作台只在服务端保存成功后更新正式 Revision；失败不乐观更新，版本冲突读取服务器最新版本。行程加载时从现有 Revision 列表恢复上一版本撤销入口，刷新后仍可撤销。
- 定向后端测试：`pytest backend/tests/travel_v2/test_trip_edit_operations.py backend/tests/travel_v2/test_search_api.py backend/tests/travel_v2/test_api_contract.py -q --no-cov` —— 24 passed，1 条依赖弃用 warning。
- 前端：`npm test -- src/api/travelV2.test.ts src/components/travel/v2/TravelV2EditDialog.test.tsx` —— 2 files、8 tests passed；`npx tsc --noEmit` 通过。
- 生产构建：`NEXT_DIST_DIR=.next-codex-travel-v2-batch3 npm run build` —— Next.js 14.2.29 编译、静态生成 13 页和所有页面路由均通过。Next 自动向 `tsconfig.json` 添加的本次构建目录项已单独移除；已有用户配置保留。
- PostgreSQL 集成用例需 `TRAVEL_V2_TEST_POSTGRES=1` 并连接 `agent_memory`，本阶段未连接共享库，未将其报告为通过。完整 Docker 依赖构建沿用 Batch 2 的 Debian 镜像 500/502 限制；运行端只构建/替换 app overlay。

### 真实浏览器验证

- Edge 登录账户读取真实行程 `440deaaa-fcaa-5dc0-9724-7abbfee1fa30`。编辑产生 v2，刷新后仍读到 v2；通过页面恢复上一 Revision 产生 v3，行程内容恢复为原 08:30 与原交通状态。当前行程内容已恢复，Revision 按追加历史递增到 v3。
- 一次会造成既有安排时路冲突的 08:35 更新被服务端拒绝，未显示成功且正式版本未改变；有效时间修改保存并可撤销。
- 未带登录态的 `GET http://localhost:3220/api/travel/v2/trips?limit=1` 返回 401；已登录浏览器的真实行程、编辑、Revision 查询和撤销调用通过，没有绕过鉴权。
- 1440×1000 桌面和 390×844 手机视口均在真实页面检查，手机端编辑面板可打开。已有落盘截图仍是此前真实 V2 页面：`D:\tmp\travel-v2-screenshots-20261009\travel-v2-desktop-1440x1000.png`、`D:\tmp\travel-v2-screenshots-20261009\travel-v2-mobile-390x844.png`；本轮 Edge 新截图已在浏览器结果中核对，保存下载方式受浏览器 URL 协议策略阻止，未通过其他路径绕过。

## Batch 4：未开始

最终浏览器验收、保存失败重试/撤销等收口项待 Batch 3 完成并经用户确认后处理。

## 等待用户

Batch 3 报告完成。请确认后再开始 Batch 4；当前不继续自动保存收口或清理旧页面。

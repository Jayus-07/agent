# 旅游 V2 前端 FE-3

状态：实现中，待提交并创建 PR；真实 Provider E2E 因缺少认证凭据阻塞。

## 目标

基于 `a67449b07a2bc9b25ba61b01c9b336d4512842f9` 完成 FE-3A–D：前端只展示后端实际 Provider 状态、来源和查询时间；地图按验证等级展示有效点位；酒店、美食、动车的未知事实明确显示；只有用户明确选入并经服务端成功后才更新正式 Revision。

## 范围与约束

- 仅修改旅游 V2 前端与本任务跟踪文档；不修改后端、数据库、Gateway 或 Provider 接入架构。
- 复用 FE-2 的 Trip 写入守卫、CAS、Revision、幂等键、UNKNOWN 重试和 Restore；不创建或修改共享行程。
- 不伪造 Provider 结果、库存、营业状态、余票、票价、路线几何或地图定位。
- 不合并 main，不开始 FE-4；交付 PR 后等待评审。

## 验收

- 查询状态终结且可区分成功、无结果、业务失败、超时、不可用、未启用、取消和传输错误；来源与时间来自响应。
- 无 Trip 查询不写行程；有 Trip 但未选择不改变 Revision；仅服务端确认选入后更新 Revision。
- estimated 与 unknown 坐标不呈现为 verified；地图空态不假设城市；无后端路线几何时不绘制路线。
- 酒店/动车维持 not_booked，缺失库存、营业、余票、价格或有效期时不显示虚假确定值。
- 定向 Vitest、TypeScript、diff 检查和 1280×900 / 390×844 浏览器验收；Fixture 浏览器证据与真实 Provider E2E 分开报告。

## 当前工作位置

- 分支：`codex/travel-v2-fe-3`
- Worktree：`C:\Users\wh\.codex\worktrees\travel-v2-fe-3\agent`
- 起始提交：`a67449b07a2bc9b25ba61b01c9b336d4512842f9`

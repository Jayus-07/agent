# FE-3 施工进度

## 当前状态

- 代码实现与定向验证完成，待提交并创建 PR。
- 真实 Provider E2E 阻塞：本机请求 `POST /api/travel/v2/search/food` 返回 HTTP 401 `未认证：no-credential`，没有 Provider 状态、来源、结果或查询时间。未将 Fixture 浏览器验收记为真实 Provider 通过。
- FE-2 的真实持久化 E2E 仍保持未完成状态。

## 实现内容

- `TravelSearchPanel` 删除按查询类型推断 Provider 的固定来源和本机生成的查询时间。终态只消费后端的状态、来源、查询时间与验证字段；传输超时单独显示，其他请求错误不伪装成业务失败。
- 查询运行记录覆盖成功、无结果、业务失败、超时、Provider 不可用、未启用、取消及请求未完成；旧请求迟到不会改写最新结果，终态活动不再永久显示查询中。
- 酒店明确标注房态与每晚价格未知、尚未预订；美食缺少营业状态时标未知；动车展示来源、观测时间及后端验证状态，未知余票/价格不补造确定值，保存后仍为计划而非购票。
- 地图校验经纬度，缺失或未知验证等级显示为 unknown；移除空态杭州中心和虚构直线路线；已保存地点与临时候选使用不同标签。候选被选中时清除正式行程卡片选中态，从地图重新选择正式地点时同步清除候选。
- 保留 FE-2 的 Trip 写入守卫、CAS、Revision、幂等键、UNKNOWN 同键同 payload 重试及 Restore 流程；查询及查看候选不会直接写入 Trip。
- 仅修改前端和本任务跟踪文件；未触碰后端、数据库、Gateway、Provider 架构或共享行程。

## 契约依赖 / 阻塞

- 酒店搜索响应未提供房态库存来源或可订状态，因此前端显示未知；没有新增 freshness/availability 字段。
- 行程没有路线几何契约，因此地图不绘制路线。
- 当前响应没有通用有效期字段，前端只显示实际观测时间，不推断结果仍有效。
- 当前真实 Provider E2E 需要有效认证及可用 Provider。修复认证/提供授权测试凭据后，下一步用真实 BFF 请求重复查询并记录后端来源、时间及验证信息；不会用 Fixture 替代该验收。

## 验证证据

最终定向检查在 `frontend` 目录执行：

| 命令 | 结果 | 耗时 |
| --- | --- | ---: |
| `npm test -- src/components/travel/v2/TravelSearchPanel.test.tsx src/components/travel/v2/TravelSearchActivity.test.tsx src/components/travel/v2/TravelV2Map.test.tsx src/components/travel/v2/TravelV2TripWorkspace.test.tsx src/components/travel/v2/travelV2WriteState.test.ts src/api/travelV2.test.ts` | 6 个文件、59 个测试通过 | 5.81 s |
| `npx tsc --noEmit` | 通过 | 约 10 s |
| `git diff --check` | 通过 | 0.91 s |

使用已有 Microsoft Edge 执行本地 Fixture 视觉验收，没有下载浏览器依赖。查询页在 1280×900 和 390×844 检查；地图在相同桌面/手机视口检查，移动视口无横向溢出。Fixture 只证明 UI 呈现与响应式，不证明 Provider 可用：

- [桌面查询 Fixture](D:/tmp/travel-v2-fe-3-preview/travel-v2-fe-3-search-desktop-fixture.png)
- [手机查询 Fixture](D:/tmp/travel-v2-fe-3-preview/travel-v2-fe-3-search-mobile-fixture.png)
- [桌面地图 Fixture](D:/tmp/travel-v2-fe-3-preview/travel-v2-fe-3-map-desktop-fixture.png)
- [手机地图 Fixture](D:/tmp/travel-v2-fe-3-preview/travel-v2-fe-3-map-mobile-fixture.png)

## 下一步

1. 完成最终 diff 与状态检查。
2. 在 `codex/travel-v2-fe-3` 提交并推送，创建面向 `main` 的 PR。
3. PR 明确列出真实 Provider 认证阻塞和上述后端契约依赖；不合并 main、不开始 FE-4，等待评审。

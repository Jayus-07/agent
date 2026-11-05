# TASK: travel-workbench-ux3 — 旅游用户端 V2 工作台体验三连修

## 背景

用户真机反馈三个体验问题（2026-11-05）：

1. **打字机**：旅游助手回复整段一次性出现；SSE 只有 tool 结果卡片，助手基于工具结果的分析文字无流式渐显。
2. **点击交互**：点击景点无介绍、无候选；点击午餐无候选。排查结论：候选链路完好（ux2 已收进编辑弹窗并真机验证 v7/v8 落库），但桌面三点图标是纯装饰、介绍行仅 hover 可见（点击无反应、移动端完全看不到），入口形态不符预期。
3. **长提示**：「已保存…Revision 为 v2」「目的地时区尚未确认…」等大块常驻横幅太占空间，要求改成几秒自动消失的 toast。

## 决策记录（与 ux2 的关系）

- **不恢复左列候选面板**，不推翻 ux2 #2/#3「候选入口统一收进弹窗」的决策；改为「⋯ 按钮直达弹窗对应候选视图」（meal→换家餐厅、place→替换地点），一步出候选。
- **打字机用前端伪装流式**（复用 `useTypewriter`，2026-10-03 拍板口径）；不做后端真流式——Reporter 输出走 JSON 包裹+事实校验+失败回模板管线（`reporter_renderer.py`），重构风险大收益小。后端/SSE 契约零改动。
- **时区提示 toast 化 + 头部小徽标兜底**：toast 进行程只弹一次（localStorage `travel-tz-toast-shown:{tripId}` + 会话内 ref 双保险）；徽标「时区未确认」常驻可点击重看完整提示。持续性风险信号不丢。
- **SAVED 家族 5 处调用点全部 toast 化**（4500ms）；错误态（VALIDATING/SAVING/UNKNOWN/CONFLICT/FAILED/INVALID）保留常驻横幅（需承载重试/刷新按钮）。
- 其余 6 类常驻提示（conversationNotice/refreshNotice/pendingTripWrite 锁定态/savingMessage 绿条+error 红条/编辑弹窗 UNKNOWN/refreshPending 卡）不动。

## 范围

- 前端代码：`frontend/src/components/travel/`（4 个旅游组件文件 + 3 个测试文件）及 `components/shared/Toast.tsx`（a11y）。任务记录存放在本目录。
- 后端、SSE 契约、旅游 agent 链路：零改动。

## 验收标准

1. 工作台对话面板与 Live 助手面板：本会话新产生的助手终态回复打字机渐显（~2s，长文自动加速，reduced-motion 直显）；历史消息与轮询占位直显不重放。
2. 桌面/移动时间轴卡片：点击卡片=选中联动地图+介绍行选中展开；⋯ 按钮=餐食直达「换家餐厅」候选、景点直达「替换地点」候选（弹窗打开即自动查询出候选）。
3. SAVED 反馈为 toast（几秒消失，无常驻横幅）；时区问题 toast 一次+头部徽标；错误/锁定态横幅行为不变。
4. 相关单测全绿；类型检查不引入 main 基线之外的新错误。
5. 真机浏览器验证三场景（见 PROGRESS 待办）。

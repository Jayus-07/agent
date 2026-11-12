# PROGRESS：三端上下文压缩状态与质量验收

## 实现状态

- Context Budget Manager 只在真实 L5 摘要飞行启动后发出 `summary_started`；采用失败、投影校验失败或留待下一轮时发出受限原因的 `summary_deferred`。
- 主 Chat 沿用 Runner 请求级事件 sink；Travel V2 以产品、租户、用户、会话、请求建立独立作用域，字段 allowlist 拒绝正文及身份信息，seq 分配与事件入队同锁。
- 企业助手、客服、旅游助手对开始、完成、回退、未确认状态有消费逻辑；客服终态会清除 pending，旅游终态会把残留的 running 标记成未确认。完成事件即使 `saved_tokens=0` 也按成功消费，避免已采用摘要被误报为未确认。
- 用户显式禁令加入现有 ProtectedFacts 抽取与确定性补丁链路；没有更换 L5 模板、Provider、模型角色或生产模型配置。
- 企业助手服务端拒绝客户端模型覆盖；产品路由保持在企业助手边界，不生成跨助手 handoff。静态能力、MCP、工作流仍经过既有执行期产品授权。

## 本轮验证（当前工作树）

- 后端定向 T2：身份/模型覆盖、产品路由、L5 抽取与 pin、事件隔离、Travel SSE、客服流程共 128 passed（含最终否定式确认修复后的复跑）；`auto_compact.py` 与 Golden 评测脚本 `py_compile` 通过。
- 前端定向 Vitest：`navConfig.test.ts`、`travelRuntime.test.ts`、`chat-reducer.test.ts`、客服首页和旅游首页用例共 65 passed。
- RAG smoke 路径过滤和 workflow 契约：25 passed。
- `npm run build` 完整通过：Next.js 编译、类型检查、19 个静态页面生成及 build trace 均完成。
- 企业助手空态使用本地生产构建和合成 API 做过浏览器 smoke，console errors 为 0。客服与旅游首页用 Vitest 页面渲染用例验证通过；尚未对三端都做真实浏览器 smoke，因此该项不能表述为三端浏览器验收全通过。
- `git diff --check` 通过。
- PR #460 的首个提交 `29a01d7e83598521191413d2cbf2617ddf514efa` 上，自动 Review 报告两个 P2：后续正向确认仍补回旧禁令；禁令提取会丢失对象范围。已改为只把用户消息用于约束撤销判断、保留完整动作句，并新增五项回归用例，覆盖后续正向撤销、助手复述、否定式确认、范围保留和客服分离约束。
- 安全 diff scan `d363edb0-05c5-40c2-ac86-a5fefad9a9e9` 审阅的是 Review 修复前快照；后续完整 PR 区间 scan `22da767e-5a85-4ad7-9013-d629a93b8d90` 覆盖 30 个差异文件，未发现已验证安全问题。最后补充的否定式确认改动发生在 `11d2e20` 后，随新提交做增量复核。
- PR #460 首个提交的 GitHub 门禁已全部完成：Evaluation V2 免费契约、管理端构建/测试、RAG offline preflight、RAG Smoke、密钥扫描均通过；8 cases real API 评测按 PR 规则跳过。修复后的新 head 尚未推送，因此需要重新运行门禁。
- 最新核对的 `origin/main` 为 `4eaa1ad5089159b81bb622cb47384c6870e167ff`；分支已有 PR commit `29a01d7e83598521191413d2cbf2617ddf514efa` 和修复提交 `11d2e206997ee8206d3ccddfac546584233b6397`，当前另有一项待提交的否定式确认收紧。

## Golden 合成评测（2026-11-12）

- Golden 是本地脚本，直连已配置 Provider；不是管理端评测中心。评测要求 `LLM_BUDGET_MODE=enforce`，一个 T01 样本限制在同一请求预算内运行摘要、基线回答和压缩后回答，共 3 次 Provider 调用。
- 首次有效诊断发现 T01 的明确约束“不要替我预订”未进入摘要保护事实，protected fact recall 为 3/4。已在现有 ProtectedFacts 链路加入窄范围显式禁令抽取，并补单测验证确定性补回及正向请求不误判。
- 修复后重跑 T01：基线答案与 L5 后答案命中率均为 0.60；日期和禁令都在 L5 摘要中，protected fact recall 为 4/4，`constraint_violations` 为空，未触发补丁（摘要模型已保留受保护事实）。压缩没有降低本例答案分数；答案模型仍未主动复述日期和禁令，这是本例回答质量限制，不是 L5 摘要丢失。
- 修复后样本摘要 131 tokens，摘要调用用量 508 prompt + 115 completion tokens。评测报告没有输出三次调用的统一费用总额，因此不报告未经验证的金额。
- 两次成功诊断均只写入合成会话；删除前核对 owner 为 `p4-golden/agent/default`，每次删除 14 条消息和 1 个会话，并验证剩余均为 0。没有读取或改动用户业务会话。
- 初始评测种子 SQL 已改为兼容 `(product, session_id)` 唯一键，显式写入产品/租户，并在随机 ID 冲突时回滚，避免写入其他会话。新增 fake-DB 用例覆盖该边界。

## 待完成与风险

- 真实外接模型的 T01 合成评测已记录：protected fact recall 4/4，约束违规为空；T02 和 CS01 尚未执行，保持 `UNVERIFIED`。
- Docker 共享栈 `agent` 上次检查运行 21 个服务、宿主可用内存约 3.11 GB，配置同时引用多个工作树。本轮依仓库 `AGENTS.md` 未运行 `devctl.bat rebuild`；共享栈代码服务健康状态 `UNVERIFIED`。
- 安全 diff scan 未发现已验证问题；它不能代替生产环境对 APISIX 身份头、Provider、数据库和 Redis 配置的核验。
- 真实模型 T02/CS01、三端完整浏览器 smoke、本机共享 Docker 栈重建仍未完成；均保持未验证，不把 T01 或页面级测试扩大解释为全链路通过。
- `11d2e20` 之后的否定式确认收紧尚未提交；最新差异安全 scan、自动 Review 和 CI 尚未完成；未合并，也未部署。

## 下一步

1. 复核并提交最终约束判断收紧与本任务记录，推送到 PR #460。
2. 按最新提交的精确差异完成安全复核，等待 CI 与自动 Review；对新意见只修复新增差异并做对应验证。
3. 所有 GitHub 合并门禁通过后按最新 PR head 合并。共享 Docker 栈验证、T02/CS01 真实模型评测仍需单独完成或明确保持未验证。
4. 云端服务器部署不属于此次合并。

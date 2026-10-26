# 前端工程与交互契约规范

> 适用于 `frontend/`（用户端）、`frontend-admin/`（管理端）、`frontend-cs/`（客服端）。本规范定义新开发行为和验收，不宣称当前全部页面均符合。优先沿用三个独立 Next.js 应用；不因去重自动改为 monorepo、跨 Docker 构建目录或统一登录态。

## 1. 每一层只承担自己的职责

| 层 | 推荐代码入口 | 责任 | 不应该做 |
| --- | --- | --- | --- |
| Next.js Route/BFF | 各端 `src/app/api/[...path]/route.ts` | 按既有代理协议转发，服务端持有受保护凭据 | 将私密 Key 放在 `NEXT_PUBLIC_*` |
| API Client | 各端 `src/api/client.ts`、`src/api/errors.ts` | 统一 HTTP 错误、refresh、超时、幂等键、请求取消 | 每个业务页面重复实现 fetch、401 或重试 |
| Domain API | 各端 `src/api/*.ts` | 封装一组业务操作及 DTO 映射 | 持有全局鉴权规则或不必要的 UI state |
| Store / Hooks | `src/store/`、`src/hooks/` | 交互状态、会话生命周期与可取消副作用 | 信任前端状态作为服务端数据真相 |
| Components / Pages | `src/components/`、`src/app/` | 展示、可访问性、响应式交互 | 绕过 API Client 直接触发敏感业务写入 |

**数据权威**：服务端决定登录身份、权限、租户、价格、账本、旅行正式行程 revision、客服状态和写操作最终结果。表单输入、AI 草案、本地缓存、前端 optimistic state 均不能自行判定持久成功。

## 2. REST / SSE / WS / 错误状态

- 请求必须从客户端统一出口处理 `401/403/404/409/422/429/5xx` 与结构化业务 `failed/degraded/empty`；`HTTP 200` 不是业务成功。错误码及页面文案映射应集中，不直接把异常堆栈、密钥、provider raw body 展示给用户。
- **认证**：沿用本端现有 `api/client.ts` + `lib/auth.ts` + BFF 的可信边界；401 的刷新与原请求重试必须保持受控次数。对非幂等写请求不得因网络中断/刷新而盲目重投；依当前业务幂等协议核验。
- **SSE**：逐事件遵守 `backend/orchestration/graph/event_schema.py`、`backend/app/api/stream_resume.py` 和 [公共契约](../architecture/Frozen-Contracts.md)。处理 `meta/status/log/delta/done/error/paused` 等**实际声明的**核心事件（按 schema 兼容 AUX 事件）；`paused` 是用户余额不足终态，不是执行续跑；不得仅凭前端收到 `done` 而忽略终态业务错误；处理序号去重和不可恢复断流。
- **取消**：组件卸载或用户停止时取消相应读流；不要将浏览器断线当作已撤销服务器副作用。长任务以服务端 task/revision/status 查询恢复；避免多个组件竞争不同 AbortController。
- **并发**：更新使用服务端 revision/CAS 或幂等 key；冲突有可见提示，不能把前端本地最后写入覆盖正式结果。部分成功必须说明哪些信息未核验。
- **WS**：鉴权/订阅授权在后端；断线重连与去重不能跨租户收发消息。

## 3. 组件体验与可访问性

- 普通信息页有 loading / empty / error / success；写操作还必须区分 `pending_confirmation`、`saving`、`conflict`、`unknown/in_doubt`（仅在相应业务支持时使用，不新增虚假通用状态）。
- Desktop / Mobile 都需要有效操作链：键盘与焦点可达、错误关联输入、按钮禁用时可解释、长文本/流式输出不导致布局跳动。用现有设计 token 和组件，不在每页各自定义不兼容的视觉语义。
- Toast 只表示经服务器确认的写入结果；失败不能提示“保存成功”；非实时数据明确来源/更新时间。网络超时后的副作用不能自动宣称失败后重试成功。
- 三端角色不同，UI 菜单可以不同；**隐藏按钮不是权限控制**。管理端与客服端还需覆盖 viewer/editor/admin 等实际角色的入口和资源级拒绝。

## 4. 共享代码与目录整理

- 三端 `src/lib/sse-parser.ts` 等存在内容一致的文件，只构成**候选**。在决定共享包前核对消费者、构建上下文、Next standalone、TS 路径、React singleton、Docker COPY、缓存与三个应用的版本。
- 候选优先级：无状态类型/纯解析函数 → API 统一错误枚举与 DTO → 可复用展示组件；Auth、BFF 与用户/管理/坐席角色相关逻辑**默认独立**，必须有显式等价性证据才抽共享层。
- 单个旧兼容导出（例如 `lib/fetcher.ts` re-export）只有在所有引用方及构建通过、回滚方案可用时才能删除；不得以目录“看上去重复”直接清理。
- 共享代码迁移保持 API surface、事件行为、错误类型以及样式一致；大面积改目录使用 [安全重构手册](refactoring-playbook.md)。

## 5. 验收

| 编号 | 场景 | 必须通过 |
| --- | --- | --- |
| FE-01 | 三端定向类型/构建 | 改动涉及的应用能独立构建，无跨应用路径/依赖缺失 |
| FE-02 | HTTP 200 + `status=failed` | 不显示成功；错误码映射正确 |
| FE-03 | 401/403/跨租户资源 | 无越权展示或失败后循环刷新 |
| FE-04 | SSE `done/error`、乱序、断线、Abort | 终态与后端一致，无重复 delta/副作用 |
| FE-05 | 写操作 409/未知结果 | 不假成功；显示冲突或状态待核实 |
| FE-06 | 桌面与移动 | 核心流程可操作，触控、键盘、状态反馈正常 |
| FE-07 | 共享文件移动 | 全部消费者、Docker standalone 与原行为一致 |
| FE-08 | 可观测 | 不泄漏凭据；错误与 trace_id 可定位（不直接给用户展示敏感内部数据） |

测试按 [T0–T3](testing-guide.md) 选择。单纯文档/静态变更不强制安装 Chrome、Docker 或启动所有三个应用；改变 SSE/Auth、共享依赖和构建配置时必须覆盖相关端的定向测试及必要的浏览器交互。

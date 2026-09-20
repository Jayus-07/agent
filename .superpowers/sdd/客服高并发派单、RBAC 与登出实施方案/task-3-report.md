# Task 3 实施报告

## 视觉约束（实现前冻结）

- 颜色 token：`--accent/#4D6BFE` 用于主操作与可见焦点；`--accent-hover/#3B54D4` 用于悬停；`--surface/#FFFFFF` 用于内容面；`--elevated/#F8F9FB` 用于表头、输入与状态胶囊；`--text-primary/#1A1A2E` 与 `--text-secondary/#6B7280` 分别承担标题和解释。分隔线沿用现有低对比度边框，不新增 token。
- 字体/层级：沿用系统无衬线字体栈（`-apple-system, BlinkMacSystemFont, Segoe UI, Inter, Roboto`）；页面标题 20px/600，区块标题 14px/600，正文 13px/400，辅助信息 12px/400；中文文案优先保证可扫描性，不用大号营销标题。
- 布局：沿用左侧玻璃侧栏与主内容的 `max-w-7xl` 对齐；设置页采用“标题与状态摘要 → 用户/审计页签 → 表格工作区”的单列节奏，表格在窄屏保留 `overflow-x-auto`，编辑控件维持可点击尺寸。
- 视觉重点：把“当前租户的访问控制边界”做成页首的轻量摘要（管理员标识、用户总数、审计入口），而不是堆叠通用卡片；表格行的角色与客服状态使用低饱和胶囊，主色只留给保存/刷新/焦点。
- 动效与可达性：loading 仅使用轻量 shimmer/spinner，并通过 `motion-reduce:transition-none motion-reduce:animate-none` 降级；所有图标按钮有中文 `aria-label`，表单控件有可见 label，焦点环统一使用 accent 色。

## TDD 红灯记录

已先运行：

```text
npm test -- --run src/api/rbac.test.ts src/components/layout/SidebarUserMenu.test.tsx src/components/auth/RoleGate.test.tsx src/lib/auth.test.ts
Test Files 4 failed; Tests 2 failed
```

红灯证据包括：`./rbac`、`./SidebarUserMenu` 和设置页 import 缺失；`tryRefreshOnce` 未写回最新 userInfo；`logout` 未清除过期标记。这确认测试先于实现捕获了缺失模块和缺失行为。

P3 修复轮次 1 先运行：

```text
npm test -- --run src/app/layout.test.tsx src/components/layout/SidebarUserMenu.test.tsx
Test Files 2 failed; Tests 2 failed, 4 passed
```

失败分别锁定了 Sidebar 位于 QueryClientProvider 外（RootLayout 源码顺序断言）和 collapsed 菜单 `open && !collapsed` 导致 role=menu 不存在；真实 QueryClientProvider 清 cache 测试本身已证明现有菜单上下文路径可用，缺口在根布局接入。

## 实现与绿灯记录

新增实现及 P3 修复轮次 1 后运行：

```text
npm test -- --run src/app/layout.test.tsx src/api/rbac.test.ts src/components/layout/SidebarUserMenu.test.tsx src/components/auth/RoleGate.test.tsx src/lib/auth.test.ts src/components/layout/navConfig.test.ts src/api/client.test.ts
Test Files 7 passed; Tests 54 passed

ruff check frontend-admin
ruff format --check frontend-admin
All checks passed; No Python files found under the given path(s)

git diff --check
通过
```

覆盖 API 路径/query、Result 解包、版本 PATCH、HTTP 409/403 透传；viewer/editor 直达页 ForbiddenCard 且不触发请求；admin 菜单与成功/失败登出清态、query cache、WS 事件及单次导航；refresh 完整 userInfo 回写；WS logout 后关闭连接并禁止重连；导航和网络层回归。

本轮新增证据：RootLayout 顺序测试锁定 Sidebar 在 QueryClientProvider 内；真实 `QueryClientProvider` 测试写入同一 client 的 query data，点击用户菜单登出后确认该 client 已清空；collapsed 菜单测试确认 admin 访问控制、退出按钮、Escape 关闭、焦点和双击单请求/单导航。

P3 修复轮次 2 先运行 collapsed 响应式断言：

```text
npm test -- --run src/components/layout/SidebarUserMenu.test.tsx
Test Files 1 failed; Tests 1 failed, 5 passed
expected class to contain "overflow-hidden"
received "w-0 shrink-0 overflow-visible md:w-14 ..."
```

红灯正好对应移动端零宽侧栏的溢出问题；既有 collapsed admin/退出/键盘测试仍通过。

轮次 2 修复后 focused 测试：

```text
npm test -- --run src/components/layout/SidebarUserMenu.test.tsx
Test Files 1 passed; Tests 6 passed
```

新增断言锁定 `overflow-hidden md:overflow-visible`；移动端零宽侧栏裁切导航/用户菜单，md 以上保留右侧 popover 溢出。

`npx tsc --noEmit` 已运行。本次 P3 文件未产生 TypeScript 报错；命令仍被工作树已有的 `.next`/`.next-dev-*` 生成类型缺失，以及基线 `lib/fetcher.ts` 重新导出的 `createIdempotencyKey`、`mutationFetchRaw`、`MutationFetchRawOptions` 在 `api/client.ts` 中缺失所阻断。

## 两遍视觉自审

第一遍（桌面/信息层级）：页首只保留租户权限摘要，用户/审计采用同一内容面与轻分隔；平台角色、客服角色、容量与状态均按列对齐，主色只用于保存、搜索和焦点，未引入第二套主题或卡片墙。

第二遍（窄屏/状态与可达性）：表格使用 `min-w` + 横向滚动；loading、错误、空态和 409 均有中文文案/重试出口；按钮、select、checkbox、分页和 tab 均有 label/`focus-visible`；spinner/shimmer 和过渡均提供 `motion-reduce` 降级；用户菜单的 admin 入口与页内 RoleGate 双重收口。轮次 2 复查响应式边界：移动端 `overflow-hidden` 防止 `w-0` 侧栏外溢，md 以上 `overflow-visible` 保留右侧 popover，collapsed 的 admin/退出/Escape/焦点行为保持不变。敏感的 token、密码字段和 `agentId` 未渲染。

## 改动文件

- `frontend-admin/src/api/rbac.ts`、`frontend-admin/src/api/rbac.test.ts`
- `frontend-admin/src/app/settings/access/page.tsx`
- `frontend-admin/src/app/layout.tsx`、`layout.test.tsx`
- `frontend-admin/src/components/auth/RoleGate.test.tsx`
- `frontend-admin/src/components/layout/SidebarUserMenu.tsx`、`SidebarUserMenu.test.tsx`、`Sidebar.tsx`、`navConfig.tsx`、`navConfig.test.ts`
- `frontend-admin/src/lib/auth.ts`、`auth.test.ts`、`csAgentWs.ts`
- 本报告

## 风险与未验证项

- `npx tsc --noEmit` 的阻断项来自当前工作树生成目录和既有 fetcher/client 导出漂移，未修改这些非 P3 文件；合并前需先清理生成目录并恢复基线导出，再复跑类型检查。
- `npx tsc --noEmit` 仍被既有 `.next`/`.next-dev-*` 生成类型缺失与 `lib/fetcher.ts`/`api/client.ts` 导出漂移阻断；本轮新增文件未出现额外 TypeScript 报错。
- 未运行真实浏览器/网关；登录页、task mode 与窄屏折叠弹层的最终像素布局仍需部署前 smoke check。
- 未运行整套测试、后端测试或真实网关；本任务只验证指定管理端测试与静态实现。

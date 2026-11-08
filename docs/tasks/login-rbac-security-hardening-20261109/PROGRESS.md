# PROGRESS — 登录/RBAC 收尾与安全专项

## 合并记录

| PR | 内容 | 状态 |
|---|---|---|
| #387 | 登录加固：非法 JSON 400、密码策略、端准入、XFF Cookie 限流、随机体验账号 | merged |
| #388 | 注册页随机账号布局压缩 | merged |
| #389 | 管理员建号 11 位 + `ops_`/`svc_` 豁免；删除 `/ops` | merged |
| #390 | 观测/清单/模型端点鉴权（rag-traces、tokens、graph、gateway-access-logs、system-health、capabilities、llm） | merged |
| #391 | SQL CTE 裸名匹配修复（schema 限定表不再被别名遮蔽） | merged |
| #392 | 数据采集 SSRF（逐跳重定向校验）+ `target_table` 标识符校验 | merged |
| #393 | 仅 super_admin 可重置/强制下线 super_admin | merged |
| #394 | FAQ 按租户隔离（迁移 142，`(tenant_id, faq_key)` 唯一） | merged |
| #395 | PDF 页数/面积/OCR 渲染像素预算（XML 已有节点/深度预算） | merged |

## 验证证据

- 认证：`test_auth_policy_and_json.py` 6 passed；登录端准入接口实测 200/403。
- 建号：`test_rbac_user_lifecycle.py` 1 passed；实测 11 位/`ops_` 200、普通名 400。
- 观测鉴权：结构化依赖测试 10 passed；匿名实测 403、带身份 200。
- SQL CTE：`test_sql_scope_subquery` 等 16 passed；SQL 策略回归 141 passed。
- SSRF/表名：新增 4 passed；data_collection 回归 43 passed。
- 超级管理员保护：2 passed。
- FAQ：客服知识/缺口回归 10 passed；新增同问题双租户隔离用例。
- PDF/XML：预算测试 4 passed；PDF/OCR/XML 结构回归 62 passed。

## RAG 生命周期/租户结论

现有守卫已覆盖，未发现需改代码的缺口：

- 向量检索租户/权限谓词（`pgvector_store`，缺字段按 default 兼容）；
- 审核可见性过滤（pending_review/deprecated/deleted，双保险）；
- 发布 CAS（`register_published` 的 `expected_base_generation` 守卫）；
- 注册表租户过滤（`list_active`/`search`/`distinct_permission_scopes`）。

相关测试：`test_candidate_publish`、`test_review_filter`、`test_doc_registry_pg` 等通过；`test_rag_authz` 本机 9 条失败是 KB 注册表被客服种子污染的已知环境噪音（CI 隔离库通过）。

## 已知残留

1. 生产 ECS 尚未部署本批改动；发布前需跑全量发布门禁并做发布后观察。
2. 本机 git 到 github.com 的 push 多次中断，部分分支经 GitHub Git Data API 创建；网络恢复后需 `git fetch` 对齐本地 refs。
3. FAQ 迁移 142 已注册 `scripts/init_db.py`；生产发布时由迁移流程应用。
4. XFF 限流在无可信前置代理时仍可用清 Cookie 绕过；上代理后设 `TRUST_PROXY_HEADERS=true`。
5. `/llm/balance` 保持登录可读（客服工具栏依赖）；如要仅管理员可见需同步隐藏组件。

## 下一步

1. 生产发布与发布后观察（owner-observation）。
2. 按风险排期审计其余线索（SSRF 之外：RAG 扩展、部署面、CI 权限等）。
3. MFA、多租户 UI、选品 owner 授权按产品节奏推进。

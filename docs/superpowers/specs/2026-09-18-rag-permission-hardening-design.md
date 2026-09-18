# RAG 权限隔离收口设计

## 目标

把 RAG 的权限边界统一到可信请求身份上，覆盖 KB 级授权、文档级 `permission_scope`、问答/轻量检索/原始搜索/远程服务和管理面，默认 fail-closed。

## 设计

1. 网关从 JWT 的可选 `permissions` claim 注入 `X-User-Permissions`；客户端伪造头先剥离。缺少该 claim 时权限集合为 `None`，`general` 文档可见，受限文档拒绝。
2. app 的身份解析把 user、department、permissions 组合成统一 RAG 授权上下文；`/rag/*` 不再使用客户端提交的身份字段。主 Agent、远程代理和 rag-service 使用同一请求模型。
3. 文档过滤下沉到公共函数，所有 RAG 检索出口在返回或生成前执行：向量/BM25、parent/adaptive/neighbor fallback、`retrieve_knowledge`、`/rag/search` 和问答链。
4. 权限过滤异常直接返回空证据并记录指标，不退化为放行；问答缓存 key 包含规范化权限集合。
5. RAG 管理接口要求 JWT 用户，写操作和文档管理要求 admin/editor 角色；健康检查、readyz 和内部令牌边界保持不变。

## 非目标

- 本次不新增权限管理后台或数据库权限表。
- 不改变既有 `general` 文档语义。
- 不覆盖工作树已有的无关改动。

## 验收

- 未授权 KB 不出现在任何检索结果。
- 未持有 `permission_scope` 的文档不会进入 LLM、raw search、BM25/vector 轻量检索或远程 retrieve 返回值。
- 过滤器异常不放行证据。
- 不同权限集合不能复用同一答案缓存。
- 相关 RAG 和一致性测试通过。

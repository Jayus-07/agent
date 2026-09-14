# Capability Manifest 机制（存档，取自提交 01a3329）

本目录是「capability 声明唯一事实源 + fail-fast」机制的完整存档。
原实现曾落在 `backend/orchestration/router/`，2026-09-15 凌晨随架构重构
从工作区移除；提交 `01a3329` 里永久可查。

## 文件

| 文件 | 内容 |
|---|---|
| `capabilities.yaml` | 路由声明唯一事实源：12 skill / 14 capability / 2 workflow。`routed: true` 强制 ≥2 条向量路由 examples；`routed: false`（内部能力）强制写 reason |
| `manifest.py` | 加载器 + fail-fast 校验（重名 / examples 不足 / 缺 reason / 坏版本号 → `ManifestError` 启动即炸） |
| `wiring-01a3329.patch` | 当时的接线 diff：types.py 派生 `ALL_CAPABILITIES`、vector_router.py 派生 `ROUTE_EXAMPLES` + Chroma 索引数量对账自愈、test_registry_consistency 三方双向对账测试 |

## 核心思想（新架构可参考）

1. **capability 名单 / 路由 examples / workflow 清单不许手写散落多处**——
   全部由 YAML 派生，漏改一处的「静默失效」从机制上杜绝
   （当时修复的实证 bug：`competitor.analyze` 已注册却会被 LLM Router 拒绝）。
2. **启动期 fail-fast**：manifest 非法宁可不起服务。
3. **索引自愈**：Chroma 路由索引条数与 manifest 对账，不符自动重建。
4. **测试锁死链路**：manifest ↔ skills 注册表 ↔ 派生量双向对账。

## 如何在新架构接入

1. 把 `capabilities.yaml` + `manifest.py` 放回路由模块所在位置；
2. 参照 `wiring-01a3329.patch` 把派生逻辑接进新架构的
   capability 清单 / 向量路由 / LLM 路由校验；
3. 同步移植 `TestCapabilityManifest` 测试组（在 patch 内）；
4. YAML 里的 capability 清单按新架构现状修订（examples 是给向量路由的种子，
   质量直接决定路由准确率，建议每条 5-10 个真实问法）。

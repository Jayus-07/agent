# Phase 2 Completion Report — Requirement Agent 职责边界建立

> 格式依用户施工军规 §十二（八项）。基线：Phase 1（`3077c76`）+ 调整后设计报告（`5c15d3f`，用户五点最终调整全部采纳）。

---

## 1. Verdict

**PASS**（附一项如实披露的回归缺口：booking 分块宿主机挂死，见 §5/§6——booking 目录 Phase 2 零触碰，不影响本阶段改动面的验证结论）。

## 2. 修改文件

**Commit A `242430d`（7 文件，+990/-660）**：

| 文件 | 操作 | 内容 |
|---|---|---|
| `travel/agents/__init__.py` | 新增 | Agent 包声明（禁止反向依赖 graph_builder/orchestration） |
| `travel/agents/requirement_agent.py` | 新增（660 行） | RequirementAgent：13 槽确定性抽取真身 + `extract_fresh_brief`（新抽，不含 merge）+ 追问文案 + adults/children 拆分 + `_explicit_total_party` 显式总数污染排除；零 LLM（扩展位仅文档说明） |
| `travel/services/requirement_service.py` | 新增（125 行） | RequirementService：merge_brief（+adults/children 派生维护）/指纹（包装 graph_state 单一事实源）/detect_brief_change（版本+变更字段追踪）；KNOWN_MAJOR_CITIES/filter_city_names 共享件（过渡，禁止堆积，Phase 3 抽离） |
| `travel/core/intent_signals.py` | 新增（55 行） | is_new_run_query / is_cancel_run_query 真身（正则与保守判定逐字搬运）；is_avoid_patch_query 不在此（文档写明过渡归属） |
| `travel/slot_filler.py` | 改造 844→318 行 | 图节点编排保留 + 兼容入口：显式 re-export 24 个历史符号（含私有 `_RE_DATE_ISO`，commerce/extract 消费）+ `extract_brief` 组合器（agent 产新→service 合并）+ is_avoid_patch_query 过渡真身 |
| `travel/models/brief.py` | 修改 | +adults/children 可选字段（显式表达才存；party_size 派生语义由 Agent/Service 唯一写点维护；模型不做自动派生以兼容存量 checkpoint 反序列化；不参与指纹） |
| `tests/travel/test_travel_core_boundary.py` | 修改 | core 文件集有意更新（+intent_signals.py） |

**Commit B `df36bca`（1 文件，+318）**：`tests/travel/test_requirement_agent_contract.py` 57 例契约测试。

## 3. 为什么这样改

对应用户重新校准：**Phase 2 目标 = 建立职责边界，不是代码搬迁**。故不做 git mv 整体搬迁，而是按职责三分：

- **RequirementAgent（理解层）**：只回答"用户这轮说了什么"——抽取/缺失检测/TripBrief 生成/追问，无状态、零 LLM、不碰 state 与 memory；
- **RequirementService（支撑层）**：只回答"怎么与上一轮合并、需求变没变、版本怎么走"——merge/指纹/变更追踪，指纹经 graph_state 单一事实源包装（G2 不复制）；
- **slot_filler.py（节点编排+兼容）**：LangGraph 节点 `travel_slot_filler` 名与注册完全不动（test_agent_inventory 钉住），编排体（基底选择/持久化/planning_reset 三分支/notes）逐字保留，历史符号 re-export 保证 orchestration 三处 lazy import 与全部存量测试零改动。

planning.py 本期零触碰（用户禁令；Phase 1 遮蔽防护测试无需翻转）；不加任何 LLM 开关；memory schema 零扩展。

## 4. 冻结契约对应关系

| v4 冻结条目 | 本期落地 |
|---|---|
| ② Requirement Agent 职责边界（v3 §3.1） | ✅ agents/services/node 三层拆分，类契约+模块函数双公开面 |
| 确定性优先 / 零 LLM（v4 §9.2 LLM 预算=0） | ✅ 无任何 LLM import（静态扫描锁定）、无代码接口（llm_supplement 按用户令删除）、无配置开关；扩展硬约束只写在文档 |
| Memory 横向能力、偏好 memory ≠ checkpoint ≠ plan state（v4 §7） | ✅ 预填/回写留在节点体、schema 零扩展；契约测试 spy 断言 upsert 只收 origin/preferences/pace/diet 四稳定字段，一次性字段（destination/days/budget/must_go/lodging）绝不泄漏 |
| TripBrief 契约含 adults/children（v4 §3.1 TripBrief v2） | ✅ 字段落地 + party_size 派生 + D4 修复；指纹零改动（graph_state 一行未碰，经 party_size 传导） |
| 意图层素材归 Supervisor（v3 §5.3 action 枚举） | ✅ is_new_run/is_cancel 真身迁 core/intent_signals（自包含）；is_avoid_patch 过渡留守（core 不得反向依赖 agents），Phase 5 重估 |
| Tool 治理不新增（v4 §5） | ✅ 未注册任何新 capability/ToolSpec 变更 |
| 生产资产零删改（军规 §一） | ✅ validator/repair/providers/booking/graph_state/planning.py 零 diff；图节点数 10 不变 |

## 5. 测试结果（全部实跑）

| 分块 | 结果 |
|---|---|
| 契约测试（Commit B） | **57 passed** |
| test_slot_filler(49) + slot_patch_boundary(20) + planning_contract(16) + cancel_lifecycle(9) + pending_resolver(16) + core_boundary(4) | 131 passed（首跑暴露漏网消费方 `_RE_DATE_ISO`，补 compat 后全绿） |
| scenarios + user_decision + run_context | 74 passed |
| travel_graph + persistence + checkpointer + versioning + p0_mvp | 108 passed |
| 一致性三门(37) + node_runtime(40) + agent_inventory + router_prefilter_order | 104 passed |
| 金标(34) + validator(32) + quality_metrics + commerce_routing | 65 passed |
| commerce/ 全套 | **111 passed（12s）** |
| **合计** | **约 650 例全绿** |
| booking/（39 例，真 PG） | ⚠️ **宿主机两次挂死**（前后台均无输出超 25/7 分钟）——booking 目录 `git diff` 为空（Phase 2 零触碰），挂死属本机已知问题（travel 区宿主机挂死纪律有档）；留待容器内第二路径或 Phase 8 收口执行，不阻塞本阶段 |

**过程中发现并修复的额外问题**：① `travel/commerce/extract.py` import 私有符号 `_RE_DATE_ISO`（初版审计 grep 被 head 截断漏网）→ 补入兼容面；② 存量潜伏 bug「大人两位带孩子一个」：通用人数正则把「两位」误读为总人数、同伴兜底又对「带孩子」+1，两路污染导致总数漏计 → `_explicit_total_party` 对被成人/儿童表达吸收的数字段做污染排除（PARTY/FAMILY_KOU/COMPANION 三层同规则）。

## 6. 未完成事项

1. booking 39 例回归（宿主机挂死，待容器第二路径）——**不影响本阶段改动面结论**；
2. is_avoid_patch_query 过渡留守 slot_filler.py（Phase 5 生命周期重构时随 avoid 通道重估归位）；
3. KNOWN_MAJOR_CITIES/filter_city_names 暂居 requirement_service.py（Phase 3 Research Agent 统一抽离；已加"禁止堆积"注释约束）；
4. D3（路由词表）/D5（节奏慢一点）词表补丁移出本期（用户校准），待独立批次；
5. 仅儿童显式（"带1个小孩"无成人数）时 party_size 不派生（回落默认，避免与同伴 guess 口径冲突）——已测锁定，未来如需改为"本人+1+披露"另立项；
6. slot 评测集（≥50 例 95/99/98 硬指标）随 LLM 二层批次（Phase 2 校准后不含 LLM，评测集顺延）。

## 7. 风险

| 风险 | 级 | 状态 |
|---|---|---|
| 委托拆分行为漂移 | 高 | 已消解：parity 逐字段测试（5 词料×新旧路径）+ 约 650 例存量回归全绿 |
| 兼容面漏符号（运行时才炸） | 高 | 已消解：`__all__` 24 符号 + 逐符号可解析测试 + `_RE_DATE_ISO` 实战暴露并修复 |
| adults/children 与显式总数冲突 | 中 | 已消解：显式总数优先 + `_explicit_total_party` 污染排除，契约测试覆盖冲突/倒装/仅儿童/同伴四类边界 |
| 指纹口径漂移导致存量会话重排 | 中 | 已消解：graph_state 零改动（测试断言指纹单一事实源 + children 变化不影响指纹） |
| booking 回归缺口 | 低 | 目录零 diff + 挂死归因本机已知问题；Phase 8 全量收口兜底 |
| is_avoid_patch 过渡位置被误当最终归属 | 低 | 模块 docstring + 契约测试双重标记"过渡、Phase 5 重估" |

## 8. Commit

```
242430d feat(travel): Phase2 建立Requirement Agent职责边界——Agent/Service/Node三层拆分（零行为变更+D4修复）
df36bca test(travel): Phase2 Requirement Agent 契约测试57例
```

**下一阶段**：Phase 3（五专家 service 化 + Research/Planning/Optimization 三 Agent 包裹 + supervisor stage 重映射）——按军规不提前进入，待用户指令。

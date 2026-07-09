# Seed Data Framework 设计文档

> **版本**: v0.1.0 (PR-1)
> **日期**: 2026-07-02
> **状态**: PR-1 已完成 — 核心框架 + Master Data 生成器

---

## 1. 背景与动机

项目当前没有任何数据生成框架。所有测试/演示数据都是硬编码在三个 demo 脚本中，各自创建临时表、插入固定行、用完销毁。随着跨境电商业务领域模型的引入，需要一套可扩展的数据生成框架来支撑 SQL Agent、RAG、Report Agent 及未来业务 Agent 的演示和测试。

## 2. 设计目标

1. **可扩展** — 新增实体类型只需添加 Generator，不需改框架代码
2. **可复现** — 基于 seed 的确定性随机，同一 seed 两次运行结果完全一致
3. **profile 驱动** — 数据规模通过 YAML 配置，不在代码中硬编码
4. **验证内置** — 生成后自动检查引用完整性、数量级、业务规则

## 3. 架构

```
Profile (YAML) → Context (状态) → Generator → Factory → Validator
                                              ↓
                                          Exporter → JSON / Dict / PostgreSQL
```

### 3.1 核心抽象

| 组件 | 职责 |
|---|---|
| **Generator** | 生成单条实体的原始属性（dict），不含 FK |
| **Factory** | 调用 Generator + 解析 `$ref` 占位符 → 组装最终实体 |
| **Context** | 持有 RNG/Faker、已生成实体注册表、FK 引用解析 |
| **Profile** | YAML 驱动的规模配置（实体数量、分布参数） |
| **Validator** | 组合多个验证器（引用完整性、数量级、业务规则） |
| **Exporter** | 输出适配（JSON 文件 / Python dict / PostgreSQL） |

### 3.2 FK 引用机制

Generator 产出 FK 字段用 `$ref:entity:index` 占位符，Factory 统一解析为 Context 中已注册实体的实际 ID。

```python
# Generator 产出（不做 FK）
{"sku_id": "SKU-001", "product_id": "$ref:product:3"}
# Factory 解析后
{"sku_id": "SKU-001", "product_id": "P0003"}
```

### 3.3 Profile 配置

Profile 用 YAML 声明式定义：
- `entities`: 每个实体的生成数量
- `distributions`: 非均匀分布参数（订单状态、季节因子、仓库分配等）

已实现的 Profile:
- `tiny.yaml` — CI 快速验证（< 5 秒）
- `mvp.yaml` — MVP 演示 + 集成测试

## 4. PR-1 交付物

### 目录结构

```
seed_data/
├── __init__.py, __main__.py, cli.py
├── core/          # generator, factory, context, profile, validator
├── profiles/      # tiny.yaml, mvp.yaml
├── generators/    # master_data.py (Brand, Category, Channel, Warehouse, Supplier)
├── validators/    # referential.py, volume.py
├── exporters/     # json_file.py, python_dict.py
└── utils/         # constants.py, distributions.py
```

### 测试覆盖

- `tests/test_seed_framework.py` — 38 个核心框架测试
- `tests/test_seed_master_data.py` — 17 个生成器测试

### CLI 使用

```bash
# CI 快速验证
python -m seed_data --profile tiny --export json --validate

# MVP 演示
python -m seed_data --profile mvp --export json --output data/seed/mvp/
```

## 5. 后续 PR 规划

| PR | 内容 | 新增 Generator |
|---|---|---|
| PR-2 | 商品域 + 知识域 | Product, SKU, Listing, KnowledgeDoc |
| PR-3 | 订单域 + 客户域 + 库存域 | Order, Customer, Inventory |
| PR-4 | 物流域 + 广告域 + 报表域 | Logistics, Advertising, Report |

## 6. 设计决策记录

1. **dict-based** — 整个框架操作 Plain Python dict，不绑定 ORM
2. **YAML Profile** — 配置驱动，非代码 if-else
3. **$ref 占位符** — Generator 不关心 FK，Factory 统一解析
4. **不依赖 Factory Boy** — 手写 Generator/Factory，更灵活
5. **先 JSON/Dict 后 PG** — PR-1 不引入数据库依赖

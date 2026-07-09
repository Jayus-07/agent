"""Seed Data Framework — 端到端演示脚本。

替代原有三个分散 demo（sql_agent/demo_sql_agent.py, report_agent/demo_report_agent.py, multi_agent/demo.py）。

用法:
    python -m seed_data.demo_all --profile mvp
    python -m seed_data.demo_all --profile tiny --skip-export
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from seed_data.core.context import GenerationContext
from seed_data.core.profile import SeedProfile
from seed_data.core.validator import SeedValidator
from seed_data.generators.master_data import (
    BrandGenerator, CategoryGenerator, ChannelGenerator,
    WarehouseGenerator, SupplierGenerator,
)
from seed_data.generators.product import (
    ProductGenerator, SkuGenerator, ListingGenerator,
)
from seed_data.generators.knowledge import (
    KnowledgeDocGenerator, KnowledgeChunkGenerator,
)
from seed_data.generators.customer import (
    CustomerGenerator, CustomerAddressGenerator, ReviewGenerator,
)
from seed_data.generators.order import (
    OrderGenerator, OrderItemGenerator, OrderEventGenerator,
)
from seed_data.generators.inventory import (
    InventoryLevelGenerator, InventoryTransactionGenerator,
    InventoryHealthGenerator,
)
from seed_data.generators.logistics import (
    FreightBookingGenerator, ShipmentGenerator,
    TrackingEventGenerator, ReturnAuthorizationGenerator,
)
from seed_data.generators.advertising import (
    AdAccountGenerator, CampaignGenerator, AdGroupGenerator,
    AdGenerator, SpendRecordGenerator, PerformanceMetricGenerator,
)
from seed_data.generators.report import (
    ReportDefinitionGenerator, ReportExecutionGenerator,
)
from seed_data.validators.referential import ReferentialValidator
from seed_data.validators.volume import VolumeValidator
from seed_data.validators.business_rules import BusinessRuleValidator
from seed_data.exporters.json_file import JsonFileExporter


def main():
    parser = argparse.ArgumentParser(description="Seed Data 端到端演示")
    parser.add_argument("--profile", "-p", default="tiny",
                        choices=["tiny", "mvp"])
    parser.add_argument("--skip-export", action="store_true",
                        help="不导出 JSON，仅验证")
    args = parser.parse_args()

    profile = SeedProfile.from_name(args.profile)
    print(f"Profile: {profile.name} ({profile.description})")
    print(f"实体类型: {len(profile.entities)}")
    print(f"预估总量: {profile.total_entities:,}")
    print()

    ctx = GenerationContext(profile, seed=profile.seed)

    # 所有 Generator（按依赖顺序）
    all_gens = [
        ("Master", BrandGenerator(ctx.rng, profile)),
        ("Master", CategoryGenerator(ctx.rng, profile)),
        ("Master", ChannelGenerator(ctx.rng, profile)),
        ("Master", WarehouseGenerator(ctx.rng, profile)),
        ("Master", SupplierGenerator(ctx.rng, profile)),
        ("Product", ProductGenerator(ctx.rng, profile)),
        ("Product", SkuGenerator(ctx.rng, profile)),
        ("Knowledge", KnowledgeDocGenerator(ctx.rng, profile)),
        ("Product", ListingGenerator(ctx.rng, profile)),
        ("Knowledge", KnowledgeChunkGenerator(ctx.rng, profile)),
        ("Customer", CustomerGenerator(ctx.rng, profile)),
        ("Customer", CustomerAddressGenerator(ctx.rng, profile)),
        ("Order", OrderGenerator(ctx.rng, profile)),
        ("Order", OrderItemGenerator(ctx.rng, profile)),
        ("Order", OrderEventGenerator(ctx.rng, profile)),
        ("Inventory", InventoryLevelGenerator(ctx.rng, profile)),
        ("Inventory", InventoryTransactionGenerator(ctx.rng, profile)),
        ("Inventory", InventoryHealthGenerator(ctx.rng, profile)),
        ("Customer", ReviewGenerator(ctx.rng, profile)),
        ("Logistics", FreightBookingGenerator(ctx.rng, profile)),
        ("Logistics", ShipmentGenerator(ctx.rng, profile)),
        ("Logistics", TrackingEventGenerator(ctx.rng, profile)),
        ("Logistics", ReturnAuthorizationGenerator(ctx.rng, profile)),
        ("Ads", AdAccountGenerator(ctx.rng, profile)),
        ("Ads", CampaignGenerator(ctx.rng, profile)),
        ("Ads", AdGroupGenerator(ctx.rng, profile)),
        ("Ads", AdGenerator(ctx.rng, profile)),
        ("Ads", SpendRecordGenerator(ctx.rng, profile)),
        ("Ads", PerformanceMetricGenerator(ctx.rng, profile)),
        ("Report", ReportDefinitionGenerator(ctx.rng, profile)),
        ("Report", ReportExecutionGenerator(ctx.rng, profile)),
    ]

    t0 = time.time()
    domain_counts = {}

    for domain, gen in all_gens:
        if not profile.has_entity(gen.entity_name):
            continue
        entities = gen.generate_many(ctx)
        ctx.register_batch(gen.entity_name, entities)
        domain_counts[domain] = domain_counts.get(domain, 0) + len(entities)

    # 解析 FK
    refs = ctx.resolve_all_refs()

    elapsed = time.time() - t0

    # 打印摘要
    print("=" * 60)
    print(f"{'领域':<15} {'实体数':>8}")
    print("-" * 30)
    for domain in ["Master", "Product", "Knowledge", "Customer", "Order",
                    "Inventory", "Logistics", "Ads", "Report"]:
        cnt = domain_counts.get(domain, 0)
        if cnt:
            print(f"  {domain:<13} {cnt:>8,}")
    print("-" * 30)
    total = sum(domain_counts.values())
    print(f"  {'总计':<13} {total:>8,}")
    print(f"  FK 解析: {refs:,} 个")
    print(f"  耗时: {elapsed:.2f}s")
    print()

    # 验证
    print("=" * 60)
    print("数据验证")
    print("-" * 30)
    validator = SeedValidator([
        ReferentialValidator(),
        VolumeValidator(tolerance=0.5),
        BusinessRuleValidator(),
    ])
    results = validator.validate_all(ctx)
    all_ok = True
    for r in results:
        status = "PASS" if r.is_valid else "FAIL"
        print(f"  [{status}] {r.validator_name}")
        for err in r.errors[:5]:
            print(f"         ERR: {err}")
        if len(r.errors) > 5:
            print(f"         ... +{len(r.errors) - 5} 个错误")
        if not r.is_valid:
            all_ok = False

    if all_ok:
        print("\n  所有验证通过!")
    else:
        print("\n  [WARN] 存在验证错误")

    # 导出
    if not args.skip_export:
        print()
        print("=" * 60)
        exporter = JsonFileExporter(f"data/seed/{args.profile}")
        exporter.export(ctx)
        print(f"JSON 已导出到 data/seed/{args.profile}/")
        print(f"  {len(ctx.entity_names)} 个实体类型, {total:,} 条数据")

    print()
    print("=" * 60)
    print("演示场景:")
    print("-" * 30)
    print("  SQL Agent:   SELECT * FROM seed_order WHERE status='DELIVERED'")
    print(f"               → {ctx.count('order')} 条订单")
    print("  RAG:         检索 knowledge_doc 全文")
    print(f"               → {ctx.count('knowledge_doc')} 篇文档, {ctx.count('knowledge_chunk')} 个分块")
    print("  Report:      经营分析报表")
    print(f"               → {ctx.count('report_definition')} 个模板, {ctx.count('report_execution')} 次执行")
    print(f"  Multi-Agent: 跨域查询 (Order × Customer × Product × Inventory)")
    print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

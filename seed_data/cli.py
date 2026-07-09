"""Seed Data CLI — 命令行入口。

用法:
    python -m seed_data --profile tiny --export json
    python -m seed_data --profile mvp --export json --output data/seed/mvp/
    python -m seed_data --profile mvp --export dict --validate
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
    BrandGenerator,
    CategoryGenerator,
    ChannelGenerator,
    WarehouseGenerator,
    SupplierGenerator,
)
from seed_data.generators.product import (
    ProductGenerator,
    SkuGenerator,
    ListingGenerator,
)
from seed_data.generators.knowledge import (
    KnowledgeDocGenerator,
    KnowledgeChunkGenerator,
)
from seed_data.generators.customer import (
    CustomerGenerator,
    CustomerAddressGenerator,
    ReviewGenerator,
)
from seed_data.generators.order import (
    OrderGenerator,
    OrderItemGenerator,
    OrderEventGenerator,
)
from seed_data.generators.inventory import (
    InventoryLevelGenerator,
    InventoryTransactionGenerator,
    InventoryHealthGenerator,
)
from seed_data.generators.logistics import (
    FreightBookingGenerator,
    ShipmentGenerator,
    TrackingEventGenerator,
    ReturnAuthorizationGenerator,
)
from seed_data.generators.advertising import (
    AdAccountGenerator,
    CampaignGenerator,
    AdGroupGenerator,
    AdGenerator,
    SpendRecordGenerator,
    PerformanceMetricGenerator,
)
from seed_data.generators.report import (
    ReportDefinitionGenerator,
    ReportExecutionGenerator,
)
from seed_data.validators.referential import ReferentialValidator
from seed_data.validators.volume import VolumeValidator
from seed_data.validators.business_rules import BusinessRuleValidator
from seed_data.exporters.json_file import JsonFileExporter
from seed_data.exporters.python_dict import DictExporter


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed Data Framework — 跨境电商业务数据生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m seed_data --profile tiny --export json
  python -m seed_data --profile mvp --export json --output data/seed/mvp/
  python -m seed_data --profile mvp --export dict --validate
        """,
    )
    parser.add_argument(
        "--profile", "-p",
        default="tiny",
        choices=["tiny", "mvp", "medium", "full"],
        help="数据规模 Profile（默认: tiny）",
    )
    parser.add_argument(
        "--export", "-e",
        default="json",
        choices=["json", "dict", "postgres"],
        help="导出格式（默认: json）",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="JSON 输出目录（默认: data/seed/{profile}/）",
    )
    parser.add_argument(
        "--validate", "-v",
        action="store_true",
        help="生成后执行数据验证",
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=None,
        help="随机种子（默认: 从 Profile 读取）",
    )
    parser.add_argument(
        "--sync-vectordb",
        action="store_true",
        help="将 knowledge_doc 直写 ChromaDB 向量库（data/chroma/ + data/doc_db/）",
    )
    return parser


def run_generators(ctx: GenerationContext) -> None:
    """运行所有 PR-1 阶段的 Generator（Master Data）。"""
    profile = ctx.profile
    rng = ctx.rng

    # 生成顺序按依赖关系（严格遵守拓扑序）
    all_generators = [
        # 层级 0: Master Data（无 FK）
        BrandGenerator(rng, profile),
        CategoryGenerator(rng, profile),
        ChannelGenerator(rng, profile),
        WarehouseGenerator(rng, profile),
        SupplierGenerator(rng, profile),
        # 层级 1: 引用层级 0
        ProductGenerator(rng, profile),
        # 层级 2: 引用层级 1 + Customer（独立）
        SkuGenerator(rng, profile),
        CustomerGenerator(rng, profile),
        KnowledgeDocGenerator(rng, profile),
        # 层级 3: 引用层级 2
        ListingGenerator(rng, profile),
        KnowledgeChunkGenerator(rng, profile),
        CustomerAddressGenerator(rng, profile),
        # 层级 4: Order 域（引用 Customer, Channel, SKU）
        OrderGenerator(rng, profile),
        OrderItemGenerator(rng, profile),
        OrderEventGenerator(rng, profile),
        # 层级 5: Inventory（引用 SKU, Warehouse, Order）
        InventoryLevelGenerator(rng, profile),
        InventoryTransactionGenerator(rng, profile),
        InventoryHealthGenerator(rng, profile),
        # 层级 6: Review（引用 Customer, SKU, Order）
        ReviewGenerator(rng, profile),
        # 层级 7: Logistics（引用 Supplier, Warehouse, Order）
        FreightBookingGenerator(rng, profile),
        ShipmentGenerator(rng, profile),
        TrackingEventGenerator(rng, profile),
        ReturnAuthorizationGenerator(rng, profile),
        # 层级 8: Advertising（引用 AdAccount → Campaign → AdGroup → Ad → SKU）
        AdAccountGenerator(rng, profile),
        CampaignGenerator(rng, profile),
        AdGroupGenerator(rng, profile),
        AdGenerator(rng, profile),
        SpendRecordGenerator(rng, profile),
        PerformanceMetricGenerator(rng, profile),
        # 层级 9: Report
        ReportDefinitionGenerator(rng, profile),
        ReportExecutionGenerator(rng, profile),
    ]

    for gen in all_generators:
        entity_name = gen.entity_name
        if not profile.has_entity(entity_name):
            print(f"  [SKIP] {entity_name} — Profile 中未配置")
            continue

        print(f"  [GEN ] {entity_name} ...", end=" ", flush=True)
        t0 = time.time()
        entities = gen.generate_many(ctx)
        # 注册到 Context（generate_many 不自动注册）
        ctx.register_batch(entity_name, entities)
        elapsed = time.time() - t0
        actual = ctx.count(entity_name)
        expected = profile.entity_count(entity_name)
        print(f"OK {actual} 条 (期望 {expected}, 耗时 {elapsed:.2f}s)")


def run_validation(ctx: GenerationContext) -> bool:
    """运行验证器，返回是否全部通过。"""
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
        for err in r.errors:
            print(f"     ERROR: {err}")
        for warn in r.warnings:
            print(f"     WARN:  {warn}")
        if not r.is_valid:
            all_ok = False

    return all_ok


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # 加载 Profile
    print(f"加载 Profile: {args.profile}")
    profile = SeedProfile.from_name(args.profile)
    print(f"  {profile}")

    # 创建 Context
    seed = args.seed if args.seed is not None else profile.seed
    ctx = GenerationContext(profile, seed=seed)
    print(f"  随机种子: {seed}")

    # 运行生成器
    print("\n生成数据...")
    t0 = time.time()
    run_generators(ctx)

    # 解析所有 $ref 占位符 → 实际 FK 值
    refs_resolved = ctx.resolve_all_refs()
    print(f"\nFK 引用解析: {refs_resolved} 个")

    total_time = time.time() - t0

    # 摘要
    print(f"\n摘要:")
    for name, count in sorted(ctx.summary().items()):
        print(f"  {name}: {count}")

    # 验证
    if args.validate:
        print(f"\n验证数据...")
        all_ok = run_validation(ctx)
        if not all_ok:
            print("\n[WARN] 验证发现错误，请检查上述输出")
            return 1

    # 导出
    print(f"\n导出 ({args.export})...")
    if args.export == "json":
        output_dir = args.output or f"data/seed/{args.profile}"
        exporter = JsonFileExporter(output_dir)
        exporter.export(ctx)
        print(f"  OK 已导出到 {output_dir}/")
    elif args.export == "dict":
        exporter = DictExporter()
        data = exporter.export(ctx)
        print(f"  OK 导出 {len(data)} 个实体类型")

    # 直写向量库
    if args.sync_vectordb:
        print(f"\n同步向量库...")
        try:
            from seed_data.importers.knowledge_importer import KnowledgeDocImporter
            importer = KnowledgeDocImporter()
            result = importer.import_from_generation_context(ctx)
            print(f"  Chunk 级: {result['chunks']} 条")
            print(f"  Doc 级:   {result['docs']} 条")
            if result['errors']:
                print(f"  错误:     {result['errors']}")
            stats = importer.stats()
            print(f"  向量库总量: {stats['chunks']} chunks, {stats['docs']} docs")
        except Exception as e:
            print(f"  [WARN] 向量库同步失败: {e}")

    print(f"\n总耗时: {total_time:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

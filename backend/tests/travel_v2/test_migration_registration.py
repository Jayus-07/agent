from scripts.init_db import MIGRATION_TARGETS, discover_migrations


def test_travel_v2_and_model_health_migrations_run_in_safe_order() -> None:
    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert unregistered == []
    sequence = [
        "084_travel_v2_schema.sql",
        "085_drop_legacy_travel_planning.sql",
        "086_travel_v2_arrangements.sql",
        "094_travel_v2_ai_edit_requests.sql",
        "095_travel_v2_ai_edit_pending_resolution.sql",
        "096_travel_v2_revision_shares.sql",
        "097_llm_model_health_probe_source.sql",
        "101_travel_v2_trip_conversations.sql",
    ]
    for migration in sequence:
        assert MIGRATION_TARGETS[migration] == "memory"
    positions = [names.index(migration) for migration in sequence]
    assert positions == sorted(positions)


def test_travel_v2_template_seed_is_registered_and_runs_after_schema() -> None:
    # 129 是纯种子数据，仅依赖 084 建出的 templates 表；101 属于 ORDER_LAST
    # 会被 discover_migrations 压到末尾，种子在它之前执行是安全的，
    # 因此不并入上面的 sequence 断言，只守护「已登记 + 晚于建表」。
    # （2026-11-01：原编号 125 与先行合入 main 的 125_mall_wallet 冲突，顺延为 129。）
    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]
    assert unregistered == []
    assert MIGRATION_TARGETS["129_travel_v2_template_seed.sql"] == "memory"
    assert names.index("129_travel_v2_template_seed.sql") > names.index(
        "084_travel_v2_schema.sql"
    )

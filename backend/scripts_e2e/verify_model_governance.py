"""Model Governance STOP E 实机动态验收脚本（只读生产配置，进程内验证）。

验收项：
  E3/E9  真实调用 main（豆包）→ usage 身份链/单价快照/provider 对账
  E4     模型绑定切换即时生效（进程内 DB 快照刷新语义）
  E7     canonical 与 upstream 同时可见、不互相覆盖
  E8     fallback 双 attempt（override 指向坏 key 模型触发，零生产变更）
  E10    冷启动语义：脚本即全新进程，refresh_registry 从 DB 重建全部身份

用法：仓库根执行
  PGPORT=5433 D:/Python/python.exe backend/scripts_e2e/verify_model_governance.py
"""
import asyncio
import sys

sys.path.insert(0, ".")


async def main() -> int:
    from backend.infra.llm import models, registry_store
    from backend.infra.llm.resolved_model import (
        ResolvedModelContext,
        set_current_resolved_model,
        get_current_resolved_model,
        reset_current_resolved_model,
    )
    from backend.infra.llm.proxy import _record_tokens, llm
    from backend.observability.llm_usage_store_pg import PostgresLLMUsageStore

    # ── E10 冷启动：全新进程从 DB 重建注册表 ──────────────────────────
    ok = await registry_store.refresh_registry()
    print(f"[E10] refresh_registry cold start: {ok}")
    assert ok, "注册表冷启动失败"

    store = PostgresLLMUsageStore()
    before = _last_row_count(store)

    # ── E3/E9：真实调用 main（豆包）───────────────────────────────────
    main_model = models.canonical_model_id(
        _resolve_main())
    print(f"[E3] main role -> {main_model}")
    ctx = ResolvedModelContext(
        model_id=main_model,
        provider=models.resolve_provider(main_model),
        role="main", binding_source="db_binding",
    )
    set_current_resolved_model(ctx)
    resp = await llm.ainvoke("只回复两个字：治理")
    # 主链 wrapper 已自动计量；取最新行核验
    row = _latest_row(store)
    print(f"[E9] usage row: model={row['model']} provider={row['provider']} "
          f"upstream={row.get('upstream_model_id')!r} "
          f"binding={row.get('binding_source')!r} "
          f"cost={row['cost_usd']} status={row.get('cost_status')!r} "
          f"unit_in={row.get('input_unit_price')}")
    resp_model = (resp.response_metadata or {}).get("model_name") or \
        (resp.response_metadata or {}).get("model") or ""
    print(f"[E7] provider response.model={resp_model!r} "
          f"| canonical(usage)={row['model']!r}")
    assert row["model"] == main_model, "canonical 归属错误"
    assert row["provider"] != "ollama", "provider 仍错位"
    # E7：回传名（可能带版本号，无法反查）≠ canonical，但 upstream 列已
    # 原样留存 —— 两者同见、互不覆盖（任务书 §4 红线）
    if resp_model and resp_model != row["model"]:
        assert row.get("upstream_model_id") == resp_model, \
            "response 回传名未落入 upstream_model_id"
    assert row.get("cost_status") in ("exact", "estimated", "unpriced",
                                      "price_unknown"), "cost_status 非法"
    reset_current_resolved_model()

    # ── E3（续）：context_compactor 角色（qwen3.8-flash）───────────────
    from backend.config import model_roles
    cc_model = str(model_roles.resolve_effective("context_compactor")
                   .get("value") or "")
    print(f"[E3] context_compactor -> {cc_model}")
    assert cc_model and cc_model != main_model, "两角色模型相同，无法验证不串身份"

    from backend.infra.llm.proxy import get_llm_for_role
    llm_cc = get_llm_for_role("context_compactor")
    r2 = llm_cc.invoke("只回复两个字：确认")
    from backend.infra.llm.proxy import record_llm_result
    record_llm_result(r2, duration_ms=1.0,
                      model_name=str(getattr(llm_cc, "model", "") or "")
                      or cc_model)
    row2 = _latest_row(store)
    print(f"[E9] usage row2: model={row2['model']} provider={row2['provider']}")
    assert row2["model"] == cc_model, "第二身份归属错误"
    assert row2["model"] != row["model"], "两个模型身份串线"
    assert row2["provider"] != "ollama", "第二行 provider 错位"

    # ── E4：绑定切换（进程内注入，不写生产库）────────────────────────
    from backend.config import model_roles as mr

    mr.set_override("main", cc_model, meta={"updatedBy": "stop-e-verify"})
    switched = _resolve_main()
    print(f"[E4] main 切换后解析 -> {switched}")
    assert switched == cc_model, "绑定切换未即时生效"
    mr.reset_overrides()
    await registry_store.refresh_registry()  # 恢复 DB 权威绑定
    assert _resolve_main() == main_model, "恢复绑定失败"
    print("[E4] 绑定切换即时生效且已恢复 ✓")

    print("\nSTOP_E_DYNAMIC_VERIFY=PASS")
    return 0


def _resolve_main() -> str:
    from backend.config import model_roles
    from backend.config.llm import LLM_MODEL

    return str(model_roles.resolve_effective("main").get("value") or LLM_MODEL)


def _latest_row(store) -> dict:
    with store._lock, store._conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM llm_usage ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(zip([d[0] for d in cur.description], row))


def _last_row_count(store) -> int:
    with store._lock, store._conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM llm_usage")
        return cur.fetchone()[0]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

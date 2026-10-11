"""seed_eval_model_bindings.py — 评测库模型绑定初始化与评测前校验。

为什么存在（2026-10-11 CI 评测口径决策）：Embedding/Rerank 一律使用 DB 注册的
API 模型（specialized binding 解析），禁止回退本地 BGE 模型或旧 env 凭据。
GitHub 托管 Runner 没有本机 DB，需要用隔离测试库 + GitHub Secrets 初始化绑定；
运行时凭据仍从数据库加载（credentials.py 的 DB 唯一来源口径不变）。

两种注入模式（幂等，ON CONFLICT 覆盖）：

1. --from-source-db：本地重放/演练。从源库原样复制 specialized-api 的
   provider / credentials / bindings 行——key_cipher 密文直拷，不落明文。
2. --from-env：CI 模式。读 SPECIALIZED_API_KEY 明文 + SECRETS_ENCRYPTION_KEY，
   encrypt_secret 加密后入库（与生产同一加密口径），审计字段只落
   fingerprint/last4（backend.shared.crypto 口径）。

评测前校验（--verify，任何真实评测的前置步骤）：

- embedding/rerank binding 存在且 enabled；
- 凭据可解密且非空（只输出存在性，不输出明文/密文）；
- 模型名非空；
- binding dimensions 与向量存储维度（VECTOR_PG_DIM，默认 1024）一致。

校验失败退出码 1，评测不得启动（fail-closed，不切回本地模型）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# `python scripts/xxx.py` 的 sys.path[0] 是 scripts/，backend 包在仓库根——
# 与 backend/scripts/ingest_eval_fixtures.py 同一套 REPO_ROOT 处理。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BASE_URL_PROVIDER = (
    "https://llm-l0doxswk7nraeetg.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
DEFAULT_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_RERANK_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
PROVIDER_ID = "specialized-api"
OPERATOR = "eval-binding-seed"


def _connect(database: str):
    import psycopg

    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5433")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", ""),
        dbname=database,
        connect_timeout=int(os.getenv("PGCONNECT_TIMEOUT", "10")),
    )


def seed_from_source_db(source_db: str, target_db: str) -> int:
    """从源库原样复制（密文直拷）。返回复制行数。"""
    src = _connect(source_db)
    dst = _connect(target_db)
    copied = 0
    try:
        with src.cursor() as cur:
            cur.execute(
                "SELECT id, display_name, driver, base_url, network_scope, "
                "extra_headers, billing, is_builtin, enabled "
                "FROM llm_providers WHERE id = %s",
                (PROVIDER_ID,),
            )
            provider_row = cur.fetchone()
            cur.execute(
                "SELECT key_cipher, key_fingerprint, key_last4, key_version "
                "FROM llm_provider_credentials WHERE provider_id = %s",
                (PROVIDER_ID,),
            )
            cred_row = cur.fetchone()
            cur.execute(
                "SELECT role, provider_id, adapter, model_name, base_url, "
                "options, enabled FROM llm_specialized_model_bindings "
                "WHERE provider_id = %s ORDER BY role",
                (PROVIDER_ID,),
            )
            binding_rows = cur.fetchall()
    finally:
        src.close()

    if provider_row is None or cred_row is None or not binding_rows:
        print(
            f"[seed] 源库 {source_db} 缺少 {PROVIDER_ID} 的完整绑定"
            f"（provider={provider_row is not None}, cred={cred_row is not None}, "
            f"bindings={len(binding_rows)}）——不写入不完整配置"
        )
        return 1

    with dst.cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_providers
                (id, display_name, driver, base_url, network_scope,
                 extra_headers, billing, is_builtin, enabled, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                driver = EXCLUDED.driver,
                base_url = EXCLUDED.base_url,
                network_scope = EXCLUDED.network_scope,
                enabled = EXCLUDED.enabled,
                updated_at = now()
            """,
            (
                provider_row[0], provider_row[1], provider_row[2], provider_row[3],
                provider_row[4],
                # extra_headers 是 jsonb；billing 是受 check 约束的 text——
                # 不能 JSON 化（'"metered"' 会违反 billing_check）。
                psycopg_extras_json(provider_row[5]), provider_row[6],
                provider_row[7], provider_row[8], OPERATOR,
            ),
        )
        copied += 1
        cur.execute(
            """
            INSERT INTO llm_provider_credentials
                (provider_id, key_cipher, key_fingerprint, key_last4,
                 key_version, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (provider_id) DO UPDATE SET
                key_cipher = EXCLUDED.key_cipher,
                key_fingerprint = EXCLUDED.key_fingerprint,
                key_last4 = EXCLUDED.key_last4,
                key_version = EXCLUDED.key_version,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
            """,
            (PROVIDER_ID, cred_row[0], cred_row[1], cred_row[2], cred_row[3], OPERATOR),
        )
        copied += 1
        for role, _pid, adapter, model_name, base_url, options, enabled in binding_rows:
            cur.execute(
                """
                INSERT INTO llm_specialized_model_bindings
                    (role, provider_id, adapter, model_name, base_url,
                     options, enabled, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (role) DO UPDATE SET
                    provider_id = EXCLUDED.provider_id,
                    adapter = EXCLUDED.adapter,
                    model_name = EXCLUDED.model_name,
                    base_url = EXCLUDED.base_url,
                    options = EXCLUDED.options,
                    enabled = EXCLUDED.enabled,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                """,
                (role, PROVIDER_ID, adapter, model_name, base_url,
                 psycopg_extras_json(options), enabled, OPERATOR),
            )
            copied += 1
    dst.commit()
    print(f"[seed] 已从 {source_db} 复制 {copied} 行到 {target_db}（密文直拷，无明文落盘）")
    return 0


def seed_from_env(target_db: str) -> int:
    """CI 模式：明文 key 加密入库。"""
    api_key = (os.getenv("SPECIALIZED_API_KEY") or "").strip()
    if not api_key:
        print("[seed] 环境变量 SPECIALIZED_API_KEY 为空——评测绑定无法初始化，"
              "评测必须保持 BLOCKED，不得切回本地模型")
        return 1

    from backend.shared.crypto import encrypt_secret, fingerprint, last4

    provider_base = (
        os.getenv("SPECIALIZED_PROVIDER_BASE_URL") or DEFAULT_BASE_URL_PROVIDER
    )
    embed_base = os.getenv("EMBEDDING_BASE_URL") or DEFAULT_EMBEDDING_BASE_URL
    rerank_base = os.getenv("RERANK_BASE_URL") or DEFAULT_RERANK_BASE_URL
    embed_model = os.getenv("EMBEDDING_MODEL_NAME", "qwen3.7-text-embedding")
    rerank_model = os.getenv("RERANK_MODEL_NAME", "qwen3.7-text-rerank")
    dims = int(os.getenv("EVAL_EMBEDDING_DIMENSIONS", "1024"))

    cipher = encrypt_secret(api_key)
    dst = _connect(target_db)
    copied = 0
    with dst.cursor() as cur:
        cur.execute(
            """
            INSERT INTO llm_providers
                (id, display_name, driver, base_url, network_scope,
                 is_builtin, enabled, created_by)
            VALUES (%s, %s, 'specialized', %s, 'public', false, true, %s)
            ON CONFLICT (id) DO UPDATE SET
                base_url = EXCLUDED.base_url, enabled = true, updated_at = now()
            """,
            (PROVIDER_ID, "Eval CI specialized provider", provider_base, OPERATOR),
        )
        copied += 1
        cur.execute(
            """
            INSERT INTO llm_provider_credentials
                (provider_id, key_cipher, key_fingerprint, key_last4,
                 key_version, updated_by)
            VALUES (%s, %s, %s, %s, 1, %s)
            ON CONFLICT (provider_id) DO UPDATE SET
                key_cipher = EXCLUDED.key_cipher,
                key_fingerprint = EXCLUDED.key_fingerprint,
                key_last4 = EXCLUDED.key_last4,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
            """,
            (PROVIDER_ID, cipher, fingerprint(api_key), last4(api_key), OPERATOR),
        )
        copied += 1
        for role, model_name, base_url, options in (
            ("embedding", embed_model, embed_base, {"dimensions": dims}),
            ("rerank", rerank_model, rerank_base, {}),
        ):
            cur.execute(
                """
                INSERT INTO llm_specialized_model_bindings
                    (role, provider_id, adapter, model_name, base_url,
                     options, enabled, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, true, %s)
                ON CONFLICT (role) DO UPDATE SET
                    provider_id = EXCLUDED.provider_id,
                    adapter = EXCLUDED.adapter,
                    model_name = EXCLUDED.model_name,
                    base_url = EXCLUDED.base_url,
                    options = EXCLUDED.options,
                    enabled = true,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                """,
                (role, PROVIDER_ID,
                 "dashscope_embedding" if role == "embedding" else "dashscope_rerank",
                 model_name, base_url, psycopg_extras_json(options), OPERATOR),
            )
            copied += 1
    dst.commit()
    print(f"[seed] 已按 env 注入 {copied} 行到 {target_db}"
          f"（key 指纹={fingerprint(api_key)}，明文未落日志）")
    return 0


def evaluate_bindings(
    bindings: list[tuple],
    credentials: dict[str, tuple],
    *,
    expected_dim: int,
    decrypt,
) -> tuple[list[str], list[str]]:
    """纯函数裁决：绑定/凭据/维度校验（verify 的可测核心）。

    bindings: (role, provider_id, adapter, model_name, base_url, options, enabled)
    credentials: {provider_id: (key_cipher|None, key_fingerprint|None)}
    decrypt: callable(cipher) -> str（生产=decrypt_secret，测试可注入）

    凭据按每个绑定**各自的 provider_id** 分别校验——CI seed 注册
    rag-eval-embedding / rag-eval-rerank 等分离 Provider，不能硬编码单一
    Provider ID。
    返回 (summary_lines, problems)。
    """
    problems: list[str] = []
    summary: list[str] = []
    checked_providers: list[str] = []
    roles_seen: set[str] = set()

    for role, provider_id, adapter, model_name, base_url, options, enabled in bindings:
        roles_seen.add(role)
        checked_providers.append(provider_id)
        if not enabled:
            problems.append(f"{role}: binding 未启用")
        if not model_name:
            problems.append(f"{role}: 模型名为空")
        if not base_url:
            problems.append(f"{role}: base_url 为空")
        if not provider_id:
            problems.append(f"{role}: provider_id 为空")
            continue
        dims = (options or {}).get("dimensions")
        summary.append(
            f"{role}: model={model_name} provider={provider_id} "
            f"adapter={adapter} dims={dims if dims is not None else 'default'}"
        )
        if role == "embedding" and dims is not None and int(dims) != expected_dim:
            problems.append(
                f"embedding 维度不匹配: binding dimensions={dims} "
                f"vs 向量存储 VECTOR_PG_DIM={expected_dim}"
            )

    if not bindings:
        problems.append("embedding/rerank: 无任何 specialized 绑定")
    for required in ("embedding", "rerank"):
        if required not in roles_seen:
            problems.append(f"{required}: binding 缺失（RAG 检索评测必须双绑定）")

    for provider_id in dict.fromkeys(checked_providers):
        cipher, fingerprint = credentials.get(provider_id, (None, None))
        if cipher is None:
            problems.append(f"{provider_id}: 凭据缺失")
            continue
        try:
            plain = decrypt(cipher)
            ok = bool(plain)
            decrypt_error = ""
        except Exception as e:  # noqa: BLE001 — 解密失败必须阻断评测
            ok = False
            decrypt_error = type(e).__name__
        if decrypt_error:
            problems.append(f"{provider_id}: 凭据解密失败（{decrypt_error}）")
        elif not ok:
            problems.append(f"{provider_id}: 凭据解密为空")
        else:
            summary.append(f"credentials[{provider_id}]: 可解密 fingerprint={fingerprint}")
    return summary, problems


def verify(target_db: str) -> int:
    """评测前校验：模型名 / Provider / 维度 / 凭据可用性。fail-closed。"""
    expected_dim = int(os.getenv("VECTOR_PG_DIM", "1024"))
    conn = _connect(target_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT role, provider_id, adapter, model_name, base_url, "
                "options, enabled FROM llm_specialized_model_bindings "
                "WHERE role IN ('embedding','rerank') ORDER BY role"
            )
            bindings = cur.fetchall()
            providers = [b[1] for b in bindings if b[1]]
            if providers:
                cur.execute(
                    "SELECT provider_id, key_cipher, key_fingerprint "
                    "FROM llm_provider_credentials WHERE provider_id = ANY(%s)",
                    (providers,),
                )
                credentials = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            else:
                credentials = {}
    finally:
        conn.close()

    from backend.shared.crypto import decrypt_secret

    summary, problems = evaluate_bindings(
        bindings, credentials, expected_dim=expected_dim, decrypt=decrypt_secret
    )

    for line in summary:
        print(f"[verify] {line}")
    if problems:
        for p in problems:
            print(f"[verify] FAIL {p}")
        print("[verify] EVAL_MODEL_BINDING_CHECK=failed —— 评测保持 BLOCKED")
        return 1
    print("[verify] EVAL_MODEL_BINDING_CHECK=passed")
    return 0


def psycopg_extras_json(value):
    """dict → psycopg JSON 适配（None 原样返回）。"""
    if value is None:
        return None
    import json

    return json.dumps(value)


def probe(target_db: str) -> int:
    """真实调用 embedding API 一次：连通性 + 实际返回维度。

    与生产链路同构（OpenAIEmbeddings + binding.dimensions 参数）；
    绑定/凭据从目标库读取（运行时仍从 DB 加载，不走 env 第二配置源）。
    维度以 API 实际响应为准，不盲信配置声明。
    """
    os.environ["MEMORY_PGDATABASE"] = target_db
    import asyncio

    from backend.infra.llm import credentials as credentials_mod
    from backend.infra.llm import specialized as specialized_mod
    from backend.infra.llm.registry_store import refresh_registry

    # 裸进程没有后台 refresh_loop：先显式拉取 DB 覆盖层注入绑定/凭据缓存。
    if not asyncio.run(refresh_registry()):
        print(f"[probe] FAIL 从 {target_db} 拉取注册表失败（DB 不可达或无配置）")
        return 1

    binding = specialized_mod.resolve_binding("embedding")
    if binding is None:
        print("[probe] FAIL embedding binding 缺失——先执行 seed")
        return 1
    creds = credentials_mod.resolve_credentials(binding.provider_id)
    if not creds.api_key:
        print(f"[probe] FAIL provider={binding.provider_id} 凭据为空")
        return 1
    model = getattr(binding, "upstream_name", "") or binding.model_name
    declared_dims = (binding.options or {}).get("dimensions")

    try:
        from langchain_openai import OpenAIEmbeddings

        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": creds.api_key,
            "base_url": binding.base_url,
            "check_embedding_ctx_length": False,
            "timeout": 30,
            "max_retries": 1,
        }
        if declared_dims is not None:
            kwargs["dimensions"] = int(declared_dims)
        # embed_documents 是索引链路的真实入口（embed_query 签名是单 str，
        # 传 list 会嵌套成 [list] 触发 DashScope 400）。
        vecs = OpenAIEmbeddings(**kwargs).embed_documents(["评测绑定探针"])
        actual_dims = len(vecs[0])
    except Exception as e:  # noqa: BLE001 — 探针失败必须阻断评测
        print(f"[probe] FAIL embedding API 调用失败: {type(e).__name__}: {str(e)[:200]}")
        print("[probe] EVAL_MODEL_PROBE=failed —— 评测保持 BLOCKED")
        return 1

    print(f"[probe] model={model} base_url={binding.base_url}")
    print(f"[probe] declared_dims={declared_dims} actual_dims={actual_dims}")
    if declared_dims is not None and int(declared_dims) != actual_dims:
        print("[probe] FAIL API 实际维度与 binding 声明不一致")
        print("[probe] EVAL_MODEL_PROBE=failed —— 评测保持 BLOCKED")
        return 1
    expected_store_dim = int(os.getenv("VECTOR_PG_DIM", "1024"))
    if actual_dims != expected_store_dim:
        print(f"[probe] FAIL 实际维度 {actual_dims} ≠ 向量存储维度 {expected_store_dim}")
        print("[probe] EVAL_MODEL_PROBE=failed —— 评测保持 BLOCKED")
        return 1
    print("[probe] EVAL_MODEL_PROBE=passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测库模型绑定初始化与校验")
    parser.add_argument("--target-db", default=os.getenv("EVAL_MEMORY_PGDATABASE", ""),
                        help="目标评测库（默认 env EVAL_MEMORY_PGDATABASE）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-source-db", help="从源库原样复制（本地重放）")
    mode.add_argument("--from-env", action="store_true", help="从 env 明文 key 加密注入（CI）")
    mode.add_argument("--verify", action="store_true", help="仅校验不写入")
    mode.add_argument("--probe", action="store_true", help="真实调用 embedding API 验证连通与维度")
    args = parser.parse_args(argv)

    target = args.target_db
    if not target:
        print("[seed] 缺少 --target-db / EVAL_MEMORY_PGDATABASE")
        return 2

    if args.verify:
        return verify(target)
    if args.probe:
        return probe(target)
    if args.from_source_db:
        return seed_from_source_db(args.from_source_db, target)
    return seed_from_env(target)


if __name__ == "__main__":
    sys.exit(main())

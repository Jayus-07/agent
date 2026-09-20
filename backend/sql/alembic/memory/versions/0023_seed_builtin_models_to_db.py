"""0023 — 代码层内置模型种入 DB（「DB 统一控制」收尾）。

背景与决策见 docs/model-config-governance-design.md §B.15：

`AVAILABLE_MODELS` 代码种子是「合并视图」时代的残留 —— 0017 已把 9 个厂商行
迁入 `llm_providers`，本迁移把最后 8 条模型行迁入 `llm_models`，随后代码层
清单清空、`get_available_models()` 改为 **DB-only**。之后：

- 「清单展示 = 代码 ∪ DB」的合并语义退役；界面不再出现
  「代码层内置模型，不可移除」—— 所有行一视同仁（受角色占用检查约束）。
- 凡引用 DB 中不存在的模型（角色绑定 / 启动校验 / 运行时调用）按三层报错：
  绑定时 400、启动时告警、调用时明确异常。

`ON CONFLICT (name) DO NOTHING`：幂等，且不覆盖用户可能已在管理端改过的同名行。
display/description/单价取自迁移时刻的代码清单（2026-09-21 快照）；pricing
落 JSONB（键名与 registry_store._model_entry 的读取约定一致）。siliconflow
两行单价未核，沿用代码注释口径回 0，接入后按账单回填。

执行：`alembic -c alembic.ini -n memory upgrade head`
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

# 每行：(name, provider_id, display, description, input_price, output_price)
# source 取 'builtin'（llm_models_source_check 仅允许 builtin/user），语义即
# 「内置模型已迁库」；created_by='migration-0023' 供 downgrade 精确回滚与溯源。
_SEED_MODELS = [
    ("qwen3.7-plus", "qwen", "Qwen 3.7 Plus - 在线",
     "阿里云百炼 Qwen3.7-Plus，OpenAI 兼容协议，需要 API Key", 0.4, 1.2),
    ("qwen3.7-plus@tp", "qwen_tp", "Qwen 3.7 Plus - Token Plan",
     "阿里云百炼模型包端点（sk-sp- Key），需配置 QWEN_TP_API_KEY", 0.0, 0.0),
    ("qwen2.5:3b", "ollama", "Qwen 2.5 (3B) - 本地",
     "本地 Ollama，免费，无需 API Key", 0.0, 0.0),
    ("deepseek-v4-flash", "deepseek", "DeepSeek V4-Flash - 云端",
     "DeepSeek V4-Flash，高并发低延迟，需要 API Key", 0.14, 0.28),
    ("MiniMax-M3", "minimax", "MiniMax M3 - 云端",
     "MiniMax-M3，OpenAI 兼容协议，需要 API Key", 3.0, 15.0),
    ("Qwen/Qwen3-32B-AWQ", "vllm", "Qwen3 32B (AWQ) - 自托管",
     "自托管 vLLM（OpenAI 兼容），需配置 VLLM_API_BASE/VLLM_API_KEY", 0.0, 0.0),
    ("Qwen/Qwen3-32B", "siliconflow", "Qwen3 32B - 硅基流动",
     "硅基流动 Qwen3-32B，OpenAI 兼容协议，需在供应商页配置 API Key", 0.0, 0.0),
    ("Qwen/Qwen3-8B", "siliconflow", "Qwen3 8B - 硅基流动",
     "硅基流动 Qwen3-8B，OpenAI 兼容协议，需在供应商页配置 API Key", 0.0, 0.0),
]


def _insert_sql(name: str, provider_id: str, display: str,
                description: str, in_p: float, out_p: float) -> str:
    # 文案均不含单引号（快照核对过），直接字面量内联即可
    pricing = f'{{"input_price_per_1m": {in_p!r}, "output_price_per_1m": {out_p!r}}}'
    return (
        "INSERT INTO llm_models "
        "(name, provider_id, display_name, description, pricing, enabled, source, created_by, model_kind) "
        f"VALUES ('{name}', '{provider_id}', '{display}', '{description}', "
        f"'{pricing}', true, 'builtin', 'migration-0023', 'chat') "
        "ON CONFLICT (name) DO NOTHING"
    )


def upgrade() -> None:
    for row in _SEED_MODELS:
        op.execute(_insert_sql(*row))


def downgrade() -> None:
    # 只回滚本迁移种入的行（created_by 标记限定），不碰用户数据
    op.execute("DELETE FROM llm_models WHERE created_by = 'migration-0023'")
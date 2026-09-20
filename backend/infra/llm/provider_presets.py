"""provider_presets.py — 供应商预置端点目录（只读参考数据）

| 用途 | 管理端「新增/编辑供应商」抽屉的厂商·协议·端点候选来源 |
|---|---|
| 落点 | **代码内置**，只读下发（`GET /sys/providers/presets`） |
| 不进 DB | 见 `docs/model-config-governance-design.md` B.0 边界：驱动与厂商能力矩阵留代码 |

## 为什么需要它

同一家厂商在**不同计费计划下有完全不同的数据面端点**，用错会产生额外费用：

| 厂商 | 按量付费 | Coding Plan |
|---|---|---|
| 火山引擎（方舟） | `ark.cn-beijing.volces.com/api/v3` | `ark.cn-beijing.volces.com/api/coding/v3` |
| 阿里云（百炼） | `{WorkspaceId}.cn-beijing.maas.aliyuncs.com/...` | `coding.dashscope.aliyuncs.com/v1` |

而按量付费侧阿里云百炼已从旧的 `dashscope.aliyuncs.com` 共享域名迁移到
**业务空间专属域名** `https://{WorkspaceId}.{region}.maas.aliyuncs.com`，
`WorkspaceId` 不填就调不通（见 `_PLACEHOLDER_NOTE`）。

## 与 `models.py` 的分工

- `models.py:PROVIDERS` —— **解析链**用的驱动/能力矩阵（driver、默认模型、billing），
  有 7 家，参与「模型名 → provider → 凭据」推导。
- 本模块 —— **登记链**用的端点候选（厂商 × 计划 × 协议 → 官方 Base URL），
  **不参与任何解析**，只作为「实例（instance）」字段的推荐值来源。

两者互补且不重叠：本模块增删一条预置**不改变任何运行时行为**。

## 计费口径：三选一计划只映射到两个 `billing`

设计文档 B.2 已拍板「coding plan 与按量 API 的差别只有三点，三点都是数据」，
B.3 的 billing 表把 `subscription` 的归属写成「`qwen_tp`、新增 coding plan」。
故 **Token Plan 与 Coding Plan 都落 `subscription`**（预付/订阅制），
只有按量付费落 `metered`。见 `PLAN_BILLING`。

⚠️ **不要为此新增第四个 `billing` 值**：DB 的 CHECK 约束
（`0017_llm_providers.py`：`billing IN ('metered','subscription','local')`）、
`ModelConfigService` 内三处白名单、`get_provider_billing`、
以及 `compute_cost_usd` / `budget.py` / `quota.py` 都要跟着改；
而计划类型的区分靠 `display_name` 与 provider id 已足够。

## 免密钥

本模块只有静态数据与纯函数，无 IO、无网络、不读 env。
"""
from __future__ import annotations

from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 计划类型
# ---------------------------------------------------------------------------

PLAN_TOKEN = "token_plan"
PLAN_CODING = "coding_plan"
PLAN_METERED = "metered"

PLAN_LABELS: dict[str, str] = {
    PLAN_TOKEN: "Token Plan",
    PLAN_CODING: "Coding Plan",
    PLAN_METERED: "按量付费",
}

#:「计划 → billing」映射。依据设计文档 B.2 / B.3 拍板结论：
#: Token Plan 与 Coding Plan 都是预付/订阅制（`qwen_tp` 已是此语义，
#: 见 `models.py` 中 `qwen_tp` 的注释「模型包按购买量计费，不走 token 计价」）。
PLAN_BILLING: dict[str, str] = {
    PLAN_TOKEN: "subscription",
    PLAN_CODING: "subscription",
    PLAN_METERED: "metered",
}

#: 计划展示顺序（前端「三选一」的顺序以此为准）。
PLAN_ORDER: tuple[str, ...] = (PLAN_TOKEN, PLAN_CODING, PLAN_METERED)

#: 预置条目的 driver 只允许这两个 —— 与 `ProviderCreateRequest.driver`
#: 的 `Literal["openai","anthropic","ollama"]` 交集。`ollama` 是自托管，
#: 没有「官方预置端点」，故不出现在目录里。
PRESET_DRIVERS: tuple[str, ...] = ("openai", "anthropic")

_DRIVER_LABELS: dict[str, str] = {
    "openai": "OpenAI 兼容",
    "anthropic": "Anthropic 兼容",
}

_PLACEHOLDER_NOTE = (
    "需把 {WorkspaceId} 换成你自己的业务空间 ID，否则无法调用；"
    "旧的 dashscope.aliyuncs.com 共享域名已迁移，若仍用 DashScope 原生 API，路径为 /api/v1"
)

_VOLC_NOTE = "按量付费是 /api/v3，Coding Plan 是 /api/coding/v3，两端点不同，用错会产生额外费用"

_KIMI_CODE_NOTE = "Kimi Code 只提供 Anthropic 兼容端点，没有 OpenAI 兼容入口"

_DEEPSEEK_NOTE = "DeepSeek 官方允许不带 /v1 的基址（等价于 /v1），探测会按官方口径归一化"


def _preset(
    preset_id: str,
    plan: str,
    vendor: str,
    variant: str,
    driver: str,
    base_url: str,
    api_key_hint: str = "",
    note: str = "",
) -> dict[str, Any]:
    """构造一条预置条目（内部工厂，保证字段齐全）。"""
    return {
        "id": preset_id,
        "plan": plan,
        "vendor": vendor,
        "variant": variant,
        "driver": driver,
        "base_url": base_url,
        "api_key_hint": api_key_hint,
        "note": note,
        "placeholders": _placeholders_of(base_url),
    }


def _placeholders_of(base_url: str) -> list[str]:
    """抽出 base_url 里的 `{Name}` 占位符，供前端提示用户替换。"""
    names: list[str] = []
    idx = 0
    while True:
        start = base_url.find("{", idx)
        if start < 0:
            break
        end = base_url.find("}", start + 1)
        if end < 0:
            break
        names.append(base_url[start + 1 : end])
        idx = end + 1
    return names


# ---------------------------------------------------------------------------
# 目录本体（43 条）
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: tuple[dict[str, Any], ...] = (
    # ---------------- Token Plan（16）----------------
    _preset(
        "aliyun-token-plan-cn-openai", PLAN_TOKEN, "阿里云百炼", "北京", "openai",
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "sk- 开头",
    ),
    _preset(
        "aliyun-token-plan-cn-anthropic", PLAN_TOKEN, "阿里云百炼", "北京", "anthropic",
        "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic",
        "sk- 开头",
    ),
    _preset(
        "aliyun-token-plan-sg-openai", PLAN_TOKEN, "阿里云百炼", "新加坡", "openai",
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "sk- 开头",
    ),
    _preset(
        "aliyun-token-plan-sg-anthropic", PLAN_TOKEN, "阿里云百炼", "新加坡", "anthropic",
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
        "sk- 开头",
    ),
    _preset(
        "qianfan-token-plan-personal-openai", PLAN_TOKEN, "百度千帆", "个人版", "openai",
        "https://qianfan.baidubce.com/v2/tokenplan/personal",
        "千帆 API Key",
    ),
    _preset(
        "qianfan-token-plan-personal-anthropic", PLAN_TOKEN, "百度千帆", "个人版", "anthropic",
        "https://qianfan.baidubce.com/anthropic/tokenplan/personal",
        "千帆 API Key",
    ),
    _preset(
        "tencent-token-plan-enterprise-gz-openai", PLAN_TOKEN, "腾讯云", "企业版 · 广州", "openai",
        "https://tokenhub.tencentmaas.com/plan/v3",
        "TokenHub API Key",
    ),
    _preset(
        "tencent-token-plan-enterprise-gz-anthropic", PLAN_TOKEN, "腾讯云", "企业版 · 广州", "anthropic",
        "https://tokenhub.tencentmaas.com/plan/anthropic",
        "TokenHub API Key",
    ),
    _preset(
        "tencent-token-plan-enterprise-sg-openai", PLAN_TOKEN, "腾讯云", "企业版 · 新加坡", "openai",
        "https://tokenhub-intl.tencentmaas.com/plan/v3",
        "TokenHub API Key",
    ),
    _preset(
        "tencent-token-plan-personal-openai", PLAN_TOKEN, "腾讯云", "个人版", "openai",
        "https://api.lkeap.cloud.tencent.com/plan/v3",
        "TokenHub API Key",
    ),
    _preset(
        "tencent-token-plan-personal-anthropic", PLAN_TOKEN, "腾讯云", "个人版", "anthropic",
        "https://api.lkeap.cloud.tencent.com/plan/anthropic",
        "TokenHub API Key",
    ),
    _preset(
        "jd-token-plan-openai", PLAN_TOKEN, "京东云", "", "openai",
        "https://modelservice.jdcloud.com/tokenPlan/openai/v1",
        "京东云 API Key",
    ),
    _preset(
        "jd-token-plan-anthropic", PLAN_TOKEN, "京东云", "", "anthropic",
        "https://modelservice.jdcloud.com/tokenPlan/anthropic",
        "京东云 API Key",
    ),
    _preset(
        "mimo-token-plan-openai", PLAN_TOKEN, "小米 MiMo", "", "openai",
        "https://token-plan-cn.xiaomimimo.com/v1",
        "sk- 开头",
    ),
    _preset(
        "mimo-token-plan-anthropic", PLAN_TOKEN, "小米 MiMo", "", "anthropic",
        "https://token-plan-cn.xiaomimimo.com/anthropic",
        "sk- 开头",
    ),
    _preset(
        "qiniu-token-plan-openai", PLAN_TOKEN, "七牛云", "", "openai",
        "https://api.qnaigc.com/v1",
    ),
    # ---------------- Coding Plan（8）----------------
    _preset(
        "volc-coding-anthropic", PLAN_CODING, "火山引擎（方舟）", "", "anthropic",
        "https://ark.cn-beijing.volces.com/api/coding",
        "ARK_API_KEY",
        _VOLC_NOTE,
    ),
    _preset(
        "volc-coding-openai", PLAN_CODING, "火山引擎（方舟）", "", "openai",
        "https://ark.cn-beijing.volces.com/api/coding/v3",
        "ARK_API_KEY",
        _VOLC_NOTE,
    ),
    _preset(
        "aliyun-coding-anthropic", PLAN_CODING, "阿里云（百炼）", "", "anthropic",
        "https://coding.dashscope.aliyuncs.com/apps/anthropic",
        "sk- 开头",
    ),
    _preset(
        "aliyun-coding-openai", PLAN_CODING, "阿里云（百炼）", "", "openai",
        "https://coding.dashscope.aliyuncs.com/v1",
        "sk- 开头",
    ),
    _preset(
        "glm-coding-anthropic", PLAN_CODING, "智谱（GLM）", "", "anthropic",
        "https://open.bigmodel.cn/api/anthropic",
        "通用 API Key",
    ),
    _preset(
        "glm-coding-openai", PLAN_CODING, "智谱（GLM）", "", "openai",
        "https://open.bigmodel.cn/api/coding/paas/v4",
        "通用 API Key",
    ),
    _preset(
        "kimi-code-cn-anthropic", PLAN_CODING, "Kimi Code", "国内", "anthropic",
        "https://api.kimi.com/coding/",
        "",
        _KIMI_CODE_NOTE,
    ),
    _preset(
        "kimi-code-intl-anthropic", PLAN_CODING, "Kimi Code", "海外", "anthropic",
        "https://api.kimi.ai/coding/",
        "",
        _KIMI_CODE_NOTE,
    ),
    # ---------------- 按量付费（19）----------------
    _preset(
        "aliyun-metered-cn-openai", PLAN_METERED, "阿里云百炼", "北京", "openai",
        "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "sk- 开头",
        _PLACEHOLDER_NOTE,
    ),
    _preset(
        "aliyun-metered-cn-anthropic", PLAN_METERED, "阿里云百炼", "北京", "anthropic",
        "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/apps/anthropic",
        "sk- 开头",
        _PLACEHOLDER_NOTE,
    ),
    _preset(
        "volc-metered-openai", PLAN_METERED, "火山引擎（方舟）", "", "openai",
        "https://ark.cn-beijing.volces.com/api/v3",
        "ARK_API_KEY",
        _VOLC_NOTE,
    ),
    _preset(
        "glm-metered-cn-openai", PLAN_METERED, "智谱（GLM）", "国内", "openai",
        "https://open.bigmodel.cn/api/paas/v4",
        "通用 API Key",
    ),
    _preset(
        "glm-metered-intl-openai", PLAN_METERED, "智谱（GLM）", "国际", "openai",
        "https://api.z.ai/api/paas/v4",
        "通用 API Key",
    ),
    _preset(
        "glm-metered-anthropic", PLAN_METERED, "智谱（GLM）", "", "anthropic",
        "https://open.bigmodel.cn/api/anthropic",
        "通用 API Key",
    ),
    _preset(
        "qianfan-metered-openai", PLAN_METERED, "百度千帆", "", "openai",
        "https://qianfan.baidubce.com/v2",
        "千帆 API Key",
    ),
    _preset(
        "tencent-tokenhub-metered-gz-openai", PLAN_METERED, "腾讯云 TokenHub", "广州", "openai",
        "https://tokenhub.tencentmaas.com/v1",
        "TokenHub API Key",
    ),
    _preset(
        "tencent-tokenhub-metered-sg-openai", PLAN_METERED, "腾讯云 TokenHub", "新加坡", "openai",
        "https://tokenhub-intl.tencentmaas.com/v1",
        "TokenHub API Key",
    ),
    _preset(
        "jd-metered-openai", PLAN_METERED, "京东云", "", "openai",
        "https://agentrs.jd.com/api/saas/openai-u/v1",
        "京东云 API Key",
    ),
    _preset(
        "mimo-metered-openai", PLAN_METERED, "小米 MiMo", "", "openai",
        "https://api.xiaomimimo.com/v1",
        "sk- 开头",
    ),
    _preset(
        "mimo-metered-anthropic", PLAN_METERED, "小米 MiMo", "", "anthropic",
        "https://api.xiaomimimo.com/anthropic",
        "sk- 开头",
    ),
    _preset(
        "moonshot-metered-cn-openai", PLAN_METERED, "Kimi（Moonshot）", "国内", "openai",
        "https://api.moonshot.cn/v1",
        "Moonshot API Key",
    ),
    _preset(
        "moonshot-metered-intl-openai", PLAN_METERED, "Kimi（Moonshot）", "国际", "openai",
        "https://api.moonshot.ai/v1",
        "Moonshot API Key",
    ),
    _preset(
        "deepseek-metered-openai", PLAN_METERED, "DeepSeek", "", "openai",
        "https://api.deepseek.com",
        "DeepSeek API Key",
        _DEEPSEEK_NOTE,
    ),
    _preset(
        "deepseek-metered-anthropic", PLAN_METERED, "DeepSeek", "", "anthropic",
        "https://api.deepseek.com/anthropic",
        "DeepSeek API Key",
    ),
    _preset(
        "minimax-metered-cn-openai", PLAN_METERED, "MiniMax", "国内", "openai",
        "https://api.minimax.cn/v1",
        "MiniMax API Key",
    ),
    _preset(
        "minimax-metered-intl-openai", PLAN_METERED, "MiniMax", "国际", "openai",
        "https://api.minimax.io/v1",
        "MiniMax API Key",
    ),
    _preset(
        "minimax-metered-intl-anthropic", PLAN_METERED, "MiniMax", "国际", "anthropic",
        "https://api.minimax.io/anthropic",
        "MiniMax API Key",
    ),
)

_BY_ID: dict[str, dict[str, Any]] = {item["id"]: item for item in PROVIDER_PRESETS}


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def normalize_base_url(base_url: str | None) -> str:
    """归一化 base URL 以便比对：去首尾空白、去尾斜杠、scheme/host 转小写。

    只做「形式上必然等价」的归一，**不改路径语义** —— 例如不补 `/v1`、
    不把 `/api/v3` 与 `/api/coding/v3` 视作同一个（它们本就是不同端点）。
    """
    raw = (base_url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        return raw.rstrip("/")
    scheme, _, rest = raw.partition("://")
    host, sep, path = rest.partition("/")
    normalized = f"{scheme.lower()}://{host.lower()}"
    if sep and path:
        normalized = f"{normalized}/{path.rstrip('/')}"
    return normalized.rstrip("/")


def list_presets(plan: str | None = None) -> list[dict[str, Any]]:
    """列出预置条目；`plan` 给定时按计划过滤（顺序同 `PLAN_ORDER`）。"""
    items: Iterable[dict[str, Any]] = PROVIDER_PRESETS
    if plan is not None:
        items = (item for item in PROVIDER_PRESETS if item["plan"] == plan)
    return [dict(item) for item in items]


def list_plans() -> list[dict[str, str]]:
    """列出「三选一」的计划选项（含派生 billing，供前端展示）。"""
    return [
        {
            "id": plan,
            "label": PLAN_LABELS[plan],
            "billing": PLAN_BILLING[plan],
        }
        for plan in PLAN_ORDER
    ]


def get_preset(preset_id: str | None) -> dict[str, Any] | None:
    """按 id 取一条预置（找不到返回 None，调用方自行兜底）。"""
    found = _BY_ID.get((preset_id or "").strip())
    return dict(found) if found else None


def billing_for_plan(plan: str | None) -> str | None:
    """计划 → billing；未知计划返回 None（**不猜**，由调用方决定兜底）。"""
    return PLAN_BILLING.get((plan or "").strip())


def find_preset(
    driver: str | None,
    base_url: str | None,
    billing: str | None = None,
) -> dict[str, Any] | None:
    """按（协议, base_url[, billing]）反查预置条目。

    用于编辑已有实例时回填「计划 / 厂商」下拉 —— 库里没有存 preset id
    （不值得为此加字段），改为反查。归一化后按 `driver` 与 `base_url`
    精确匹配；**不做模糊匹配**，匹配不到就返回 None，前端落「未套用预置」，
    不硬塞一个相近的预置（否则会把自建/内网地址误标成官方端点）。

    `billing` 用于**消歧**：同一 URL 可能同时属于多个计划（源数据即如此 ——
    智谱 `open.bigmodel.cn/api/anthropic` 在 Coding Plan 与按量付费下都是同一端点）。
    传入实例自身的 billing 后，优先返回 billing 相符的那条；
    不传或仍有多条命中时，按 `PLAN_ORDER` 顺序取第一条（结果始终确定）。
    """
    target_driver = (driver or "").strip().lower()
    target_url = normalize_base_url(base_url)
    if not target_driver or not target_url:
        return None

    candidates = [
        item
        for item in PROVIDER_PRESETS
        if item["driver"] == target_driver
        and normalize_base_url(item["base_url"]) == target_url
    ]
    if not candidates:
        return None

    target_billing = (billing or "").strip().lower()
    if target_billing:
        for item in candidates:
            if PLAN_BILLING.get(item["plan"]) == target_billing:
                return dict(item)
    return dict(candidates[0])


def driver_label(driver: str) -> str:
    """协议的中文展示名（未知值原样返回）。"""
    return _DRIVER_LABELS.get(driver, driver)

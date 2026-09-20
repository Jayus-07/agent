"""infra/llm/provider_presets.py —— 预置端点目录的数据完整性

目录是**纯静态参考数据**，但它有两个「错得很安静」的风险，值得锁住：

1. **计划 → billing 的映射被顺手改成三个值**。设计文档 B.2/B.3 已拍板
   Token Plan 与 Coding Plan 都是订阅制（`subscription`），只有按量付费是
   `metered`。若有人把它改成一一对应，就得连带改 DB 的 CHECK 约束、
   `ModelConfigService` 三处白名单与计价/预算链，否则写入会 500。
2. **反查（find_preset）退化成模糊匹配**。编辑态用它回填「计划/厂商」，
   一旦改成 `in` / 前缀匹配，自建或内网地址会被误标成某家厂商的官方端点，
   管理员会以为自己用的是官方地址。
"""
from __future__ import annotations

from collections import Counter

import pytest

from backend.infra.llm import provider_presets as pp


# ── 目录本体 ────────────────────────────────────────────────────────────


def test_expected_plan_distribution():
    """三张表的条数 —— 少一条就是复制时漏了某家厂商/某个协议。"""
    counts = Counter(item["plan"] for item in pp.PROVIDER_PRESETS)
    assert counts == {"token_plan": 16, "coding_plan": 8, "metered": 25}
    assert len(pp.PROVIDER_PRESETS) == 49


def test_ids_are_unique():
    ids = [item["id"] for item in pp.PROVIDER_PRESETS]
    assert len(ids) == len(set(ids))


def test_all_plans_are_known():
    for item in pp.PROVIDER_PRESETS:
        assert item["plan"] in pp.PLAN_BILLING, item["id"]


def test_drivers_limited_to_openai_and_anthropic():
    """`ollama` 是自托管，没有官方端点；混进来会做出一个填不出地址的下拉项。"""
    for item in pp.PROVIDER_PRESETS:
        assert item["driver"] in pp.PRESET_DRIVERS, item["id"]


def test_all_base_urls_are_https():
    """预置端点一律 https —— 明文 http 既被 `url_guard` 之外的链路嫌弃，
    也会让管理员以为可以裸奔。"""
    for item in pp.PROVIDER_PRESETS:
        assert item["base_url"].startswith("https://"), item["id"]


def test_every_preset_has_vendor_and_driver_label():
    for item in pp.PROVIDER_PRESETS:
        assert item["vendor"], item["id"]
        assert pp.driver_label(item["driver"]) != item["driver"], item["id"]


@pytest.mark.parametrize(
    ("preset_id", "plan"),
    [
        ("volc-metered-openai", "metered"),
        ("volc-coding-openai", "coding_plan"),
    ],
)
def test_volcengine_ends_at_distinct_endpoints(preset_id, plan):
    """火山引擎按量与 Coding Plan 是**两个不同端点**，用错会产生额外费用。

    这条不是形式化断言：它是本目录存在的主要理由。
    """
    metered = pp.get_preset("volc-metered-openai")
    coding = pp.get_preset("volc-coding-openai")
    assert metered["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert coding["base_url"] == "https://ark.cn-beijing.volces.com/api/coding/v3"
    assert metered["base_url"] != coding["base_url"]
    assert metered["note"] and coding["note"]


def test_workspace_id_placeholder_only_on_aliyun_metered():
    """业务空间专属域名必须暴露占位符，否则管理员会照抄一个调不通的地址。"""
    with_placeholder = {
        item["id"] for item in pp.PROVIDER_PRESETS if item["placeholders"]
    }
    assert with_placeholder == {
        "aliyun-metered-cn-openai",
        "aliyun-metered-cn-anthropic",
    }
    for item in pp.PROVIDER_PRESETS:
        if item["placeholders"]:
            assert item["placeholders"] == ["WorkspaceId"]
            assert "WorkspaceId" in item["note"]


# ── 2026-09-21 补录的厂商与端点（各自锁一条会安静出错的口径）─────────────


def test_minimax_cn_domain_is_minimaxi_not_dot_cn():
    """MiniMax 国内域名是 `api.minimaxi.com`（末尾带 i）。

    目录里曾写成 `api.minimax.cn` —— 全仓仅此一处，且无任何官方来源；
    而 `credentials.MINIMAX_ANTHROPIC_URL`、`scripts/migrate_model_env_to_db.py`
    与两个既有测试用的都是 `api.minimaxi.com`。域名写错的症状是连不通/401，
    但管理员看到的是「官方预置」，不会怀疑地址本身。
    """
    assert pp.get_preset("minimax-metered-cn-openai")["base_url"] == (
        "https://api.minimaxi.com/v1"
    )


def test_minimax_cn_has_anthropic_endpoint():
    """国内侧必须有 anthropic 端点 —— `models.PROVIDERS["minimax"]["driver"]`
    就是 `anthropic`（官方推荐路径）。此前国内只有 openai 条目，
    国内用户照目录配会落到国际端点 `api.minimax.io` 上（Key 不互通 → 401）。
    """
    cn = pp.get_preset("minimax-metered-cn-anthropic")
    assert cn["base_url"] == "https://api.minimaxi.com/anthropic"
    assert cn["driver"] == "anthropic"
    assert cn["base_url"] != pp.get_preset("minimax-metered-intl-anthropic")["base_url"]


def test_anthropic_presets_never_suffix_v1():
    """`anthropic` 协议条目的基址**不含** `/v1` —— 客户端自己拼 `/v1/messages`。

    目录内各家的官方 anthropic 端点（阿里云 `/apps/anthropic`、智谱 `/api/anthropic`、
    DeepSeek `/anthropic`、MiniMax `/anthropic`、Anthropic 官方根路径）都是这个口径，
    连 MiniMax 官方文档写的也是 `/anthropic/v1/messages`。写成
    `https://api.anthropic.com/v1` 会拼出 `/v1/v1/messages`。
    """
    for item in pp.PROVIDER_PRESETS:
        if item["driver"] == "anthropic":
            assert not item["base_url"].rstrip("/").endswith("/v1"), item["id"]
            assert "/v1/" not in item["base_url"], item["id"]


def test_gemini_uses_openai_compatible_endpoint():
    """Gemini 只收录 OpenAI 兼容入口。

    原生协议路径是 `models/{model}:generateContent`，与 `PRESET_DRIVERS`
    （openai / anthropic）都对不上 —— 那种地址填进目录会被 openai 客户端打出 404。
    要收录原生协议就得新增 driver，届时应另开条目而不是改这一条。
    """
    gemini = pp.get_preset("gemini-metered-openai")
    assert gemini["driver"] == "openai"
    assert gemini["base_url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    assert ":generateContent" not in gemini["base_url"]


def test_previously_absent_vendors_are_registered():
    """OpenAI / Anthropic / 硅基流动 / 百度千帆国际 此前在目录里完全缺席。

    其中硅基流动尤其别扭：`models.PROVIDERS` 认它（解析链），但登记链选不到，
    管理员只能手填地址 —— 两条链对同一家厂商的口径不一致。
    """
    assert pp.get_preset("openai-metered-openai")["base_url"] == (
        "https://api.openai.com/v1"
    )
    assert pp.get_preset("anthropic-metered-anthropic")["base_url"] == (
        "https://api.anthropic.com"
    )
    assert pp.get_preset("siliconflow-metered-openai")["base_url"] == (
        "https://api.siliconflow.cn/v1"
    )
    assert pp.get_preset("qianfan-metered-intl-openai")["base_url"] == (
        "https://api.baiduqianfan.ai/v1"
    )


# ── 计划 → billing（设计文档 B.2 / B.3 拍板，勿改成一一对应）─────────────


def test_token_plan_and_coding_plan_are_both_subscription():
    assert pp.billing_for_plan(pp.PLAN_TOKEN) == "subscription"
    assert pp.billing_for_plan(pp.PLAN_CODING) == "subscription"
    assert pp.billing_for_plan(pp.PLAN_METERED) == "metered"


def test_billing_values_stay_within_db_check_constraint():
    """不能让目录派生出 DB CHECK 之外的值，否则保存时报 500 而非 400。"""
    assert set(pp.PLAN_BILLING.values()) <= {"metered", "subscription", "local"}


def test_unknown_plan_does_not_guess_billing():
    assert pp.billing_for_plan("bogus") is None
    assert pp.billing_for_plan(None) is None
    assert pp.billing_for_plan("") is None


def test_list_plans_follows_declared_order():
    assert [p["id"] for p in pp.list_plans()] == list(pp.PLAN_ORDER)
    assert [p["label"] for p in pp.list_plans()] == [
        "Token Plan", "Coding Plan", "按量付费"
    ]


# ── 归一化 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://api.deepseek.com/", "https://api.deepseek.com"),
        ("https://API.DeepSeek.com", "https://api.deepseek.com"),
        ("  https://api.deepseek.com/  ", "https://api.deepseek.com"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_base_url(raw, expected):
    assert pp.normalize_base_url(raw) == expected


def test_normalize_keeps_path_semantics():
    """归一化只处理形式差异，**不得**把不同端点折叠成一个。

    `/api/v3` 与 `/api/coding/v3` 归一化后必须仍然不同 ——
    否则火山引擎的两条预置会被判成同一条，反查就会串。
    """
    metered = pp.normalize_base_url("https://ark.cn-beijing.volces.com/api/v3/")
    coding = pp.normalize_base_url("https://ark.cn-beijing.volces.com/api/coding/v3")
    assert metered != coding


# ── 反查（编辑态回填的依据）────────────────────────────────────────────


def test_find_preset_matches_after_normalization():
    """用户保存时可能带尾斜杠、大小写不同，回填必须仍然命中。"""
    assert pp.find_preset("openai", "https://api.deepseek.com/")["id"] == (
        "deepseek-metered-openai"
    )
    assert pp.find_preset("anthropic", "https://API.DeepSeek.com/anthropic/")["id"] == (
        "deepseek-metered-anthropic"
    )


@pytest.mark.parametrize(
    ("driver", "base_url"),
    [
        ("openai", "https://internal.corp.local/v1"),
        ("openai", ""),
        ("", "https://api.deepseek.com"),
        ("openai", None),
    ],
)
def test_find_preset_does_not_fuzzy_match(driver, base_url):
    """匹配不到就返回 None，交给前端落「未套用预置」。

    硬塞一个相近的预置会把自建/内网地址标成某家厂商的官方端点。
    """
    assert pp.find_preset(driver, base_url) is None


def test_find_preset_disambiguates_by_billing():
    """智谱 `/api/anthropic` 在 Coding Plan 与按量付费下是同一 URL。

    实例自身带的 billing 决定回填哪个计划；不传时结果仍确定（取计划顺序首条）。
    """
    url = "https://open.bigmodel.cn/api/anthropic"
    assert pp.find_preset("anthropic", url, "metered")["plan"] == "metered"
    assert pp.find_preset("anthropic", url, "subscription")["plan"] == "coding_plan"
    assert pp.find_preset("anthropic", url)["plan"] == "coding_plan"
    assert pp.find_preset("anthropic", url, "local")["plan"] == "coding_plan"


def test_find_preset_distinguishes_volcengine_endpoints():
    assert pp.find_preset(
        "openai", "https://ark.cn-beijing.volces.com/api/v3"
    )["id"] == "volc-metered-openai"
    assert pp.find_preset(
        "openai", "https://ark.cn-beijing.volces.com/api/coding/v3"
    )["id"] == "volc-coding-openai"


# ── 过滤与拷出 ──────────────────────────────────────────────────────────


def test_list_presets_filters_by_plan_without_aliasing():
    """过滤要按计划返回子集，且返回的是副本 —— 调用方改动不能污染目录。"""
    coding = pp.list_presets(pp.PLAN_CODING)
    assert len(coding) == 8
    assert all(item["plan"] == "coding_plan" for item in coding)

    coding[0]["base_url"] = "https://tampered/ "
    assert pp.get_preset(coding[0]["id"])["base_url"].startswith("https://")


def test_get_preset_returns_none_for_unknown_id():
    assert pp.get_preset("nope") is None
    assert pp.get_preset(None) is None


# ── apiKeyHint：Key 长什么样的说明 ──────────────────────────────────────


def test_aliyun_token_plan_hint_is_the_team_version_prefix():
    """阿里云百炼 Token Plan 是团队/企业版，Key 是 `sk-sp-` 前缀。

    这里锁住是因为它**错得很安静**：提示写成通用的「sk- 开头」时，用户拿个人版
    Key 去填不会报错，只会在调用时被上游拒掉，然后回来怀疑地址和模型名。
    """
    for item in pp.list_presets(pp.PLAN_TOKEN):
        if item["vendor"] != "阿里云百炼":
            continue
        assert item["api_key_hint"] == "sk-sp- 开头", item["id"]


def test_mimo_hint_stays_plain_sk_prefix():
    """小米 MiMo 是另一家厂商，Key 前缀不同 —— 别被上一条顺手改掉。"""
    hints = {i["id"]: i["api_key_hint"] for i in pp.list_presets(pp.PLAN_TOKEN)
             if i["vendor"] == "小米 MiMo"}
    assert hints, "小米 MiMo 的 Token Plan 预置不应消失"
    assert set(hints.values()) == {"sk- 开头"}

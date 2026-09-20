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
    assert counts == {"token_plan": 16, "coding_plan": 8, "metered": 19}
    assert len(pp.PROVIDER_PRESETS) == 43


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

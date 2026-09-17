"""selection_funnel/brief_node.py — 槽位抽取（P0 纯规则，无 LLM）

把「问对问题」钉死在可单测的正则上；LLM 抽取兜底留待 P1
（届时加开关，不现在留死配置）。

抽取目标：category（必填）、platform / 价格带 / 成本 / 毛利率（可选）。
预过滤层不做抽取（两处抽取必然分叉），目的地口径与旅游域一致。
"""
from __future__ import annotations

import re

from backend.selection_funnel.graph_state import (
    FUNNEL_BRIEF,
    STAGE_BRIEF,
    STATUS_NEED_INFO,
    load_brief,
    save_brief,
)

# 类目抽取：覆盖三种常见说法
#   「(对/给/为) XX 做选品」  「XX 品类/类目 的选品」  「选品：XX」
# 注意不设动词前缀捕获——「帮我给宠物零食做选品」这类堆叠前缀会把
# 「帮我给」吞进类目名，交给下方 _strip_fillers 统一清洗。
_CATEGORY_PATTERNS = (
    re.compile(r"([一-龥A-Za-z0-9]{2,12}?)(?:做|跑|来)(?:一次|个)?(?:智能)?选品"),
    re.compile(r"([一-龥A-Za-z0-9]{2,12}?)(?:品类|类目)的?(?:智能)?选品"),
    re.compile(r"(?:智能)?选品[：:\s，,]*([一-龥A-Za-z0-9]{2,12})(?=品类|类目|$)"),
)

# 口语填充字：从抽到的类目开头循环剥除（对联/抖音这类真类目不受影响，
# 受影响的边角案例由追问兜底 —— 抽不出就问，不猜）
_FILLER_CHARS = "帮我对给为请麻烦去一下您"

_STRIP_SUFFIXES = ("的", "这个", "那个")


def _extract_category(message: str) -> str:
    for pat in _CATEGORY_PATTERNS:
        m = pat.search(message)
        if not m:
            continue
        cat = m.group(1).strip()
        while cat and cat[0] in _FILLER_CHARS:
            cat = cat[1:]
        while cat.endswith(_STRIP_SUFFIXES):
            for suf in _STRIP_SUFFIXES:
                if cat.endswith(suf):
                    cat = cat[: -len(suf)]
        if cat:
            return cat
    return ""

_PLATFORMS = ("淘宝", "天猫", "拼多多", "京东", "抖音")
_RE_PLATFORM = re.compile(r"淘宝|天猫|拼多多|京东|抖音")
# 价格带：`80-150元` / `80~150` / `80到150块`
_RE_PRICE_BAND = re.compile(r"(\d+(?:\.\d+)?)\s*[-~～到]\s*(\d+(?:\.\d+)?)\s*(?:元|块)?")
# 成本：`成本30元` / `进货价 25`
_RE_UNIT_COST = re.compile(r"(?:成本|进货价|进价)\s*(\d+(?:\.\d+)?)\s*(?:元|块)")
# 毛利率：`毛利率35%` / `毛利 30 个点`
_RE_MARGIN = re.compile(r"毛利(?:率)?\s*(\d+(?:\.\d+)?)\s*(?:%|个点|点)")

_ASK_TEMPLATE = (
    "要做智能选品，我还需要知道**品类**：你想在哪个品类里选品？\n"
    "（例如：宠物零食、保温杯、蓝牙耳机。也可以顺带说明平台 / 价格带 / 成本，"
    "我说不准的不会乱猜。）"
)


def brief_node(state: dict) -> dict:
    """漏斗入口：抽取槽位；缺类目 → need_info 短路进 reporter 追问。"""
    message = state.get("user_message") or ""
    ctx_in = state.get("funnel_context") or {}

    category = str(ctx_in.get("category") or "") or _extract_category(message)
    platform_m = _RE_PLATFORM.search(message)
    platform = str(ctx_in.get("platform") or "") or (platform_m.group(0) if platform_m else "")

    price_min = price_max = None
    band = _RE_PRICE_BAND.search(message)
    if band:
        price_min, price_max = float(band.group(1)), float(band.group(2))
    cost_m = _RE_UNIT_COST.search(message)
    margin_m = _RE_MARGIN.search(message)

    brief = load_brief(state)
    updates = {
        "category": category or brief.category,
        "platform": platform or brief.platform,
        "price_min": ctx_in.get("price_min") if ctx_in.get("price_min") is not None else price_min,
        "price_max": ctx_in.get("price_max") if ctx_in.get("price_max") is not None else price_max,
        "max_unit_cost": (float(cost_m.group(1)) if cost_m else None) or brief.max_unit_cost,
        "target_margin": (float(margin_m.group(1)) / 100 if margin_m else None) or brief.target_margin,
    }
    # ctx 显式给的值优先（适配器/上游比正则更可信）
    for key in ("top_n",):
        if ctx_in.get(key) is not None:
            updates[key] = ctx_in[key]
    brief = brief.model_copy(update={k: v for k, v in updates.items() if v is not None})

    missing = brief.missing_slots()
    if missing:
        return {
            FUNNEL_BRIEF + "_done": True,
            "brief": save_brief(brief),
            "brief_missing": missing,
            "status": STATUS_NEED_INFO,
            "final_answer": _ASK_TEMPLATE,
            "stage_logs": [{
                "stage": STAGE_BRIEF, "kept": 0, "dropped": 0,
                "reasons": [], "notes": [f"缺少槽位: {', '.join(missing)}"],
            }],
            "finished": False,
        }

    # 入口硬校验（2026-09-17 P0）：错误条件放行只会产出误导性空池，先拦下追问
    errors = brief.validation_errors()
    warnings = brief.validation_warnings()
    if errors:
        ask = "需求条件有问题，先确认一下：\n" + "\n".join(f"- {e}" for e in errors)
        if warnings:
            ask += "\n\n另外提醒：\n" + "\n".join(f"- {w}" for w in warnings)
        ask += "\n\n修正后重新说一次即可，例如「给宠物零食做智能选品 80-150元 毛利率30%」。"
        return {
            FUNNEL_BRIEF + "_done": True,
            "brief": save_brief(brief),
            "brief_missing": [],
            "status": STATUS_NEED_INFO,
            "final_answer": ask,
            "stage_logs": [{
                "stage": STAGE_BRIEF, "kept": 0, "dropped": 0,
                "reasons": [], "notes": errors,
            }],
            "finished": False,
        }

    # 软提示（如未知平台）随漏斗带到报告「数据缺口与说明」
    return {
        "brief": save_brief(brief),
        "brief_missing": [],
        "notes": list(warnings),
        "status": "ok",
        "stage_logs": [{
            "stage": STAGE_BRIEF, "kept": 0, "dropped": 0,
            "reasons": [], "notes": [f"类目={brief.category}"] + warnings,
        }],
        "finished": False,
    }

"""selection_funnel/knowledge.py — 知识层 P0：合规检查 + RAG 检索增强

对应原架构的「知识层：阈值配置 · 案例库 · 平台合规规则 RAG」（2026-09-17 补齐）。
三件套，代价从小到大：

  1. 极限词扫描（纯规则零依赖）——广告法禁用语扫 Top-N 候选的
     title / promo_text / highlights，运营价值最高、误报可控（词组级匹配，
     报告措辞为「疑似，请人工复核」）
  2. 平台合规要点（内置结构化清单）——按平台给精要规则，标注以平台最新为准
  3. RAG 检索增强 —— 桥接 get_rag_pipeline().retrieve_knowledge()（轻量
     检索 3-5s，专供下游分析），知识库有合规/案例文档时注入，没有则降级
     为空 + note，绝不炸漏斗

纪律：本模块只富化报告，不做淘汰依据（合规风险交人工复核，不自动毙品）。
"""
from __future__ import annotations

from backend.shared.logger import logger

# ── 1. 广告法极限词（词组级，控制误报；命中只提示不淘汰）─────────────────
_BANNED_WORDS: tuple[str, ...] = (
    "国家级", "世界级", "最高级", "全网第一", "销量第一", "第一品牌",
    "全网最低", "全网最低价", "史上最低", "行业领先", "绝无仅有",
    "NO.1", "no.1", "TOP1", "top1", "100%", "百分之百",
    "顶级", "极品", "万能", "全能", "永久", "绝对有效", "秒杀全网",
)

_TEXT_FIELDS = ("title", "promo_text", "highlights")


def scan_banned_words(candidates: list[dict]) -> list[str]:
    """扫 Top-N 候选文案里的疑似极限词。Returns: 报告提示行列表。"""
    findings: list[str] = []
    for c in candidates:
        name = (c.get("title") or c.get("url") or "")[:28]
        for field in _TEXT_FIELDS:
            text = str(c.get(field) or "")
            if not text:
                continue
            hits = [w for w in _BANNED_WORDS if w in text]
            if hits:
                findings.append(f"- 「{name}」疑似极限词（{field}）：{'、'.join(hits)} —— 请人工复核后修改")
    return findings


# ── 2. 平台合规要点（内置精要清单，P0；以平台最新规则为准）──────────────
_PLATFORM_RULES: dict[str, list[str]] = {
    "通用": [
        "广告法禁用语：文案避免「最/第一/顶级/绝对」等极限表述（见上方扫描结果）",
        "食品类目需 SC 生产资质，化妆品需备案凭证，认证文件先备后售",
        "七日无理由退货为法定义务（定制品/鲜活易腐等除外类目除外）",
    ],
    "淘宝": [
        "发货超时/虚假发货有赔付罚则，大促期发货时限以活动规则为准",
        "主图不得含牛皮癣式大字水印与站外联系方式",
    ],
    "天猫": [
        "入驻品牌资质与类目授权链路需完整，续签考核看服务指标",
        "天猫商品需支持七天无理由与消保协议，违约扣保证金",
    ],
    "拼多多": [
        "「仅退款」政策下高客单商品需强化凭证留存（发货视频/称重记录）",
        "发货超时与缺货罚则较重，大促前核对库存深度",
    ],
    "京东": [
        "入仓商品注意 JIT 时效与库存周转考核，滞销收仓储费",
        "发票开具为硬性义务，电子普票默认随单",
    ],
    "抖音": [
        "体验分（3 分/4 分线）直接挂流量与活动资格，劣质体验会限流",
        "直播话术同样受广告法约束，极限词口播也会被处罚",
    ],
}


def platform_rules(platform: str) -> list[str]:
    """该平台合规要点：平台专属 + 通用兜底；未知平台只给通用。"""
    rules = list(_PLATFORM_RULES.get(platform or "", []))
    seen = set(rules)
    for r in _PLATFORM_RULES["通用"]:
        if r not in seen:
            rules.append(r)
    return rules


# ── 3. RAG 检索增强（有知识库文档则注入，没有则降级）────────────────────
def rag_enhance(category: str, platform: str, top_k: int = 3) -> tuple[list[str], str]:
    """检索知识库中的合规规则 / 选品案例片段。

    Returns: (片段列表, 来源说明)；不可用/无内容 → ([], "")。
    """
    query = f"{category} {platform or '全平台'} 平台合规规则 禁限售 广告法 选品案例".strip()
    try:
        from backend.rag.pipeline import get_rag_pipeline
        text = get_rag_pipeline().retrieve_knowledge(query, top_k=top_k)
    except Exception as e:
        logger.warning("[FunnelKnowledge] RAG 检索不可用，已降级: %s", e)
        return [], ""
    if not text or len(text.strip()) < 20:
        return [], ""
    lines = [ln.strip(" -•\t") for ln in text.splitlines() if len(ln.strip()) > 8]
    return lines[:top_k], "知识库"


def compliance_review(candidates: list[dict], platform: str,
                      category: str) -> tuple[list[str], list[str]]:
    """知识层总入口：极限词 + 平台规则 + RAG 增强。

    Returns: (报告提示行, notes)。notes 记录降级/空缺，绝不静默。
    """
    findings: list[str] = []
    notes: list[str] = []

    banned = scan_banned_words(candidates)
    if banned:
        findings.append("**疑似广告法极限词（请人工复核后修改文案）**")
        findings += banned

    rules = platform_rules(platform)
    if rules:
        findings.append(f"**平台合规要点（{platform or '通用'}，内置清单供参考）**")
        findings += [f"- {r}" for r in rules]

    snippets, source = rag_enhance(category, platform)
    if snippets:
        findings.append(f"**知识库相关片段（{source}）**")
        findings += [f"- {s[:160]}" for s in snippets]
    else:
        notes.append("知识库暂无命中的合规/案例文档（RAG 检索空）——"
                     "上传平台规则文档到知识库后，本段会自动增强。")
    return findings, notes

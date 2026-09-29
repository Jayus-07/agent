"""customer_service/vocab.py — 客服域规则词表单一事实源（迁移 B8）

审计（2026-09-29 现状评估 §8.1）：规则词表 5+ 处重复且口径不一，纯靠
纪律成对维护。本模块把客服域全部规则词表收敛到一份：

  - 域规则 _CS_DOMAIN_RULES（→ CS_DOMAIN_KEYWORDS / CS_DOMAIN_PATTERNS）
  - 投诉检测正则 COMPLAINT_PATTERNS
  - 确认/取消关键词（P1 修正版为唯一权威；config 中的旧副本为零消费
    方死代码，已删除——旧版含裸「不」「对」会误伤）
  - 情绪/紧迫/风险/P0 信号标记（signals.py 改为从本模块 import）
  - QUERY 服务预判词（_SERVICE_KEYWORDS）

原则：**词表值逐字搬移，规则结果零漂移**（以既有 domain/complaint/
confirmation/signals 全量测试为回归门）。

版本与热更：
  - ``VOCAB_VERSION``：词表内容版本号，调词表必须升版本（变更可追溯）；
  - 确认/取消词表支持热加载：``get_confirm_keywords()``/``get_cancel_keywords()``
    按 TTL 检查 override 文件（``CS_VOCAB_OVERRIDE_FILE``，默认
    ``backend/config/cs_vocab_override.json``，gitignored）mtime，变更即生效；
  - ``set_confirm_keywords()``/``set_cancel_keywords()`` 写 override 并在
    文件内 ``changelog`` 追加变更台账（operator/时间/动作）——修改有审计；
  - 域规则/投诉正则/信号标记为内部实现词表，本批单一源但不接热更
    （接入点已在 accessor 层留好，扩展同模式）。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

VOCAB_VERSION = "2026-09-29.1"

# override 文件默认落位（相对 backend/；gitignored，不上库）
_DEFAULT_OVERRIDE_FILE = Path(__file__).resolve().parent.parent / "config" / "cs_vocab_override.json"
_OVERRIDE_FILE = Path(os.getenv("CS_VOCAB_OVERRIDE_FILE") or _DEFAULT_OVERRIDE_FILE)
_OVERRIDE_TTL_S = 60.0

# =============================================
# 投诉检测正则（原 config.customer_service.COMPLAINT_PATTERNS，逐字搬移）
# =============================================
COMPLAINT_PATTERNS = [
    re.compile(p) for p in [
        r"投诉", r"举报", r"太差了", r"服务差", r"不满意",
        r"要.*说法", r"找.*领导", r"12315", r"消协",
        r"差评", r"曝光", r"维权",
    ]
]

# =============================================
# 客服域规则 — 单一配置块（P2.2 映射统一：两套词表成对维护）
# =============================================
# 每域同时定义 keywords（字面计数 → coarse_router 规则通道）与
# patterns（语序正则 → domain_detector 的 is_cs 判定 + 域 hint）。
# 两层语义不同但词表必须成对维护，防止各自漂移（audit P2-22）。
_CS_DOMAIN_RULES = {
    "KNOWLEDGE": {
        "keywords": [
            "怎么", "如何", "什么是", "请问", "告诉我", "介绍",
            "说明", "解释", "有没有", "能不能",
        ],
        "patterns": [
            r"怎么(办|样|弄|退|换|修)", r"如何(操|退|换|查)",
            r"什么(是|时候|原因|条件)", r"请问", r"告[诉我]",
            r"介绍(一下)?", r"解释(一下)?", r"有没有",
            r"能不能", r"可以吗", r"是否",
        ],
    },
    "TRANSACTION": {
        "keywords": [
            "订单", "物流", "快递", "发货", "收货", "签收",
            "tracking", "配送", "运输",
            # 批次C：工单进度查询（投诉单/转人工单落库后可查）
            "工单",
        ],
        "patterns": [
            r"(查|看|跟).*(订单|物流|快递|发货|收货|签收)",
            r"(订单|物流|快递).*(状态|进度|到哪|在哪)",
            r"(发货|收货|签收).*(了没|没有|了吗)",
            # 陈述式抱怨语序（2026-09-17 补召回）："没"在动词前的表述
            # （"一直没发货/还没到"）此前一条正则都不中
            r"(订单|快递|包裹|物流).*(没|未).*(发货|发出|到货|收到|动静|更新)",
            r"(一直没|迟迟没|迟迟不|还没|还没有)(发货|到货|送到|收到|更新|动静)",
            r"没(发货|到货|动静)",
            r"配送", r"运输", r"tracking",
            # P3.5 补召回：「我所有的订单都有哪些」这类列表问法此前
            # 只有「订单」1 关键词命中，落 UNKNOWN 走知识兜底
            r"(所有|全部)(的)?订单", r"订单(列表|清单)", r"都有哪些订单",
            # 批次C：工单查询问法
            r"(我的|查)(投诉)?工单", r"工单(状态|进度|列表|清单)",
            r"(投诉单|报修单).*(状态|进度|怎么样)",
        ],
    },
    "AFTER_SALES": {
        "keywords": [
            "退款", "退货", "换货", "退换", "售后", "维修",
            "保修", "质量问题", "破损",
        ],
        "patterns": [
            # P3.5 收窄（原 r"(退|换|修).*(款|货|一下|怎么)" 贪心匹配
            # "退货"一词本身，把「退货需要满足什么条件」这类政策咨询
            # 误判成退款动作）：只认显式动作意图
            r"申请(退款|退货|换货|售后)", r"退款", r"换货",
            r"(退|换|修)一下", r"(怎么|如何)(退|换|修)",
            r"我要(退|换|修)",
            r"售后", r"(质量|产品).*(问题|坏了|破损)",
            r"保修", r"维修", r"不好用", r"坏了",
        ],
    },
    "ACCOUNT": {
        "keywords": [
            "账户", "账号", "密码", "登录", "注册", "修改信息",
            "地址", "收货地址",
        ],
        "patterns": [
            r"(修改|更改|换).*(密码|地址|手机|邮箱)",
            r"(登录|注册).*(不了|不上|失败|问题)",
            r"账户.*问题", r"账号.*异常",
        ],
    },
    "COMPLAINT": {
        "keywords": ["投诉", "举报", "不满意", "差评", "态度差"],
        "patterns": [
            r"投诉", r"举报", r"(态度|服务).*(差|烂|垃圾)",
            r"不满意", r"要.*说法", r"找.*领导",
            r"12315", r"消协", r"差评", r"曝光", r"维权",
        ],
    },
    "HUMAN": {
        "keywords": ["人工", "客服", "真人", "转接", "经理", "主管"],
        "patterns": [
            r"(转|找).*(人工|客服|真人|经理)", r"人工服务",
            r"不要机器人", r"你是.*机器人.*吗",
        ],
    },
}

# 下游消费方常量（名字保持不变）：coarse_router 用 KEYWORDS，domain_detector 用 PATTERNS
CS_DOMAIN_KEYWORDS = {
    domain: rules["keywords"] for domain, rules in _CS_DOMAIN_RULES.items()
}
CS_DOMAIN_PATTERNS: dict[str, list] = {
    domain: [re.compile(p) for p in rules["patterns"]]
    for domain, rules in _CS_DOMAIN_RULES.items()
}

# =============================================
# 确认 / 取消关键词（P1 修正版为唯一权威，原 confirmation.py 内定义）
# =============================================
# P1 修正（2026-09-17）：移除裸词「不」「对」——子串匹配误伤严重：
#   "确认不要了" 因「不」…仍由「不要」命中 CANCEL（保留）；
#   "对吧" 曾因「对」误判 CONFIRM → 已移除，改用「对的」。
CONFIRM_KEYWORDS = frozenset({
    "确认", "确定", "好的", "同意", "可以", "没问题", "是的", "对的",
    "嗯", "ok", "yes", "confirm",
})
CANCEL_KEYWORDS = frozenset({
    "取消", "算了", "不要", "否", "放弃", "cancel", "no",
})
# 疑问句不算表态："这个可以取消吗" / "可不可以退" / "确认吗？" → NONE
# （此前「可以取消吗」会被判成 CANCEL 直接取消用户pending —— P0 级误判）
QUESTION_MARKERS = ("吗", "么", "?", "？", "可不可以", "能不能", "要不要", "行不行", "是否")

# =============================================
# 情绪 / 紧迫度 / 风险信号标记（原 understanding/signals.py，逐字搬移）
# =============================================
ANGRY_MARKERS = (
    "骗子", "欺诈", "垃圾", "气死", "忍无可忍", "曝光", "报警",
    "12315", "受骗", "胡说", "恶心", "无耻",
)
DISSATISFIED_MARKERS = (
    "不满意", "太慢", "拖了", "敷衍", "第四次", "又", "再也不", "受不了",
    "没人管", "没人处理",
)
URGENT_MARKERS = (
    "立刻", "马上", "尽快", "现在就", "紧急", "今天必须", "等着用",
)
# 跨用户/越权探测与工具滥用信号（只升风险，不做拦截——拦截是 Input Guard 职责）
RISK_MARKERS = (
    "别人的订单", "他人的订单", "他的订单", "她的订单", "所有用户的",
    "全部用户", "别人的手机号", "后台取消", "直接改数据库", "忽略之前的指令",
    "忽略以上", "打印你的系统提示词", "开发者模式",
)
# P0 升级子集（监管/舆情信号，设计方案 §4.6 P0 档）：ANGRY_MARKERS 的高危
# 子集，命中即 Supervisor 直通投诉专家（迁移 B5 消费）。只做既有词条的
# 子集选择、不新增词条——词表扩充走本模块版本化变更。
P0_ESCALATION_MARKERS = ("12315", "曝光", "报警")

# QUERY 复合问题预判词（原 experts/query.py _SERVICE_KEYWORDS，逐字搬移）
QUERY_SERVICE_KEYWORDS = {
    "order": ["订单", "退款", "退货", "售后"],
    "logistics": ["物流", "快递", "发货", "到货", "签收", "配送"],
    "ticket": ["工单", "投诉单", "报修单"],
}


# =============================================
# 确认/取消词表热加载（override 文件 + mtime TTL + changelog 审计）
# =============================================
_override_cache: dict[str, tuple[float, set[str]]] = {}


def _read_override() -> dict:
    """读 override 文件；不存在/非法返回空 dict（不抛异常）。"""
    try:
        if not _OVERRIDE_FILE.exists():
            return {}
        return json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_hot_list(name: str, base: frozenset[str]) -> frozenset[str]:
    """override 合并（base ∪ override），mtime TTL 内零 IO。"""
    try:
        mtime = _OVERRIDE_FILE.stat().st_mtime if _OVERRIDE_FILE.exists() else 0.0
    except OSError:
        return base
    cached = _override_cache.get(name)
    if cached and cached[0] == mtime:
        return frozenset(cached[1])
    items = (_read_override() or {}).get(name)
    merged = set(base)
    if isinstance(items, list):
        merged |= {str(i) for i in items}
    _override_cache[name] = (mtime, merged)
    return frozenset(merged)


def get_confirm_keywords() -> frozenset[str]:
    """确认词表（base ∪ override，热加载）。"""
    return _get_hot_list("confirm_keywords", CONFIRM_KEYWORDS)


def get_cancel_keywords() -> frozenset[str]:
    """取消词表（base ∪ override，热加载）。"""
    return _get_hot_list("cancel_keywords", CANCEL_KEYWORDS)


def _write_override(update: dict[str, list[str]], operator: str, action: str) -> None:
    """写 override + 追加 changelog 台账（修改有审计）。"""
    doc = _read_override() if _OVERRIDE_FILE.exists() else {}
    changelog = doc.pop("changelog", []) or []
    doc.update(update)
    doc["changelog"] = (changelog + [{
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "operator": operator,
        "action": action,
        "keys": sorted(update.keys()),
        "vocab_version": VOCAB_VERSION,
    }])[-100:]  # 台账上限 100 条，防无限膨胀
    _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDE_FILE.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    _override_cache.clear()
    from backend.shared.logger import logger

    logger.warning(
        "[CS Vocab] 词表 override 变更（有审计）: operator=%s action=%s keys=%s version=%s",
        operator, action, sorted(update.keys()), VOCAB_VERSION,
    )


def set_confirm_keywords(items: list[str], operator: str = "admin") -> None:
    """热更确认词表（追加式：与 base 合并生效）+ 变更台账。"""
    _write_override({"confirm_keywords": [str(i) for i in items]}, operator, "set_confirm_keywords")


def set_cancel_keywords(items: list[str], operator: str = "admin") -> None:
    """热更取消词表（追加式：与 base 合并生效）+ 变更台账。"""
    _write_override({"cancel_keywords": [str(i) for i in items]}, operator, "set_cancel_keywords")

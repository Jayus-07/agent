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

VOCAB_VERSION = "2026-10-04.1"

# =============================================
# 出域与寒暄词表（2026-10-04 对话体验改造 T4）
# =============================================
# 独立客服窗口口径：UNKNOWN 域消息先过寒暄词表（→chat_fallback 一次 LLM），
# 再过出域词表（→固定话术，零 LLM）。判定顺序铁律：先客服信号后出域——
# 含客服域信号的句子不得被出域词表截胡（"订单里的行程单丢了"属客服诉求）。
# 两段变更均过 vocab_gate（fail-closed）。
OUT_OF_SCOPE_PATTERNS = [
    re.compile(p) for p in [
        # 旅游话题（话题词参考 router_node 旅游 prefilter 强信号集；
        # 机票/酒店等出行事务是客服窗口场景的出域扩展——prefilter 不管它们）
        r"旅游|旅游攻略|景点|行程(规划)?|自由行|跟团|自驾游|一日游",
        r"机票|火车票|高铁票|酒店|民宿|门票|签证",
        # 选品漏斗域话题
        r"选品|选款|爆款|铺货|竞品分析|类目分析",
        # 明显平台外话题（客服窗口只答购物客服）
        r"天气|股市|股票|彩票|外卖点餐|打车|导航",
    ]
]

CHITCHAT_PATTERNS = [
    re.compile(p) for p in [
        r"^(你好|您好|hi|hello|嗨|哈喽)[~～!！。.？?]?$",
        r"^(在吗|在么|有人吗|在不在)[~～!！。.？?]?$",
        r"^(谢谢|多谢|感谢|辛苦了|麻烦了|辛苦啦)[你们大家啦呀哦哈]?[~～!！。.]?$",
        r"^(好的|好嘞|嗯+|哦+|噢|ok|OK|了解|收到|好的收到|好的呢|收到啦)[~～!！。.，,]?$",
        r"^(早上|中午|下午|晚上)好[~～!！。.]?$",
        r"你是(谁|什么)|(你|您)是(机器人|人工智能|真人|AI|ai)吗",
        r"(你|您)会什么|你能(做|干)什么",
        r"^(拜拜|再见|晚安|早安)[~～!！。.]?$",
    ]
]

# 出域固定话术（V1：零 LLM 零检索，毫秒级）；topic 由分诊识别到的域填充
OUT_OF_SCOPE_TEMPLATE = (
    "您好，这里是独立的客服窗口，我可以帮您处理订单、退款、物流、账号"
    "这类购物相关问题。您说的「{topic}」不在本窗口的服务范围内，"
    "有购物方面的问题随时找我。"
)


def format_out_of_scope(topic: str = "该问题") -> str:
    return OUT_OF_SCOPE_TEMPLATE.format(topic=topic)


def match_out_of_scope(text: str) -> bool:
    """出域命中判定：调用方必须先确认无客服域信号（顺序铁律见段注释）。"""
    return any(p.search(text or "") for p in OUT_OF_SCOPE_PATTERNS)


def match_chitchat(text: str) -> bool:
    """寒暄命中判定：调用方必须先确认无客服域信号（顺序铁律见段注释）。"""
    return any(p.search(text or "") for p in CHITCHAT_PATTERNS)

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
            # 2026-10-03（C7 门禁基线）：促销/商品参数域词
            "活动", "促销", "秒杀", "优惠券", "参数", "规格",
            "会员日", "满减", "签到", "功能", "材质", "了解",
        ],
        "patterns": [
            # 2026-10-03 去重（门禁基线发现）：原「请问/告[诉我]/介绍(一下)?/
            # 解释(一下)?」与同名关键词一词双票，把单关键词句虚增成 2 票
            # 抢域（"让你们主管出面解释"被解释双票误判 KNOWLEDGE），删除。
            # 2026-10-03（门禁基线）：去「退|换|修」——售后动作句归
            # AFTER_SALES（"怎么退货"双域双票时 KNOWLEDGE 靠字典序抢赢）
            r"怎么(办|样|弄)", r"如何(操|查)",
            r"什么(是|时候|原因|条件)", r"有没有",
            r"能不能", r"可以吗", r"是否",
            # 2026-10-03（C7 门禁基线）：政策问句与高频「怎么用/如何开通」句式
            r"(规定|政策|规则|条件|标准)是什么",
            r"怎么(用|领|开|绑|查|看|参加|拿|买)",
            r"如何(绑定|开通|领取|使用|查询|参加|购买|操作)",
            r"(是什么|是什么样的)",
            r"(满足|符合).*(条件|要求)",
            r"有什么(活动|优惠|福利)",
            r"(保修|质保)(几年|多久|多长|多少年|期)",
            r"(退款|退货).*(多久|几天|到账)",
            r"(多久|几天|什么时候)(退款)?到账",
            r"(赔付|赔偿)(标准|规则)",
            r"(退款|退货|换货|售后|保修)(规定|政策|规则|条件|标准|时效)",
            r"怎么(用|领|开|绑|查|看|参加|拿|买|购)",
        ],
    },
    "TRANSACTION": {
        "keywords": [
            "订单", "物流", "快递", "发货", "收货", "签收",
            "tracking", "配送", "运输",
            # 批次C：工单进度查询（投诉单/转人工单落库后可查）
            "工单",
            # 2026-10-03（C7 门禁基线）：包裹/投诉单/报修单/下单高频词补齐
            "包裹", "投诉单", "报修单", "下单",
        ],
        "patterns": [
            r"(查|看|跟).*(订单|物流|快递|发货|收货|签收)",
            r"(订单|物流|快递).*(状态|进度|到哪|在哪)",
            r"(发货|收货|签收|付款|到货).*(了没|没有|了吗)",
            # 陈述式抱怨语序（2026-09-17 补召回）："没"在动词前的表述
            # （"一直没发货/还没到"）此前一条正则都不中；2026-10-03 补「不」
            # （"怎么不更新"）与「付款/到货」动词位
            r"(订单|快递|包裹|物流).*(没|未|不).*(发货|发出|到货|收到|更新|动静)",
            r"(一直没|迟迟没|迟迟不|还没|还没有)(发货|到货|送到|收到|更新|动静)",
            r"没(发货|到货|动静)", r"(没|未)收到货",
            # 2026-10-03 去重（门禁基线）：裸词「配送/运输/tracking」与同名
            # 关键词一词双票，把"配送一直拖延…转人工"顶成 TRANSACTION 抢赢
            # HUMAN(转人工2票)，删除；句式票走下方组合 pattern。
            r"(配送|发货|送到|运输)要(多久|几天|多长)",
            # P3.5 补召回：「我所有的订单都有哪些」这类列表问法此前
            # 只有「订单」1 关键词命中，落 UNKNOWN 走知识兜底
            r"(所有|全部)(的)?订单", r"订单(列表|清单)", r"都有哪些订单",
            # 批次C：工单查询问法
            r"(我的|查)(投诉)?工单", r"工单(状态|进度|列表|清单)",
            # 2026-10-03（C7 门禁基线）句式补召回：
            r"(投诉单|报修单).*(状态|进度|怎么样|处理|有人)",
            r"工单.*(状态|进度|列表|清单|怎么样|有人|处理|到哪)",
            r"驿站|取件码",
            r"包裹.*(到哪|在哪|到哪里)",
            r"(几|多久|什么时候).*(到|送|发货|发出)",
            r"(快递|物流).*(单号|信息)",
            r"(配送|发货|运输)时效",
            r"(退款|退货).*(物流|派送|运输|还在)",
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
            # 2026-10-03（门禁基线）：裸词退款/换货/售后/保修/维修与关键词
            # 一词双票，把"我想了解一下退款规则"等政策问句抢成动作域，去重；
            # 显式动作票走句式 pattern。
            r"申请(退款|退货|换货|售后|维修|报修)",
            r"退款", r"退货", r"换货", r"售后", r"维修", r"保修",
            r"(退|换|修)一下", r"(怎么|如何)(退|换|修|操作)",
            r"我要(退|换|修)", r"(如何|怎么)(操作|申请)?退(款|货)",
            r"操作退款|退款操作",
            r"(质量|产品).*(问题|坏了|破损)",
            r"不好用", r"坏了",
            # 2026-10-03（C7 门禁基线）高频动作句式补召回：
            r"(退|换)(钱|掉)", r"(帮我|给我).*(退|换|修)(了|掉|一下)?", r"退钱",
            r"报修", r"(失灵|断裂|进水|裂痕|瑕疵|打不着)",
            r"(质量|商品|东西|收到).*(问题|坏了|坏的|破损|瑕疵|裂痕|过期)",
            r"(退|换|修)(了|掉)", r"修修|修一下|能修",
            r"(要求|需要).*(处理|退|换|修)",
            r"(用了|用).*(就)?坏",
            r"(退款|退货|换货|售后)(流程|进度|规则)",
            r"(要|需要|想)(维修|修理|修)", r"(送修|返修|寄修|维修点)",
            r"退(这个|它|掉)|换(一件|个)",
        ],
    },
    "ACCOUNT": {
        "keywords": [
            "账户", "账号", "密码", "登录", "注册", "修改信息",
            "地址", "收货地址", "邮箱", "手机号",
        ],
        "patterns": [
            r"(修改|更改|换).*(密码|地址|手机|邮箱)",
            r"(登录|注册).*(不了|不上|失败|问题)",
            r"登录不上|登陆不上|登不上",
            r"账户.*问题", r"账号.*异常",
            # 2026-10-03（C7 门禁基线）高频句式补召回：
            r"密码.*(锁|错误|不对|错了|找回|重置)",
            r"(收不到|没收到).*(验证码|短信)",
            r"(改|换)一?下(默认)?(密码|地址|手机|邮箱)",
            r"(换绑|换个?个?)(手机号?|邮箱|地址)",
            r"(验证码|短信).*(收不到|没收到)",
            r"(支付|登录|交易)?密码.*(怎么|如何|更改|修改|改)",
            r"(手机号?|地址|邮箱).*(怎么|如何|更改|修改|改|换)",
            r"地址.*(重复|删)|(删|删除)(一?个)?地址",
        ],
    },
    "COMPLAINT": {
        # 2026-10-03（C7 门禁基线）：补强情绪/监管信号关键词——「太差了/
        # 12315/曝光」此前只在 COMPLAINT_PATTERNS（analyzer 正则），域规则
        # 计票看不见，单信号句全落 UNKNOWN
        "keywords": [
            "投诉", "举报", "不满意", "差评", "态度差",
            "太差了", "忍无可忍", "维权", "12315", "消协", "曝光", "报警", "骗子", "欺诈",
            "建议", "反馈", "意见", "说法",
        ],
        "patterns": [
            # 2026-10-03（门禁基线）：裸词投诉/举报/12315/消协/差评/曝光/维权/
            # 太差了 与关键词一词双票，抢赢 HUMAN 转人工与售后域，去重；
            # 显式表态加权票走句式 pattern。
            r"(态度|服务|配送).*(差|烂|垃圾|恶劣)",
            r"要.*说法", r"找.*领导",
            # 2026-10-03（C7 门禁基线）句式补召回：
            r"给(个|我)?说法", r"(打|向|去).*(12315|消协)",
            r"(必须|给我).*(说法|处理|解决)", r"(垃圾|无耻|恶心)",
            r"(提个|有个|给个|提|给).{0,2}(建议|意见)", r"反馈(一个|下|：|问题)",
            r"建议(优化|改进|增加|支持)",
            r"希望.*(增加|改进|优化|支持)",
            r"(说好|承诺).*(结果|还没|没兑现)",
            r"(没消息|没结果|没回应|没人(管|理|处理))",
            r"(再)?(不解决|不处理|拖延).*我就|我就.*(报警|曝光|投诉)",
            r"我要(投诉|举报|给差评|维权)", r"(非常|很|特别|太)不满意",
            r"(电话|热线).*(占线|打不通)",
            r"投诉(你们|商家|平台|到底)", r"举报(你们|商家|平台)",
            r"(假货|假一赔十|虚假宣传|欺诈|少发|漏发|送错|发错|拉黑|不兑现|刁难|野蛮分拣)",
            r"(要求|要).*(处理|解决|说法|赔偿|赔付)",
            # 12315/消协 为零误报词，恢复双票权重（监管信号必须高置信）
            r"12315", r"消协",
        ],
    },
    "HUMAN": {
        # 2026-10-03（C8 门禁基线）：主管链路角色词补齐（领导/组长/负责人）
        "keywords": ["人工", "客服", "真人", "转接", "经理", "主管", "领导", "组长", "负责人", "店长"],
        "patterns": [
            # 2026-10-03（C8 门禁基线）：动词扩展（让/叫/请）+ 角色扩展；
            # 「你是机器人吗」放宽「到底是/究竟是」前缀
            r"(转|找|让|叫|请|把).*(人工|客服|真人|经理|主管|领导|组长|负责人|店长)",
            r"人工服务", r"不要机器人", r"你(到底|究竟)?是.*机器人",
            r"人工.*(在吗|在不在|呢|给我|帮我)",
            r"(主管|经理|领导|组长|负责人|店长).*(哪|电话|来|呢)",
            r"接(个|一)?(人|人工|客服)", r"(跟|别让我跟)(机器人?|机器)(耗|聊|说)",
            r"(还是|直接|给我|帮我|我要)转?人工", r"人工(处理|操作|接手|回电)",
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
# 2026-10-03（C10 门禁基线）：裸「么」过宽——"就这么办"被误当疑问；
# 收窄为「什么/怎么」并补假设语气「的话」（"取消的话钱多久到账"≠表态）。
QUESTION_MARKERS = (
    "吗", "什么", "怎么", "?", "？", "可不可以", "能不能", "要不要", "行不行",
    "是否", "的话",
)

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
    """写 override + 追加 changelog 台账（修改有审计）。

    C10 词表变更评测门（2026-10-03，fail-closed）：落盘前回放
    confirm/cancel 黄金集，候选词表引入回归或跌破准确率下限 → 拒绝写入
    （VocabGateRejected）；门禁基础设施自身故障 fail-open 放行 + error
    留痕（可用性优先，与 hybrid ReviewFilter 同哲学）。
    """
    from backend.customer_service.vocab_gate import VocabGateRejected, evaluate_vocab_change

    try:
        report = evaluate_vocab_change(update)
        if not report.ok:
            from backend.shared.logger import logger
            logger.error(
                "[CS Vocab] 词表变更被评测门拒绝（fail-closed）: action=%s keys=%s reason=%s",
                action, sorted(update.keys()), report.reason,
            )
            raise VocabGateRejected(report.reason)
    except VocabGateRejected:
        raise
    except Exception as gate_err:  # 门禁基建故障 ≠ 变更有罪，放行留痕
        from backend.shared.logger import logger
        logger.error("[CS Vocab] 词表评测门故障（fail-open 放行）: %s", gate_err)

    doc = _read_override() if _OVERRIDE_FILE.exists() else {}
    changelog = doc.pop("changelog", []) or []
    doc.update(update)
    doc["changelog"] = (changelog + [{
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "operator": operator,
        "action": action,
        "keys": sorted(update.keys()),
        "vocab_version": VOCAB_VERSION,
        "gated": True,
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

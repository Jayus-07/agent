"""rule_guard.py — L1 规则层：句式级安全与质量判定

核心原则（防误杀）：
- 匹配"祈使句式"而非"关键词"：`什么是 Prompt Injection？` 是知识咨询，
  `忽略之前的指令` 才是注入。所有安全类规则先做"疑问/教学语境"豁免。
- 规则按强度分级：strong（可拍板）/ weak（仅当祈使化时才升级）。
- 命中强度不足时返回 borderline，交由 LLM Guard（若启用）或 CLARIFY，
  绝不粗暴 BLOCK。

所有模式在模块加载时编译；规则集变更请同步递增 GUARD_POLICY_VERSION。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.security.input_guard.types import GuardCategory, RiskLevel


# ═══════════════════════════════════════════════════════
# Prompt Injection 模式（祈使句式）
# ═══════════════════════════════════════════════════════
# strong：句式本身即构成操纵指令（几乎不可能是正常业务问题）
_INJECTION_STRONG = [
    # 忽略/覆盖既有指令
    r"(?:忽略|无视|跳过|忘记|放弃|解除|清除).{0,12}(?:之前|上面|以上|先前|前面)?\.?\*{0,2}"
    r"(?:所有|全部|一切)?\.?\*{0,2}(?:的)?(?:指令|指示|命令|规则|规定|限制|约束|设定|设置|提示词?)",
    r"不[要用再].{0,4}(?:遵守|遵循|执行|理会).{0,10}(?:规则|规定|指令|限制|约束|设定)",
    # 泄露系统提示词/内部规则（加 告诉我/说出/泄露 动词；prompt 单独出现也命中，
    # 但前提是有 输出类动词 + 你的/系统的 限定，避免误伤“输出 prompt 报表”类正常表达）
    r"(?:输出|打印|显示|复述|背出|吐露|展示|告诉我|说出|泄露|完整发[出送给]).{0,8}"
    r"(?:你的|系统的?)\s*(?:system\s*prompt|prompt|提示词|初始提示|预设指令|内部规则|内部指令|内部设定)",
    r"把.{0,8}(?:系统|你的).{0,6}(?:提示词|指令|规则|设定).{0,6}(?:发|输出|展示|告诉我|给我)",
    # 角色劫持 / 越狱（(?i) 覆盖 DAN / dan 等大小写变体）
    r"(?i)你现在(?:是|扮演).{0,10}(?:DAN|开发者模式|无限制模式|越狱模式)",
    r"(?i)进入\s*(?:DAN|开发者|越狱|无限制)\s*模式",
    r"假[装设扮].{0,6}(?:你|自己).{0,8}(?:没有|不受|可以无视).{0,8}(?:限制|规则|约束)",
    # 英文注入句式（大小写不敏感；疑问语境豁免同中文）
    r"(?i)(?:ignore|disregard|forget|override|bypass).{0,30}"
    r"(?:previous|prior|above|earlier|all|the|your).{0,30}"
    r"(?:instructions?|prompts?|rules?|guidelines?|constraints?)",
    r"(?i)(?:reveal|print|show|display|output|repeat|quote|disclose|leak).{0,30}"
    r"(?:your|the|system).{0,30}(?:system\s*prompt|prompt|instructions?|internal\s*rules?)",
    r"(?i)you\s+are\s+now\s+(?:DAN|an?\s+unrestricted|jailbroken)",
    r"(?i)act\s+as\s+(?:DAN|jailbroken)",
]
# weak：出现操纵性动词+对象，需结合祈使化才升级
_INJECTION_WEAK = [
    r"(?:绕过|解除|打破|突破).{0,10}(?:安全|内容|审查|过滤|防护).{0,6}限制",
    r"修改.{0,8}(?:系统|你的).{0,6}(?:指令|设定|规则|提示词)",
    r"泄露.{0,10}(?:内部|机密|上下文|训练数据)",
    r"(?:jailbreak|prompt\s*injection\s*attack)",
]

# 疑问/教学语境豁免 — 命中任一即视为"谈论概念"而非"实施操纵"
_QUESTION_CONTEXT = [
    r"^请问", r"什么是", r"是什么", r"什么叫", r"啥意思",
    r"解释一下", r"介绍一下", r"介绍一下", r"科普",
    r"如何(?:防范|防止|防御|检测|识别|应对|避免)",
    r"怎么(?:防范|防止|防御|检测|识别|应对)",
    r"哪些(?:常见|类型|方式|手段)",
    r"举[个一]\s*[例子]", r"案例", r"原理",
    # 英文教学/疑问语境（"What is prompt injection?" 等）
    r"(?i)what\s+(?:is|are|does|do)", r"(?i)how\s+(?:do|does|can)\s+(?:i|we|you)\s+"
    r"(?:prevent|defend|protect|detect|avoid)",
    r"(?i)explain\s+(?:what|how)", r"(?i)tell\s+me\s+about",
]

# ═══════════════════════════════════════════════════════
# 有害请求模式（真正要求执行恶意行为）
# ═══════════════════════════════════════════════════════
_HARMFUL_STRONG = [
    r"(?:帮我|给我|替我|教我|帮忙).{0,12}(?:写|编写|生成|制作|开发).{0,12}"
    r"(?:病毒|木马|勒索软件|恶意代码|恶意程序|蠕虫|钓鱼邮件|钓鱼网站|爆破脚本|攻击脚本)",
    r"(?:帮我|给我|替我|教我|教我怎么|帮我怎么).{0,10}"
    r"(?:入侵|黑[进客]|攻击|渗透|爆破|拖库|撞库).{0,12}(?:网站|系统|服务器|数据库|公司|内网|平台|账号)",
    r"(?:帮我|教我|给我).{0,10}(?:伪造|篡改).{0,10}(?:财务|账目|报表|凭证|发票|合同|数据)",
    r"(?:怎么|如何).{0,8}(?:洗钱|逃税|诈骗|贩毒|制作炸药|制造武器)",
    r"(?:批量|大量).{0,6}(?:发送|群发).{0,8}(?:垃圾邮件|诈骗|钓鱼)",
    r"(?:帮我|给我|替我).{0,8}(?:群发|批量发送).{0,10}(?:诈骗|钓鱼|垃圾邮件)",
]
# weak：动词+目标出现但句式不完整（边界，需祈使化/LLM 复核）
_HARMFUL_WEAK = [
    r"(?:怎么|如何).{0,6}(?:绕过|破解).{0,12}(?:登录|验证|密码|防火墙|认证|权限)",
    r"(?:窃取|盗取|偷取).{0,10}(?:数据|资料|客户信息|商业机密)",
]

# ═══════════════════════════════════════════════════════
# 敏感域 / 越权预判（只做 domain/sensitivity 标注，不做权限判定）
# ═══════════════════════════════════════════════════════
_SENSITIVE_DOMAINS: dict[str, list[str]] = {
    "HR": [r"员工工资", r"薪资", r"薪酬", r"绩效评[分定]", r"身份证号", r"员工信息"],
    "FINANCE": [r"银行账号", r"银行账[户卡]", r"成本核算", r"利润明细", r"公司账[户目]"],
    "LEGAL": [r"未公开合同", r"保密协议", r"法律[纠纷诉讼]"],
    "SECURITY": [r"系统密码", r"数据库密码", r"访问令牌", r"api\s*key", r"密钥"],
    # 泛内部数据（“把内部敏感数据全部导出来”类请求）
    "INTERNAL": [r"内部数据", r"内部资料", r"敏感数据", r"内部文档", r"内部文件",
                 r"商业机密", r"机密数据"],
}
_BUSINESS_DOMAINS: dict[str, list[str]] = {
    "OPERATIONS": [r"订单", r"库存", r"商品", r"客户", r"会员", r"供应商", r"物流"],
}
# 大规模数据动作动词
_MASS_EXPORT = r"(?:全部|所有|全量|整库|批量).{0,10}(?:导出|下载|拉取|备份|拷贝|发[给送])|" \
               r"(?:导出|下载|拉取|备份|拷贝).{0,10}(?:全部|所有|全量|整库)"
_MASS_WRITE = r"(?:删除|清空|清掉|抹掉|更新|修改|改掉|重置).{0,10}(?:所有|全部|全量)"

# ═══════════════════════════════════════════════════════
# 业务范围：问候 / 超范围 / 垃圾 / 模糊
# ═══════════════════════════════════════════════════════
_GREETING = [r"^你好", r"^您好", r"^hello\b", r"^hi\b", r"^嗨", r"^哈喽",
             r"^早上好", r"^下午好", r"^晚上好", r"^在吗", r"^你是谁", r"^请问你是"]
_CAPABILITY = [r"(?:你会|你能|你可以|都会|能帮我|能做|会做|支持).{0,10}(?:什么|啥|哪些|吗|么)",
               r"有什么功能", r"你的功能", r"做什么的", r"自我介绍"]
# 娱乐/通用知识类主题词（超出企业智能运营范围）
_OOS_KEYWORDS = [r"写[首一].*诗", r"作[首一].*诗", r"写[一个]*小说", r"讲[个一].*[笑话段子]",
                 r"量子力学", r"相对论", r"黑洞", r"星座", r"占卜", r"算命", r"塔罗",
                 r"菜谱", r"做[饭菜的]做法", r"旅游攻略", r"电影推荐", r"小说推荐",
                 r"游戏推荐", r"减肥食谱", r"健身计划", r"足球比分", r"篮球比分",
                 r"推荐.{0,4}(?:电影|电视剧|小说|游戏|歌曲)", r"比分是多少",
                 r"写[一个]*故事", r"编[一个]*故事", r"歌词", r"押韵"]
# 垃圾：键盘乱序 / 纯符号 / 纯数字串
# 注意：不加 re.ASCII —— ASCII 模式下 \W 会把中文当非词字符，
# 导致“查询今天库存不足的商品”被误判为纯符号垃圾（实测踩坑）。
_GARBAGE_ASCII_SEQ = re.compile(r"^[a-z]{2,}$")
_GARBAGE_SYMBOLS_ONLY = re.compile(r"^[\W_]+$")
_GARBAGE_DIGITS_ONLY = re.compile(r"^\d+$")
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
# ═══════════════════════════════════════════════════════
# 查询意图动词（2026-09-15 整改）
# 带"查什么/算什么"意图的短句有明确动作指向，不属于模糊请求——
# "查询技术部有多少人"应放行进链路，而不是被 CLARIFY 拦下。
# ═══════════════════════════════════════════════════════
_QUERY_INTENT = re.compile(
    r"查询|查一下|查下|查查|统计|列出|罗列|汇总|对比|环比|同比|"
    r"多少|几[个条名只辆台]|占比|排名|top\s*\d"
)

# ═══════════════════════════════════════════════════════
# 模糊问题判定辅助
# ═══════════════════════════════════════════════════════
_VAGUE_PATTERNS = [r"^帮我看看$", r"^分析一下$", r"^帮我分析一下$", r"怎么办$",
                   r"^(?:随便|随意).{0,4}(?:说说|聊聊|讲讲)$"]
# 业务名词（判断"短问题是否仍含业务语义"）
# 2026-09-15 整改：补齐组织/项目域名词。此前词表只有电商+制度词，
# "查询技术部有多少人"（9 字符、无电商词）被 detect_vague 误判为模糊请求
# 短路 CLARIFY，正常查询进不了链路。
_BUSINESS_NOUNS = re.compile(
    r"库存|销售|销售额|销量|订单|商品|sku|采购|补货|供应商|客户|会员|物流|"
    r"退货|退款|售后|运营|报表|日报|周报|月报|竞品|价格|成本|利润|毛利|"
    r"流量|转化|复购|gmv|履约|发货|收货|仓库|缺货|滞销|爆款|选品|"
    r"政策|制度|流程|规范|考核|报销|请假|审批|"
    r"部门|员工|人员|人数|人手|团队|组织|岗位|职级|招聘|考勤|"
    r"项目|预算|经费|合同|差旅|会议|资产|绩效"
)
# 祈使动词（配合 weak 命中升级）
_IMPERATIVE = re.compile(r"帮我|请你|给我|替我|教我|马上|立即|现在(就)?")
# 空格拆字绕过：安全类规则同时在"去空白变体"上匹配
_WS_COMPACT = re.compile(r"\s+")


@dataclass
class RuleFinding:
    """规则层单项判定。

    strength: strong=可拍板；weak=边界（交给 LLM Guard 或 CLARIFY）
    """

    category: GuardCategory
    risk: RiskLevel
    confidence: float
    reason: str
    strength: str = "strong"
    domain: str | None = None
    sensitivity: RiskLevel | None = None
    needs_permission: bool = False
    hits: list[str] = field(default_factory=list)


def _has_question_context(q: str) -> bool:
    """是否命中疑问/教学语境（命中则安全类句式降级为正常问题）。"""
    return (
        any(re.search(p, q) for p in _QUESTION_CONTEXT)
        or q.endswith(("?", "？"))
    )


def _is_imperative(q: str) -> bool:
    return bool(_IMPERATIVE.search(q))


def _match_any(patterns: list[str], q: str) -> list[str]:
    return [p for p in patterns if re.search(p, q)]


def _security_variants(q: str) -> list[str]:
    """安全类规则的匹配变体：原文 + 去空白版（反"空格拆字"绕过）。"""
    compact = _WS_COMPACT.sub("", q)
    return [q, compact] if compact != q else [q]


class RuleGuard:
    """规则层检测器：每个 detect_* 返回 Finding 或 None。"""

    # ── 注入 ──────────────────────────────────────────
    def detect_injection(self, q: str) -> RuleFinding | None:
        strong_hits: list[str] = []
        weak_hits: list[str] = []
        for variant in _security_variants(q):
            strong_hits = _match_any(_INJECTION_STRONG, variant)
            weak_hits = _match_any(_INJECTION_WEAK, variant)
            if strong_hits or weak_hits:
                break
        if not strong_hits and not weak_hits:
            return None
        if _has_question_context(q):
            return None  # "什么是 Prompt Injection？"类知识咨询 → 放行
        if strong_hits:
            conf = min(0.97, 0.88 + 0.05 * len(strong_hits))
            return RuleFinding(
                GuardCategory.PROMPT_INJECTION, RiskLevel.CRITICAL, conf,
                "命中指令操纵/提示词泄露句式", "strong", hits=strong_hits[:3],
            )
        # 仅 weak：祈使化才升级为边界命中，否则视为正常提及
        if weak_hits and _is_imperative(q):
            return RuleFinding(
                GuardCategory.PROMPT_INJECTION, RiskLevel.HIGH, 0.6,
                "疑似指令操纵（弱信号）", "weak", hits=weak_hits[:3],
            )
        return None

    # ── 有害请求 ──────────────────────────────────────
    def detect_harmful(self, q: str) -> RuleFinding | None:
        strong_hits: list[str] = []
        weak_hits: list[str] = []
        for variant in _security_variants(q):
            strong_hits = _match_any(_HARMFUL_STRONG, variant)
            weak_hits = _match_any(_HARMFUL_WEAK, variant)
            if strong_hits or weak_hits:
                break
        if not strong_hits and not weak_hits:
            return None
        if strong_hits:
            conf = min(0.96, 0.85 + 0.05 * len(strong_hits))
            return RuleFinding(
                GuardCategory.HARMFUL, RiskLevel.CRITICAL, conf,
                "命中恶意行为执行请求", "strong", hits=strong_hits[:3],
            )
        if weak_hits:
            if _has_question_context(q):
                return None  # 安全研究/解释类咨询
            return RuleFinding(
                GuardCategory.HARMFUL, RiskLevel.HIGH, 0.6,
                "疑似高风险请求（弱信号）", "weak", hits=weak_hits[:3],
            )
        return None

    # ── 敏感域 / 越权预判 ─────────────────────────────
    def detect_sensitive(self, q: str) -> RuleFinding | None:
        export_hit = re.search(_MASS_EXPORT, q)
        write_hit = re.search(_MASS_WRITE, q)

        domain, sens_kw = None, None
        for dom, pats in _SENSITIVE_DOMAINS.items():
            hits = _match_any(pats, q)
            if hits:
                domain, sens_kw = dom, hits[0]
                break
        biz_domain = None
        if domain is None:
            for dom, pats in _BUSINESS_DOMAINS.items():
                if _match_any(pats, q):
                    biz_domain = dom
                    break

        # 大规模导出：敏感域 → HIGH；普通业务域 → MEDIUM（数据治理提醒）
        if export_hit:
            if domain:
                return RuleFinding(
                    GuardCategory.SENSITIVE_DATA, RiskLevel.HIGH, 0.9,
                    f"批量导出敏感域数据（domain={domain}）", "strong",
                    domain=domain, sensitivity=RiskLevel.HIGH,
                    needs_permission=True,
                )
            if biz_domain:
                return RuleFinding(
                    GuardCategory.SENSITIVE_DATA, RiskLevel.MEDIUM, 0.8,
                    f"批量导出业务数据（domain={biz_domain}）", "strong",
                    domain=biz_domain, sensitivity=RiskLevel.MEDIUM,
                    needs_permission=True,
                )
        # 大规模写/删：当前系统查询型为主，标记权限需求交由 Tool 层把关
        if write_hit and (biz_domain or domain):
            return RuleFinding(
                GuardCategory.SENSITIVE_DATA, RiskLevel.HIGH, 0.85,
                "大规模写入/删除请求", "strong",
                domain=domain or biz_domain, sensitivity=RiskLevel.HIGH,
                needs_permission=True,
            )
        # 敏感字段访问（如"查询所有员工工资"）→ 预判 + 权限标记
        if domain:
            return RuleFinding(
                GuardCategory.SENSITIVE_DATA, RiskLevel.HIGH, 0.8,
                f"敏感域数据访问（domain={domain}, kw={sens_kw}）", "strong",
                domain=domain, sensitivity=RiskLevel.HIGH,
                needs_permission=True,
            )
        return None

    # ── 问候 / 能力咨询 ───────────────────────────────
    def detect_greeting(self, q: str) -> RuleFinding | None:
        cap_hits = _match_any(_CAPABILITY, q)
        greet_hits = _match_any(_GREETING, q)
        if cap_hits:
            return RuleFinding(
                GuardCategory.GREETING, RiskLevel.LOW, 0.9,
                "能力咨询", "strong",
            )
        if greet_hits and len(q) <= 24:
            return RuleFinding(
                GuardCategory.GREETING, RiskLevel.LOW, 0.9,
                "问候", "strong",
            )
        return None

    # ── 垃圾输入 ──────────────────────────────────────
    def detect_garbage(self, q: str) -> RuleFinding | None:
        s = q.strip()
        if not s:
            return None
        compact = re.sub(r"\s+", "", s.lower())
        # 纯数字 / 纯符号
        if (_GARBAGE_DIGITS_ONLY.match(compact) or _GARBAGE_SYMBOLS_ONLY.match(compact)) \
                and len(compact) >= 3:
            return RuleFinding(
                GuardCategory.GARBAGE, RiskLevel.LOW, 0.9, "纯数字/纯符号输入", "strong",
            )
        # 短键盘乱序（无空格纯小写字母，不含业务/英文单词结构）
        if _GARBAGE_ASCII_SEQ.match(compact) and len(compact) >= 4 \
                and len(s) <= 20 and not re.search(r"[aeiou]{2}", compact):
            return RuleFinding(
                GuardCategory.GARBAGE, RiskLevel.LOW, 0.8, "疑似键盘乱序输入", "strong",
            )
        # 重复短语轰炸（"哈哈哈哈哈"在 format 重复率之下仍可出现）
        if len(s) >= 6:
            for n in (1, 2):
                unit = s[:n]
                if unit and s.strip(unit).strip(unit) == "" and len(s) / n >= 6:
                    return RuleFinding(
                        GuardCategory.GARBAGE, RiskLevel.LOW, 0.85,
                        "重复短语轰炸", "strong",
                    )
        return None

    # ── 超范围 ────────────────────────────────────────
    def detect_out_of_scope(self, q: str) -> RuleFinding | None:
        hits = _match_any(_OOS_KEYWORDS, q)
        if not hits or _BUSINESS_NOUNS.search(q):
            return None
        if len(hits) >= 2:
            return RuleFinding(
                GuardCategory.OUT_OF_SCOPE, RiskLevel.LOW, 0.85,
                f"超出业务范围（{hits[0]}）", "strong",
            )
        return RuleFinding(
            GuardCategory.OUT_OF_SCOPE, RiskLevel.LOW, 0.6,
            f"疑似超出业务范围（{hits[0]}）", "weak",
        )

    # ── 模糊问题 ──────────────────────────────────────
    def detect_vague(self, q: str) -> RuleFinding | None:
        if len(q) >= 10:
            return None
        # 带查询意图的短句有明确动作指向，不算模糊（防误杀，2026-09-15）
        if _QUERY_INTENT.search(q):
            return None
        if any(re.match(p, q) for p in _VAGUE_PATTERNS):
            return RuleFinding(
                GuardCategory.AMBIGUOUS, RiskLevel.LOW, 0.7,
                "请求过于模糊", "strong",
            )
        if not _BUSINESS_NOUNS.search(q):
            return RuleFinding(
                GuardCategory.AMBIGUOUS, RiskLevel.LOW, 0.55,
                "短查询且缺少业务对象", "weak",
            )
        return None

    # ── 业务正向信号（用于"放行确认"）────────────────
    def has_business_signal(self, q: str) -> bool:
        return bool(_BUSINESS_NOUNS.search(q))

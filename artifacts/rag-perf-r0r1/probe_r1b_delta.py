"""P1-D v3：分别对比「主分类器」与「兜底」的基线/新版差异。"""
import json
import re as _re

from backend.rag.retrieval import multi_query as mq_mod
from backend.rag.retrieval.hybrid import (
    COMPLEX_STRONG_PATTERNS, COMPLEX_WEAK_PATTERNS, _classify_query_tier,
    is_simple_fact_query,
)

_SIMPLE = ("是多少", "是多久", "多久", "几天", "多长时间", "什么时间",
           "什么时候", "多少钱", "多少个")
_PREFIX = ("什么是", "多少", "几点", "几号", "谁", "哪个", "哪里")
_BIZ = ["差评", "投诉", "退款", "退货", "换货", "售后", "合规", "审核", "罚款",
        "违规", "处罚", "上架", "下架", "Listing", "广告", "投放", "缺货", "断货",
        "调拨", "滞销", "库存", "FBA", "毛利", "利润", "客单价", "转化率", "SKU", "SPU"]


def _baseline_tier(query: str) -> str:
    """d1b9668 版 _classify_query_tier（冻结复刻，含简单事实豁免在强信号之前）。"""
    q = query.strip()
    parts = [x.strip() for x in _re.split(r"[。！？；，、!?;,]", q) if x.strip()]
    qmarks = q.count("？") + q.count("?")
    if (qmarks >= 2 and len(parts) >= 2) or len(parts) >= 3:
        return "hybrid_multi_query"
    simple = any(p in q for p in _SIMPLE) or q.startswith(_PREFIX)
    if simple:
        return "hybrid"
    if any(p in q for p in COMPLEX_STRONG_PATTERNS):
        return "hybrid_multi_query"
    weak = [p for p in COMPLEX_WEAK_PATTERNS if p in q]
    if len(weak) >= 2 and (len(parts) >= 2 or len(q) > 16):
        return "hybrid_multi_query"
    from backend.rag.retrieval.query_router import EXACT_IDENTIFIER_PATTERNS
    for pat in EXACT_IDENTIFIER_PATTERNS:
        if pat.search(q):
            return "hybrid"
    if any(mark in q for mark in ("「", "《", '"')):
        return "hybrid"
    if weak:
        return "hybrid"
    compact = _re.sub(r"[，。！？、；：「」《》\s!?;,.\[\]{}()\"'：:？—\-…]", "", q)
    if len(compact) <= 8:
        return "vector_only"
    return "hybrid"


def _baseline_complex(query: str) -> bool:
    q = query.strip()
    if any(p in q for p in COMPLEX_STRONG_PATTERNS):
        return True
    weak = [p for p in COMPLEX_WEAK_PATTERNS if p in q]
    clauses = len([x for x in _re.split(r"[。！？；，、!?;,]", q) if x.strip()])
    if len(weak) >= 2 and (clauses >= 2 or len(q) > 16):
        return True
    if any(p in q for p in _SIMPLE) or q.startswith(_PREFIX):
        return False
    if any(k in q for k in _BIZ):
        return True
    if len(q) < 5:
        return False
    if len(q) > 40:
        return True
    if weak:
        return False
    return False


QUERIES = [
    "退款审核时间是多少？", "退款审核时间是多少", "退款的审核时间是多少",
    "审核时间", "审核时间是多久", "退款要审核多久", "退款多久到账",
    "库存周转率是多少", "库存周转率怎么算", "库存周转率多少算健康",
    "什么是库存周转率", "现货商品的发货时间是多久？",
    "退货政策是什么，和换货有什么区别？", "对比退款和退货的审核时间",
    "报销流程怎么走？", "订单号 A123456789 的状态", "差评怎么处理",
    "库存周转率的计算公式和统计口径是什么，多久更新一次？",
    "退款和退货的审核时间分别是多少", "审核需要哪些材料、由谁审批、多久出结果",
    "多少个工作日完成审核", "多少钱可以包邮", "哪个部门负责审核",
    "退款申请之后谁审核", "审核一下库存", "帮我分析一下退款审核流程",
]

fallback_diffs, tier_diffs = [], []
for q in QUERIES:
    if _baseline_complex(q) != mq_mod._is_complex(q)[0]:
        fallback_diffs.append({"q": q, "base": _baseline_complex(q),
                               "new": mq_mod._is_complex(q)[0],
                               "reason": mq_mod._is_complex(q)[1]})
    if _baseline_tier(q) != _classify_query_tier(q):
        tier_diffs.append({"q": q, "base": _baseline_tier(q),
                           "new": _classify_query_tier(q),
                           "simple_fact": is_simple_fact_query(q) or None})

print(json.dumps({"fallback_diffs": fallback_diffs, "tier_diffs": tier_diffs},
                 ensure_ascii=False, indent=1))

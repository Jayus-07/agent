"""Evidence Gate 的查询范围与敏感信息判定。

RAG 只能基于当前授权知识库回答。对实时外部事实、凭据/个人敏感信息、
明显越权操作和已知不在知识域覆盖范围内的政策请求，先行拒答，避免
高相关但无权威证据的文档把请求误判为可答。

该模块只做确定性判定，不访问数据库、不调用模型，便于离线回放和审计。
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class QueryScopeDecision:
    """查询范围判定结果。"""

    blocked: bool
    category: str = ""
    reason: str = ""


_REMEDIATION_PATTERNS = (
    "重置",
    "修改",
    "更换",
    "找回",
    "设置",
    "申请",
    "忘记",
)

_BLOCK_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "unauthorized_sensitive",
        "请求涉及他人隐私、凭据或将客户数据导出到个人空间",
        (
            r"(?:查|看|知道|获取).{0,12}(?:别人的工资|同事的绩效)",
            r"(?:同事|他人).{0,8}绩效",
            r"(?:CEO|老板|总经理).{0,8}邮箱",
            r"客户数据.{0,12}(?:个人网盘|私人网盘|个人邮箱)",
            r"绕过.{0,8}(?:审批|授权)",
        ),
    ),
    (
        "credential_disclosure",
        "请求直接披露系统凭据；如需处理凭据应走重置或授权流程",
        (
            r"(?:数据库|客服系统|服务器|系统).{0,8}密码",
            r"密码.{0,8}(?:是多少|是什么|给我|发我)",
            r"(?:token|api[ _-]?key|密钥).{0,8}(?:是多少|是什么|给我|发我)",
        ),
    ),
    (
        "live_external_fact",
        "请求依赖当前时间或外部实时票务信息，当前 RAG 没有可核验实时来源",
        (
            r"现在几点",
            r"(?:鼓浪屿|船票|余票).{0,12}(?:买|订|价格|多少钱|还有)",
            r"(?:实时|当前|现在).{0,10}(?:票价|余票|航班|船班)",
        ),
    ),
    (
        "uncovered_business_policy",
        "请求属于当前知识库未覆盖的外部或专项政策，不能用相似资料代答",
        (
            r"护照办理",
            r"学区",
            r"海外派遣.{0,8}津贴",
            r"团建经费",
            r"内推奖金",
            r"远程办公.{0,10}设备补贴",
            r"机房门禁",
            r"合同盖章.{0,10}(?:走批|流程)",
        ),
    ),
)


def assess_query_scope(question: str) -> QueryScopeDecision:
    """判断请求是否超出当前 RAG 可安全回答的范围。"""

    normalized = re.sub(r"\s+", "", str(question or "")).lower()
    if not normalized:
        return QueryScopeDecision(False)

    for category, reason, patterns in _BLOCK_PATTERNS:
        if category == "credential_disclosure" and any(
            marker in normalized for marker in _REMEDIATION_PATTERNS
        ):
            continue
        if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns):
            return QueryScopeDecision(True, category=category, reason=reason)
    return QueryScopeDecision(False)


__all__ = ["QueryScopeDecision", "assess_query_scope"]

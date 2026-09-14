"""travel/models — 旅游域数据契约

对齐 backend/sql 的 SQLResult、business_analysis 的 BusinessInsight 口径：
Pydantic 模型 + 全字段类型注解，作为域图内部与 Skill 层共用的单一事实源。
"""
from backend.travel.models.brief import (
    PACE_KEYWORDS,
    PACE_LABELS,
    PACE_LEVELS,
    PREFERENCE_KEYWORDS,
    REQUIRED_SLOTS,
    TravelBrief,
)
from backend.travel.models.graph_result import (
    STATUS_FAILED,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NO_DATA,
    STATUS_SUCCESS,
    TravelGraphResult,
    build_travel_graph_result,
)
from backend.travel.models.itinerary import (
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    TransitLeg,
)
from backend.travel.models.poi import Poi
from backend.travel.models.validation import (
    ValidationReport,
    Violation,
)

__all__ = [
    "TravelBrief",
    "REQUIRED_SLOTS",
    "PACE_LEVELS",
    "PACE_LABELS",
    "PREFERENCE_KEYWORDS",
    "PACE_KEYWORDS",
    "Poi",
    "Itinerary",
    "ItineraryDay",
    "ItineraryItem",
    "TransitLeg",
    "Violation",
    "ValidationReport",
    "TravelGraphResult",
    "build_travel_graph_result",
    "STATUS_SUCCESS",
    "STATUS_NEEDS_CLARIFICATION",
    "STATUS_NO_DATA",
    "STATUS_FAILED",
]

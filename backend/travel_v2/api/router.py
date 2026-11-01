from fastapi import APIRouter

from backend.travel_v2.api.auto_optimize import router as auto_optimize_router
from backend.travel_v2.api.bookings import router as bookings_router
from backend.travel_v2.api.exports import router as exports_router
from backend.travel_v2.api.search import router as search_router
from backend.travel_v2.api.shares import router as shares_router
from backend.travel_v2.api.templates import router as templates_router
from backend.travel_v2.api.trips import router as trips_router

router = APIRouter()
router.include_router(trips_router)
router.include_router(templates_router)
router.include_router(search_router)
router.include_router(shares_router)
router.include_router(exports_router)
# 预订确认门（收编重构 §4.2：原聊天子图壳的 REST 化形态）
router.include_router(bookings_router)
# 自动优选（收编重构 U3：只读编排，选入走既有 selections 端点）
router.include_router(auto_optimize_router)

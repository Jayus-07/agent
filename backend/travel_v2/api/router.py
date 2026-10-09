from fastapi import APIRouter

from backend.travel_v2.api.search import router as search_router
from backend.travel_v2.api.templates import router as templates_router
from backend.travel_v2.api.trips import router as trips_router

router = APIRouter()
router.include_router(trips_router)
router.include_router(templates_router)
router.include_router(search_router)

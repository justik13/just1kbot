from aiogram import Router

from .device_view_routes import router as device_view_router
from .legacy_routes import router as legacy_router
from .list_routes import router as list_router

router = Router()

router.include_router(list_router)
router.include_router(device_view_router)
router.include_router(legacy_router)

__all__ = [
    "router",
]

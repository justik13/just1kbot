from aiogram import Router

from .yookassa_routes import router as yookassa_router
from .showcase_routes import router as showcase_router
from .balance_routes import router as balance_router
from .purchase_routes import router as purchase_router
from .tariff_change_routes import router as tariff_change_router

router = Router()
router.include_router(balance_router)
router.include_router(purchase_router)
router.include_router(tariff_change_router)
router.include_router(showcase_router)
router.include_router(yookassa_router)

__all__ = ["router"]

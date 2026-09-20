from importlib import import_module

from aiogram import Router

showcase_router = import_module(".showcase_routes", __name__).router
balance_router = import_module(".balance_routes", __name__).router
purchase_router = import_module(".purchase_routes", __name__).router

router = Router()
router.include_router(balance_router)
router.include_router(purchase_router)
router.include_router(showcase_router)

__all__ = ["router"]

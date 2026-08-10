from aiogram import Router

from services import account_topup as _account_topup
from services.account_topup_refresh import request_topup_status_refresh

# Keep the public account_topup API compatible while the refresh operation is
# isolated from the settlement implementation. Existing handlers import the
# helper from services.account_topup, so attach the same callable before the
# balance router is imported.
_account_topup.request_topup_status_refresh = request_topup_status_refresh

from .showcase_routes import router as showcase_router
from .balance_routes import router as balance_router
from .purchase_routes import router as purchase_router
from .tariff_change_routes import router as tariff_change_router

router = Router()
router.include_router(balance_router)
router.include_router(purchase_router)
router.include_router(tariff_change_router)
router.include_router(showcase_router)

__all__ = ["router"]

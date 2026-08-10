from importlib import import_module

from aiogram import Router

from services import account_topup as _account_topup
from services.account_topup_refresh import request_topup_status_refresh

# Keep the public account_topup API compatible while the refresh operation is
# isolated from the settlement implementation. Existing handlers import the
# helper from services.account_topup, so attach the same callable before the
# balance router is imported.
_account_topup.request_topup_status_refresh = request_topup_status_refresh

showcase_router = import_module(".showcase_routes", __name__).router
balance_router = import_module(".balance_routes", __name__).router
purchase_router = import_module(".purchase_routes", __name__).router
tariff_change_router = import_module(".tariff_change_routes", __name__).router

router = Router()
router.include_router(balance_router)
router.include_router(purchase_router)
router.include_router(tariff_change_router)
router.include_router(showcase_router)

__all__ = ["router"]

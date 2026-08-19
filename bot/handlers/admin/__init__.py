from bot.filters import AdminFilter
from .dashboard import router as dashboard_router
from .users import router as users_router
from .servers import router as servers_router
from .tariffs import router as tariffs_router
from .broadcast import router as broadcast_router
from .payments import router as payments_router
from .payment_queues import router as payment_queues_router
from .purchases import router as purchases_router
from .disputes import router as disputes_router

ADMIN_ROUTERS = (
    dashboard_router,
    users_router,
    servers_router,
    tariffs_router,
    broadcast_router,
    payments_router,
    payment_queues_router,
    purchases_router,
    disputes_router,
)

# ── Centralised admin gate ──────────────────────────────────
# Every admin router is protected with AdminFilter, so *any* new
# handler registered under any admin router is automatically rejected
# for non-admin users without requiring a manual is_admin() call.
for _r in ADMIN_ROUTERS:
    _r.message.filter(AdminFilter())
    _r.callback_query.filter(AdminFilter())

__all__ = [
    "ADMIN_ROUTERS",
    "dashboard_router",
    "users_router",
    "servers_router",
    "tariffs_router",
    "broadcast_router",
    "payments_router",
    "payment_queues_router",
    "purchases_router",
    "disputes_router",
]

from aiogram import Router

from bot.filters import AdminFilter

from .broadcast import router as broadcast_router
from .dashboard import router as dashboard_router
from .disputes import router as disputes_router
from .payment_queues import router as payment_queues_router
from .payments import router as payments_router
from .purchases import router as purchases_router
from .servers import router as servers_router
from .tariffs import router as tariffs_router
from .users import router as users_router

# ── Root Admin Gate ─────────────────────────────────────────
# A single root router that guards the ENTIRE admin tree.
# AdminFilter is attached to admin_router.message and admin_router.callback_query.
# Because aiogram checks root router filters before descending into sub_routers,
# ANY handler in ANY nested admin subrouter is automatically protected.
admin_router = Router(name="admin_root")
admin_router.message.filter(AdminFilter())
admin_router.callback_query.filter(AdminFilter())

# Register all top-level admin branch routers into admin_router using the public API.
# Note: disputes_router is already included by dashboard_router.
admin_router.include_routers(
    dashboard_router,
    users_router,
    servers_router,
    tariffs_router,
    broadcast_router,
    payments_router,
    payment_queues_router,
    purchases_router,
)

__all__ = [
    "admin_router",
    "broadcast_router",
    "dashboard_router",
    "disputes_router",
    "payment_queues_router",
    "payments_router",
    "purchases_router",
    "servers_router",
    "tariffs_router",
    "users_router",
]

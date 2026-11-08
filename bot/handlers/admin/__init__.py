from aiogram import Router

from bot.filters import AdminFilter

from .broadcast import router as broadcast_router
from .dashboard import router as dashboard_router
from .payments import router as payments_router
from .purchases import router as purchases_router

# ── Root Admin Gate ─────────────────────────────────────────
admin_router = Router(name="admin_root")
admin_router.message.filter(AdminFilter())
admin_router.callback_query.filter(AdminFilter())

admin_router.include_routers(
    dashboard_router,
    broadcast_router,
    payments_router,
    purchases_router,
)

__all__ = [
    "admin_router",
    "broadcast_router",
    "dashboard_router",
    "payments_router",
    "purchases_router",
]

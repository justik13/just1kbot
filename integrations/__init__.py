"""Integrations registry for modular, pluggable bot and web extensions."""

import logging
from collections.abc import Sequence

from aiogram import Router
from aiohttp import web

from integrations.amnezia_bridge import AmneziaBridgeIntegration
from integrations.base import BaseIntegration
from integrations.incy import IncyIntegration

logger = logging.getLogger(__name__)

# Registry of all known integration modules
ALL_INTEGRATIONS: Sequence[type[BaseIntegration]] = (
    IncyIntegration,
    AmneziaBridgeIntegration,
)


def register_all_web_routes(app: web.Application) -> None:
    """Iterate over all integrations and register web endpoints if enabled.

    Fail-Fast: raises RuntimeError if an enabled critical integration fails to register its routes
    or if route collisions are detected across integrations.
    """
    seen_routes: set[tuple[str, str]] = {
        (r.method, r.resource.canonical if r.resource else "")
        for r in app.router.routes()
    }

    for integration in ALL_INTEGRATIONS:
        if integration.is_enabled():
            routes_before_len = len(app.router.routes())
            try:
                integration.register_web_routes(app)
                all_routes = list(app.router.routes())
                new_routes = all_routes[routes_before_len:]
                for r in new_routes:
                    m = r.method
                    path = r.resource.canonical if r.resource else ""
                    if (m, path) in seen_routes:
                        raise ValueError(
                            f"Route collision detected: {m} {path} is already registered"
                        )
                    seen_routes.add((m, path))
                logger.info("Integration '%s' registered web routes", integration.name)
            except Exception as e:
                logger.exception(
                    "Failed to register web routes for integration '%s': %s",
                    integration.name,
                    type(e).__name__,
                )
                if getattr(integration, "is_critical", True):
                    raise RuntimeError(
                        f"Failed to register web routes for critical integration '{integration.name}'"
                    ) from e
        else:
            logger.debug("Integration '%s' is disabled, skipping web routes", integration.name)


def get_all_bot_routers() -> list[Router]:
    """Return all active aiogram Routers from enabled integrations.

    Fail-Fast: raises RuntimeError if an enabled critical integration fails to construct its bot router.
    """
    routers = []
    for integration in ALL_INTEGRATIONS:
        if integration.is_enabled():
            try:
                router = integration.get_bot_router()
                if router:
                    routers.append(router)
            except Exception as e:
                logger.exception("Failed to get bot router for integration '%s': %s", integration.name, type(e).__name__)
                if getattr(integration, "is_critical", True):
                    raise RuntimeError(f"Failed to get bot router for critical integration '{integration.name}'") from e
    return routers


__all__ = [
    "ALL_INTEGRATIONS",
    "AmneziaBridgeIntegration",
    "BaseIntegration",
    "IncyIntegration",
    "get_all_bot_routers",
    "register_all_web_routes",
]

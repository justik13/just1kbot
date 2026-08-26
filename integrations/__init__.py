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


def _is_integration_enabled(integration: type[BaseIntegration]) -> bool:
    try:
        return bool(integration.is_enabled())
    except Exception as e:
        name = getattr(integration, "name", type(integration).__name__)
        logger.exception("Failed to evaluate is_enabled for integration '%s': %s", name, type(e).__name__)
        if getattr(integration, "is_critical", True):
            raise RuntimeError(f"Critical integration '{name}' failed during is_enabled check") from e
        return False


def _rollback_new_resources(app: web.Application, resources_before_count: int) -> None:
    """Safely unindex and remove newly added resources from aiohttp application router."""
    if not hasattr(app.router, "_resources"):
        return
    current_resources = list(app.router._resources)
    new_resources = current_resources[resources_before_count:]
    for res in new_resources:
        if hasattr(app.router, "unindex_resource"):
            try:
                app.router.unindex_resource(res)
            except Exception:
                pass
    del app.router._resources[resources_before_count:]


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
        if _is_integration_enabled(integration):
            routes_before_len = len(app.router.routes())
            resources_before_count = len(app.router._resources) if hasattr(app.router, "_resources") else 0
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
                _rollback_new_resources(app, resources_before_count)
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
        if _is_integration_enabled(integration):
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

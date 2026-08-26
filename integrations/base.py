"""Base integration interface for modular and pluggable features."""

from abc import ABC, abstractmethod
from typing import ClassVar

from aiogram import Router
from aiohttp import web


class BaseIntegration(ABC):
    """Abstract base class for modular bot and web integrations."""

    name: ClassVar[str]
    is_critical: ClassVar[bool] = True

    @classmethod
    @abstractmethod
    def is_enabled(cls) -> bool:
        """Check if this integration is configured and enabled in settings."""
        ...

    @classmethod
    def register_web_routes(cls, app: web.Application) -> None:
        """Register HTTP endpoints with the aiohttp application if enabled."""
        return None

    @classmethod
    def get_bot_router(cls) -> Router | None:
        """Return aiogram Router for this integration if enabled, or None."""
        return None

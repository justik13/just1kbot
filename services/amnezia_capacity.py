"""Live Amnezia capacity checks kept outside the device domain service."""

from services.amnezia_client import AmneziaClient, close_http_session


class ServerCapacityUnavailable(RuntimeError):
    """The server capacity could not be verified safely."""


class ServerAtCapacity(RuntimeError):
    """The live Amnezia server has no free client slots."""


async def ensure_server_capacity(
    *,
    api_url: str,
    api_key: str,
    max_clients: int,
) -> None:
    client = AmneziaClient(api_url, api_key)
    try:
        clients = await client.get_all_clients()
        if clients is None:
            raise ServerCapacityUnavailable(
                "Unable to verify live Amnezia capacity"
            )
        if len(clients) >= max_clients:
            raise ServerAtCapacity("Server is full")
    finally:
        await close_http_session()

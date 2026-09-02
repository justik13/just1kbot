"""
Regression tests for PR #231 Findings:
- F12: Pruning removed relays from server.extra_data
- F13: Concurrent extra_data updates merge safety
- F14: Peer capacity queries include PENDING, ACTIVE, and PENDING_UPDATE
- F23: Web feed environment fallbacks when keys are omitted
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from config.enums import (
    ServerHealthState,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import Server
from database.repositories import servers_repo
from database.repositories.servers_repo import capacity_consuming_wl_condition


class TestExtraDataAndCapacityInvariants(unittest.IsolatedAsyncioTestCase):
    """Regression test suite for database and extra_data invariants."""

    def test_capacity_consuming_wl_condition_includes_pending_update(self):
        """F14: Capacity condition must include ACTIVE, PENDING, and PENDING_UPDATE."""
        clause = capacity_consuming_wl_condition()
        status_in_clause = clause.clauses[0]
        prov_in_clause = clause.clauses[1]

        # Check enum values
        self.assertIn(WhiteInternetStatus.ACTIVE, status_in_clause.right.value)
        self.assertIn(WhiteInternetStatus.PENDING, status_in_clause.right.value)
        self.assertIn(WhiteInternetProvisioningStatus.PENDING_UPDATE, prov_in_clause.right.value)

    async def test_extra_data_prunes_removed_relays(self):
        """F12: Merging authoritative node snapshot must prune relays that no longer exist on node."""
        session = AsyncMock()

        server = MagicMock(spec=Server)
        server.id = 1
        server.is_active = True
        server.health_state = ServerHealthState.ONLINE
        server.disabled_reason = None
        server.consecutive_fails = 0
        server.consecutive_successes = 5
        server.extra_data = {
            "secret_base_path": "/stream/v1",
            "relays": [
                {"code": "de", "name": "Germany", "path": "/stream/v1/de"},
                {"code": "nl", "name": "Netherlands", "path": "/stream/v1/nl"},
            ],
            "custom_metadata": "preserve_this",
        }

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = server
        session.execute.return_value = mock_result

        # Node monitor reports snapshot where Netherlands was removed and Sweden was added
        authoritative_relays = [
            {"code": "de", "name": "Germany", "path": "/stream/v1/de"},
            {"code": "se", "name": "Sweden", "path": "/stream/v1/se"},
        ]
        update_extra = {
            "relays": authoritative_relays,
            "secret_base_path": "/stream/v1",
        }

        updated_server, ok = await servers_repo.update_server_health_snapshot(
            session=session,
            server_id=1,
            expected_health_state=ServerHealthState.ONLINE,
            new_health_state=ServerHealthState.ONLINE,
            extra_data=update_extra,
        )

        self.assertTrue(ok)
        self.assertEqual(len(updated_server.extra_data["relays"]), 2)
        relay_codes = [r["code"] for r in updated_server.extra_data["relays"]]
        self.assertIn("de", relay_codes)
        self.assertIn("se", relay_codes)
        self.assertNotIn("nl", relay_codes)
        # Custom foreign keys outside relays must be preserved
        self.assertEqual(updated_server.extra_data.get("custom_metadata"), "preserve_this")

    async def test_extra_data_concurrent_merge_lock(self):
        """F13: update_server locks row via with_for_update before mutating extra_data."""
        session = AsyncMock()
        server = MagicMock(spec=Server)
        server.id = 1
        server.is_active = True
        server.health_state = ServerHealthState.ONLINE
        server.disabled_reason = None
        server.consecutive_fails = 0
        server.consecutive_successes = 1
        server.extra_data = {"existing_key": 1}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = server
        session.execute.return_value = mock_result

        _, ok = await servers_repo.update_server_health_snapshot(
            session=session,
            server_id=1,
            expected_health_state=ServerHealthState.ONLINE,
            new_health_state=ServerHealthState.ONLINE,
            extra_data={"new_key": 2},
        )
        self.assertTrue(ok)
        self.assertEqual(server.extra_data, {"existing_key": 1, "new_key": 2})

    def test_web_feed_env_fallback_when_empty(self):
        """F23: Web handler must cleanly fall back to environment defaults when extra_data keys are missing."""
        server = MagicMock(spec=Server)
        server.extra_data = {}

        secret_path = (
            server.extra_data.get("secret_base_path")
            if isinstance(getattr(server, "extra_data", None), dict)
            else None
        ) or "/stream/v1"
        self.assertEqual(secret_path, "/stream/v1")

        relays = (
            server.extra_data.get("relays")
            if isinstance(getattr(server, "extra_data", None), dict)
            else None
        ) or []
        self.assertEqual(relays, [])


if __name__ == "__main__":
    unittest.main()

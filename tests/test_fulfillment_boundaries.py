import ast
import unittest
from pathlib import Path
from database.models import VPNProfile

ROOT = Path(__file__).parents[1]
PRODUCTION = (
    "services/device_service.py",
    "services/subscription.py",
    "services/profile_deletion_service.py",
    "services/workers/cleanup.py",
)
WRITES = {
    "create_user",
    "create_user_result",
    "update_client",
    "update_client_result",
    "delete_user",
    "delete_user_result",
}


class FulfillmentBoundaryTests(unittest.TestCase):
    def test_business_services_do_not_call_amnezia_writes(self):
        for relative in PRODUCTION:
            tree = ast.parse((ROOT / relative).read_text())
            calls = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            self.assertFalse(calls & WRITES, relative)

    def test_executor_is_the_only_service_write_boundary(self):
        found = set()
        for path in (ROOT / "services").rglob("*.py"):
            if path.name == "amnezia_client.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in WRITES
                for node in ast.walk(tree)
            ):
                found.add(path.relative_to(ROOT).as_posix())
        # Traffic remains read/usage code and is deliberately not part of the
        # user lifecycle boundary; it will be migrated independently.
        self.assertIn("services/api_operations_executor.py", found)
        self.assertNotIn("services/device_service.py", found)
        self.assertNotIn("services/subscription.py", found)

    def test_profile_lifecycle_constraints(self):
        constraints = {item.name for item in VPNProfile.__table__.constraints}
        self.assertIn("ck_vpn_profiles_provisioning_status", constraints)
        self.assertIn("ck_vpn_profiles_desired_version_positive", constraints)


class CapacityBoundaryTests(unittest.TestCase):
    def test_capacity_preflight_runs_outside_handler_transaction(self):
        source = (ROOT / "bot/handlers/connection/device_create_routes.py").read_text()
        commit = source.index("await session.commit()")
        capture = source.index("await capture_server_peer_snapshot", commit)
        create = source.index("await DeviceService.create_device", capture)
        self.assertLess(commit, capture)
        self.assertLess(capture, create)

    def test_device_service_has_no_amnezia_client(self):
        source = (ROOT / "services/device_service.py").read_text()
        self.assertNotIn("AmneziaClient", source)


class DeviceCreateLockRegressionTests(unittest.TestCase):
    def test_creating_devices_lock_uses_telegram_id(self):
        source = (ROOT / "bot/handlers/connection/device_create_routes.py").read_text()
        self.assertIn("telegram_user_id = message.from_user.id", source)
        self.assertIn("db_user_id = user.id", source)
        self.assertIn("_creating_devices[telegram_user_id] = True", source)
        self.assertIn("_creating_devices.pop(telegram_user_id, None)", source)

    def test_creating_devices_lock_released_after_snapshot_failure(self):
        source = (ROOT / "bot/handlers/connection/device_create_routes.py").read_text()
        finally_block = source[source.rindex("    finally:") :]
        self.assertIn("_creating_devices.pop(telegram_user_id, None)", finally_block)

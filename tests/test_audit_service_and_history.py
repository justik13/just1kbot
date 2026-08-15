import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import AuditLog, User
from database.repositories.audit_repo import get_user_audit_logs
from services.audit_service import AuditService
from utils.formatters import format_audit_details


class AuditServiceAndHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_service_dict_serialization(self):
        session = AsyncMock()
        with patch("services.audit_service.create_audit_log", new_callable=AsyncMock) as mock_create:
            await AuditService.log_action(
                session,
                admin_id=123,
                action="TEST_ACTION",
                target_type="User",
                target_id=456,
                details={"amount": 500, "reason": "bonus"},
            )
            mock_create.assert_called_once()
            kwargs = mock_create.call_args.kwargs
            self.assertEqual(kwargs["admin_id"], 123)
            self.assertEqual(kwargs["action"], "TEST_ACTION")
            self.assertEqual(kwargs["target_type"], "user")
            self.assertEqual(kwargs["target_id"], 456)
            parsed = json.loads(kwargs["details"])
            self.assertEqual(parsed, {"amount": 500, "reason": "bonus"})

    async def test_audit_service_helpers(self):
        session = AsyncMock()
        with patch("services.audit_service.create_audit_log", new_callable=AsyncMock) as mock_create:
            await AuditService.log_user_action(
                session,
                user_id=10,
                action="USER_REGISTER",
                details={"telegram_id": 999},
            )
            self.assertEqual(mock_create.call_args.kwargs["target_type"], "user")
            self.assertEqual(mock_create.call_args.kwargs["target_id"], 10)
            self.assertEqual(mock_create.call_args.kwargs["action"], "USER_REGISTER")

        with patch("services.audit_service.create_audit_log", new_callable=AsyncMock) as mock_create:
            await AuditService.log_admin_action(
                session,
                admin_id=777,
                action="EDIT_SERVER",
                target_type="server",
                target_id=2,
                details={"server_name": "Node 1"},
            )
            self.assertEqual(mock_create.call_args.kwargs["admin_id"], 777)
            self.assertEqual(mock_create.call_args.kwargs["target_type"], "server")
            self.assertEqual(mock_create.call_args.kwargs["target_id"], 2)

    def test_format_audit_details_json(self):
        details_json = json.dumps({
            "amount": 490,
            "days": 30,
            "tariff_name": "Стандарт",
            "server_name": "Нидерланды",
            "conversion": True,
            "username": "durov",
        })
        formatted = format_audit_details(details_json)
        self.assertIn("Сумма: 490 ₽", formatted)
        self.assertIn("Срок: 30 дн.", formatted)
        self.assertIn("Тариф: Стандарт", formatted)
        self.assertIn("Сервер: Нидерланды", formatted)
        self.assertIn("Перерасчет: Да", formatted)
        self.assertIn("Username: @durov", formatted)

    def test_format_audit_details_plain_and_kv(self):
        # Key-value format
        kv = "amount=200, days=15, reason=test"
        res = format_audit_details(kv)
        self.assertIn("Сумма: 200 ₽", res)
        self.assertIn("Срок: 15 дн.", res)
        self.assertIn("Причина: test", res)

        # Plain text
        plain = "some raw info"
        self.assertEqual(format_audit_details(plain), " (some raw info)")

        # Empty
        self.assertEqual(format_audit_details(None), "")
        self.assertEqual(format_audit_details(""), "")

    async def test_get_user_audit_logs_query_building(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result

        # Call with user_id and telegram_id
        await get_user_audit_logs(session, user_id=5, telegram_id=500000, offset=0, limit=10)
        session.execute.assert_called_once()
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("audit_logs", compiled.lower())
        self.assertIn("5", compiled)
        self.assertIn("500000", compiled)

    async def test_show_user_audit_handler_rendering(self):
        from bot.handlers.admin.users.list_routes import show_user_audit
        from utils.datetime_helpers import now_utc

        callback = AsyncMock()
        callback.from_user.id = 111
        callback.data = "admin_user_audit:999:1"
        callback.message.edit_text = AsyncMock()

        user = User(id=15, telegram_id=999, username="test_user")
        now = now_utc()
        log1 = AuditLog(
            id=1,
            admin_id=111,
            action="ADMIN_SUB_GRANT",
            target_type="user",
            target_id=15,
            details=json.dumps({"tariff_name": "VIP", "days": "30 дн."}),
            created_at=now,
        )
        log2 = AuditLog(
            id=2,
            admin_id=0,
            action="PAYMENT_SUCCESS",
            target_type="user",
            target_id=15,
            details=json.dumps({"amount": 490, "provider": "yookassa"}),
            created_at=now,
        )

        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.list_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.list_routes._get_user_with_profiles", return_value=user),
            patch("database.repositories.audit_repo.get_user_audit_logs_count", return_value=2),
            patch("database.repositories.audit_repo.get_user_audit_logs", return_value=[log1, log2]),
        ):
            await show_user_audit(callback, session)

            callback.message.edit_text.assert_called_once()
            text_rendered = callback.message.edit_text.call_args.args[0]
            self.assertIn("История действий пользователя ID 999", text_rendered)
            self.assertIn("Выдача подписки админом", text_rendered)
            self.assertIn("Пополнение баланса", text_rendered)
            self.assertIn("Тариф: VIP", text_rendered)
            self.assertIn("Сумма: 490 ₽", text_rendered)

    async def test_admin_device_delete_creates_exactly_one_audit_log(self):
        from database.models import Server, VPNProfile
        from services.device_service import DeviceService

        profile = VPNProfile(
            id=42,
            user_id=10,
            server_id=1,
            device_name="iPhone",
            peer_id="peer-123",
            client_name="tg_999_p42",
            provisioning_status="active",
        )
        server = Server(id=1, name="NL-Node-1", api_url="https://vpn.test", api_key="secret")

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = profile
        session.execute.return_value = mock_result
        session.get.return_value = server

        admin_id = 999999

        with (
            patch("services.device_service.is_admin", return_value=True),
            patch("services.device_service.ensure_delete_operation", new_callable=AsyncMock),
            patch("services.device_service.AuditService.log_action", new_callable=AsyncMock) as mock_log,
        ):
            success = await DeviceService.delete_device(
                session, profile, actor_id=admin_id, force=True
            )
            self.assertTrue(success)

            # MUST be called exactly once with ADMIN_DEVICE_DELETE
            self.assertEqual(mock_log.call_count, 1)
            mock_log.assert_called_once_with(
                session,
                admin_id=admin_id,
                action="ADMIN_DEVICE_DELETE",
                target_type="user",
                target_id=10,
                details={
                    "device_name": "iPhone",
                    "profile_id": 42,
                    "server_name": "NL-Node-1",
                    "force": True,
                },
            )

    async def test_user_device_delete_creates_exactly_one_audit_log(self):
        from database.models import Server, VPNProfile
        from services.device_service import DeviceService

        profile = VPNProfile(
            id=43,
            user_id=12,
            server_id=1,
            device_name="MacBook",
            peer_id="peer-456",
            client_name="tg_888_p43",
            provisioning_status="active",
        )
        server = Server(id=1, name="DE-Node-1", api_url="https://vpn.test", api_key="secret")

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = profile
        session.execute.return_value = mock_result
        session.get.return_value = server

        with (
            patch("services.device_service.ensure_delete_operation", new_callable=AsyncMock),
            patch("services.device_service.AuditService.log_action", new_callable=AsyncMock) as mock_log,
        ):
            success = await DeviceService.delete_device(
                session, profile, actor_id=None, force=False
            )
            self.assertTrue(success)

            # MUST be called exactly once with DEVICE_DELETE
            self.assertEqual(mock_log.call_count, 1)
            mock_log.assert_called_once_with(
                session,
                admin_id=0,
                action="DEVICE_DELETE",
                target_type="user",
                target_id=12,
                details={
                    "device_name": "MacBook",
                    "profile_id": 43,
                    "server_name": "DE-Node-1",
                    "force": False,
                },
            )

    async def test_admin_delete_device_apply_route_does_not_duplicate_audit_log(self):
        from bot.handlers.admin.users.device_routes import admin_delete_device_apply
        from database.models import Server, VPNProfile

        callback = AsyncMock()
        callback.from_user.id = 777
        callback.data = "admin_delete_device_apply:888:42"
        callback.message.edit_text = AsyncMock()

        user = User(id=15, telegram_id=888, username="client")
        profile = VPNProfile(
            id=42,
            user_id=15,
            server_id=1,
            device_name="iPhone",
            peer_id="peer-123",
            client_name="tg_888_p42",
            provisioning_status="active",
            server=Server(id=1, name="NL-Node-1"),
        )

        session = AsyncMock()

        with (
            patch("bot.handlers.admin.users.device_routes.is_admin", return_value=True),
            patch("bot.handlers.admin.users.device_routes._get_user_with_profiles", return_value=user),
            patch("bot.handlers.admin.users.device_routes.get_profile_by_id", return_value=profile),
            patch("bot.handlers.admin.users.device_routes.DeviceService.delete_device", new_callable=AsyncMock, return_value=True) as mock_delete,
        ):
            await admin_delete_device_apply(callback, session)

            mock_delete.assert_called_once_with(
                session,
                profile,
                actor_id=777,
                force=True,
            )
            callback.message.edit_text.assert_called_once()
            # Verify no direct AuditService call occurred in device_routes (module has no AuditService import)
            import bot.handlers.admin.users.device_routes as dr
            self.assertFalse(hasattr(dr, "AuditService"))


if __name__ == "__main__":
    unittest.main()

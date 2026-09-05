import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DB = os.getenv("TEST_DATABASE_URL")


class Migration0022Tests(unittest.TestCase):
    def test_migration_0022_in_script_directory(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        rev = scripts.get_revision("0022_servers_protocol_not_null")
        self.assertIsNotNone(rev)
        self.assertEqual(rev.down_revision, "0021_wi_orphan_cleanups")
        self.assertEqual(scripts.get_heads(), ["0022_servers_protocol_not_null"])

    def test_migration_0022_source_content(self):
        m22_path = Path("alembic/versions/0022_servers_protocol_not_null.py")
        self.assertTrue(m22_path.is_file())
        content = m22_path.read_text(encoding="utf-8")

        self.assertIn("def upgrade()", content)
        self.assertIn("def downgrade()", content)
        self.assertIn("UPDATE servers", content)
        self.assertIn("protocol = 'xray'", content)
        self.assertIn("capabilities @> '[\"xray_origin\"]'::jsonb", content)
        self.assertIn("protocol = 'amneziawg2'", content)
        self.assertIn("nullable=False", content)
        self.assertIn("server_default=\"amneziawg2\"", content)

    def test_server_model_protocol_column_attributes(self):
        from database.models import Server

        col = Server.__table__.c.protocol
        self.assertFalse(col.nullable)
        self.assertIsNotNone(col.server_default)
        self.assertEqual(col.server_default.arg, "amneziawg2")
        self.assertIsNotNone(col.default)
        self.assertEqual(col.default.arg, "amneziawg2")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class Migration0022PostgresTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
                "REDIS_URL": "redis://localhost:6379/1",
                "REDIS_PASSWORD": "test",
                "ADMIN_IDS": "[123456789]",
                "SUPPORT_USERNAME": "test_support",
                "DOMAIN": "test.domain",
                "SSL_EMAIL": "test@domain.com",
                "YOOKASSA_SHOP_ID": "123456",
                "YOOKASSA_SECRET_KEY": "test_secret",
                "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
                "YOOKASSA_WEBHOOK_PORT": "8080",
                "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_URL": DB,
            },
        )
        self.env_patcher.start()
        from config.settings import get_settings

        get_settings.cache_clear()

    def tearDown(self):
        self.env_patcher.stop()
        from config.settings import get_settings

        get_settings.cache_clear()

    async def test_protocol_not_null_and_defaults(self):
        from database.models import Server

        engine = create_async_engine(DB)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            # Test default protocol on insert
            srv1 = Server(
                name=f"test-amnezia-{uuid.uuid4().hex[:6]}",
                api_url="https://awg.test",
                api_key="key",
            )
            session.add(srv1)
            await session.flush()
            self.assertEqual(srv1.protocol, "amneziawg2")

            # Test explicit xray protocol on insert
            srv2 = Server(
                name=f"test-xray-{uuid.uuid4().hex[:6]}",
                api_url="https://xray.test",
                api_key="key",
                protocol="xray",
                capabilities=["xray_origin"],
            )
            session.add(srv2)
            await session.flush()
            self.assertEqual(srv2.protocol, "xray")

            # Test database NOT NULL constraint rejection
            with self.assertRaises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO servers (name, api_url, api_key, protocol, is_active, created_at) "
                        "VALUES ('fail', 'http://fail.test', 'key', NULL, true, NOW())"
                    )
                )
        await engine.dispose()


if __name__ == "__main__":
    unittest.main()

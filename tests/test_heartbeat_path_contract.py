import os
import unittest
from pathlib import Path
from unittest.mock import patch

from services.workers import heartbeat


class HeartbeatPathContractTests(unittest.TestCase):
    def test_python_and_generated_healthcheck_use_production_path(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                Path("/opt/just1kbot/.heartbeat"),
                heartbeat.get_heartbeat_file(),
            )
        deploy = (Path(__file__).parents[1] / "deploy.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('HEARTBEAT_FILE="/opt/just1kbot/.heartbeat"', deploy)
        self.assertNotIn('HEARTBEAT_FILE="/tmp/just1kbot_heartbeat"', deploy)

    def test_local_path_can_be_overridden(self):
        with patch.dict(
            os.environ,
            {"JUST1KBOT_HEARTBEAT_FILE": "/local/test-heartbeat"},
            clear=True,
        ):
            self.assertEqual(
                Path("/local/test-heartbeat"), heartbeat.get_heartbeat_file()
            )

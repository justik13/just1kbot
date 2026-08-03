import os
import unittest
from pathlib import Path
from unittest.mock import patch

from services.workers import heartbeat


class HeartbeatPathContractTests(unittest.TestCase):
    def test_python_and_generated_healthcheck_use_runtime_path(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                Path("/run/just1kbot/heartbeat"),
                heartbeat.get_heartbeat_file(),
            )
        deploy = (Path(__file__).parents[1] / "scripts" / "deploy.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('HEARTBEAT_FILE="$RUNTIME_DIR/heartbeat"', deploy)
        self.assertIn("HEARTBEAT_FILE=/run/just1kbot/heartbeat", deploy)
        self.assertNotIn('HEARTBEAT_FILE="/opt/just1kbot/.heartbeat"', deploy)

    def test_local_file_can_be_overridden_explicitly(self):
        with patch.dict(
            os.environ,
            {"JUST1KBOT_HEARTBEAT_FILE": "/local/test-heartbeat"},
            clear=True,
        ):
            self.assertEqual(
                Path("/local/test-heartbeat"), heartbeat.get_heartbeat_file()
            )

    def test_project_directory_does_not_override_runtime_path(self):
        with patch.dict(
            os.environ,
            {
                "JUST1KBOT_DIR": "/local/project",
                "just1kbot_DIR": "/local/other-project",
            },
            clear=True,
        ):
            self.assertEqual(
                Path("/run/just1kbot/heartbeat"),
                heartbeat.get_heartbeat_file(),
            )


if __name__ == "__main__":
    unittest.main()

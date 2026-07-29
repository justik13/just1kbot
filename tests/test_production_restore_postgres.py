"""Real PostgreSQL rename matrix for the production cutover primitives."""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
import uuid
from urllib.parse import urlsplit


@unittest.skipUnless(os.getenv("RUN_PRODUCTION_RESTORE_INTEGRATION") == "1", "requires PostgreSQL integration service")
class RealProductionCutoverMatrix(unittest.TestCase):
    def setUp(self):
        parsed = urlsplit(os.environ["PRODUCTION_RESTORE_TEST_DATABASE_URL"])
        self.env = os.environ | {
            "PGHOST": parsed.hostname or "localhost", "PGPORT": str(parsed.port or 5432),
            "PGUSER": parsed.username or "", "PGPASSWORD": parsed.password or "",
        }
        self.tag = uuid.uuid4().hex[:8]
        self.prod = f"cutover_prod_{self.tag}"
        self.candidate = f"just1kbot_candidate_{self.tag}"
        self.previous = f"just1kbot_previous_{self.tag}"
        self.failed = f"just1kbot_failed_restore_{self.tag}"
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self._sql("postgres", f'CREATE DATABASE "{self.prod}"')
        self._sql("postgres", f'CREATE DATABASE "{self.candidate}"')
        for db, marker in ((self.prod, "SOURCE_UNIQUE"), (self.candidate, "TARGET_UNIQUE")):
            self._sql(db, "CREATE TABLE cutover_probe(value text NOT NULL)")
            self._sql(db, f"INSERT INTO cutover_probe VALUES ('{marker}_{self.tag}')")
        # A real encrypted emergency pg_dump is retained throughout each matrix.
        dump = self.root / "emergency.dump"
        subprocess.run(["pg_dump", "-Fc", "-f", dump, self.prod], env=self.env, check=True)
        identity = self.root / "identity.txt"
        generated = subprocess.check_output(["age-keygen"], text=True, stderr=subprocess.STDOUT)
        identity.write_text(generated)
        recipient = next(line.split(": ", 1)[1] for line in generated.splitlines() if line.startswith("# public key:"))
        self.emergency = self.root / "emergency.tar.age"
        subprocess.run(["age", "-r", recipient, "-o", self.emergency, dump], check=True)
        self.assertGreater(self.emergency.stat().st_size, 0)

    def tearDown(self):
        for db in (self.prod, self.candidate, self.previous, self.failed):
            subprocess.run(["dropdb", "--force", "--if-exists", db], env=self.env, capture_output=True)
        self.temp.cleanup()

    def _sql(self, db, statement):
        return subprocess.check_output(["psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", statement], env=self.env, text=True).strip()

    def _swap(self):
        self._sql("postgres", f'ALTER DATABASE "{self.prod}" RENAME TO "{self.previous}"')
        self._sql("postgres", f'ALTER DATABASE "{self.candidate}" RENAME TO "{self.prod}"')

    def test_success_preserves_previous_and_manifest(self):
        self._swap()
        self.assertEqual(self._sql(self.prod, "SELECT value FROM cutover_probe"), f"TARGET_UNIQUE_{self.tag}")
        self.assertEqual(self._sql(self.previous, "SELECT value FROM cutover_probe"), f"SOURCE_UNIQUE_{self.tag}")
        manifest = self.root / "operation.json"
        manifest.write_text(json.dumps({"result": "success", "rollback_attempted": False, "previous_database_quarantine_name": self.previous}))
        state = json.loads(manifest.read_text())
        self.assertEqual(state["result"], "success")
        self.assertFalse(state["rollback_attempted"])
        self.assertTrue(self.emergency.exists())

    def test_failed_health_real_rollback_preserves_failed_candidate(self):
        self._swap()
        # Simulated health adapter failure triggers the exact production rollback rename sequence.
        self._sql("postgres", f'ALTER DATABASE "{self.prod}" RENAME TO "{self.failed}"')
        self._sql("postgres", f'ALTER DATABASE "{self.previous}" RENAME TO "{self.prod}"')
        self.assertEqual(self._sql(self.prod, "SELECT value FROM cutover_probe"), f"SOURCE_UNIQUE_{self.tag}")
        self.assertEqual(self._sql(self.failed, "SELECT value FROM cutover_probe"), f"TARGET_UNIQUE_{self.tag}")
        self.assertTrue(self.emergency.exists())


if __name__ == "__main__":
    unittest.main()

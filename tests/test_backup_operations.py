import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import tarfile
import unittest
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
OPS = ROOT / "ops"


class BackupOperationsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backup = (OPS / "backup_postgres.sh").read_text()
        cls.verify = (OPS / "verify_backup.sh").read_text()
        cls.rehearsal = (OPS / "restore_rehearsal.sh").read_text()
        cls.deploy = (ROOT / "deploy.sh").read_text()

    def test_backup_requires_age_recipient(self):
        self.assertIn("BACKUP_AGE_RECIPIENT is missing or invalid", self.backup)

    def test_plaintext_config_is_cleaned_after_success(self):
        self.assertIn("rm -rf -- \"$tmpdir\"", self.backup)

    def test_plaintext_config_is_cleaned_after_error(self):
        self.assertIn("trap finish EXIT INT TERM", self.backup)

    def test_dump_list_precedes_publication(self):
        self.assertLess(self.backup.index("pg_restore --list"), self.backup.index('mv -- "$local_partial" "$final"'))

    def test_corrupt_dump_cannot_publish(self):
        self.assertIn('pg_restore --list "$tmpdir/dump.custom"', self.backup)

    def test_encryption_failure_cannot_publish(self):
        self.assertLess(self.backup.rindex('age -r "$BACKUP_AGE_RECIPIENT"'), self.backup.index('mv -- "$local_partial" "$final"'))

    def test_retention_follows_all_required_publication(self):
        self.assertLess(self.backup.index("required off-site publication failed"), self.backup.index("mapfile -t expired"))

    def test_nonblocking_exclusive_lock(self):
        self.assertIn("flock -n 9", self.backup)

    def test_atomic_local_rename(self):
        self.assertIn('mv -- "$local_partial" "$final"', self.backup)

    def test_checksum_mismatch_is_detected(self):
        self.assertIn("external checksum mismatch", self.verify)

    def test_wrong_identity_is_rejected(self):
        self.assertIn("decryption failed", self.verify)

    def test_malicious_paths_are_rejected(self):
        self.assertIn("p.is_absolute() or '..' in p.parts", self.verify)

    def test_links_are_rejected(self):
        self.assertIn("not member.isfile()", self.verify)

    def test_unknown_format_is_rejected(self):
        self.assertIn("manifest['format_version'] != 1", self.verify)

    def test_missing_config_is_rejected(self):
        self.assertIn("'config.env'", self.verify)

    def test_scripts_do_not_echo_secret_values(self):
        for text in (self.backup, self.verify, self.rehearsal):
            self.assertNotIn("set -x", text)

    def test_offsite_checksum_is_verified(self):
        self.assertIn('sha256sum "$offsite_partial"', self.backup)

    def test_required_offsite_failure_is_fatal(self):
        self.assertIn("required off-site publication failed", self.backup)

    def test_rehearsal_creates_separate_database(self):
        self.assertIn('test_db="just1kbot_rehearsal_', self.rehearsal)

    def test_rehearsal_never_targets_production_database(self):
        self.assertNotIn("just1kbot_bot", self.rehearsal)

    def test_rehearsal_database_removed_after_success(self):
        self.assertIn('dropdb --force --if-exists --maintenance-db="$MAINTENANCE_DATABASE" "$test_db"', self.rehearsal)

    def test_rehearsal_database_removed_after_error(self):
        self.assertIn("trap finish EXIT INT TERM", self.rehearsal)

    def test_keep_option_only_controls_test_database(self):
        self.assertIn("--keep-test-db", self.rehearsal)
        self.assertIn('[[ "$test_db" == just1kbot_rehearsal_* ]]', self.rehearsal)

    def test_destructive_restore_is_removed(self):
        wrapper = (OPS / "just1kbot-restore.sh").read_text()
        self.assertNotIn("dropdb", wrapper)
        self.assertNotIn("systemctl stop", wrapper)

    def test_all_shell_scripts_parse(self):
        for script in [ROOT / "deploy.sh", *OPS.glob("*.sh")]:
            subprocess.run(["bash", "-n", str(script)], check=True)

    def test_deploy_uses_systemd_timer_not_backup_cron(self):
        self.assertIn("Persistent=true", self.deploy)
        self.assertNotIn('echo "0 3 * * * /usr/local/bin/just1kbot-backup.sh"', self.deploy)


class BackupFailurePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.bin = self.root / "bin"; self.bin.mkdir()
        self.backups = self.root / "backups"
        self.envfile = self.root / ".env"
        self.envfile.write_text("DATABASE_URL='postgresql://user:pass@localhost/source'\nDB_ENCRYPTION_KEY='canary'\nREDIS_URL='redis://test'\nBOT_TOKEN='secret-canary'\n")
        self.log = self.root / "calls"
        self.env = os.environ | {"PATH": f"{self.bin}:{os.environ['PATH']}", "ENV_FILE": str(self.envfile),
            "BACKUP_DIR": str(self.backups), "BACKUP_LOCK_FILE": str(self.root / "lock"),
            "BACKUP_AGE_RECIPIENT": "age1testrecipient", "CALL_LOG": str(self.log), "REVISION_FILE": str(self.root / "revisions")}
        (self.root / "revisions").write_text("rev_one\nrev_one\n")
        self._shim("age", '''out=""; input=""; while (($#)); do case "$1" in -o) out=$2; shift 2;; -r|-i) shift 2;; -d) shift;; *) input=$1; shift;; esac; done; [[ ${AGE_FAIL:-0} != 1 ]] || { : >"$out"; exit 9; }; if [[ -n "$input" ]]; then cp "$input" "$out"; else printf encrypted >"$out"; fi''')
        self._shim("pg_dump", '''echo pg_dump >>"$CALL_LOG"; if [[ ${1:-} == --version ]]; then echo 'pg_dump (PostgreSQL) 16.4'; exit; fi; for a in "$@"; do [[ $a == --file=* ]] && printf dump >"${a#--file=}"; done''')
        self._shim("pg_restore", '''echo pg_restore >>"$CALL_LOG"; exit ${PG_RESTORE_FAIL:-0}''')
        self._shim("psql", '''echo psql >>"$CALL_LOG"; n=$(cat "${REVISION_INDEX:-/dev/null}" 2>/dev/null || echo 1); mapfile -t r <"$REVISION_FILE"; echo "${r[$((n-1))]:-rev_one}"; echo $((n+1)) >"${REVISION_INDEX:-/dev/null}"''')
        self._shim("find",'''echo retention >>"$CALL_LOG"; exec /usr/bin/find "$@"''')

    def tearDown(self): self.temp.cleanup()

    def _shim(self, name, body):
        path=self.bin/name; path.write_text("#!/bin/bash\nset -e\n"+body+"\n"); path.chmod(0o755)

    def run_backup(self, **extra):
        env=self.env | {k:str(v) for k,v in extra.items()}; env["REVISION_INDEX"]=str(self.root/"revision-index")
        return subprocess.run([OPS/"backup_postgres.sh"],env=env,text=True,capture_output=True)

    def visible(self, directory=None): return list((directory or self.backups).glob("*.tar.age"))

    def test_missing_recipient_executes_and_publishes_nothing(self):
        result=self.run_backup(BACKUP_AGE_RECIPIENT="")
        self.assertNotEqual(result.returncode,0); self.assertEqual(self.visible(),[])

    def test_age_failure_removes_final_and_partial_files(self):
        result=self.run_backup(AGE_FAIL=1)
        self.assertNotEqual(result.returncode,0); self.assertFalse(self.backups.exists() and list(self.backups.iterdir()))

    def test_locked_second_process_never_calls_dump(self):
        lock=self.root/"lock"
        holder=subprocess.Popen(["flock",str(lock),"sleep","3"])
        try:
            result=self.run_backup(); self.assertNotEqual(result.returncode,0)
            self.assertNotIn("pg_dump",self.log.read_text() if self.log.exists() else "")
        finally: holder.terminate(); holder.wait()

    def test_machine_readable_result_and_persistent_pin(self):
        result_file = self.root / "backup-result"
        pin = "restore_20260729T120000Z_deadbeef"
        result = self.run_backup(BACKUP_RESULT_FILE=result_file, BACKUP_ARTIFACT_PIN=pin)
        self.assertEqual(result.returncode, 0, result.stderr)
        contract = dict(line.split("=", 1) for line in result_file.read_text().splitlines())
        artifact = pathlib.Path(contract["artifact_path"])
        self.assertEqual(contract["artifact_pin"], pin)
        self.assertEqual(contract["artifact_sha256"], __import__("hashlib").sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual((pathlib.Path(str(artifact) + ".pin")).stat().st_mode & 0o777, 0o600)

    def test_retention_preserves_pinned_artifact(self):
        self.backups.mkdir()
        pinned = self.backups / "just1kbot-pg-v1-20200101T000000Z.tar.age"
        pinned.write_text("old"); pathlib.Path(str(pinned) + ".sha256").write_text("sidecar")
        pathlib.Path(str(pinned) + ".pin").write_text("pin")
        for day in ("02", "03", "04"):
            p = self.backups / f"just1kbot-pg-v1-202001{day}T000000Z.tar.age"
            p.write_text(day); pathlib.Path(str(p) + ".sha256").write_text("sidecar")
        result = self.run_backup(BACKUP_RETENTION_COUNT=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(pinned.exists()); self.assertTrue(pathlib.Path(str(pinned) + ".pin").exists())

    def test_alembic_query_failure_does_not_publish(self):
        self._shim("psql","exit 7")
        result=self.run_backup(); self.assertNotEqual(result.returncode,0); self.assertEqual(self.visible(),[])

    def test_alembic_change_does_not_publish(self):
        (self.root/"revisions").write_text("rev_one\nrev_two\n")
        result=self.run_backup(); self.assertNotEqual(result.returncode,0); self.assertEqual(self.visible(),[])

    def test_required_offsite_failure_removes_local_pair(self):
        bad=self.root/"not-a-directory"; bad.write_text("x")
        result=self.run_backup(BACKUP_OFFSITE_DIR=bad,BACKUP_REQUIRE_OFFSITE="true")
        self.assertNotEqual(result.returncode,0); self.assertEqual(self.visible(),[]); self.assertNotIn("tail",self.log.read_text())

    def test_all_offsite_publication_failures_follow_required_policy(self):
        for stage in ("chmod","sidecar_rename","artifact_rename"):
            for required in ("true","false"):
                with self.subTest(stage=stage,required=required):
                    offsite=self.root/f"offsite-{stage}-{required}"; self.log.unlink(missing_ok=True)
                    self._shim("chmod",'''if [[ ${FAIL_STAGE:-} == chmod && "$*" == *"$BACKUP_OFFSITE_DIR"* ]]; then exit 9; fi; exec /bin/chmod "$@"''')
                    self._shim("mv",'''src=${2:-}; if [[ ${FAIL_STAGE:-} == sidecar_rename && $src == "$BACKUP_OFFSITE_DIR"/*.sha256.partial ]]; then exit 9; fi; if [[ ${FAIL_STAGE:-} == artifact_rename && $src == "$BACKUP_OFFSITE_DIR"/*.tar.age.partial ]]; then exit 9; fi; exec /bin/mv "$@"''')
                    result=self.run_backup(BACKUP_OFFSITE_DIR=offsite,BACKUP_REQUIRE_OFFSITE=required,FAIL_STAGE=stage)
                    self.assertEqual(result.returncode==0,required=="false",result.stderr)
                    self.assertEqual(len(self.visible()),1 if required=="false" else 0)
                    self.assertEqual(len(list(self.backups.glob("*.sha256"))),1 if required=="false" else 0)
                    self.assertEqual(self.visible(offsite),[]); self.assertEqual(list(offsite.glob("*.sha256")),[])
                    self.assertEqual(list(offsite.glob(".*partial")),[])
                    calls=self.log.read_text() if self.log.exists() else ""
                    self.assertEqual("retention" in calls,required=="false")
                    shutil.rmtree(self.backups,ignore_errors=True); (self.root/"revision-index").unlink(missing_ok=True)

    def _archive(self, member_kind="safe"):
        bundle=self.root/"bundle.tar"; data=b"x"
        with tarfile.open(bundle,"w") as tf:
            names=["manifest.json","checksums.sha256","dump.custom","config.env"]
            if member_kind in ("absolute","parent"): names[0]="/manifest.json" if member_kind=="absolute" else "../manifest.json"
            for name in names:
                info=tarfile.TarInfo(name); info.size=len(data)
                if member_kind in ("symlink","hardlink") and name=="manifest.json":
                    info.type=tarfile.SYMTYPE if member_kind=="symlink" else tarfile.LNKTYPE; info.linkname="../../escape"; info.size=0
                import io
                tf.addfile(info,io.BytesIO(data) if info.size else None)
        artifact=self.root/"bad.tar.age"; shutil.copy(bundle,artifact); artifact.chmod(0o600)
        (self.root/"bad.tar.age.sha256").write_text(f"{__import__('hashlib').sha256(artifact.read_bytes()).hexdigest()}  bad.tar.age\n")
        identity=self.root/"identity"; identity.write_text("id")
        return artifact, os.environ|{"PATH":f"{self.bin}:{os.environ['PATH']}","AGE_IDENTITY_FILE":str(identity)}

    def _valid_archive(self, internal_lines=None, external_text=None):
        import hashlib, json
        payload=self.root/"payload"; shutil.rmtree(payload,ignore_errors=True); payload.mkdir()
        (payload/"dump.custom").write_bytes(b"custom-dump")
        (payload/"config.env").write_text("DATABASE_URL=x\nDB_ENCRYPTION_KEY=x\nREDIS_URL=x\nBOT_TOKEN=x\n")
        (payload/"manifest.json").write_text(json.dumps({"format_version":1,"created_at_utc":"20260729T000000Z","database_name":"db","postgresql_version":"16","alembic_revision":"rev_one","git_commit_sha":"abc","files":["dump.custom","config.env"]}))
        correct=[f"{hashlib.sha256((payload/name).read_bytes()).hexdigest()}  {name}" for name in ("dump.custom","config.env")]
        (payload/"checksums.sha256").write_text("\n".join(correct if internal_lines is None else internal_lines)+"\n")
        bundle=self.root/"valid-bundle.tar"
        with tarfile.open(bundle,"w") as tf:
            for name in ("manifest.json","checksums.sha256","dump.custom","config.env"): tf.add(payload/name,arcname=name)
        artifact=self.root/"valid.tar.age"; shutil.copy(bundle,artifact); artifact.chmod(0o600)
        digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
        (self.root/"valid.tar.age.sha256").write_text(external_text if external_text is not None else f"{digest}  valid.tar.age\n")
        identity=self.root/"valid-identity"; identity.write_text("id")
        env=os.environ|{"PATH":f"{self.bin}:{os.environ['PATH']}","AGE_IDENTITY_FILE":str(identity),"CALL_LOG":str(self.log)}
        return artifact,env,correct

    def test_strict_external_checksum_schema(self):
        import hashlib
        cases=("0"*64+"  other.tar.age\n","0"*64+"  valid.tar.age\nextra\n","0"*64+"  /valid.tar.age\n")
        for text in cases:
            with self.subTest(text=text):
                artifact,env,_=self._valid_archive(external_text=text)
                self.assertNotEqual(subprocess.run([OPS/"verify_backup.sh",artifact],env=env,capture_output=True).returncode,0)

    def test_strict_internal_checksum_schema_and_hashes(self):
        artifact,env,correct=self._valid_archive()
        bad="0"*64
        cases=(
            [correct[1]], [correct[0]], correct+[f"{bad}  extra"],
            [correct[0],correct[0]], [correct[0],f"{bad}  ../config.env"],
            [f"{bad}  dump.custom",correct[1]],
        )
        for lines in cases:
            with self.subTest(lines=lines):
                artifact,env,_=self._valid_archive(internal_lines=lines)
                self.assertNotEqual(subprocess.run([OPS/"verify_backup.sh",artifact],env=env,capture_output=True).returncode,0)

    def test_correct_checksum_bundle_passes_verification(self):
        artifact,env,_=self._valid_archive()
        result=subprocess.run([OPS/"verify_backup.sh",artifact],env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_absolute_and_parent_archives_are_rejected(self):
        for kind in ("absolute","parent"):
            artifact,env=self._archive(kind); self.assertNotEqual(subprocess.run([OPS/"verify_backup.sh",artifact],env=env,capture_output=True).returncode,0)

    def test_actual_symlink_and_hardlink_archives_are_rejected(self):
        for kind in ("symlink","hardlink"):
            artifact,env=self._archive(kind); self.assertNotEqual(subprocess.run([OPS/"verify_backup.sh",artifact],env=env,capture_output=True).returncode,0)

    def test_corrupt_external_checksum_is_rejected(self):
        artifact,env=self._archive(); (self.root/"bad.tar.age.sha256").write_text("0"*64+"  bad.tar.age\n")
        self.assertNotEqual(subprocess.run([OPS/"verify_backup.sh",artifact],env=env,capture_output=True).returncode,0)

    def test_wrong_identity_is_executed_and_rejected(self):
        artifact,env=self._archive(); self._shim("age","exit 5")
        self.assertNotEqual(subprocess.run([OPS/"verify_backup.sh",artifact],env=env,capture_output=True).returncode,0)

    def _rehearsal(self, drop_fail=False, revision="rev_one"):
        artifact=self.root/"artifact"; artifact.write_text("encrypted"); artifact.chmod(0o600)
        verifier=self.bin/"verify"
        verifier.write_text("#!/bin/bash\nset -e\n[[ $1 == --extract-dir ]]; mkdir -p \"$2\"; printf dump >\"$2/dump.custom\"; printf '{\"alembic_revision\":\"rev_one\"}' >\"$2/manifest.json\"\n"); verifier.chmod(0o755)
        self._shim("createdb",'''echo "$1" >"$DB_STATE"''')
        self._shim("dropdb",'''[[ ${DROP_FAIL:-0} != 1 ]] || exit 9; rm -f "$DB_STATE"''')
        self._shim("pg_restore","exit 0")
        self._shim("psql",f'''input=$(cat); args="$* $input"; if [[ $args == *pg_database* ]]; then [[ -e "$DB_STATE" ]] && echo 1 || echo 0; elif [[ $args == *information_schema* ]]; then echo 1; elif [[ $args == *"count(*) FROM alembic_version"* ]]; then echo 1; elif [[ $args == *"version_num FROM alembic_version"* ]]; then echo {revision}; else echo 1; fi''')
        env=os.environ|{"PATH":f"{self.bin}:{os.environ['PATH']}","VERIFY_BACKUP":str(verifier),"DB_STATE":str(self.root/"database"),"DROP_FAIL":"1" if drop_fail else "0","REHEARSAL_CRITICAL_TABLES":"payments"}
        return subprocess.run([OPS/"restore_rehearsal.sh",artifact],env=env,text=True,capture_output=True), self.root/"database"

    def test_manifest_revision_mismatch_fails_rehearsal(self):
        result,state=self._rehearsal(revision="rev_other")
        self.assertNotEqual(result.returncode,0); self.assertIn("result=failure",result.stdout); self.assertFalse(state.exists())

    def test_drop_failure_is_visible_and_never_reports_success(self):
        result,state=self._rehearsal(drop_fail=True)
        self.assertNotEqual(result.returncode,0); self.assertTrue(state.exists())
        self.assertIn("cleanup=failed",result.stdout); self.assertNotIn("result=success",result.stdout)

    def test_successful_cleanup_is_confirmed(self):
        result,state=self._rehearsal()
        self.assertEqual(result.returncode,0,result.stderr); self.assertFalse(state.exists())
        self.assertIn("result=success",result.stdout); self.assertIn("cleanup=success",result.stdout)

    def test_legacy_cron_filter_preserves_other_lines(self):
        source="MAILTO=root@example.test\n0 3 * * * /usr/local/bin/just1kbot-backup.sh\n*/2 * * * * /usr/local/bin/just1kbot-healthcheck.sh\n5 4 * * * /other --flag\n"
        result=subprocess.check_output(["awk",'!(NF == 6 && $6 == "/usr/local/bin/just1kbot-backup.sh")'],input=source,text=True)
        self.assertNotIn("just1kbot-backup.sh",result); self.assertIn("just1kbot-healthcheck.sh",result); self.assertIn("/other --flag",result); self.assertIn("MAILTO",result)


@unittest.skipUnless(os.getenv("RUN_BACKUP_INTEGRATION") == "1", "requires PostgreSQL and age")
class RealBackupRehearsalIntegrationTest(unittest.TestCase):
    def test_real_dump_verify_and_isolated_restore(self):
        required = ("age", "age-keygen", "pg_dump", "pg_restore", "psql", "createdb", "dropdb")
        if any(shutil.which(command) is None for command in required):
            self.skipTest("backup tools are unavailable")
        url = os.environ["BACKUP_TEST_DATABASE_URL"]
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            identity, envfile, backups = root / "identity", root / ".env", root / "backups"
            subprocess.run(["age-keygen", "-o", identity], check=True, capture_output=True, text=True)
            recipient = subprocess.check_output(["age-keygen", "-y", identity], text=True).strip()
            envfile.write_text(f"DATABASE_URL='{url}'\nDB_ENCRYPTION_KEY='canary-key'\nREDIS_URL='redis://test'\nBOT_TOKEN='canary-token'\n")
            env = os.environ | {"BACKUP_AGE_RECIPIENT": recipient, "AGE_IDENTITY_FILE": str(identity),
                                "ENV_FILE": str(envfile), "BACKUP_DIR": str(backups),
                                "BACKUP_LOCK_FILE": str(root / "backup.lock"), "PROJECT_DIR": str(ROOT),
                                "VERIFY_BACKUP": str(OPS / "verify_backup.sh"),
                                "REHEARSAL_DATABASE_URL": url,
                                "PGHOST": "localhost", "PGPORT": "5432", "PGUSER": "projectx", "PGPASSWORD": "projectx",
                                "REHEARSAL_CRITICAL_TABLES": "backup_rehearsal_data"}
            preserved=f"preserved-{uuid.uuid4()}"
            subprocess.run(["psql", url, "-c", f"CREATE TABLE IF NOT EXISTS backup_rehearsal_data(value text); TRUNCATE backup_rehearsal_data; INSERT INTO backup_rehearsal_data VALUES ('{preserved}');"], check=True, capture_output=True)
            subprocess.run(["psql", url, "-c", "CREATE TABLE IF NOT EXISTS alembic_version(version_num varchar(32)); INSERT INTO alembic_version SELECT 'test' WHERE NOT EXISTS (SELECT 1 FROM alembic_version);"], check=True, capture_output=True)
            completed = subprocess.run([OPS / "backup_postgres.sh"], env=env, text=True, capture_output=True, check=True)
            self.assertNotIn("canary-token", completed.stdout + completed.stderr)
            artifact = next(backups.glob("*.tar.age"))
            subprocess.run([OPS / "verify_backup.sh", artifact], env=env, check=True, capture_output=True)
            kept_db=None
            try:
                rehearsal = subprocess.run([OPS / "restore_rehearsal.sh", "--keep-test-db", artifact], env=env, text=True, capture_output=True, check=True)
                self.assertIn("result=success",rehearsal.stdout); self.assertIn("cleanup=kept",rehearsal.stdout)
                kept_db=rehearsal.stdout.split("rehearsal_database=",1)[1].split()[0]
                restored=subprocess.check_output(["psql","-d",kept_db,"-Atc","SELECT value FROM backup_rehearsal_data"],env=env,text=True).strip()
                self.assertEqual(restored,preserved)
                restored_revision=subprocess.check_output(["psql","-d",kept_db,"-Atc","SELECT version_num FROM alembic_version"],env=env,text=True).strip()
                source_revision=subprocess.check_output(["psql",url,"-Atc","SELECT version_num FROM alembic_version"],text=True).strip()
                self.assertEqual(restored_revision,source_revision)
            finally:
                if kept_db: subprocess.run(["dropdb","--force","--if-exists",kept_db],env=env,check=True)
            self.assertEqual(subprocess.check_output(["psql",url,"-Atc","SELECT value FROM backup_rehearsal_data"],text=True).strip(),preserved)
            cleanup_probe=f"just1kbot_rehearsal_cleanup_probe_{os.getpid()}"
            subprocess.run(["createdb",cleanup_probe],env=env,check=True)
            self.assertEqual(subprocess.check_output(["psql","-XAt","-d","postgres","-v",f"target_db={cleanup_probe}"],env=env,input="SELECT count(*) FROM pg_database WHERE datname = :'target_db';\n",text=True).strip(),"1")
            subprocess.run(["dropdb","--force","--if-exists","--maintenance-db=postgres",cleanup_probe],env=env,check=True)
            self.assertEqual(subprocess.check_output(["psql","-XAt","-d","postgres","-v",f"target_db={cleanup_probe}"],env=env,input="SELECT count(*) FROM pg_database WHERE datname = :'target_db';\n",text=True).strip(),"0")
            rehearsal = subprocess.run([OPS / "restore_rehearsal.sh", artifact], env=env, text=True, capture_output=True, check=True)
            self.assertIn("cleanup=success",rehearsal.stdout)
            dbname=rehearsal.stdout.split("rehearsal_database=",1)[1].split()[0]
            self.assertEqual(subprocess.check_output(["psql",url,"-Atc",f"SELECT count(*) FROM pg_database WHERE datname='{dbname}'"],text=True).strip(),"0")
            self.assertEqual(list(backups.glob(".backup-work.*")), [])

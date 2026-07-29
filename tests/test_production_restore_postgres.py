"""Script-level production restore matrix against a real PostgreSQL server."""
import json, os, pathlib, shutil, subprocess, sys, tempfile, time, unittest, uuid
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[1]
OPS = ROOT / "ops"
LOCK_KEY = 5346144733319417682

@unittest.skipUnless(os.getenv("RUN_PRODUCTION_RESTORE_INTEGRATION") == "1", "requires PostgreSQL and age")
class RestoreProductionScriptIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = pathlib.Path(self.tmp.name)
        p = urlsplit(os.environ["PRODUCTION_RESTORE_TEST_DATABASE_URL"])
        self.pg = os.environ | {"PGHOST":p.hostname or "localhost","PGPORT":str(p.port or 5432),"PGUSER":p.username or "","PGPASSWORD":p.password or ""}
        self.tag=uuid.uuid4().hex[:8]; self.prod=f"prod_{self.tag}"; self.backups=self.root/"backups"; self.opsdir=self.root/"operations"; self.bin=self.root/"bin"
        for d in (self.backups,self.opsdir,self.bin): d.mkdir(mode=0o700)
        self.identity=self.root/"identity"; generated=subprocess.check_output(["age-keygen"],stderr=subprocess.STDOUT,text=True); self.identity.write_text(generated); self.identity.chmod(0o600)
        self.recipient=next(x.split(": ",1)[1] for x in generated.splitlines() if x.startswith("# public key:"))
        self.envfile=self.root/"config.env"; self.key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
        self._write_env(); self._createdb(self.prod); self._migrate(self.prod); self._sql(self.prod,"CREATE TABLE restore_probe(value text NOT NULL); INSERT INTO restore_probe VALUES ('TARGET')")
        target_result=self.root/"target.result"; env=self._base_env()|{"BACKUP_RESULT_FILE":str(target_result)}
        subprocess.run([OPS/"backup_postgres.sh"],env=env,check=True,capture_output=True,text=True)
        contract=dict(line.split("=",1) for line in target_result.read_text().splitlines()); self.artifact=pathlib.Path(contract["artifact_path"])
        self._drop(self.prod); self._createdb(self.prod); self._migrate(self.prod); self._sql(self.prod,"CREATE TABLE restore_probe(value text NOT NULL); INSERT INTO restore_probe VALUES ('SOURCE')")
        self.state=self.root/"service.state"; self.state.write_text("active")
        self.adapter=self.bin/"service-adapter"; self.adapter.write_text(f'''#!/bin/bash
case "$1" in stop) if [[ -f "{self.root/'reject-rollback-stop'}" ]] && [[ $(psql -XAt -d "{self.prod}" -c 'SELECT value FROM restore_probe' 2>/dev/null) == TARGET ]]; then exit 9; fi; echo inactive >"{self.state}";; start) [[ ! -f "{self.root/'start-fail'}" ]] || exit 9; echo active >"{self.state}";; is-active) [[ $(cat "{self.state}") == active ]];; *) exit 2;; esac
'''); self.adapter.chmod(0o755)
        self.health=self.bin/"health"; self.health.write_text(f'''#!/bin/bash
value=$(psql -XAt -d "{self.prod}" -c 'SELECT value FROM restore_probe' 2>/dev/null) || exit 1
[[ ! -f "{self.root/'fail-target'}" || "$value" != TARGET ]] || exit 1
[[ ! -f "{self.root/'fail-all'}" ]] || exit 1
'''); self.health.chmod(0o755)
        self.alembic=self.bin/"alembic"; self.alembic.write_text(f'#!/bin/bash\nexec "{sys.executable}" -m alembic "$@"\n'); self.alembic.chmod(0o755)

    def tearDown(self):
        rows=self._sql("postgres",f"SELECT datname FROM pg_database WHERE datname='{self.prod}' OR datname LIKE 'just1kbot_candidate_%' OR datname LIKE 'just1kbot_previous_%' OR datname LIKE 'just1kbot_failed_restore_%'",check=False).splitlines()
        for db in rows: self._drop(db)
        self.tmp.cleanup()

    def _write_env(self): self.envfile.write_text(f"DATABASE_URL=postgresql://{self.pg['PGUSER']}:{self.pg['PGPASSWORD']}@{self.pg['PGHOST']}:{self.pg['PGPORT']}/{self.prod}\nDB_ENCRYPTION_KEY={self.key}\nREDIS_URL=redis://test\nBOT_TOKEN=123456:TEST\n")
    def _sql(self,db,q,check=True):
        r=subprocess.run(["psql","-XAt","-v","ON_ERROR_STOP=1","-d",db,"-c",q],env=self.pg,text=True,capture_output=True)
        if check and r.returncode: self.fail(r.stderr)
        return r.stdout.strip()
    def _createdb(self,db): subprocess.run(["createdb",db],env=self.pg,check=True,capture_output=True)
    def _drop(self,db): subprocess.run(["dropdb","--force","--if-exists",db],env=self.pg,capture_output=True)
    def _migrate(self,db):
        url=f"postgresql+asyncpg://{self.pg['PGUSER']}:{self.pg['PGPASSWORD']}@{self.pg['PGHOST']}:{self.pg['PGPORT']}/{db}"
        subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=ROOT,env=os.environ|{"DATABASE_URL":url,"DB_ENCRYPTION_KEY":self.key,"BOT_TOKEN":"123456:TEST"},check=True,capture_output=True)
    def _base_env(self):
        return self.pg|{"ENV_FILE":str(self.envfile),"PROJECT_DIR":str(ROOT),"BACKUP_DIR":str(self.backups),"BACKUP_LOCK_FILE":str(self.root/"backup.lock"),"BACKUP_AGE_RECIPIENT":self.recipient,"AGE_IDENTITY_FILE":str(self.identity),"DB_ENCRYPTION_KEY":self.key,"DATABASE_URL":f"postgresql+asyncpg://{self.pg['PGUSER']}:{self.pg['PGPASSWORD']}@{self.pg['PGHOST']}:{self.pg['PGPORT']}/{self.prod}","BOT_TOKEN":"123456:TEST"}
    def _restore_env(self):
        return self._base_env()|{"RESTORE_TEST_MODE":"true","RESTORE_PRODUCTION_DATABASE":self.prod,"RESTORE_OPERATION_DIR":str(self.opsdir),"RESTORE_LOCK_FILE":str(self.root/"restore.lock"),"DEPLOY_LOCK_FILE":str(self.root/"deploy.lock"),"VERIFY_BACKUP":str(OPS/"verify_backup.sh"),"BACKUP_COMMAND":str(OPS/"backup_postgres.sh"),"REHEARSAL_COMMAND":str(OPS/"restore_rehearsal.sh"),"RESTORE_CANDIDATE_VALIDATOR":str(OPS/"validate_restore_candidate.py"),"RESTORE_ADVISORY_HELPER":str(OPS/"hold_restore_advisory_lock.py"),"RESTORE_PYTHON":sys.executable,"RESTORE_ALEMBIC":str(self.alembic),"RESTORE_SERVICE_ADAPTER":str(self.adapter),"RESTORE_HEALTHCHECK":str(self.health),"RESTORE_HEALTH_TIMEOUT":"15","RESTORE_CRASH_WINDOW_SECONDS":"0","RESTORE_PG_FREE_SPACE_BYTES":str(20*1024**3),"TMPDIR":str(self.root)}
    def _run(self,extra=None):
        return subprocess.run([OPS/"restore_production.sh","--artifact",self.artifact,"--confirm-production-restore"],cwd=ROOT,env=self._restore_env()|(extra or {}),text=True,capture_output=True,timeout=240)
    def _manifest(self):
        files=list(self.opsdir.glob("*.json")); self.assertEqual(len(files),1); return json.loads(files[0].read_text())

    def test_actual_script_success_and_advisory_lock_lifetime(self):
        ready=self.root/"lock-ready"; release=self.root/"lock-release"; release.write_text("hold")
        hook=self.bin/"lock-hook"; hook.write_text(f'#!/bin/bash\ntouch "{ready}"\nwhile [[ -e "{release}" ]]; do sleep .1; done\n'); hook.chmod(0o755)
        proc=subprocess.Popen([OPS/"restore_production.sh","--artifact",self.artifact,"--confirm-production-restore"],cwd=ROOT,env=self._restore_env()|{"RESTORE_TEST_AFTER_ADVISORY_HOOK":str(hook)},text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        deadline=time.time()+180
        while not ready.exists() and time.time()<deadline:
            if proc.poll() is not None: self.fail(proc.communicate()[1])
            time.sleep(.2)
        self.assertTrue(ready.exists()); self.assertEqual(self._sql("postgres",f"SELECT pg_try_advisory_lock({LOCK_KEY})"),"f")
        release.unlink(); out,err=proc.communicate(timeout=120); self.assertEqual(proc.returncode,0,err)
        self.assertEqual(self._sql(self.prod,"SELECT value FROM restore_probe"),"TARGET")
        m=self._manifest(); self.assertEqual(m["result"],"success"); self.assertTrue(pathlib.Path(m["emergency_backup_path"]+".pin").is_file())
        self.assertEqual(self._sql("postgres",f"SELECT pg_try_advisory_lock({LOCK_KEY}); SELECT pg_advisory_unlock({LOCK_KEY})").splitlines()[0],"t")
        self.assertEqual(self._sql(m["previous_database_quarantine_name"],"SELECT value FROM restore_probe"),"SOURCE")
        finalize=subprocess.run([OPS/"restore_production.sh","--finalize-operation",m["operation_id"],"--confirm-delete-previous"],cwd=ROOT,env=self._restore_env()|{"RESTORE_FINALIZE_SAFETY_SECONDS":"0"},text=True,capture_output=True,timeout=240)
        self.assertEqual(finalize.returncode,0,finalize.stderr); finalized=self._manifest(); self.assertEqual(finalized["result"],"finalized")
        self.assertTrue(pathlib.Path(finalized["finalize_backup_path"]+".pin").exists()); self.assertEqual(self._sql("postgres",f"SELECT count(*) FROM pg_database WHERE datname='{m['previous_database_quarantine_name']}'"),"0")

    def test_actual_script_automatic_rollback(self):
        (self.root/"fail-target").touch(); r=self._run(); self.assertEqual(r.returncode,20,r.stderr)
        self.assertEqual(self._sql(self.prod,"SELECT value FROM restore_probe"),"SOURCE")
        m=self._manifest(); self.assertEqual(m["result"],"rolled_back"); self.assertEqual(self._sql(m["failed_candidate_name"],"SELECT value FROM restore_probe"),"TARGET"); self.assertTrue(pathlib.Path(m["emergency_backup_path"]+".pin").exists())

    def test_postgresql_helpers_use_safe_stdin_parameters(self):
        sleeper=subprocess.Popen(["psql","-XAt","-d",self.prod,"-c","SELECT pg_sleep(60)"],env=self.pg,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        deadline=time.time()+10
        while time.time()<deadline and self._sql("postgres",f"SELECT count(*) FROM pg_stat_activity WHERE datname='{self.prod}'") == "0": time.sleep(.1)
        r=subprocess.run([OPS/"restore_production.sh","--test-pg-helpers"],env=self._restore_env()|{"RESTORE_TEST_HELPER_DATABASE":self.prod},text=True,capture_output=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr); self.assertIn("exists=true",r.stdout); self.assertIn("size=positive",r.stdout); self.assertIn(f"owner={self.pg['PGUSER']}",r.stdout); self.assertIn("attributes=present",r.stdout); self.assertIn("terminate=success",r.stdout)
        sleeper.wait(timeout=10); self.assertNotEqual(sleeper.returncode,0)
        bad=subprocess.run([OPS/"restore_production.sh","--test-pg-helpers"],env=self._restore_env()|{"RESTORE_TEST_HELPER_DATABASE":"bad';DROP_DATABASE"},capture_output=True); self.assertNotEqual(bad.returncode,0)

    def test_rollback_failure_is_nonterminal_and_blocks_next_restore(self):
        (self.root/"fail-target").touch(); r=self._run({"RESTORE_TEST_FAIL_RENAME_NUMBER":"4"}); self.assertEqual(r.returncode,42,r.stderr)
        self.assertEqual(self._manifest()["result"],"rollback_failed")
        again=self._run(); self.assertNotEqual(again.returncode,0); self.assertIn("incomplete_operation_exists",again.stderr)
        before={p.name:p.read_bytes() for p in self.opsdir.iterdir()}; inspect=subprocess.run([OPS/"restore_production.sh","--inspect-incomplete"],env=self._restore_env(),text=True,capture_output=True); self.assertEqual(inspect.returncode,0,inspect.stderr); self.assertEqual(before,{p.name:p.read_bytes() for p in self.opsdir.iterdir()})

    def test_rollback_stop_failure_performs_no_rollback_rename(self):
        (self.root/"fail-target").touch(); (self.root/"reject-rollback-stop").touch(); r=self._run(); self.assertEqual(r.returncode,42,r.stderr)
        m=self._manifest(); self.assertEqual(m["result"],"rollback_failed"); self.assertEqual(self._sql(self.prod,"SELECT value FROM restore_probe"),"TARGET")
        self.assertEqual(self._sql(m["previous_database_quarantine_name"],"SELECT value FROM restore_probe"),"SOURCE"); self.assertFalse(m["failed_candidate_name"] and self._sql("postgres",f"SELECT count(*) FROM pg_database WHERE datname='{m['failed_candidate_name']}'")!="0")
        again=self._run(); self.assertNotEqual(again.returncode,0)

    def test_first_and_second_rename_failures_recover_old_service(self):
        for number in ("1","2"):
            with self.subTest(rename=number):
                r=self._run({"RESTORE_TEST_FAIL_RENAME_NUMBER":number}); self.assertNotEqual(r.returncode,0); self.assertEqual(self._sql(self.prod,"SELECT value FROM restore_probe"),"SOURCE"); self.assertEqual(self._manifest()["result"],"failed_safe")
            if number=="1":
                shutil.rmtree(self.opsdir); self.opsdir.mkdir(mode=0o700)

    def test_advisory_helper_death_prevents_rename(self):
        r=self._run({"RESTORE_TEST_ADVISORY_EXIT_AFTER_ACQUIRE":"true"}); self.assertNotEqual(r.returncode,0)
        self.assertEqual(self._sql(self.prod,"SELECT value FROM restore_probe"),"SOURCE")
        self.assertEqual(self._manifest()["result"],"failed_safe")

    def test_old_service_health_failure_requires_manual_recovery(self):
        # Arm the old-health failure only after pre-stop health and lock acquisition.
        hook=self.bin/"arm-health-failure"; hook.write_text(f'#!/bin/bash\ntouch "{self.root/"fail-all"}"\n'); hook.chmod(0o755)
        r=self._run({"RESTORE_TEST_FAIL_RENAME_NUMBER":"1","RESTORE_TEST_AFTER_ADVISORY_HOOK":str(hook)}); self.assertEqual(r.returncode,43,r.stderr); self.assertEqual(self._manifest()["result"],"requires_manual_recovery")

    def test_emergency_backup_failure_recovers_or_fails_closed(self):
        failing=self.bin/"backup-failure"; failing.write_text("#!/bin/bash\nexit 9\n"); failing.chmod(0o755)
        r=self._run({"BACKUP_COMMAND":str(failing)}); self.assertNotEqual(r.returncode,0); self.assertEqual(self._manifest()["result"],"failed_safe"); self.assertEqual(self.state.read_text().strip(),"active")
        shutil.rmtree(self.opsdir); self.opsdir.mkdir(mode=0o700); self.state.write_text("active"); (self.root/"start-fail").touch()
        r=self._run({"BACKUP_COMMAND":str(failing)}); self.assertEqual(r.returncode,43,r.stderr); self.assertEqual(self._manifest()["result"],"requires_manual_recovery")

    def test_emergency_verifier_and_rehearsal_failures_record_artifact(self):
        for kind in ("verify","rehearsal"):
            with self.subTest(kind=kind):
                counter=self.root/f"{kind}.count"; wrapper=self.bin/f"fail-{kind}"
                real=OPS/("verify_backup.sh" if kind=="verify" else "restore_rehearsal.sh")
                wrapper.write_text(f'''#!/bin/bash
n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" >"{counter}"
[[ "{kind}" != verify || "$n" -lt 2 ]] || exit 9
[[ "{kind}" != rehearsal || "$n" -lt 1 ]] || exit 9
exec "{real}" "$@"
'''); wrapper.chmod(0o755)
                key="VERIFY_BACKUP" if kind=="verify" else "REHEARSAL_COMMAND"; r=self._run({key:str(wrapper)}); self.assertNotEqual(r.returncode,0); m=self._manifest(); self.assertEqual(m["result"],"failed_safe"); self.assertTrue(m["emergency_backup_path"]); self.assertTrue(pathlib.Path(m["emergency_backup_path"]+".pin").exists()); self.assertEqual(self._sql(self.prod,"SELECT value FROM restore_probe"),"SOURCE")
            if kind=="verify": shutil.rmtree(self.opsdir); self.opsdir.mkdir(mode=0o700); self.state.write_text("active")

    def test_manifest_production_binding_blocks_commands_before_actions(self):
        (self.root/"fail-target").touch(); r=self._run({"RESTORE_TEST_FAIL_RENAME_NUMBER":"4"}); self.assertEqual(r.returncode,42,r.stderr)
        path=next(self.opsdir.glob("*.json")); data=json.loads(path.read_text()); data["original_production_database"]="prod_other"; path.write_text(json.dumps(data)); path.chmod(0o600); before=path.read_bytes(); state=self.state.read_text()
        inspect=subprocess.run([OPS/"restore_production.sh","--inspect-incomplete"],env=self._restore_env(),capture_output=True); self.assertNotEqual(inspect.returncode,0); self.assertEqual(path.read_bytes(),before); self.assertEqual(self.state.read_text(),state)
        rollback=subprocess.run([OPS/"restore_production.sh","--rollback-operation",data["operation_id"],"--confirm-production-rollback"],env=self._restore_env(),capture_output=True); self.assertNotEqual(rollback.returncode,0); self.assertEqual(self.state.read_text(),state)
        finalize=subprocess.run([OPS/"restore_production.sh","--finalize-operation",data["operation_id"],"--confirm-delete-previous"],env=self._restore_env()|{"RESTORE_FINALIZE_SAFETY_SECONDS":"0"},capture_output=True); self.assertNotEqual(finalize.returncode,0); self.assertEqual(path.read_bytes(),before)

if __name__ == "__main__": unittest.main()

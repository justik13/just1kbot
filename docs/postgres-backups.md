# Encrypted PostgreSQL backups

Backups use format version **1**: a PostgreSQL custom dump, a required configuration
component, a JSON manifest, and internal SHA-256 checksums are placed in an explicit
tar allowlist and encrypted with `age`. Only the resulting `*.tar.age` artifact and
its external checksum are published.

Provision an age identity offline. Put only its public recipient in
`/etc/just1kbot-backup.conf` as `BACKUP_AGE_RECIPIENT=age1...`. Never place the
private identity in the backup directory or on the backup server unless an operator
temporarily supplies it for verification/rehearsal via `AGE_IDENTITY_FILE`.

`BACKUP_OFFSITE_DIR` may point at a separately mounted filesystem.
`BACKUP_REQUIRE_OFFSITE=true` makes a verified, atomically renamed off-site copy
mandatory. Retention defaults to 14 artifacts (minimum 2) and runs only after all
required publication succeeds. A rehearsal verifies the archive and restores only
to a uniquely named `just1kbot_rehearsal_*` database; it never cuts over production
or restores `.env`.

The visible `*.tar.age` file is the publication commit marker: its verified checksum
sidecar is renamed first, and the encrypted artifact is renamed last, locally and
off-site. Traps remove partial files and orphan sidecars. Rehearsal success is only
reported after default cleanup is confirmed from a maintenance connection; a failed
drop is reported as `cleanup=failed` with a nonzero exit status. Backup publication
also requires one stable Alembic revision before and after the dump.

Verification treats checksum files as strict schemas: the external sidecar contains
exactly one hash for the artifact basename, while the internal manifest contains
exactly one hash each for `dump.custom` and `config.env`. Off-site publication uses
one controlled failure path for directory creation, copies, validation, permissions,
and both commit-marker renames; optional failures preserve the local pair, whereas
required failures remove it and suppress retention.

## Manual production restore and cutover

Production restore is never scheduled.  A root operator must supply both the encrypted
artifact and the exact, non-interactive confirmation flag:

```bash
AGE_IDENTITY_FILE=/secure/offline-key.txt \
BACKUP_AGE_RECIPIENT=age1... \
sudo /usr/local/bin/restore_production.sh \
  --artifact /root/backups/just1kbot/just1kbot-pg-v1-YYYYMMDDTHHMMSSZ.tar.age \
  --confirm-production-restore
```

The tool and deploy share `/run/lock/just1kbot-deploy.lock`; restore additionally
uses nonblocking restore and backup locks. It pins artifact and sidecar identity,
size, and checksums around strict verification, extracts configuration only into a
mode-0700 workspace, and compares the encryption key without displaying it. It does
**not** replace `.env`.

The dump is restored to a generated `just1kbot_candidate_*` database. Its manifest
revision and critical tables are checked before current migrations are run solely
with a guarded candidate URL. A read-only application validator exercises the ORM
and encrypted fields without polling, workers, messages, payments, or VPN API calls.
Only then is the bot stopped and a retention-exempt encrypted emergency backup made,
strictly verified, and rehearsed.

The short cutover renames production to `just1kbot_previous_*` and candidate to the
production name. The old database is retained. After startup the bounded health
window requires service activity, consecutive fresh heartbeats, the existing health
contract, database reads, durable payment queue reads, and no immediate crash. Any
post-swap failure stops the service, quarantines the candidate as
`just1kbot_failed_restore_*`, restores the previous name, restarts, and repeats health
validation. A rollback-health failure exits with critical code 42 and preserves all
databases and backups.

Root-only JSON state lives in `/root/restore-operations`. Unknown interruptions are
never resumed automatically:

```bash
sudo /usr/local/bin/restore_production.sh --inspect-incomplete
sudo /usr/local/bin/restore_production.sh \
  --rollback-operation restore_YYYYMMDDTHHMMSSZ_deadbeef \
  --confirm-production-rollback
```

Inspection is read-only. Manual rollback accepts only names already recorded in the
root-owned manifest. After a successful operation and the configured safety window,
finalization verifies health and the pinned emergency backup, makes another fresh
encrypted production backup, and only then deletes the exact previous database:

```bash
sudo /usr/local/bin/restore_production.sh \
  --finalize-operation restore_YYYYMMDDTHHMMSSZ_deadbeef \
  --confirm-delete-previous
```

Never remove a previous/failed database or emergency artifact as part of the main
restore flow. Investigate and finalize explicitly.

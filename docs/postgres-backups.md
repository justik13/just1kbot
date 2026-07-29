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

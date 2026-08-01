# Deployment rollback

`deploy.sh` treats an update as one application and operational transaction.
Before stopping the current bot or changing root-owned operational files, it
publishes a root-only snapshot under
`/var/lib/just1kbot/rollback-releases`.

A ready `release-*` snapshot contains:

- application source and the deployed virtual environment;
- the prior application systemd unit;
- installed backup, verification, rehearsal and healthcheck scripts;
- backup and healthcheck systemd service/timer files;
- previous persistent/runtime enable, mask and active unit states;
- logrotate configuration;
- backup configuration and local age identity when present;
- the current domain-specific Nginx site and symlinks when `DOMAIN` is set;
- source commit metadata.

The snapshot excludes `.env`, heartbeat, logs, temporary/cache files,
PostgreSQL data, Redis data, encrypted backup artifacts, UFW state and Let's
Encrypt account/certificate storage.

## Atomic snapshot publication

The existing application snapshot is moved under a hidden
`.incomplete-operational-*` name while operational state is appended. It is
renamed to `release-*` only after both file and unit-state manifests are
complete. A hard interruption therefore cannot expose a partially complete
snapshot as a valid rollback release.

Operational file manifests distinguish `present` and `absent` paths and keep
symlinks as symlinks. Restore removes paths that did not exist before the
deploy. Secret parents under `/root` are created with mode `0700`.

## Mutation ordering

After snapshot publication, an active previous service is stopped before the
first application-directory `rsync`, virtualenv replacement, migration or
operational installation. Deployment requires exact `inactive` state and
verifies that the previous `MainPID` no longer exists.

Only after the application has stopped does the transaction pause backup and
healthcheck timers. It waits for an already-running backup to finish, then
creates the required pre-migration encrypted backup using the previously
installed, known-good backup tooling. New backup scripts, healthcheck, units,
logrotate and Nginx configuration are installed only in the activation stage
after migrations succeed.

## Readiness

The service must remain `active`, keep one nonzero `MainPID`, retain its
initial `NRestarts`, produce a heartbeat newer than the start time and then
advance that heartbeat once more, and pass the installed local healthcheck.
Readiness is bounded by `READINESS_TIMEOUT` and
`READINESS_POLL_INTERVAL`.

The healthcheck uses `/run/just1kbot/heartbeat`, bounded PostgreSQL and Redis
probes, its own lock and a shared deployment-operation lock. Lock contention is
an error rather than a false healthy result.

## Rollback behavior

On failure, deployment:

1. stops the failed application process and captures redacted diagnostics;
2. restores application files while preserving the live `.env`;
3. restores operational files and removes paths that were previously absent;
4. reloads systemd;
5. stops tracked units, restores persistent/runtime enablement, starts units
   that were previously active, and restores persistent/runtime masks last;
6. validates restored Nginx configuration before starting a previously-active
   Nginx service;
7. reloads Nginx after its previous state is restored;
8. starts the previous application release and applies the same readiness gate.

The order supports the rare but valid state where a unit was both active and
masked: rollback temporarily unmasks it, starts it, then reapplies the mask.

A healthy rollback still returns nonzero because the requested release was not
deployed. A failed rollback returns a distinct critical code and leaves the
snapshot for manual diagnosis.

Alembic upgrades are never reversed automatically. An application rollback
after migration can therefore require operator diagnosis when the old code is
not forward-compatible with the upgraded schema. Successful deployments retain
the three newest ready snapshots.

## Credential boundaries

The deploy classifies initial install versus update before configuring Redis.
On update, Redis credentials and production `.env` values are preserved. The
`.env` file is converted to and validated as `root:just1kbot 0640`. Live code
and the virtual environment are `root:just1kbot` and read-only for the service
user; runtime writes are limited to `/run/just1kbot` and
`/var/log/just1kbot`.

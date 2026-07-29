# Application deployment rollback

`deploy.sh` treats the application update as a transaction. Before changing
`/opt/just1kbot`, it atomically publishes a root-only snapshot under
`/var/lib/just1kbot/rollback-releases`. The snapshot contains application
source, the deployed virtual environment, the prior systemd unit, and source
commit metadata. It excludes `.env`, heartbeat, logs, temporary/cache files,
and all PostgreSQL, Redis, and encrypted-backup data.

After publishing the snapshot, an active previous service is stopped before
the first application-directory `rsync`, venv update, migration, or unit
change. Deployment requires exact `inactive` state and verifies that the old
`MainPID` no longer exists. Failure to establish both facts ends the deploy
without mutating the active release. The new readiness gate rejects reuse of
the saved old PID, so `systemctl start` cannot accidentally validate a process
that was already running.

Readiness is bounded (75 seconds by default). The service must remain
`active`, keep one nonzero `MainPID`, retain its initial `NRestarts`, produce a
heartbeat newer than the start time and then advance that heartbeat once more,
and pass the installed local healthcheck. `READINESS_TIMEOUT` and
`READINESS_POLL_INTERVAL` may tune the window; production paths are not
environment-overridable through the deploy entry point.

On failure, deployment stops the service, records redacted systemd diagnostics,
restores the snapshot while preserving the live `.env`, restores the prior
unit, and applies the same readiness gate to the prior version. A healthy
rollback still returns nonzero because the requested release was not deployed.
A failed rollback returns a distinct critical code and leaves the snapshot for
manual diagnosis.

Alembic upgrades are never reversed automatically. An application rollback
after migration can therefore require operator diagnosis when the old code is
not forward-compatible with the upgraded schema. Successful deployments retain
the three newest rollback snapshots; cleanup warnings do not stop a healthy
service.

The deploy classifies initial install versus update before configuring Redis.
An existing `.env` must be a root/bot-owned regular non-symlink file without
group or other permissions. On update, Redis `requirepass` and `.env` are both
left unchanged; deployment never rotates an infrastructure credential from an
interactive application update. Initial installation retains the existing
credential setup and creates `.env` with mode `0600`.

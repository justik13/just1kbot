# Legacy / incomplete installation reset

Use `scripts/reset_legacy_install.sh` only when there is **no valid ownership manifest** and an old or interrupted Just1kBot installation blocks a fresh deployment.

Typical symptoms:

- `/usr/local/sbin/just1kbot` exists, but `/opt/just1kbot/deploy.sh` is missing;
- `state` reports an incomplete or residual installation;
- a stale installer journal remains under `/var/lib/just1kbot/install-state/transaction.json`;
- a previous failed install left Just1kBot service units, helpers, or dedicated Redis artifacts;
- the normal manifest-driven uninstall cannot run because no valid manifest exists.

## Reset

From the project checkout:

```bash
sudo bash scripts/reset_legacy_install.sh
```

The script requires the exact confirmation:

```text
RESET JUST1KBOT
```

It refuses to run when an ownership manifest already exists. For a managed installation, use the normal uninstall commands instead.

The reset removes only resources that can be positively identified as Just1kBot legacy resources:

- legacy global CLI when it is root-owned and contains the Just1kBot control-plane markers;
- known Just1kBot systemd units only after their ownership markers are verified; foreign or unverified units are **not stopped, disabled, or deleted**;
- known Just1kBot helper tools with the Just1kBot marker;
- `/opt/just1kbot` when its `deploy.sh` is confirmed as a Just1kBot tree;
- dedicated Redis data/config only when both the dedicated Redis unit and its config prove ownership; otherwise they are preserved and reported;
- the Just1kBot service user only when the transaction journal proves the installer created `service-user:just1kbot`; otherwise the user is preserved and reported;
- stale Just1kBot state and installer logs.

PostgreSQL `just1kbot_bot` and role `just1kbot` are removed **only** when both objects contain the same `managed-by=just1kbot;installation-id=...` ownership marker. Otherwise they are preserved and reported for manual verification.

The reset deliberately does **not** modify:

- `/etc/redis/redis.conf` or global Redis;
- UFW, nftables, or iptables;
- unrelated Nginx sites;
- Docker, WireGuard, or Amnezia resources.

## Reinstall after reset

```bash
sudo bash deploy.sh state
sudo bash deploy.sh deploy --dry-run
sudo bash deploy.sh deploy
```

If PostgreSQL, dedicated Redis resources, or the `just1kbot` service user were preserved because ownership could not be proven, inspect each resource manually before deciding whether to remove it.

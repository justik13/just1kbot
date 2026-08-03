# Just1kBot

Telegram-бот для продажи VPN-доступа на базе **AmneziaWG 2.0**.
Пользователь покупает подписку, создаёт устройства и получает конфигурации,
а администратор управляет серверами, тарифами, пользователями, платежами и
рассылками из Telegram.

## Возможности

### Пользователь

- пополнение внутреннего баланса через YooKassa;
- покупка, продление и смена тарифа с баланса;
- создание, переименование и удаление VPN-устройств;
- получение `.vpn` и `.conf` конфигураций;
- история операций, профиль подписки и реферальная программа;
- уведомления об окончании подписки и раздел поддержки.

### Администратор

- статистика пользователей, подписок и серверов;
- управление пользователями, банами, тарифами и подписками;
- добавление отдельных Amnezia-серверов через Telegram;
- просмотр и ручная обработка платежей;
- рассылки с сохранением прогресса;
- аудит административных действий и режим технических работ.

## Стек

| Компонент | Технология |
|---|---|
| Python | 3.12 |
| Telegram | aiogram 3 |
| ORM | SQLAlchemy 2 async |
| База данных | PostgreSQL 16 |
| FSM | отдельный Redis 7 |
| HTTP | aiohttp; managed Nginx или внешний reverse proxy |
| Платежи | YooKassa |
| VPN | Amnezia API, `amneziawg2` |
| Миграции | Alembic |
| Backup | `pg_dump` + `age` |

# Production installer

## Платформа

**Ubuntu 24.04 LTS и Python 3.12 — основная полностью протестированная
production-платформа.** Именно эта комбинация используется в CI.

Installer не делает жёсткий запрет только по номеру ОС. Другие версии Ubuntu и
Debian допускаются как совместимые, если read-only preflight подтверждает:

- `apt` и `dpkg-query`;
- работающий systemd;
- системный Python **ровно 3.12**, под который создан `requirements.lock`;
- необходимые PostgreSQL, Redis и системные команды;
- отсутствие конфликтов с существующими ресурсами.

На неподдерживаемой или несовместимой системе installer завершается до
управляемых изменений и объясняет, какой capability отсутствует.

## Работа на занятом сервере

Чистый сервер не требуется. До первой мутации проверяются:

- зарезервированные пути, типы файлов и symlink;
- service account и systemd unit names;
- PostgreSQL role/database;
- Redis unit и порт;
- CLI path;
- домен, Nginx site, сертификат и внутренний HTTP port;
- незавершённые transaction journals.

После длительного package step проверки повторяются, чтобы закрыть TOCTOU race.
Installer:

- устанавливает только отсутствующие packages и не выполняет `apt upgrade`;
- не изменяет UFW, nftables или iptables;
- не изменяет `/etc/redis/redis.conf`;
- не удаляет Nginx default site;
- не принимает чужой path/unit/database/site/certificate под управление;
- не трогает Docker, WireGuard, AmneziaWG и VPN peers;
- выводит operation, stage, problem, cause и конкретное следующее действие.

Если установка пакета `redis-server` временно запустила глобальный Redis,
installer возвращает этот generic service в прежнее inactive/disabled состояние.
Just1kBot использует только собственный Redis unit.

## Изолированный Redis

```text
just1kbot-redis.service
127.0.0.1:6380
/etc/just1kbot/redis.conf
/var/lib/just1kbot/redis/
```

Общий Redis на `6379` не перенастраивается и не очищается. При переходе со
старой установки ephemeral FSM-состояния не копируются: namespace Aiogram
`fsm:*` сам по себе не доказывает принадлежность конкретному боту. Старый Redis
остаётся без изменений.

Dedicated Redis создаётся внутри той же rollback-covered activation transaction,
что systemd, operational tooling, proxy configuration и global CLI.

## Ownership manifest и durable journal

Root-only manifest:

```text
/var/lib/just1kbot/install-state/manifest.json
```

Durable journal текущей операции:

```text
/var/lib/just1kbot/install-state/transaction.json
```

Manifest содержит installation ID, platform metadata, proxy/Redis mode и точный
список управляемых paths, units, PostgreSQL objects, Nginx sites и certificates.
PostgreSQL role/database дополнительно получают `COMMENT`:

```text
managed-by=just1kbot;installation-id=<uuid>
```

Update, repair и uninstall требуют совпадения ownership proof. Повреждённый или
неоднозначный state блокирует destructive operation.

Если процесс прерван или сервер перезагрузился:

```bash
sudo just1kbot state
sudo just1kbot install-recover
sudo just1kbot install-rollback
```

Journal создаётся до `apt-get`. При ошибке первичной установки выполняется
автоматический manifest-driven rollback. Если сбой произошёл до manifest,
удаляется пустой journal; установленные system packages намеренно не удаляются.
Update использует application/operational rollback и сохраняет journal для
последующей диагностики.

## Воспроизводимые зависимости

Production virtualenv устанавливается только из committed lock:

```bash
python -m pip install --no-deps --require-hashes -r requirements.lock
```

Lock генерируется и проверяется на Ubuntu 24.04 / Python 3.12 с toolchain
`pip==25.3`, `pip-tools==7.5.2`. Запрещены alternate indexes, trusted hosts,
VCS/local/direct URL dependencies и requirements без SHA-256 hashes.

# Установка

Read-only state и dry run:

```bash
sudo bash deploy.sh state
sudo bash deploy.sh deploy --dry-run
```

## Managed Nginx/TLS

По умолчанию installer использует уже работающий системный Nginx:

```bash
sudo bash deploy.sh deploy
```

Он не включает и не запускает глобальный Nginx service автоматически. Nginx
должен быть active и его текущий `nginx -t` должен проходить. Installer создаёт
только manifest-owned site и certificate выбранного домена. Существующий
certificate без ownership proof не усыновляется.

Публикуются только:

```text
POST /webhook/yookassa
GET  /health
```

## External proxy mode

Для Caddy, Traefik, Apache, собственного Nginx layout или отдельной ingress-ноды:

```bash
sudo just1kbot deploy --external-proxy
```

В этом режиме:

- Nginx и Certbot не устанавливаются и не требуются;
- глобальный proxy/TLS не изменяется;
- приложение слушает только автоматически выбранный свободный loopback port;
- создаётся root-only upstream contract:

```text
/var/lib/just1kbot/install-state/external-proxy.nginx.conf
```

Показать contract:

```bash
sudo just1kbot proxy-config
```

## Неинтерактивная установка

```bash
sudo env \
  BOT_TOKEN='...' \
  DB_PASSWORD='...' \
  REDIS_PASSWORD='...' \
  ADMIN_IDS='123456789,987654321' \
  SUPPORT_USERNAME='support_username' \
  DOMAIN='vpn.example.com' \
  SSL_EMAIL='owner@example.com' \
  YOOKASSA_SHOP_ID='...' \
  YOOKASSA_SECRET_KEY='...' \
  bash deploy.sh --yes
```

Для external proxy добавьте:

```text
JUST1KBOT_PROXY_MODE=external
```

# Global CLI и state-aware menu

После установки доступен root-owned launcher:

```text
/usr/local/sbin/just1kbot
```

Без аргументов он показывает меню, зависящее от состояния установки. При
`foreign_collision`, `corrupted_state` или неизвестном ownership mutating
пункты скрыты; доступны только state, doctor и support bundle.

Основные команды:

```bash
sudo just1kbot state
sudo just1kbot status
sudo just1kbot doctor
sudo just1kbot doctor --json
sudo just1kbot logs
sudo just1kbot restart
sudo just1kbot backup
sudo just1kbot repair --check
sudo just1kbot repair --apply
sudo just1kbot support-bundle
```

`repair --apply` исправляет только manifest-owned drift: permissions, legacy
service shell, CLI launcher, enabled/active state подтверждённых units. Он не
усыновляет и не переписывает Nginx/TLS/PostgreSQL data/firewall/foreign files.
После применения обязательно запускается полный ownership-aware doctor.

Support bundle создаётся вне installation state:

```text
/root/just1kbot-support-bundles/
```

Он не содержит `.env`, dumps, backup archives, age identity, tokens, passwords,
API keys или credential-bearing URLs. Это явный operator export и uninstall его
автоматически не удаляет.

# Обновление из GitHub

Фиксированный источник:

```text
https://github.com/justik13/projectx.git
refs/heads/main
```

Проверка:

```bash
sudo just1kbot update --check
```

Интерактивное обновление требует вручную ввести полный fetched SHA:

```bash
sudo just1kbot update
```

Unattended update требует заранее проверенный полный SHA:

```bash
sudo just1kbot update \
  --sha 0123456789abcdef0123456789abcdef01234567 \
  --yes
```

Updater проверяет `git fsck`, clean checkout, exact commit, отсутствие symlink и
submodule, а также наличие всего production safety stack: installer policies,
manifest/journal, complete updater, repair, doctor, support bundle и
ownership-aware uninstall. Неполный release не публикуется.

# Backup и restore

```bash
sudo just1kbot backup
sudo just1kbot verify-backup /path/backup.tar.age
sudo just1kbot restore-test /path/backup.tar.age
sudo just1kbot restore-production /path/backup.tar.age
sudo just1kbot restore-status
sudo just1kbot restore-recover
sudo just1kbot restore-rollback
sudo just1kbot restore-finalize
```

Encrypted PostgreSQL backups:

```text
/root/backups/just1kbot/
```

Age identity:

```text
/root/.config/just1kbot/backup.agekey
```

Production restore сначала использует staging database, проверяет её, создаёт
pre-cutover backup и только затем выполняет короткий cutover.

# Полное безопасное удаление

Сохранить PostgreSQL и backups:

```bash
sudo just1kbot uninstall --keep-data
```

Перед остановкой сервисов создаётся и проверяется свежий encrypted backup.
Application, dedicated Redis, units, CLI, Nginx/TLS или external proxy contract
удаляются, а residual manifest сохраняет ownership только оставшихся данных.

Полностью удалить manifest-owned installation и data:

```bash
sudo just1kbot uninstall --purge-data
```

Требуется точная фраза:

```text
DELETE JUST1KBOT
```

Uninstall:

- сверяет manifest resource и PostgreSQL `COMMENT` с installation ID;
- удаляет только подтверждённые paths/units/user/database/site/certificate;
- не изменяет firewall, global Redis, foreign proxy sites или system packages;
- не вызывает Docker, WireGuard, AWG или VPN tooling;
- выполняет post-scan files, units, processes, user, PostgreSQL и proxy/TLS;
- не выводит success, пока manifest-owned остатки существуют.

# Amnezia API

Бот не использует глобальные `AMNEZIA_API_URL` и `AMNEZIA_API_KEY`. Каждый
VPN-сервер добавляется через Telegram-админку, а API key хранится в PostgreSQL в
зашифрованном виде.

`scripts/setup-amnezia-api.sh` — standalone utility для ручного запуска на
отдельной VPN-ноде. Installer, update, repair, menu и uninstall её не вызывают,
не копируют в global CLI и не включают в ownership manifest бота.

# CI

Primary workflow: Ubuntu 24.04 / Python 3.12.

Он выполняет:

- deterministic regeneration и installation `requirements.lock`;
- Ruff и ShellCheck;
- root control-plane help;
- 500+ unit/contract/runtime tests;
- shared-host package scenarios;
- state collision tests;
- failure-injection matrix до и внутри rollback-covered activation;
- ownership-aware uninstall contracts;
- Alembic upgrade, downgrade до base, проверку отсутствия application tables и
  повторный upgrade;
- compile всех Python trees;
- `git diff --check`.

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
- управление отдельными Amnezia-серверами;
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
| HTTP | aiohttp + Nginx |
| Платежи | YooKassa |
| VPN | Amnezia API, `amneziawg2` |
| Миграции | Alembic |
| Backup | `pg_dump` + `age` |

# Production installer

## Поддерживаемая платформа

Автоматический installer поддерживает **только Ubuntu 24.04 LTS** с системным
Python 3.12. На другой ОС он завершается до установки пакетов и ничего не
изменяет.

Installer рассчитан на сервер, где уже могут работать другие приложения. Он:

- проверяет зарезервированные пути, symlink, units, порты, PostgreSQL и Nginx
  до `apt-get`;
- не изменяет UFW, nftables или iptables;
- не изменяет `/etc/redis/redis.conf`;
- не удаляет и не отключает Nginx default site;
- не трогает Docker, WireGuard, AmneziaWG и другие VPN-приложения;
- отказывается перезаписывать существующий ресурс без доказанного ownership;
- выводит операцию, этап, проблему, причину и конкретное следующее действие.

## Изоляция Redis

Just1kBot использует отдельный systemd-сервис:

```text
just1kbot-redis.service
127.0.0.1:6380
/etc/just1kbot/redis.conf
/var/lib/just1kbot/redis/
```

Общий Redis на `6379` не перенастраивается и не очищается. При переходе со
старой установки ephemeral FSM-состояния не копируются: стандартный namespace
Aiogram `fsm:*` не доказывает принадлежность конкретному боту. Старый Redis
остаётся без изменений.

## Ownership manifest и durable journal

Все управляемые ресурсы записываются в root-only manifest:

```text
/var/lib/just1kbot/install-state/manifest.json
```

Текущая изменяющая операция записывается в:

```text
/var/lib/just1kbot/install-state/transaction.json
```

Manifest содержит installation ID, платформу, режим Redis и список конкретных
paths, systemd units, PostgreSQL objects, Nginx sites и сертификатов. Uninstall
не удаляет ресурс, которого нет в manifest.

Если процесс был прерван или сервер перезагрузился, обычный deploy блокируется:

```bash
sudo bash deploy.sh state
sudo bash deploy.sh install-recover
sudo bash deploy.sh install-rollback
```

`install-rollback` автоматически удаляет только ресурсы, созданные незавершённой
первичной установкой. Для update используется application rollback и затем
`install-recover` подтверждает healthy state.

## Зависимости

`requirements.txt` задаёт допустимый диапазон для разработки.
Production virtualenv устанавливается только из `requirements.lock`:

```bash
python -m pip install --no-deps --require-hashes -r requirements.lock
```

Lock генерируется и проверяется на Ubuntu 24.04 / Python 3.12 с зафиксированным
toolchain `pip==25.3`, `pip-tools==7.5.2`. CI требует byte-for-byte совпадения
повторной генерации и проверяет установку всех hash-locked пакетов.

# Установка

Запускать из отдельного checkout репозитория:

```bash
sudo bash deploy.sh deploy
```

Старый совместимый запуск также направляется в safe installer:

```bash
sudo bash deploy.sh --yes
```

Неинтерактивный пример:

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

Обязательны реальные значения YooKassa, публичный домен и email для Certbot.
`SUPPORT_USERNAME` передаётся без `@`.

Перед изменениями можно выполнить:

```bash
sudo bash deploy.sh state
sudo bash deploy.sh deploy --dry-run
```

Dry run описывает план и не изменяет сервер.

## Что публикует Nginx

Для указанного домена создаётся только manifest-owned site. Публикуются:

```text
POST /webhook/yookassa
GET  /health
```

Остальные маршруты возвращают `404`. Приложение слушает только
`127.0.0.1:8080`. Существующий Nginx default site не удаляется.

Если `nginx -t` после изменения не проходит, предыдущий site автоматически
восстанавливается.

# Обновление из GitHub

Источник и ref зафиксированы:

```text
https://github.com/justik13/projectx.git
refs/heads/main
```

Проверить доступную версию:

```bash
sudo bash deploy.sh update --check
```

Интерактивное обновление показывает fetched commit и требует ввести полный SHA:

```bash
sudo bash deploy.sh update
```

Для автоматизации ожидаемый commit обязателен:

```bash
sudo bash deploy.sh update \
  --sha 0123456789abcdef0123456789abcdef01234567 \
  --yes
```

Updater:

- загружает только фиксированный `main`;
- сравнивает fetched commit с ожидаемым SHA до публикации release;
- запрещает symlink, submodule и control characters в tracked paths;
- проверяет `git fsck`, clean checkout и обязательные installer files;
- сохраняет exact SHA в `.release-version` и manifest metadata;
- разворачивает код через transactional application deploy;
- при ошибке сохраняет проверенный release и понятную диагностику.

# Операционные команды

После установки доступна команда:

```bash
sudo just1kbot state
sudo just1kbot status
sudo just1kbot doctor
sudo just1kbot logs
sudo just1kbot restart
sudo just1kbot backup
```

Эквивалент через checkout или live control plane:

```bash
sudo bash deploy.sh <command>
```

`doctor` выполняет read-only проверки:

- Ubuntu 24.04;
- manifest и отсутствие незавершённого journal;
- application и dedicated Redis services;
- systemd sandbox, runtime HOME и permissions;
- heartbeat;
- PostgreSQL/Alembic, Redis и Telegram API;
- Nginx configuration;
- backup timers, возраст и SHA-256 последнего backup.

# Backup и restore

Автоматический encrypted PostgreSQL backup создаётся timer-ом и хранится в:

```text
/root/backups/just1kbot/
```

Локальный age identity:

```text
/root/.config/just1kbot/backup.agekey
```

Ключ необходимо сохранить вне production-сервера. Без него backup нельзя
расшифровать.

Команды:

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

Production restore сначала восстанавливает staging database, проверяет её,
создаёт свежий pre-cutover backup и только затем выполняет короткий cutover.
Предыдущая database сохраняется до отдельного `restore-finalize`.

# Удаление

Официальный uninstall manifest-driven и требует явный режим.

Удалить приложение, dedicated Redis и runtime, сохранив PostgreSQL и backups:

```bash
sudo just1kbot uninstall --keep-data
```

Перед остановкой сервисов создаётся новый backup и выполняется его строгая
verification.

Удалить все manifest-owned данные:

```bash
sudo just1kbot uninstall --purge-data
```

Для purge требуется TTY и точная фраза:

```text
DELETE JUST1KBOT
```

Uninstall:

- проверяет ownership каждого удаляемого path/unit/user/database/certificate;
- не изменяет firewall;
- не трогает global Redis;
- не удаляет чужие Nginx sites или сертификаты;
- не вызывает Docker или VPN tooling;
- после удаления собирает все остатки и не выводит success, пока они существуют.

Старые `scripts/deploy.sh` и `scripts/uninstall.sh` являются только совместимыми
wrappers и не позволяют обойти safe control plane.

# Amnezia API

Бот не требует глобальные `AMNEZIA_API_URL` и `AMNEZIA_API_KEY`. Каждый
VPN-сервер добавляется через Telegram-админку, а API key сохраняется в
PostgreSQL в зашифрованном виде.

`scripts/setup-amnezia-api.sh` — отдельная standalone-утилита для VPN-ноды. Она
не вызывается installer, update, repair, menu или uninstall. Запускать её нужно
только вручную на соответствующей ноде после отдельного review.

# CI

Основной workflow работает на Ubuntu 24.04 / Python 3.12 и выполняет:

- повторную генерацию и проверку `requirements.lock`;
- установку зависимостей через `--require-hashes`;
- Python static analysis;
- ShellCheck всех shell scripts;
- полный unittest suite;
- shared-host installer regression tests;
- Alembic upgrade, downgrade до base и повторный upgrade;
- проверку отсутствия application tables после downgrade;
- компиляцию всех Python trees;
- `git diff --check`.

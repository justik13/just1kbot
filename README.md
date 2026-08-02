# Just1kBot

Telegram-бот для продажи VPN-доступа на базе **AmneziaWG 2.0**.

Пользователь покупает подписку, создаёт устройства и получает конфигурации для подключения. Администратор управляет серверами, тарифами, пользователями, платежами и рассылками непосредственно из Telegram.

## Возможности

### Пользователь

- пополнение внутреннего баланса через YooKassa и отдельная покупка, продление или смена тарифа с баланса;
- создание, переименование и удаление VPN-устройств;
- получение `.vpn` и `.conf` конфигураций;
- история операций баланса и профиль подписки;
- реферальная программа;
- уведомления об окончании подписки;
- раздел поддержки.

### Администратор

- статистика пользователей, подписок и серверов;
- управление пользователями, банами и подписками;
- управление Amnezia-серверами и тарифами;
- просмотр и ручная обработка платежей;
- рассылки с сохранением прогресса;
- аудит административных действий;
- режим технических работ.

## Стек

| Компонент | Технология |
|---|---|
| Язык | Python 3.11+ |
| Telegram | aiogram 3 |
| ORM | SQLAlchemy 2 async |
| База данных | PostgreSQL |
| Очереди и FSM | Redis |
| Платежи | YooKassa |
| VPN API | Amnezia API, `amneziawg2` |
| HTTP | aiohttp |
| Шифрование | Fernet |
| Миграции | Alembic |

## Структура

```text
bot/                         Telegram handlers, middlewares и webhook
config/                      настройки приложения
database/                    модели, repositories и подключение к PostgreSQL
services/                    платежи, Amnezia, подписки и workers
scripts/                     deploy, backup, restore, uninstall и Amnezia tooling
scripts/ops/                 canonical backup/restore/deployment transaction scripts
scripts/lib/                 PostgreSQL и operational rollback libraries
alembic/                     миграции PostgreSQL
deploy.sh                    единственная корневая shell-точка входа и меню
```

## Локальный запуск

```bash
git clone <repo-url>
cd projectx
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m bot.main
```

Минимальный `.env`:

```env
BOT_TOKEN=123456:ABC-DEF
ADMIN_IDS=[123456789]
DATABASE_URL=postgresql+asyncpg://user:password@127.0.0.1:5432/database
DB_ENCRYPTION_KEY=<Fernet key>
REDIS_URL=redis://:password@127.0.0.1:6379/0
REDIS_PASSWORD=password

YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_RETURN_URL=https://t.me/{bot_username}
YOOKASSA_WEBHOOK_PORT=8080
```

Генерация `DB_ENCRYPTION_KEY`:

```bash
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

`DB_ENCRYPTION_KEY` нельзя менять или терять. Им зашифрованы конфигурации VPN и секреты Amnezia в PostgreSQL.

# Production deployment

## Первичная установка

Запускать из отдельного checkout репозитория на Ubuntu или Debian:

```bash
sudo bash deploy.sh
```

Неинтерактивный вариант:

```bash
sudo env \
  BOT_TOKEN='...' \
  DB_PASSWORD='...' \
  REDIS_PASSWORD='...' \
  ADMIN_IDS='123456789' \
  DOMAIN='vpn.example.com' \
  SSL_EMAIL='admin@example.com' \
  YOOKASSA_SHOP_ID='...' \
  YOOKASSA_SECRET_KEY='...' \
  bash deploy.sh --yes
```

`ADMIN_IDS` для `deploy.sh --yes` передаётся числами через запятую, например `ADMIN_IDS='123456789,987654321'`. Скрипт сам сохранит значение в `.env` как JSON-массив `[123456789,987654321]`, который ожидает Pydantic.

`DOMAIN` имеет смысл только вместе с YooKassa. Nginx публикует только:

- `POST /webhook/yookassa`;
- `GET /health`.

Остальные HTTP-маршруты возвращают `404`. Порт приложения `8080`, PostgreSQL и Redis закрываются UFW для внешнего доступа.

## Безопасное обновление

Новый код должен находиться в отдельном checkout или release-каталоге. Нельзя сначала заменять файлы непосредственно в `/opt/just1kbot`, иначе rollback не сможет сохранить предыдущую версию.

```bash
cd /root/releases/projectx-new
sudo bash deploy.sh
```

При существующем `/opt/just1kbot/.env` скрипт автоматически выбирает режим обновления:

1. проверяет `.env`, production secrets, PostgreSQL cluster и Redis;
2. нормализует `DOMAIN`, прежде чем использовать его в root-owned Nginx paths;
3. атомарно сохраняет предыдущий код, virtualenv, основной systemd unit и operational state;
4. snapshot включает backup/restore/health scripts, timers, logrotate, backup config/key и текущий Nginx site;
5. останавливает старый процесс и подтверждает завершение прежнего PID;
6. останавливает operational timers и ждёт завершения уже запущенного backup;
7. создаёт обязательный encrypted PostgreSQL backup старым проверенным tooling;
8. копирует новый release и создаёт новый root-owned virtualenv;
9. запускает `alembic upgrade head`;
10. устанавливает новые operational scripts, units, Nginx и основной application unit;
11. запускает новую версию и ожидает два обновления heartbeat;
12. проверяет PostgreSQL и Redis с ограниченными таймаутами;
13. при ошибке возвращает application release, operational files, timer states и Nginx configuration.

Автоматический downgrade PostgreSQL при rollback **не выполняется**. Если новая миграция несовместима со старым кодом, требуется ручное решение администратора.

## Dry run

```bash
sudo bash deploy.sh --dry-run
```

Команда только показывает план и ничего не изменяет.

## Эксплуатационные команды

```bash
sudo bash deploy.sh status
sudo bash deploy.sh logs
sudo bash deploy.sh restart
sudo bash deploy.sh backup
```

Старые совместимые флаги `--status`, `--logs`, `--restart` и `--backup` также поддерживаются. Неизвестная команда завершается с кодом `2` и никогда не запускает deployment.

### Статус

```bash
sudo bash deploy.sh status
```

Показывает:

- состояние приложения, PostgreSQL, Redis и Nginx;
- состояние backup и healthcheck timers;
- `MainPID` и число systemd-рестартов;
- возраст heartbeat;
- результат проверки PostgreSQL и Redis.

### Логи

```bash
sudo bash deploy.sh logs
```

Открывает `journalctl -u just1kbot -f`.

### Перезапуск

```bash
sudo bash deploy.sh restart
```

Команда ждёт готовность приложения. Успех возвращается только после появления свежего heartbeat и успешной проверки PostgreSQL с Redis.

# Backup и восстановление

## Автоматический backup

После deployment устанавливается systemd timer:

```bash
systemctl status just1kbot-backup.timer
```

Backup создаётся ежедневно около `03:00 UTC` и сохраняется в:

```text
/root/backups/just1kbot/
```

Артефакт содержит:

- PostgreSQL custom-format dump;
- зашифрованную копию production `.env`;
- Alembic revision;
- manifest и checksums.

Архив шифруется `age`. При первичной установке без готовой backup-конфигурации создаются:

```text
/etc/just1kbot-backup.conf
/root/.config/just1kbot/backup.agekey
```

Закрытый ключ `/root/.config/just1kbot/backup.agekey` необходимо скопировать в защищённое место вне production-сервера. Потеря этого ключа делает backup нечитаемым.

Ручной backup:

```bash
sudo bash deploy.sh backup
```

## Проверка восстановления

`restore-test` не заменяет рабочую production-БД. Команда расшифровывает backup, создаёт временную PostgreSQL database, восстанавливает данные, проверяет Alembic revision и критические таблицы, затем удаляет временную database.

```bash
sudo AGE_IDENTITY_FILE=/root/.config/just1kbot/backup.agekey \
  bash deploy.sh restore-test \
  /root/backups/just1kbot/just1kbot-pg-v1-YYYYMMDDTHHMMSSZ.tar.age
```

Production restore/cutover выполняется только вручную после успешного rehearsal, полной остановки writers и отдельного подтверждённого плана восстановления.

# Rollback deployment

Перед обновлением snapshots сохраняются в:

```text
/var/lib/just1kbot/rollback-releases/
```

Хранятся последние три полностью готовых `release-*`. Незавершённый operational snapshot остаётся под скрытым `.incomplete-operational-*` и никогда не считается готовым release snapshot.

Snapshot не копирует production `.env`, PostgreSQL data, Redis data и encrypted backup artifacts. Он сохраняет:

- предыдущий application code и virtualenv;
- основной systemd unit;
- установленные backup/restore/health scripts;
- backup и healthcheck units/timers вместе с enabled/active state;
- logrotate configuration;
- backup config и локальный age identity, если они существовали;
- текущий domain-specific Nginx site и symlink state.

При неудачном запуске новой версии deployment transaction:

- останавливает неуспешный процесс;
- возвращает предыдущий application release;
- сохраняет текущий production `.env`;
- восстанавливает operational files и отсутствовавшие до deploy paths;
- возвращает persistent/runtime enable, mask и active state units;
- проверяет восстановленную Nginx configuration до запуска Nginx;
- запускает предыдущую версию и повторяет readiness gate.

Схема PostgreSQL автоматически назад не откатывается. UFW и Let's Encrypt account/certificate storage не входят в automatic rollback snapshot.

# Healthcheck

Systemd timer запускает healthcheck каждые две минуты:

```bash
systemctl status just1kbot-healthcheck.timer
journalctl -u just1kbot-healthcheck.service
```

Проверяются:

- активность systemd-сервиса;
- heartbeat `/run/just1kbot/heartbeat` и возраст не более 180 секунд;
- `SELECT 1` в PostgreSQL;
- `PING` в Redis;
- загрузка production `.env` из `/opt/just1kbot`.

Healthcheck имеет отдельный lock, shared deploy-operation lock, process timeout и сетевые таймауты. Конфликт lock возвращает ошибку, а не ложный healthy status.

# YooKassa

YooKassa используется только для пополнения внутреннего рублёвого баланса. Подтверждённый `payment.succeeded` зачисляется в append-only ledger ровно один раз; тариф не активируется webhook-ом. Покупка, продление и смена тарифа выполняются отдельным подтверждением и атомарным списанием с баланса.

Публичный webhook:

```text
POST https://<DOMAIN>/webhook/yookassa
```

Публичный health endpoint:

```text
GET https://<DOMAIN>/health
```

Webhook должен быть настроен в кабинете YooKassa только после успешного deployment и проверки HTTPS.

# Amnezia API

Бот работает с протоколом `amneziawg2`.

Настройка API-сервера запускается через единственную корневую точку входа:

```bash
sudo bash deploy.sh amnezia
```

После настройки сервер добавляется через Telegram-админку.

# Безопасность

- Amnezia API keys и VPN configs шифруются в PostgreSQL;
- секреты фильтруются из traceback и логов;
- webhook имеет ограничение размера request body;
- Redis, PostgreSQL и внутренний webhook port не публикуются наружу;
- systemd unit работает от отдельного пользователя;
- live code и virtualenv принадлежат root и доступны service user только для чтения;
- release rollback не перезаписывает `.env`;
- обновление запрещено из live-каталога;
- migrations выполняются только после обязательного encrypted backup;
- operational tooling меняется только внутри rollback transaction;
- restore по команде является только изолированным rehearsal.

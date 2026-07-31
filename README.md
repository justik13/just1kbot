# Just1kBot

Telegram-бот для продажи VPN-доступа на базе **AmneziaWG 2.0**.

Пользователь покупает подписку, создаёт устройства и получает конфигурации для подключения. Администратор управляет серверами, тарифами, пользователями, платежами и рассылками непосредственно из Telegram.

## Возможности

### Пользователь

- покупка и продление подписки через YooKassa;
- создание, переименование и удаление VPN-устройств;
- получение `.vpn` и `.conf` конфигураций;
- история платежей и профиль подписки;
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
ops/                         backup, restore rehearsal и deployment transaction
alembic/                     миграции PostgreSQL
deploy.sh                    установка, обновление и эксплуатационные команды
setup-amnezia-api.sh         настройка HTTPS для Amnezia API
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

Остальные HTTP-маршруты возвращают `404`. Порт приложения `8080`, PostgreSQL `5432` и Redis `6379` закрываются UFW для внешнего доступа.

## Безопасное обновление

Новый код должен находиться в отдельном checkout или release-каталоге. Нельзя сначала заменять файлы непосредственно в `/opt/just1kbot`, иначе rollback не сможет сохранить предыдущую версию.

```bash
cd /root/releases/projectx-new
sudo bash deploy.sh
```

При существующем `/opt/just1kbot/.env` скрипт автоматически выбирает режим обновления:

1. проверяет, что `.env` является обычным файлом с закрытыми permissions;
2. проверяет наличие `BOT_TOKEN`, `DATABASE_URL`, `REDIS_URL` и корректного `DB_ENCRYPTION_KEY`;
3. не запрашивает и не меняет production-пароли;
4. останавливает старый процесс и проверяет, что его PID завершён;
5. создаёт обязательный зашифрованный PostgreSQL backup;
6. сохраняет предыдущий код, virtualenv и systemd unit в rollback snapshot;
7. копирует новый release и устанавливает зависимости;
8. запускает `alembic upgrade head`;
9. запускает новую версию;
10. ожидает два обновления heartbeat и проверяет PostgreSQL с Redis;
11. при ошибке возвращает предыдущий application release.

Автоматический downgrade PostgreSQL при rollback **не выполняется**. Если новая миграция несовместима со старым кодом, требуется ручное решение администратора.

## Dry run

```bash
sudo bash deploy.sh --dry-run
```

Команда только показывает план и ничего не изменяет.

## Эксплуатационные команды

```bash
sudo bash deploy.sh --status
sudo bash deploy.sh --logs
sudo bash deploy.sh --restart
sudo bash deploy.sh --backup
```

Неизвестный или ошибочно написанный аргумент завершает скрипт с кодом `2`. Полный deployment при этом не запускается.

### Статус

```bash
sudo bash deploy.sh --status
```

Показывает:

- состояние приложения, PostgreSQL, Redis и Nginx;
- состояние backup и healthcheck timers;
- `MainPID` и число systemd-рестартов;
- возраст heartbeat;
- результат проверки PostgreSQL и Redis.

### Логи

```bash
sudo bash deploy.sh --logs
```

Открывает `journalctl -u just1kbot -f`.

### Перезапуск

```bash
sudo bash deploy.sh --restart
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
sudo bash deploy.sh --backup
```

## Проверка восстановления

`--restore` не заменяет рабочую production-БД. Он расшифровывает backup, создаёт временную PostgreSQL database, восстанавливает данные, проверяет Alembic revision и критические таблицы, затем удаляет временную database.

```bash
sudo AGE_IDENTITY_FILE=/root/.config/just1kbot/backup.agekey \
  bash deploy.sh --restore \
  /root/backups/just1kbot/just1kbot-pg-v1-YYYYMMDDTHHMMSSZ.tar.age
```

Production restore/cutover выполняется только вручную после успешного rehearsal, полной остановки writers и отдельного подтверждённого плана восстановления.

# Rollback приложения

Перед обновлением release snapshots сохраняются в:

```text
/var/lib/just1kbot/rollback-releases/
```

Хранятся последние три snapshot. В них не копируется production `.env`.

При неудачном запуске новой версии deployment transaction:

- останавливает неуспешный процесс;
- возвращает предыдущий код, virtualenv и systemd unit;
- сохраняет текущий production `.env`;
- запускает предыдущую версию;
- повторно выполняет readiness gate.

Схема PostgreSQL автоматически назад не откатывается.

# Healthcheck

Systemd timer запускает healthcheck каждые две минуты:

```bash
systemctl status just1kbot-healthcheck.timer
journalctl -u just1kbot-healthcheck.service
```

Проверяются:

- активность systemd-сервиса;
- наличие heartbeat и возраст не более 180 секунд;
- `SELECT 1` в PostgreSQL;
- `PING` в Redis;
- загрузка production `.env` из `/opt/just1kbot`.

# YooKassa

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

Пример настройки API-сервера:

```bash
sudo bash setup-amnezia-api.sh \
  --domain api.example.com \
  --email admin@example.com
```

После настройки сервер добавляется через Telegram-админку.

# Безопасность

- Amnezia API keys и VPN configs шифруются в PostgreSQL;
- секреты фильтруются из traceback и логов;
- webhook имеет ограничение размера request body;
- Redis, PostgreSQL и внутренний webhook port не публикуются наружу;
- systemd unit работает от отдельного пользователя;
- release rollback не перезаписывает `.env`;
- обновление запрещено из live-каталога;
- migrations выполняются только после обязательного encrypted backup;
- restore по команде является только изолированным rehearsal.

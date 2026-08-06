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
| FSM | Redis 7 |
| HTTP | aiohttp + Caddy |
| Платежи | YooKassa |
| VPN | Amnezia API, `amneziawg2` |
| Миграции | Alembic |
| Backup | `pg_dump` + `gzip` + `age` |

# Установка и запуск (Docker)

> [!WARNING]
> **Только для новых установок (Greenfield)!**
> Данный способ развертывания предназначен только для запуска бота на новых, чистых серверах. Миграция с системных (systemd/bash) установок не поддерживается этим PR.

Проект использует Docker Compose для запуска бота, PostgreSQL, Redis и Caddy.
Caddy является единственной публичной точкой входа и проксирует внутренний
HTTP endpoint бота на `bot:8080`.

## Требования

- Docker Engine
- Docker Compose v2
- публичный DNS-запись для `DOMAIN`, направленная на VPS
- свободные порты `80` и `443`

## Первая установка

1. Склонируйте репозиторий:
   ```bash
   git clone https://github.com/justik13/just1kbot.git
   cd just1kbot
   ```

2. Создайте файл настроек:
   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

3. Заполните `.env`.

   Важные параметры:
   - `BOT_TOKEN`, `ADMIN_IDS`, `SUPPORT_USERNAME`;
   - `DB_ENCRYPTION_KEY`;
   - `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`;
   - `REDIS_PASSWORD`;
   - `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_RETURN_URL`;
   - `DOMAIN`, `SSL_EMAIL`;
   - `BACKUP_AGE_RECIPIENT`.

   Пароли PostgreSQL и Redis указываются **как обычные raw-значения**. URL-кодировать их вручную не нужно: Docker entrypoint формирует `DATABASE_URL` и `REDIS_URL` с корректным URL-encoding автоматически.

4. Запустите проект:
   ```bash
   docker compose up -d --build
   ```

   При старте бот дождётся PostgreSQL и Redis, применит Alembic migrations и затем запустит приложение.

5. Проверьте состояние:
   ```bash
   docker compose ps
   docker compose logs --tail=100 bot
   ```

   В production ожидается:
   - `db` — healthy;
   - `redis` — healthy;
   - `bot` — healthy;
   - `caddy` — running.

## Обновление и rollback

Для обновления:

```bash
git pull
docker compose up -d --build
```

Миграции выполняются автоматически при старте нового контейнера бота.

Перед обновлением production рекомендуется иметь свежий зашифрованный backup БД.

**Rollback приложения:**

```bash
git reset --hard <previous-commit>
docker compose up -d --build
```

Rollback должен выполняться только после проверки совместимости схемы БД.
Если новая миграция уже была применена, простой откат Git-коммита не откатывает
схему PostgreSQL автоматически. Для destructive schema rollback используйте
отдельную процедуру восстановления БД из backup.

# Бэкапы и восстановление

PostgreSQL, Redis и данные Caddy хранятся в Docker volumes. Зашифрованные
PostgreSQL backups сохраняются в локальную директорию `./backups/`.

> [!CAUTION]
> **Приватный ключ `age` НИКОГДА не должен храниться на production-сервере.**
> На сервере хранится только `BACKUP_AGE_RECIPIENT` — публичный recipient для
> шифрования. Приватный ключ нужен только для расшифровки и восстановления.

> [!WARNING]
> **Ограничения Disaster Recovery**
> Локальные зашифрованные backups защищают от повреждения приложения или базы
> данных, но не являются полноценным Disaster Recovery решением. Если VPS или
> его диск будет потерян, локальные backups также будут потеряны. Для полноценного
> DR копируйте `.sql.gz.age` во внешнее независимое хранилище.

## Автоматические бэкапы через host cron

Backup container не работает постоянно. Он запускается как одноразовый job.
Для ежедневного запуска используйте cron на host-системе.

Создайте запись через `crontab -e`:

```cron
# Каждый день в 02:00
0 2 * * * flock -n /tmp/just1kbot-backup.lock sh -c 'cd /absolute/path/to/just1kbot && docker compose --profile tools run --rm backup >> /absolute/path/to/just1kbot/backups/backup.log 2>&1'
```

Замените `/absolute/path/to/just1kbot` на фактический путь проекта.
Пользователь, от имени которого работает cron, должен иметь доступ к Docker.

`flock` предотвращает параллельный запуск двух backup jobs.

## Ручной backup

```bash
docker compose --profile tools run --rm backup
```

Backup выполняется по схеме:

```text
PostgreSQL
  ↓
pg_dump
  ↓
gzip
  ↓
age encrypt (BACKUP_AGE_RECIPIENT)
  ↓
*.sql.gz.age
```

После успешного шифрования plaintext dump удаляется. При ошибке backup script
также удаляет временный plaintext dump.

Старые encrypted backups старше 7 дней удаляются автоматически.

## Восстановление

1. Скопируйте `.sql.gz.age` на локальный компьютер, где хранится private age key.
2. Расшифруйте backup локально:
   ```bash
   age -d -i private_key.txt backup.sql.gz.age > backup.sql.gz
   ```
3. Распакуйте:
   ```bash
   gunzip backup.sql.gz
   ```
4. Остановите бота:
   ```bash
   docker compose stop bot
   ```
5. Перед восстановлением создайте новый backup текущей БД.
6. Скопируйте SQL dump в PostgreSQL container:
   ```bash
   docker cp backup.sql just1kbot_db:/tmp/restore.sql
   ```
7. Восстановите dump в целевую БД. Для кастомных `POSTGRES_USER` и `POSTGRES_DB`
   значения берутся из environment самого PostgreSQL container:
   ```bash
   docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/restore.sql'
   ```

   Восстановление существующей БД может столкнуться с уже существующими таблицами
   или объектами. Для полного disaster recovery предпочтительно восстанавливать
   dump в пустую целевую БД после остановки приложения.

8. Запустите бота:
   ```bash
   docker compose start bot
   ```

9. Проверьте:
   ```bash
   docker compose ps
   docker compose logs --tail=100 bot
   ```

# Безопасность

- не коммитьте `.env`;
- не храните private age key на production-сервере;
- не публикуйте `8080` бота наружу — публичный трафик должен идти через Caddy;
- используйте отдельные сильные пароли PostgreSQL и Redis;
- регулярно копируйте encrypted backups во внешнее хранилище.

# Troubleshooting

## Bot перезапускается

```bash
docker compose ps -a
docker compose logs --tail=200 bot
```

Частые причины:

- отсутствует обязательная переменная в `.env`;
- неверный `DB_ENCRYPTION_KEY`;
- PostgreSQL недоступен;
- Redis недоступен;
- некорректный формат `ADMIN_IDS`;
- некорректный `DOMAIN`, `SSL_EMAIL` или YooKassa settings.

## Caddy не выдаёт HTTPS

Проверьте:

```bash
getent hosts "$DOMAIN"
ss -lntp | grep -E ':80|:443'
docker compose logs --tail=200 caddy
```

DNS для `DOMAIN` должен указывать на VPS, а порты `80/443` должны быть доступны извне.

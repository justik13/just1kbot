# just1kbot

Telegram-бот для продажи и управления VPN-подписками через Amnezia API, с PostgreSQL, Redis, YooKassa, фоновыми задачами и административным интерфейсом.

Ветка `bot` содержит приложение и production-инсталлятор `just1kbot.sh`.

## Поддерживаемые системы

Проверяемая целевая конфигурация:

- Ubuntu 22.04 / 24.04;
- Debian 12;
- AlmaLinux / Rocky Linux 9 с `dnf` и EPEL;
- `systemd` должен работать как init-система;
- Python 3.10 или новее;
- минимум 1 ГБ свободного места, 2 ГБ RAM рекомендуется.

Скрипт устанавливает PostgreSQL, Redis, Nginx, Certbot, `age`, Python и остальные системные зависимости. Docker не требуется.

## Что подготовить до установки

1. Создайте DNS-запись `A` для домена бота, указывающую на VPS. Запись `AAAA` добавляйте только при рабочем IPv6.
2. Откройте входящие TCP-порты `80` и `443` в firewall и панели хостинга.
3. Подготовьте:
   - токен Telegram-бота;
   - Telegram ID администраторов;
   - username поддержки;
   - `shopId` и секретный ключ YooKassa;
   - реальный email для Let's Encrypt.

Инсталлятор не меняет firewall автоматически.

## Установка

```bash
curl -fsSL https://raw.githubusercontent.com/justik13/just1kbot/bot/just1kbot.sh -o just1kbot.sh
sudo bash just1kbot.sh install
```

Во время интерактивной установки скрипт запросит обязательные значения. Для локальных PostgreSQL и Redis безопасные пароли генерируются автоматически.

После успешной установки:

- сервис: `just1kbot.service`;
- CLI: `/usr/local/bin/just1kbot`;
- конфигурация: `/etc/just1kbot/just1kbot.env`;
- активный релиз: `/opt/just1kbot/current`;
- релизы: `/opt/just1kbot/releases/<commit-sha>`;
- резервные копии: `/var/backups/just1kbot`;
- webhook YooKassa: `https://ВАШ_ДОМЕН/webhook/yookassa`;
- health-check: `https://ВАШ_ДОМЕН/health`.

### Неинтерактивная установка

Все обязательные значения можно передать через окружение:

```bash
sudo env \
  NON_INTERACTIVE=1 \
  BOT_TOKEN='123456789:telegram-token' \
  ADMIN_IDS='[123456789]' \
  SUPPORT_USERNAME='support_username' \
  YOOKASSA_SHOP_ID='shop-id' \
  YOOKASSA_SECRET_KEY='secret-key' \
  DOMAIN='vpn.example.com' \
  SSL_EMAIL='admin@example.com' \
  bash just1kbot.sh install
```

Опционально можно передать собственные `DATABASE_URL`, `REDIS_URL`, `REDIS_PASSWORD`, `DB_ENCRYPTION_KEY` и `YOOKASSA_WEBHOOK_PORT`. Для внешних PostgreSQL и Redis инсталлятор не меняет конфигурацию удалённых сервисов.

## Безопасное обновление

```bash
sudo just1kbot update
```

Обновление выполняется по commit SHA и не перезаписывает текущий релиз на месте:

1. скачивается новый архив GitHub;
2. создаётся отдельный virtualenv;
3. проверяются настройки, импорты и синтаксис Python;
4. бот останавливается;
5. создаётся зашифрованный согласованный дамп БД;
6. применяются миграции Alembic;
7. атомарно переключается symlink `current`;
8. запускается сервис и проверяется локальный `/health`;
9. при ошибке кода выполняется возврат на предыдущий релиз.

Миграции БД не всегда обратимы. При несовместимой миграции используйте созданный перед обновлением бэкап и команду `restore`.

Продолжение обновления без бэкапа запрещено. Аварийное исключение:

```bash
sudo ALLOW_UPDATE_WITHOUT_BACKUP=1 just1kbot update
```

## Управление

```bash
sudo just1kbot status
sudo just1kbot doctor
sudo just1kbot restart
sudo just1kbot stop
sudo just1kbot start
sudo just1kbot logs
sudo just1kbot edit-env
```

После изменения `.env` выполните:

```bash
sudo just1kbot restart
sudo just1kbot doctor
```

Устаревшие параметры `AMNEZIA_API_URL`, `AMNEZIA_API_KEY` и `WEBHOOK_URL` автоматически удаляются из production-конфигурации: серверы Amnezia управляются через записи в БД.

## Резервные копии

```bash
sudo just1kbot backup
```

В архив входят:

- PostgreSQL dump в custom-формате;
- production `.env`;
- commit, ветка и время создания.

Архив шифруется `age` и сохраняется как:

```text
/var/backups/just1kbot/just1kbot_YYYYMMDDTHHMMSSZ.tar.gz.age
```

Приватный ключ находится в `/etc/just1kbot/backup.agekey`, имеет права `0600` и доступен только root. **Скопируйте этот ключ в отдельное защищённое хранилище. Без него восстановить архив невозможно. Не храните ключ рядом с единственной копией бэкапа.**

Восстановление:

```bash
sudo just1kbot restore /var/backups/just1kbot/just1kbot_YYYYMMDDTHHMMSSZ.tar.gz.age
```

Команда останавливает сервис, очищает текущие объекты БД через `pg_restore --clean`, восстанавливает дамп и запускает health-check. Production `.env` из архива автоматически не подменяется.

## TLS и Nginx

По умолчанию Certbot выпускает сертификат через webroot и Nginx публикует только:

- `POST /webhook/yookassa`;
- `GET /health`;
- ACME challenge;
- остальные пути возвращают `404`.

Для локального теста без сертификата допускается:

```bash
sudo INSTALL_TLS=0 bash just1kbot.sh install
```

При этом будет настроен HTTP reverse proxy, но такой режим не является production-защищённым и не должен использоваться для YooKassa.

## Диагностика

```bash
sudo just1kbot doctor
sudo journalctl -u just1kbot.service -n 200 --no-pager
sudo nginx -t
curl -fsS http://127.0.0.1:8080/health
```

Лог инсталлятора: `/var/log/just1kbot-install.log`. Токены и основные секреты маскируются перед записью.

## Удаление

Удалить приложение, сохранив конфигурацию, ключ и бэкапы:

```bash
sudo just1kbot uninstall
```

Полное удаление файлов приложения:

```bash
sudo just1kbot uninstall --purge
```

PostgreSQL, Redis, Nginx, базы данных и TLS-сертификаты автоматически не удаляются, чтобы не повредить другие сервисы на VPS.

## Разработка и проверки

```bash
bash tests/test_installer.sh
```

Проверка запускается в GitHub Actions и включает синтаксис Bash, компиляцию Python-файлов и статические инварианты production-инсталлятора.

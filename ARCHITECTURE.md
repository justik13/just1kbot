# Архитектурная карта `just1kbot`

## 1. Назначение, стек и запуск

`just1kbot` — монолитная платформа управления подписками (Standard VLESS TLS и White Internet VLESS XHTTP), биллингом (ЮKassa + внутренний баланс), HWID-устройствами и оркестрацией v2-топологии нод (`just1knode`). В одном Python-процессе работают Telegram-бот (polling), HTTP-сервер (Mini App + REST API, фиды подписок, вебхуки ЮKassa, `/health`) и супервизор фоновых воркеров.

- **Стек (`pyproject.toml`, `requirements.txt`, `Dockerfile`):** Python 3.12, `aiogram` 3, `aiohttp`, `SQLAlchemy` 2 (async) + `asyncpg`, `Alembic`, `Pydantic` v2 / `pydantic-settings`, `cryptography` (Fernet/MultiFernet), `redis` (asyncio), `cachetools`, `aiofiles`. Фронтенд — vanilla ES-modules JS + CSS без сборщика (`frontend/miniapp/`, `bot/templates/vless_landing/`).
- **Точка входа приложения:** `python -m bot.main` (`bot/main.py`).
- **Docker Compose (`docker-compose.yml`, `docker-entrypoint.sh`):**
  - `db` (PostgreSQL 16), `redis` (Redis 7.4, `maxmemory-policy noeviction`);
  - `migrate` — одноразовый контейнер `alembic upgrade head` (миграции намеренно вынесены из процесса бота; `bot` ждёт `service_completed_successfully`);
  - `bot` — read-only контейнер (`Dockerfile`), `docker-entrypoint.sh` валидирует обязательные env, собирает URL-кодированные `DATABASE_URL`/`REDIS_URL` и запускает `python -m bot.main`; `HEALTHCHECK` ходит на `GET /health`;
  - `caddy` (`Dockerfile.caddy`, `Caddyfile`, `caddy_conf.d/`) — TLS-терминация (`strict_sni_host`, `GODEBUG=multipathtcp=0`), security headers, IP-фильтр вебхуков ЮKassa, `import conf.d/*.caddy`, проксирование `/webhook/*`, `/yookassa/*`, `/sub/*`, `{$WHITE_INTERNET_SUB_PATH_PREFIX:/sub/wl}/*`, `/app*`, `/api/app/*` на `bot:8080`;
  - профиль `tools`: `backup` (`Dockerfile.backup`, `scripts/docker/backup.sh`) — `pg_dump` + `gzip` + `age`-шифрование, опциональная выгрузка по `BACKUP_REMOTE_URI` или в Google Drive (`rclone`);
  - профиль `nodes`: локальный dev-запуск нод из соседнего `../just1knode` (`DEV_NODE_API_KEY`).
- **Управляющие и сервисные скрипты (`scripts/`):**
  - `scripts/setup.sh` — первичная установка и настройка хоста (включая `vm.overcommit_memory=1`);
  - `scripts/cli.sh` — CLI `just1kbot` (`status`, `version`, `logs`, `update` с двухфазным `_update-post`, `preflight`, `backup`, `gdrive`, `restore`, `restart`, `start`, `stop`, `config`, `doctor`, `uninstall`);
  - `scripts/audit_invariants.py` — проверка 12 доменных/финансовых/БД-инвариантов;
  - `scripts/reencrypt_database.py` — поэтапная перешифровка БД при ротации `DB_ENCRYPTION_KEY`;
  - `scripts/audit_stop_words.py` — статическая проверка комплаенса пользовательских текстов и шаблонов.

## 2. Ключевые директории и зоны ответственности

- `config/` — базовый уровень без внутренних зависимостей: `enums.py` (доменные `StrEnum`), `constants.py` (тарифные лимиты, интервалы, профили XHTTP, `YOOKASSA_IP_RANGES`), `tariffs.py` (формула Standard), `settings.py` (Pydantic `Settings` + строгие валидаторы env), `guides_data.json` (SSOT гайдов и ссылок клиентов).
- `bot/` — слой интерфейсов:
  - `bot/main.py` — сборка `Bot`/`Dispatcher`, `aiohttp.web.Application`, middleware, `HealthcheckAccessLogger`, graceful shutdown и отмена фоновых задач;
  - `bot/handlers/` — Telegram-вход (`start.py`, `notifications.py`), вебхуки и `/health` (`webhook.py`), фиды подписок и лендинг (`vless_web.py`, `white_internet_web.py`);
  - `bot/keyboards/` — inline-клавиатуры (`start.py`, `notifications.py`);
  - `bot/middlewares/correlation.py` — `request_id` и безопасное мета-логирование Telegram-апдейтов без тела сообщений;
  - `bot/miniapp/` — роутер и статика (`routes.py`), авторизация/лимиты (`auth.py`), Pydantic-схемы (`schemas.py`), хендлеры пользователя (`handlers_user.py`), идентификации (`handlers_admin.py`) и админки (`admin_common.py`, `admin_nodes_handlers.py`, `admin_ops_handlers.py`, `admin_users_handlers.py`, `admin_user_adjust_handlers.py`);
  - `bot/templates/vless_landing/` и `bot/templates/vless_landing_status.html` — SSR-лендинг подписок и медиа-ассеты (`assets/`);
  - `bot/texts/` — фасад текстов (`__init__.py`, `backend.py`, `runtime/`).
- `database/` — `connection.py` (пул `asyncpg`, `session_scope`, очередь post-commit задач), `models.py` (ORM-модели и CHECK/partial-индексы), `sql_helpers.py`, `repositories/` (изолированные запросы и блокировки по сущностям).
- `alembic/` — `env.py` (async-миграции с экранированием `%` в `DATABASE_URL`) и `versions/`.
- `services/` — доменная логика: подписки (`subscription.py`, `vless_subscription_service.py`, `white_internet_service.py`), заказы/биллинг/рефералы (`order_service.py`, `fulfillment_service.py`, `order_notifications.py`, `referral_bonus.py`), авторизация Mini App (`miniapp_auth_service.py`), рендер гайдов (`guides_content.py`), админ-инструменты и рассылки (`admin_nodes_service.py`, `admin_tools_service.py`, `audit_service.py`, `broadcast_service.py`), оркестрация нод (`topology_sync_service.py`, `xray_node_client.py`), HTTP-транспорт ЮKassa (`yookassa_service.py`), фоновые воркеры (`services/workers/`).
- `integrations/payment_gateways/` — абстракция платёжного шлюза (`base.py`, `factory.py`) и реализация ЮKassa (`yookassa.py`).
- `frontend/miniapp/` — SPA Mini App (`index.html`, `css/tokens.css`, `css/components.css`, `js/app.js`, `js/api.js`, `js/views/`).
- `utils/` — шифрование (`encryption.py`), SSRF/FQDN-валидация (`security.py`), HTTP и Telegram rate-limit (`http_rate_limiter.py`, `rate_limiter.py`), редактирование секретов в логах (`logging_security.py`), парсинг клиентов/HWID (`client_parser.py`), `correlation.py`, `admin.py`, `telegram.py`, `traffic_helpers.py`.

## 3. Точки входа, основные потоки и контракт `just1kbot` ↔ `just1knode`

### Telegram-бот
- `bot/handlers/start.py`: `/start` (включая `ref_<id>`) проводит онбординг (`SubscriptionService.process_onboarding`), формирует краткую сводку баланса/подписок/реферального уровня и отдаёт inline-кнопки: Mini App (`/app`), админку (`/app?mode=admin` для `ADMIN_IDS`), реферальный шеринг и опциональный MTProto-прокси из `system_settings` (`mtproto_proxy_url`). В `bot/main.py` выставляется `MenuButtonWebApp` на `/app`.
- Все необработанные сообщения и legacy-callback'и падают в fallback-хендлеры `start.py`, направляющие в Mini App.

### Mini App и JSON API
- Регистрация маршрутов: `bot/handlers/webhook.py` → `bot/miniapp/routes.py`.
- Оболочка: `GET /app`, `/app/`, `/app/assets/{asset_path}` (включая виртуальные пути `guides_data.json` и `guides/*`).
- Авторизация (`bot/miniapp/auth.py`, `services/miniapp_auth_service.py`): проверка HMAC-SHA256 подписи Telegram `initData` по `BOT_TOKEN` из заголовков `X-Telegram-Init-Data` или `Authorization` (`tma`/`bearer`), TTL 24 ч, допустимый сдвиг в будущее 5 мин; истёкшая подпись возвращает `401` с `code: "init_data_expired"`. Админ-маршруты дополнительно проверяют `ctx.is_admin` (`ADMIN_IDS`).
- Пользовательский API (`/api/app/me`, `/api/app/user/*`): дашборд, котировка/покупка/даунгрейд Standard, покупка/продление/доп. слот/докупка ГБ/даунгрейд White Internet, пополнение баланса, ручная проверка статуса заказа, автопродление, удаление/сброс/приоритеты HWID-устройств.
- Админский API (`/api/app/admin/*`): обзор, CRUD/refresh/toggle/lifecycle нод, глобальные INCY-настройки, поиск/карточка/корректировка/DM/ротация токенов/сброс устройств пользователей, превью/запуск/отмена рассылок, массовые бонусы и продления, финансы и подтверждение ручной проверки заказов, переключатель maintenance платежей, аудит-логи.

### Подписки и веб-лендинг
- Единый обработчик `serve_unified_subscription_feed` (`bot/handlers/vless_web.py`), делегируемый также из `bot/handlers/white_internet_web.py`:
  - маршруты: `GET /sub/{token}`, `/sub/access/{token}`, `/sub/vless/{token}`, `GET {WHITE_INTERNET_SUB_PATH_PREFIX}/{token}` (по умолчанию `/sub/wl/{token}`), `GET {prefix}/ping`, `GET /sub/assets/happ_guide.mp4`, `POST .../{token}/report` (экстренное обращение в поддержку с алертом админам в Telegram).
- Логика ответа:
  1. Поиск токена в `vless_subscriptions` и `white_internet_subscriptions` (кросс-фолбэк для единой ссылки).
  2. Если нет `X-Hwid`/`X-Device-Hwid`/`X-Device-Id`: браузер получает HTML-лендинг (`bot/templates/vless_landing/` или `vless_landing_status.html` с 5с автообновлением для WI `PENDING`), не-браузер — `403` с `x-hwid-required: true`.
  3. С валидным HWID выполняется регистрация в `user_devices` (`user_devices_repo.register_user_device`) с проверкой общего лимита и 24ч churn-лимита; при превышении возвращается stub-фид со статус-заголовками лимита.
  4. Собирается Base64-фид: активные ссылки Standard (`generate_vless_links`, выбор `ru_hub` консистентным хэшем по UUID с учётом ingress-здоровья и нагрузки `<85%`) + White Internet (`generate_bi_xhttp_links` только по `ru_hub`, синхронизированным на текущую эпоху Xray) либо inline-заглушки (`127.0.0.1:443`) при pending/лимите/исчерпании. Передаются заголовки INCY/Happ (`Subscription-Userinfo`, `Profile-Title`, `Announce`, `Device-Limit`, `Device-Active-Count`, `Profile-Update-Interval`, `Routing`, DoH-параметры).

### Платежи, вебхуки и `/health`
- Вход: `POST /webhook/yookassa`, `POST /yookassa/webhook`, `GET /health` (`bot/handlers/webhook.py`).
- `GET /health`: 5-секундный in-memory кэш под single-flight lock; проверяет PostgreSQL (`SELECT 1`), Redis (`ping`) и `JUST1KBOT_HEARTBEAT_FILE` (`STOPPED` или возраст `>180` с → `503`).
- Проверка источника вебхуков: `Caddyfile` (`remote_ip`) + проверка в приложении по `YOOKASSA_IP_RANGES` + `YOOKASSA_EXTRA_IPS` через `get_trusted_client_ip`.
- Идемпотентность: канонический SHA-256 `event_key` в `webhook_inbox` с `SELECT ... FOR UPDATE`; поддерживаются события `payment.waiting_for_capture`, `payment.succeeded`, `payment.canceled`, `refund.succeeded`.
- Обработка (`services/order_service.py`, `services/fulfillment_service.py`, `services/referral_bonus.py`):
  - способы оплаты: `wallet`, `yookassa` и mixed (предварительный резерв/списание баланса FIFO: бонусы → реал + доплата через ЮKassa);
  - при неуспешном fulfillment оплаченного заказа средства компенсируются на баланс, а заказ помечается `requires_manual_review`;
  - реферальные бонусы начисляются только с реальных денег (пополнения и внешние покупки, но не pure-wallet);
  - возвраты (`refund.succeeded`) отзывают доступ/баланс, при несовпадениях сумм/ID или возврате по отменённому заказу ставят флаг ручной проверки и шлют алерт админам;
  - если заказ ещё не найден при приходе вебхука, строка `webhook_inbox` остаётся `pending`, а хендлер отвечает `503` (чтобы ЮKassa повторила доставку), также её подхватывает `cleanup`-воркер.

### Фоновые воркеры (`services/workers/__init__.py`)
Супервизор управляет 7 воркерами с экспоненциальным backoff (неблокирующим, по timestamp), 15-минутным cooldown для некритичных воркеров и Telegram-алертами админам:
1. `traffic` (`traffic.py`, 60 с) — забирает `/v1/traffic/snapshot`, начисляет дельты Standard/WI, отслеживает 90% и исчерпание квоты WI, шлёт админ-алерт при `>1 ТБ/мес` на пользователя.
2. `cleanup` (`cleanup.py`, 60 с / 1 ч / 24 ч) — сверяет зависшие `pending`-заказы в ЮKassa и `pending`-вебхуки; раз в час отключает истёкший VLESS (после `VPN_ACCESS_GRACE_HOURS = 4`) и `financial_hold`, докручивает `pending_revoked_uuids`, рассинхронизированные VLESS-подписки и дрейф эпох VLESS-серверов (`vless_synced_epoch != xray_instance_epoch` → `sync_all_active_to_server`), применяет отложенные WI-даунгрейды; раз в сутки чистит `audit_logs` (`>180 дн.`), `webhook_inbox` (`>30 дн.`) и старые `device_registration_events`.
3. `notifications` (`notifications.py`, 30 мин) — автопродление Standard/WI и уведомления за 3д/1д/2ч/истёк.
4. `credit_notifications` (`credit_notifications.py`, 5 мин) — доотправка отложенных пушей о зачислении и реферальном бонусе (`Order.metadata_`).
5. `heartbeat` (`heartbeat.py`, 60 с) — атомарно обновляет `JUST1KBOT_HEARTBEAT_FILE`, проверяемый в `GET /health`.
6. `node_monitor` (`node_monitor.py`, 60 с, параллельность до 6 нод) — опрашивает `/v1/health` и `/v1/node/status`, ведёт персистентный автомат `ONLINE` → `WAITING_CONFIRMATION` (60 с) → `PROBLEM` → `ONLINE` (3 успеха) / `MANUAL_DISABLED`, контролирует диск (`>85%`), SSL, лимит трафика хостинга (80% warn / auto-disable), синтетические ingress/CDN/bridge-зонды (с автоисключением сбойных `eu_hub` через `blocked_eu_ids`) и запускает ресинк топологии.
7. `white_internet_reconciliation` (`white_internet_reconciliation.py`, 30 с, `critical=True`) — приводит состояние WI-подписок и `device_uuid` к целевому на всех активных WI `ru_hub`, раз в 10 мин проводит inventory drift check (восстанавливает пропавшие и отключает «лишние» UUID).

### Контракт взаимодействия `just1kbot` ↔ `just1knode`
Клиент — `services/xray_node_client.py` (`XrayNodeClient`), оркестратор топологии — `services/topology_sync_service.py`, управление нодами — `services/admin_nodes_service.py`:
- **Транспорт и авторизация:** HTTPS REST (типовой порт ноды 8444), заголовок `X-API-Key` (из `servers.api_key`, зашифровано Fernet), `Accept`/`Content-Type: application/json`.
- **TLS и SSRF:** DNS-резолвер `SafeResolver` (`utils/security.py`) режет приватные/loopback/metadata адреса (если не включены `ALLOW_LOCAL_HTTP`/`ALLOW_LOCAL_HTTPS`); при заданном `XRAY_NODE_CA_FILE` проверяется CA, иначе по умолчанию допускаются самоподписанные сертификаты (`allow_insecure_tls=True`). В админке добавление ноды разрешено только по публичному FQDN (IP-литералы запрещены).
- **Ретраи и ошибки:** экспоненциальный backoff на HTTP `502/503/504`, сетевые ошибки и таймауты (`max_retries=2`, для `PUT /v1/node/links` — 1). UUID в URL маскируются в логах.
- **Эндпоинты `just1knode`, вызываемые со стороны бота:**
  - `GET /v1/health` — fail-closed проверка (`status == "ok"`, `xray_running is True`, `grpc_ok is True`), отдаёт `node_epoch`, `boot_id`, `starttime`, `capabilities`/`services`, `system_load`.
  - `GET /v1/node/status` — auto-discovery v2 (`role`: `ru_hub`/`eu_hub`/`exit_node`, домены, CDN-домены, `bridge_uuid`, `location_code`, `xhttp_locations`, `sni_routes`, `last_apply`, `ssl_cert`).
  - `PUT /v1/node/links` — пуш вычисленной топологии (SSOT = бот):
    - `exit_node`: `allowed_hub_ips`, `incoming_hubs`;
    - `eu_hub`: `allowed_ru_ips`, `incoming_ru_hubs`, `exit_nodes`, `routes` (с детерминированным UUIDv5 по `(eu_id, exit_id, eu_bridge_uuid)`);
    - `ru_hub`: `standard.bridges`, `bi.{bridges, locations, secret_base_path}`, опционально `probe_bridges`.
  - `POST /v1/clients/sync` — синхронизация одного клиента (`client_id`, `desired_state`: `active`|`disabled`, `version`, `expected_node_epoch`, `idempotency_key`, `service`: `vless`|`white_internet`, `email`). Ответ маппится в `SyncResult`: `APPLIED`, `ALREADY_NEWER`, `FENCED` (включая HTTP `409`/`412`), `FAILED`, с полями `verified_epoch`, `verified_inbounds`, `all_inbounds_verified`.
  - `POST /v1/clients/batch-sync` — пакетная синхронизация (`{"clients": [...]}` → `succeeded`, `failed`; при `404` graceful fallback на поштучный sync).
  - `POST /v1/clients/inventory` — фактическое состояние клиентов в памяти Xray (`client_ids`, `service`).
  - `GET /v1/traffic/snapshot` — счётчики трафика (`node_epoch`, `boot_id`, `starttime`, `users`, `users_by_service`, `host_tx_bytes`, `host_rx_bytes`).
- **Разделение ролей нод (`database/repositories/servers_repo.py`):** пользовательские клиентские UUID синхронизируются **исключительно на `ru_hub`**; `eu_hub` и `exit_node` аутентифицируют только межузловые bridge UUID.

## 4. Хранение данных, внешние интеграции и переменные окружения

- **СУБД:** PostgreSQL 16 (`asyncpg`, пул `pool_size=20`, `max_overflow=15`, `statement_timeout=60s`, `idle_in_transaction_session_timeout=120s`), SQLAlchemy 2 Async ORM (`database/models.py`), миграции Alembic (`alembic/versions/`).
- **Redis:** поднимается в `docker-compose.yml`, обязателен в `Settings` и пингуется в `/health`, но в текущем прикладном коде как кэш/брокер не используется (кэши и rate-limiter'ы работают в памяти процесса).
- **Ключевые таблицы (`database/models.py`) и репозитории (`database/repositories/`):**
  - `users` (`users_repo.py`) — Telegram-профиль, `referred_by`, флаги `financial_hold`/`topup_blocked`/`is_bot_blocked`/`is_deleted`, счётчики трафика;
  - `servers` (`servers_repo.py`) — v2-ноды (`role`, unique `api_url`, `api_key` через `EncryptedString(critical=True)`, `health_state`, `lifecycle_status`, `capabilities`/`extra_data` JSONB, `xray_instance_epoch`/`boot_id`/`starttime`);
  - `orders` — заказы (`standard`, `white_internet`, `topup`), статусы, уникальные partial-индексы на `external_id` и `(user_id, idempotency_key)`, `metadata` JSONB;
  - `account_ledger_entries` (`account_ledger_repo.py`) — append-only леджер в целых рублях с корзинами `real`/`bonus`, строгими CHECK-ограничениями формы записей (`payment_credit`, `purchase_debit`, `refund_debit`, `admin_adjustment`) и уникальными partial-индексами по `order_id` и `idempotency_key`;
  - `webhook_inbox` — идемпотентный приёмник вебхуков (`provider`, `event_key`, `status`, `payload` JSONB);
  - `vless_subscriptions` (`vless_subscription_repo.py`) — 1:1 с `users`, SSOT Standard-подписки (`token`, `uuid`, `is_active`, `expires_at`, `device_limit`, `downgrade_cooldown_until`, `version`/`last_synced_version`, `pending_revoked_uuids`, трафик/снапшоты, флаги уведомлений);
  - `white_internet_subscriptions` (`white_internet_repo.py`) — состояние и multi-hub синхронизация WI (`status`, `provisioning_status`, `base_traffic_bytes`/`extra_traffic_bytes`, `device_limit` 1..3, `pending_device_limit`, `desired_version`/`actual_version`, `node_sync_state` JSONB, `pending_revoked_uuids`, partial unique на одну live-подписку на пользователя);
  - `user_devices` и `device_registration_events` (`user_devices_repo.py`) — единый реестр HWID с уникальным `device_uuid`, независимыми `std_priority`/`bi_priority` и скользящим 24ч окном анти-churn;
  - `system_settings` (`system_settings_repo.py`) — runtime KV-конфиг (INCY-метаданные, `mtproto_proxy_url`, `payments_maintenance_enabled`, `wi_secret_base_path`);
  - `audit_logs` (`audit_repo.py`) — журнал аудита;
  - `broadcast_progress` — персистентное состояние рассылок (резюмируются при старте бота).
- **Внешние интеграции:** Telegram Bot API (`aiogram` + Mini App `initData`), YooKassa API v3 (`integrations/payment_gateways/yookassa.py` — HTTP Basic Auth + `Idempotence-Key` + входящие вебхуки), Just1kNode v2 API (`services/xray_node_client.py`), CDN-контуры Yandex Cloud CDN и Turboflare (через профили XHTTP в `config/constants.py` и метаданные нод), удалённые бэкапы (`age` + HTTPS PUT / Google Drive `rclone`).
- **Критичные переменные окружения (имена из кода и конфигов):**
  - Обязательные (`config/settings.py`, `docker-entrypoint.sh`): `BOT_TOKEN`, `ADMIN_IDS`, `SUPPORT_USERNAME`, `DB_ENCRYPTION_KEY`, `DATABASE_URL` (в Docker из `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`), `REDIS_URL`, `REDIS_PASSWORD` (в Docker также `REDIS_HOST`), `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_RETURN_URL` (обязателен плейсхолдер `{bot_username}`), `YOOKASSA_WEBHOOK_PORT`, `DOMAIN`, `SSL_EMAIL`.
  - Безопасность, прокси, ноды: `DB_ENCRYPTION_KEYS`, `TRUSTED_PROXIES`, `CADDY_TRUSTED_PROXIES`, `YOOKASSA_EXTRA_IPS` (строго через пробел, без запятых/`;` — требование Caddy `remote_ip`), `XRAY_NODE_CA_FILE`, `ALLOW_LOCAL_HTTP`, `ALLOW_LOCAL_HTTPS`, `WEBHOOK_HOST`, `BOT_PORT`, `DEV_NODE_API_KEY`.
  - Подписки, WI и UI: `BOT_USERNAME`, `SUBSCRIPTION_DOMAIN`, `SUBSCRIPTION_BACKUP_DOMAIN`, `WHITE_INTERNET_CDN_DOMAIN`, `WHITE_INTERNET_SUB_PATH_PREFIX`, `WHITE_INTERNET_XHTTP_PATH`, `WHITE_INTERNET_PATH`, `VLESS_TLS_FINGERPRINT`, `WHITE_INTERNET_TLS_FINGERPRINT`, `VLESS_PROFILE_TITLE`, `VLESS_PROFILE_UPDATE_INTERVAL`, параметры тарифов/XHTTP `WHITE_INTERNET_*` в `config/constants.py`.
  - Эксплуатация и бэкапы: `LOG_LEVEL`, `DOCKER_DEPLOYMENT`, `JUST1KBOT_CONFIG_DIR`, `JUST1KBOT_HEARTBEAT_FILE`, `CI_TEST_MODE`, `BACKUP_AGE_RECIPIENT`, `BACKUP_REMOTE_URI`, `BACKUP_REMOTE_TOKEN`, `GDRIVE_BACKUP_ENABLED`, `GDRIVE_RETENTION_DAYS`.

## 5. Неочевидные ограничения, инварианты и жёсткие связи

- **Порядок middleware и роутеров (`bot/main.py`):**
  - В `aiogram`: `CorrelationMiddleware` → `_PrivateChatMiddleware` (отбрасывает все не-приватные чаты); роутеры строго `notifications_router` → `start_router`, так как `start_router` содержит catch-all хендлеры.
  - В `aiohttp`: `_http_correlation_middleware` → `_http_rate_limit_middleware`; пути `/health`, вебхуки ЮKassa, `/app*`, `/api/app/*`, `/sub/*`, `{WHITE_INTERNET_SUB_PATH_PREFIX}/*` обходят общий лимитер и используют собственные лимитеры внутри хендлеров. На `web.Application` стоит `client_max_size=64KB`. `HealthcheckAccessLogger` подавляет 200 OK логи `/health` и `/ping` и маскирует токены подписок (`/sub/***`, `{prefix}/***`).
- **Блокировки и post-commit side-effects:**
  - Все финансовые операции и расчёт баланса для списания обязаны проходить через каноническую блокировку `pg_advisory_xact_lock(-user_id)` + `SELECT ... FOR UPDATE` на `users` (`database/repositories/account_ledger_repo.py` `lock_checkout_user`, `services/order_service.py` `acquire_user_account_lock`).
  - `session_scope()` (`database/connection.py`) поддерживает `queue_post_commit_task(...)`: сетевые вызовы (синк нод, депровижн, пуши) исполняются только после commit (таймаут 45 с) и очищаются при rollback. В `close_db()` post-commit задачи отменяются **до** захвата `_db_lock`, чтобы исключить дедлок.
  - `topology_sync_service.py` работает через single-flight lock с coalescing-флагом `_resync_requested`, не удерживая DB-сессию во время HTTP-вызовов к нодам.
- **Финансовые инварианты:**
  - Денежные суммы — строго целые рубли (`Decimal`, `float` запрещён в `whole_rubles`). При отрицательной корзине `real` (долг после возврата) `available` обнуляется для обеих корзин.
  - Мутация JSONB-полей (`Order.metadata_`, `Server.extra_data`, `node_sync_state`) требует переприсвоения нового словаря или `flag_modified`.
- **HWID, слоты, детерминированные UUID и формат Xray email:**
  - Потолок регистраций устройств = `std_limit + wi_slot_limit`, но слоты Standard и White Internet распределяются независимо по `std_priority` и `bi_priority` (`< 0` явно исключает устройство из контура). Поверх действует суточный анти-churn лимит (`device_registration_events`).
  - Для Standard VLESS на v2-нодах для каждой локации `exit_node` генерируется детерминированный `uuid5(device_uuid, loc_code)` и синхронизируется с Xray email `<device_uuid>:<loc_code>:std` (`services/vless_subscription_service.py`): суффикс `:<loc_code>:std` используется нодой `just1knode` и для маршрутизации `:443` на нужный мост, и для обратной агрегации трафика по `<device_uuid>` в `/v1/traffic/snapshot`. При отзыве устройства/подписки `_expand_revocation_uuids` обязан отзывать все производные локационные UUID.
  - В White Internet профиль Yandex Cloud CDN (`CANONICAL_XHTTP_PROFILE` в `config/constants.py`) принудительно использует bodiless `GET` с `uplinkDataPlacement="header"` (edge возвращает `413` на body); Turboflare использует `POST` + `body` (`CANONICAL_XHTTP_TURBOFLARE_PROFILE`).
- **Инварианты reconciliation, мониторинга и сети:**
  - `white_internet_reconciliation` — единственный `critical=True` воркер: исчерпание бюджета рестартов останавливает процесс через `shutdown_event` и гасит heartbeat (`/health` → `503`).
  - Синхронизация клиентов защищена epoch fencing (`expected_node_epoch`): на `FENCED` (`409`/`412`) воркер обновляет `servers.xray_instance_epoch` из `/v1/health`; на `ALREADY_NEWER` требуется верификация через `/v1/clients/inventory` и `verified_inbounds`.
  - В `topology_sync_service.py` действует fail-safe для `blocked_eu_ids`: если все `eu_hub` помечены как проблемные, маршруты всё равно сохраняются, чтобы `ru_hub` не остался без мостов.
  - Активные `ru_hub` автоматически добавляются в динамический список доверенных прокси (`utils/http_rate_limiter.py`) для корректного извлечения клиентского IP.
  - В `docker-compose.yml` все bridge-сети имеют `MTU=1440` (ниже host uplink), чтобы не терялись крупные TLS-записи при обращении к API ЮKassa.
- **Шифрование, песочница и startup-гарды:**
  - `EncryptedString(critical=True)` (для `servers.api_key`) работает fail-closed: ошибка расшифровки вызывает `RuntimeError`.
  - При запуске под системным пользователем `just1kbot` `config/settings.py` переопределяет `HOME=/run/just1kbot`, чтобы `asyncpg`/`libpq` не падали при `ProtectHome=true`.
  - Вне Docker (`DOCKER_DEPLOYMENT != "true"`) `bot/main.py` прерывает запуск при отсутствии `backup.agekey` в `JUST1KBOT_CONFIG_DIR`.
  - `scripts/reencrypt_database.py` требует включённого `payments_maintenance_enabled` в `system_settings` (кроме `--force`).
- **Связки backend ↔ frontend и комплаенс:**
  - `frontend/miniapp/css/tokens.css` является единым источником CSS-токенов и для Mini App, и для SSR-лендинга (`bot/handlers/vless_web.py`).
  - `config/guides_data.json` и медиа в `bot/templates/vless_landing/assets/` переиспользуются одновременно лендингом (`services/guides_content.py`, `/sub/assets/happ_guide.mp4`) и Mini App (`/app/assets/guides_data.json`, `/app/assets/guides/*`).
  - `bot/miniapp/routes.py` на лету переписывает маркеры `?v=X.Y.Z` в HTML/CSS/JS на SHA-256 хэш ассетов (плюс `ETag`/`304`), поэтому в `frontend/miniapp/index.html` и ES-импортах `frontend/miniapp/js/` необходимо сохранять `?v=...`.
  - `bot/texts/__init__.py` при импорте падает на коллизии ключей между `backend.py` и `runtime/`; `utils/logging_security.py` ставит глобальный фильтр редактирования секретов на root-логгер; `.github/workflows/tests.yml` дополнительно проверяет миграции (включая `downgrade`), синтаксис JS/shell и консистентность зависимостей.

# 🏛️ JUST1KBOT ARCHITECTURE & SECURITY REFERENCE

> **ОБЛАСТЬ ДЕЙСТВИЯ:** Технический справочник по архитектуре бэкенда, модели данных, криптографической защите, сетевой топологии, асинхронным воркерам и процедурам развёртывания Telegram-бота `just1kbot`.

---

## 🏗️ 1. СТЕК ТЕХНОЛОГИЙ И КОМПОНЕНТЫ

| Компонент | Технология | Назначение |
|---|---|---|
| **Core Framework** | Python 3.12, `aiogram 3.x`, `aiohttp` | Асинхронный Telegram-бот и HTTP/Webhook сервер |
| **База данных** | PostgreSQL 16, `SQLAlchemy 2.0 (asyncio)`, `asyncpg`, `Alembic` | Реляционное хранилище с транзакционной целостностью и row-level locking |
| **Кэш & Блокировки** | Redis 7, `redis-py (asyncio)` | Rate limiters, FSM storage, single-flight locks, deduplication |
| **Reverse Proxy** | Caddy 2 | TLS-терминация, HSTS, X-Content-Type-Options, защита от атак |
| **VPN Core API** | `kyoresuas/amnezia-api` (Fastify / AWG 2.0) | Сторонний REST API для управления пирами AmneziaWG на серверах |
| **Платежи** | ЮKassa API v3 (webhook inbox, reconciliation) | Обработка платежей с проверкой IP, статусов и идемпотентности |
| **Шифрование** | `cryptography` (MultiFernet, HMAC-SHA256, age) | Защита данных в БД (MultiFernet), веб-мостов (HMAC-SHA256) и дампов (age) |

---

## 🔐 2. КРИПТОГРАФИЧЕСКАЯ ЗАЩИТА И БЕЗОПАСНОСТЬ

### 2.1 MultiFernet шифрование в PostgreSQL (`utils/encryption.py`)
* Поля `raw_config`, `api_key`, `subscription_token` и другие чувствительные данные шифруются на уровне приложения с помощью типа данных `EncryptedString` (Fernet / MultiFernet: AES-128-CBC + HMAC-SHA256).
* Поддерживается плавная ротация ключей через `DB_ENCRYPTION_KEYS` (список ключей: первый для записи новых данных, все для чтения существующих).

### 2.2 Веб-мост Amnezia (`/amnezia/open/{profile_id}`)
* Безопасная передача ключа по схеме HMAC-SHA256:
  `canonical_string = f"amnezia:{BRIDGE_TOKEN_VERSION}:{profile_id}:{user_id}:{exp}"`  
  `sig = hmac_sha256(AMNEZIA_BRIDGE_HMAC_SECRET, canonical_string)`
* TTL ссылки ограничен 15 минутами (`exp = now + 900`).
* Защита от перебора: Rate Limiting по доверенному IP (Sliding Window, Redis/In-Memory).
* Полная изоляция: страница отдаёт `Cache-Control: no-store, private` и строгие CSP-заголовки.

### 2.3 Фид подписки INCY (`/sub/{token}`)
* Авторизация по криптостойкому токену `token = secrets.token_urlsafe(32)` (длина 43 символа, лимит поля `VARCHAR(64)`).
* Атомарная генерация и ротация с row-level locking в PostgreSQL (`SELECT ... FOR UPDATE` + `populate_existing=True`).
* Защита от перебора: Rate Limiter на 30 запросов в минуту с плавным восстановлением (Token Bucket).
* Заголовки ответа: `Cache-Control: no-store`, `Content-Type: text/plain; charset=utf-8`.

---

## 🔄 3. ФОНОВЫЕ ВОРКЕРЫ И СИНХРОНИЗАЦИЯ

| Воркер | Файл | Ответственность |
|---|---|---|
| **Notifications** | `services/workers/notifications.py` | Оповещения об окончании подписки (3 дня, 1 день, истекла). Построчная блокировка `FOR UPDATE SKIP LOCKED` с мгновенным коммитом флагов до отправки в Telegram (zero-double-send). |
| **Webhook Inbox** | `services/workers/webhook_inbox.py` | Обработка входящих платежных вебхуков из очереди с проверкой идемпотентности. |
| **Backup** | `scripts/docker/backup.sh` (cron + compose profile `tools`) | Шифрованный `age` дамп PostgreSQL по расписанию; опциональная выгрузка артефакта в удалённое HTTPS-хранилище. |
| **Supervisor** | `services/workers/__init__.py` | Супервизия фоновых воркеров: экспоненциальный backoff, stability window, cooldown и fatal-shutdown для критичных задач. |

---

## 🚀 4. ДЕПЛОЙМЕНТ И ПРОИЗВОДСТВЕННЫЕ КОМАНДЫ

### 4.1 Обновление продакшена в 1 клик
```bash
just1kbot update
```
*(Выполняет: автоматический бэкап БД `just1kbot_YYYYMMDD_HHMMSS.sql.gz.age` -> `git pull` -> `alembic upgrade head` -> `docker compose up -d --build`)*

### 4.2 Консоль управления
```bash
just1kbot logs [service]    # Просмотр логов контейнеров
just1kbot restart           # Перезапуск сервисов
just1kbot backup            # Создание внеочередного бэкапа
just1kbot status            # Проверка статуса сервисов
```

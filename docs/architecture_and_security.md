# 🏛️ JUST1KBOT ARCHITECTURE & SECURITY REFERENCE

> **ОБЛАСТЬ ДЕЙСТВИЯ:** Технический справочник по архитектуре бэкенда, модели данных, криптографической защите, сетевой топологии, асинхронным воркерам и процедурам развёртывания Telegram-бота `just1kbot`.

---

## 🏗️ 1. СТЕК ТЕХНОЛОГИЙ И КОМПОНЕНТЫ

| Компонент | Технология | Назначение |
|---|---|---|
| **Core Framework** | Python 3.12, `aiogram 3.x`, `aiohttp` | Асинхронный Telegram-бот и HTTP/Webhook сервер |
| **База данных** | PostgreSQL 16, `SQLAlchemy 2.0 (asyncio)`, `asyncpg`, `Alembic` | Реляционное хранилище с транзакционной целостностью |
| **Кэш & Блокировки** | Redis 7, `redis-py (asyncio)` | Rate limiters, FSM storage, single-flight locks, deduplication |
| **Reverse Proxy** | Caddy 2 | TLS-терминация, HSTS, X-Content-Type-Options, защита от атак |
| **VPN Core API** | `kyoresuas/amnezia-api` (Fastify / AWG 2.0) | Управление пирами WireGuard/AmneziaWG на серверах |
| **Платежи** | ЮKassa API v3 (webhook inbox, reconciliation) | Обработка платежей с криптографической проверкой IP и ID |
| **Шифрование** | `cryptography` (MultiFernet, AES-256-GCM, HMAC-SHA256, age) | Защита токенов в БД, веб-мостов и резервных копий |

---

## 🔐 2. КРИПТОГРАФИЧЕСКАЯ ЗАЩИТА И БЕЗОПАСНОСТЬ

### 2.1 MultiFernet шифрование в PostgreSQL (`utils/encryption.py`)
* Поля `raw_config`, `api_key`, `subscription_token` и другие чувствительные данные шифруются на уровне приложения с помощью типа данных `EncryptedString`.
* Поддерживается плавная ротация ключей через `DB_ENCRYPTION_KEYS` (список ключей: первый для записи, все для чтения).

### 2.2 Веб-мост Amnezia (`/amnezia/open/{profile_id}`)
* Безопасная передача ключа по схеме HMAC-SHA256:
  `sig = hmac_sha256(AMNEZIA_BRIDGE_HMAC_SECRET, f"{profile_id}:{uid}:{exp}")`
* TTL ссылки ограничен 15 минутами (`exp = now + 900`).
* Защита от перебора: Rate Limiting по доверенному IP (Sliding Window, Redis/In-Memory).
* Полная изоляция: страница отдаёт `Cache-Control: no-store, private` и строгие CSP-заголовки.

### 2.3 Фид подписки INCY (`/sub/{token}`)
* Авторизация по криптостойкому токену `token = secrets.token_urlsafe(32)`.
* Изоляция и защита от перебора: Rate Limiter на 30 запросов в минуту с плавным восстановлением (Token Bucket).
* Заголовки ответа: `Cache-Control: no-store`, `Content-Type: text/plain; charset=utf-8`.

---

## 🔄 3. ФОНОВЫЕ ВОРКЕРЫ И СИНХРОНИЗАЦИЯ

| Воркер | Файл | Ответственность |
|---|---|---|
| **Notifications** | `services/workers/notifications.py` | Оповещения об окончании подписки (3 дня, 1 день, истекла). Построчная блокировка `FOR UPDATE SKIP LOCKED` с мгновенным коммитом флагов до отправки в Telegram (zero-double-send). |
| **Webhook Inbox** | `services/workers/webhook_inbox.py` | Обработка входящих платежных вебхуков из очереди с проверкой идемпотентности. |
| **Backup** | `services/workers/backup.py` | Автоматический шифрованный дамп базы данных через `age` с ротацией и отправкой в S3/Telegram. |
| **Supervisor** | `services/workers/supervisor.py` | Мониторинг жизненного цикла всех фоновых задач с автоперезапуском при сбоях. |

---

## 🚀 4. ДЕПЛОЙМЕНТ И ПРОИЗВОДСТВЕННЫЕ КОМАНДЫ

### 4.1 Обновление продакшена в 1 клик
```bash
just1kbot update
```
*(Выполняет: автоматический бэкап БД `just1kbot_YYYYMMDD_HHMMSS.sql.gz.age` -> `git pull` -> `alembic upgrade head` -> `docker compose up -d --build`)*

### 4.2 Консоль управления
```bash
just1kbot
```
*(Интерактивное меню: мониторинг здоровья, чтение логов, управление серверами, бэкапы)*

### 4.3 Проверка локальных тестов
```powershell
$env:PYTHONPATH="."
pytest -v
ruff check bot config database services utils alembic scripts tests --select E4,E7,E9,F,B,ASYNC,PLE,PLW,RUF100 --ignore PLW0603,PLW0108 --output-format full
```

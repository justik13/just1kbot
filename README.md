# Just1K Bot — Telegram VPN Subscription Bot

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Aiogram](https://img.shields.io/badge/aiogram-3.x-blue.svg)](https://docs.aiogram.dev/)
[![SQLAlchemy](https://img.shields.io/badge/sqlalchemy-2.x-green.svg)](https://www.sqlalchemy.org/)
[![PostgreSQL](https://img.shields.io/badge/postgresql-14+-blue.svg)](https://www.postgresql.org/)

Telegram-бот для продажи подписок на VPN (AmneziaWG) с интеграцией платёжной системы YooKassa.

---

## 📋 Оглавление

- [Возможности](#-возможности)
- [Архитектура](#-архитектура)
- [Требования](#-требования)
- [Установка](#-установка)
- [Конфигурация](#-конфигурация)
- [Запуск](#-запуск)
- [Развёртывание в production](#-развёртывание-в-production)
- [База данных и миграции](#-база-данных-и-миграции)
- [Резервное копирование](#-резервное-копирование)
- [Мониторинг и логи](#-мониторинг-и-логи)
- [Структура проекта](#-структура-проекта)
- [API и вебхуки](#-api-и-вебхуки)
- [Безопасность](#-безопасность)
- [Troubleshooting](#-troubleshooting)

---

## ✨ Возможности

### Для пользователей:
- 🛒 Покупка подписок через YooKassa
- 📱 Автоматическая генерация VPN-ключей (AmneziaWG)
- 👥 Реферальная система с бонусами
- 🔔 Уведомления об истечении подписки (3 дня, 1 день, 2 часа, expired, grace period 12ч)
- 📊 Личный кабинет с управлением устройствами
- 🌍 Выбор серверов (страны)

### Для администраторов:
- 📈 Дашборд со статистикой
- 👥 Управление пользователями (бан, разбан, продление)
- 🖥️ Управление серверами (добавление, удаление, активация)
- 💰 Управление тарифами
- 📢 Рассылки с прогрессом
- 🔍 Просмотр платежей
- 📝 Audit log всех действий

---

## 🏗️ Архитектура

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Telegram  │────▶│  Aiogram Bot │────▶│  Services   │
│   Clients   │◀────│  (main.py)   │◀────│   Layer     │
└─────────────┘     └──────────────┘     └─────────────┘
                           │                    │
                           ▼                    ▼
                    ┌──────────────┐     ┌─────────────┐
                    │    Redis     │     │  PostgreSQL │
                    │   (storage)  │     │   (data)    │
                    └──────────────┘     └─────────────┘
                                                │
                                                ▼
                                         ┌─────────────┐
                                         │   Amnezia   │
                                         │   Servers   │
                                         └─────────────┘
```

**Слои:**
1. **Bot layer** (`bot/`) — handlers, middlewares, keyboards, states
2. **Services layer** (`services/`) — бизнес-логика (платежи, VPN, рассылки)
3. **Database layer** (`database/`) — модели, репозитории, connection pool
4. **Utils** (`utils/`) — хелперы, безопасность, шифрование

---

## 📦 Требования

| Компонент | Версия | Примечание |
|-----------|--------|------------|
| Python | 3.10+ | Обязательно |
| PostgreSQL | 14+ | Рекомендуется 15+ |
| Redis | 6+ | Для FSM storage и кэша |
| Docker | 20+ | Опционально, для контейнеризации |

---

## 🚀 Установка

### 1. Клонирование репозитория

```bash
git clone <repository-url>
cd just1kbot
```

### 2. Создание виртуального окружения

```bash
python3.10 -m venv venv
source venv/bin/activate  # Linux/macOS
# или
venv\Scripts\activate  # Windows
```

### 3. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 4. Настройка переменных окружения

```bash
cp .env.example .env
# Отредактируйте .env, заполнив все обязательные поля
nano .env
```

### 5. Инициализация базы данных

```bash
# Создайте базу данных и пользователя в PostgreSQL
sudo -u postgres psql
CREATE DATABASE just1kbot;
CREATE USER just1kbot_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE just1kbot TO just1kbot_user;
\q

# Примените миграции (если используется Alembic)
# alembic upgrade head

# Или создайте таблицы (только для dev!)
# python -c "from database.connection import init_db; import asyncio; asyncio.run(init_db())"
```

---

## ⚙️ Конфигурация

### Переменные окружения (.env)

| Переменная | Обязательна | Описание | Пример |
|------------|-------------|----------|--------|
| `BOT_TOKEN` | ✅ | Токен бота от @BotFather | `1234567890:ABCdef...` |
| `ADMIN_IDS` | ✅ | Список ID администраторов | `[123456789, 987654321]` |
| `DATABASE_URL` | ✅ | PostgreSQL connection string | `postgresql+asyncpg://user:pass@localhost:5432/dbname` |
| `DB_ENCRYPTION_KEY` | ✅ | Ключ шифрования Fernet (32 байта base64) | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx=` |
| `REDIS_URL` | ❌ | Redis connection URL | `redis://localhost:6379/0` |
| `YOOKASSA_SHOP_ID` | ❌ | Shop ID в YooKassa | `123456` |
| `YOOKASSA_SECRET_KEY` | ❌ | Secret key YooKassa | `test_xxxxx...` |
| `YOOKASSA_WEBHOOK_PORT` | ❌ | Порт для вебхуков | `8080` |
| `SUPPORT_USERNAME` | ❌ | Username поддержки | `support` |
| `ALLOW_LOCAL_HTTP` | ❌ | Разрешить локальные HTTP запросы | `false` |

### Генерация ключа шифрования

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## ▶️ Запуск

### Development

```bash
python bot/main.py
```

### Production (с supervisor/systemd)

#### Systemd unit (`/etc/systemd/system/just1kbot.service`):

```ini
[Unit]
Description=Just1K Telegram Bot
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=just1kbot
Group=just1kbot
WorkingDirectory=/opt/just1kbot
Environment="PATH=/opt/just1kbot/venv/bin"
ExecStart=/opt/just1kbot/venv/bin/python bot/main.py
Restart=always
RestartSec=10

# Security
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable just1kbot
sudo systemctl start just1kbot
sudo systemctl status just1kbot
```

---

## 🗄️ База данных и миграции

### ⚠️ Важно!

**Не используйте `create_all()` в production!** Это может привести к потере данных при изменении схемы.

### Использование Alembic (рекомендуется)

```bash
# Инициализация (один раз)
alembic init alembic

# Настройка alembic.ini
# env.py: target_metadata = Base.metadata

# Создание миграции
alembic revision --autogenerate -m "Initial tables"

# Применение миграций
alembic upgrade head

# Откат
alembic downgrade -1
```

### Текущая схема БД

| Таблица | Описание |
|---------|----------|
| `users` | Пользователи Telegram |
| `vpn_profiles` | VPN-профили (устройства) |
| `servers` | Серверы Amnezia |
| `tariffs` | Тарифные планы |
| `payments` | Платежи |
| `payment_events` | События платежей (audit) |
| `audit_logs` | Лог действий админов |
| `broadcast_progress` | Прогресс рассылок |
| `pending_api_deletions` | Очередь удаления из API |
| `maintenance_mode` | Режим обслуживания |
| `hub_messages` | Сообщения хаб-бота |

---

## 💾 Резервное копирование

### Backup базы данных

```bash
# Полный дамп
pg_dump -U just1kbot_user -h localhost just1kbot > backup_$(date +%Y%m%d_%H%M%S).sql

# Сжатый дамп
pg_dump -U just1kbot_user -h localhost just1kbot | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Восстановление
psql -U just1kbot_user -h localhost just1kbot < backup_YYYYMMDD_HHMMSS.sql
```

### Cron для автоматического backup

```bash
# /etc/cron.d/just1kbot-backup
0 3 * * * root pg_dump -U just1kbot_user just1kbot | gzip > /backups/just1kbot_$(date +\%Y\%m\%d).sql.gz
```

### Хранение бэкапов

- Локально: `/backups/` (минимум 7 дней)
- S3-compatible storage (рекомендуется)
- Шифрование чувствительных данных перед отправкой

---

## 📊 Мониторинг и логи

### Логирование

Логи пишутся в stdout/stderr с форматом:
```
2024-01-15 10:30:45 - INFO - [req-abc123] bot.main: Bot started
```

**Фильтрация секретов:**
- API keys
- Tokens
- Database URLs
- JWT
- VPN URI

### Интеграция с Sentry (рекомендуется)

```bash
pip install sentry-sdk
```

```python
# bot/main.py
import sentry_sdk

sentry_sdk.init(
    dsn="https://your-dsn@sentry.io/project-id",
    traces_sample_rate=0.1,
    environment="production",
)
```

### Алерты

Бот автоматически отправляет алерты админам при:
- Критических ошибках (дедупликация 5 мин)
- Падении воркеров
- Проблемах с платежами

---

## 📁 Структура проекта

```
just1kbot/
├── bot/                      # Bot layer
│   ├── main.py              # Entry point
│   ├── handlers/            # Command handlers
│   │   ├── admin/           # Admin commands
│   │   │   ├── broadcast.py # Рассылки
│   │   │   ├── dashboard.py # Дашборд
│   │   │   ├── servers.py   # Управление серверами
│   │   │   ├── tariffs.py   # Управление тарифами
│   │   │   ├── users.py     # Управление пользователями
│   │   │   └── payments.py  # Просмотр платежей
│   │   ├── connection.py    # Подключение VPN
│   │   ├── payment.py       # Оплата
│   │   ├── profile.py       # Профиль
│   │   ├── start.py         # /start
│   │   ├── support.py       # Поддержка
│   │   └── webhook.py       # YooKassa webhook
│   ├── keyboards/           # Inline & reply клавиатуры
│   ├── middlewares/         # Middleware цепочка
│   │   ├── ban_check.py     # Проверка бана
│   │   ├── clean_chat.py    # Очистка чата
│   │   ├── correlation.py   # Request ID
│   │   ├── db_session.py    # DB session
│   │   ├── throttling.py    # Rate limiting
│   │   └── user_context.py  # Контекст пользователя
│   ├── states.py            # FSM states
│   ├── texts.py             # Тексты интерфейса
│   └── constants.py         # Константы
│
├── config/
│   └── settings.py          # Pydantic settings
│
├── database/
│   ├── models.py            # SQLAlchemy модели
│   ├── connection.py        # Connection pool
│   └── repositories/        # Репозитории
│       ├── user_repo.py
│       ├── payment_repo.py
│       └── ...
│
├── services/                # Business logic
│   ├── amnezia_client.py    # Amnezia API client
│   ├── yookassa_service.py  # YooKassa integration
│   ├── payment_service/     # Payment logic
│   ├── subscription.py      # Subscription management
│   ├── device_service.py    # Device management
│   ├── referral_service.py  # Referral system
│   ├── ban_service.py       # Ban/unban
│   ├── maintenance_service.py # Maintenance mode
│   ├── audit_service.py     # Audit logging
│   ├── workers/             # Background workers
│   │   ├── heartbeat.py
│   │   ├── notifications.py
│   │   └── cleanup.py
│   └── slots_cache.py       # Slot caching
│
├── utils/                   # Helpers
│   ├── security.py          # SSRF protection
│   ├── encryption.py        # EncryptedString type
│   ├── rate_limiter.py      # Rate limiting
│   ├── datetime_helpers.py  # Time utilities
│   ├── formatters.py        # Formatting helpers
│   └── ...
│
├── docs/
│   └── amnezia_docs.md      # Amnezia API docs
│
├── deploy.sh                # Deploy script
├── setup-amnezia-api.sh     # Amnezia setup
├── uninstall.sh             # Uninstall script
├── requirements.txt         # Dependencies
├── .env.example             # Environment template
└── README.md                # This file
```

---

## 🔌 API и вебхуки

### YooKassa Webhook

**Endpoint:** `POST http://127.0.0.1:8080/webhook/yookassa`

**Обработчики событий:**
- `payment.succeeded` — оплата успешна
- `payment.waiting_for_capture` — ожидание подтверждения
- `payment.canceled` — отмена платежа

**Flow:**
1. Пользователь создаёт платёж → `Payment` со статусом `pending`
2. YooKassa отправляет webhook → обновление статуса
3. При `succeeded` → продление подписки, начисление бонусов
4. Аудит-событие в `payment_events`

### Amnezia API

**Протоколы:** `amneziawg2`, `openvpn`, `ikev2`

**Методы:**
- `GET /api/v1/status` — статус сервера
- `POST /api/v1/client` — создание клиента
- `DELETE /api/v1/client/{peer_id}` — удаление клиента
- `GET /api/v1/client/{peer_id}/status` — статус клиента

---

## 🔒 Безопасность

### Защита данных

| Механизм | Описание |
|----------|----------|
| **Шифрование БД** | `EncryptedString` для API keys, configs |
| **Fernet** | Симметричное шифрование (AES-128-CBC) |
| **SSRF Protection** | Блокировка локальных IP в запросах |
| **Rate Limiting** | Ограничение запросов от пользователей |
| **Input Sanitization** | Очистка HTML в логах |

### Секреты в логах

Автоматическая маскировка:
- `api_key=[REDACTED]`
- `[DB_URL_REDACTED]`
- `[JWT_REDACTED]`
- `[VPN_URI_REDACTED]`

### Доступ к серверам

- Только HTTPS для внешних запросов
- Валидация SSL сертификатов
- Circuit breaker для API вызовов

---

## 🐛 Troubleshooting

### Бот не запускается

```bash
# Проверка логов
journalctl -u just1kbot -f

# Проверка .env
python -c "from config.settings import get_settings; print(get_settings())"

# Проверка подключения к БД
psql -U just1kbot_user -h localhost -d just1kbot -c "SELECT 1"
```

### Ошибка `DB_ENCRYPTION_KEY невалиден`

```bash
# Сгенерируйте новый ключ
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Обновите .env
```

### Платежи не проходят

1. Проверьте webhook:
```bash
curl -X POST http://127.0.0.1:8080/webhook/yookassa \
  -H "Content-Type: application/json" \
  -d '{"type":"payment.succeeded","object":{"id":"test"}}'
```

2. Проверьте логи вебхука
3. Убедитесь, что порт 8080 открыт для YooKassa

### VPN не работает после оплаты

1. Проверьте `pending_api_deletions` — возможно, неудачное удаление
2. Проверьте статус серверов в админке
3. Перезапустите воркер уведомлений

### Рассылка зависла

```sql
-- Сброс зависшей рассылки
UPDATE broadcast_progress 
SET status = 'cancelled', updated_at = NOW() 
WHERE status = 'in_progress' 
  AND updated_at < NOW() - INTERVAL '1 hour';
```

---

## 📞 Поддержка

- Telegram: @your_support_username
- Email: support@example.com

---

## 📝 License

Proprietary. All rights reserved.

---

## 🔄 Changelog

### v1.0.0 (2024)
- Initial release
- YooKassa integration
- AmneziaWG support
- Referral system
- Admin dashboard
- Broadcast system
- Grace period (12h)
- Self-healing (pending API deletions)
- Circuit breakers
- Structured logging with request IDs

---

## ⚠️ Known Issues & TODO

### P0 — Блокеры
- [ ] Внедрить Alembic для миграций БД
- [ ] Добавить retry для `_sync_expires_to_servers`
- [ ] Написать тесты на платёжную логику

### P1 — Первый месяц
- [ ] Уменьшить `STALE_PAYMENT_THRESHOLD` до 300–600с
- [ ] Добавить Sentry
- [ ] Уменьшить `pool_size` до 10
- [ ] Добавить лимит на pending-платежи

---

**Generated:** 2024
**Version:** 1.0.0
